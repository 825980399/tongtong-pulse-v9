# -*- coding: utf-8 -*-
"""★第348批 回归测试（刀1③ / 刀2② / 刀4④ / 刀5 / 刀8 / 刀10）。

纪律：
  * **不得依赖现场数据**（铁律179）：所有用例用 `tmp_path` 造隔离根，
    生产 `data/` 一律只读或干脆不碰。
  * 每个判据必须带**负控**（判别力自证，铁律168）：只证"能通过"等于没验。
"""
import io
import json
import logging
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ============================ 刀5 容量闸门 ============================
def _mk_nodes(n, lvl=None, imp=None):
    from nucleus.mnemosyne.PulseNode import PulseNode
    _lvl = lvl or PulseNode.EVOL_L2
    _imp = imp or PulseNode.IMPORTANCE_S  # L2+S ⇒ 走热池
    return [PulseNode(value="m348-node-%03d" % i, evol_level=_lvl,
                      importance=_imp, source_organ="m348_test")
            for i in range(n)]


def test_d5_load_batch_hits_capacity_gate():
    """批量装载后热池被闸门夹紧（正控）。"""
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    p = PulseNodePool(max_hot=5, max_warm=50000)
    p.load_batch(_mk_nodes(20))
    assert len(p._hot) == 5
    assert len(p._warm) == 15


def test_d5_gate_is_the_cause_not_something_else():
    """负控：把闸门打掉，同样的输入必须越界 —— 否则「夹紧」不是闸门干的。

    这是本刀最关键的一条：没有它，`len(hot)==5` 可能来自任何别处。
    """
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    p = PulseNodePool(max_hot=5, max_warm=50000)
    p._enforce_capacity = lambda: None
    p.load_batch(_mk_nodes(20))
    assert len(p._hot) == 20
    assert len(p._warm) == 0


def test_d5_load_batch_lazy_hits_capacity_gate():
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    p = PulseNodePool(max_hot=5, max_warm=50000)
    p._cold_storage_enabled = False  # 委派分支
    p.load_batch_lazy(_mk_nodes(20))
    assert len(p._hot) == 5


# ============================ 刀8 池指纹台账 ============================
def _report(tag, n=3):
    return {"issues": [{"type": "dead_code", "severity": "low",
                        "file": "%s_%d.py" % (tag, i), "line": i + 1,
                        "method": "m%d" % i, "desc": "d"} for i in range(n)]}


def test_d8_fingerprint_written_and_real_delete_detected(tmp_path):
    import nucleus.audit_problem_pool as P
    root = str(tmp_path)
    ad = os.path.join(root, "audit")
    t1 = 1792740000.0
    P.persist_pool(_report("a"), root, ad, now=t1)
    led = P.read_ledger(root, ad)
    assert led["state"] == "ok"
    assert led["last_known_snapshot"]["snapshot"].endswith(".json")
    assert led["last_known_snapshot"]["entries_sha1_16"]

    snaps = P.list_snapshots(root, ad)
    assert len(snaps) == 1
    os.remove(snaps[0])
    d = P.detect_pool_loss(root, ad, t1)
    assert d["state"] == "ok"
    assert d["missing"] == [os.path.basename(snaps[0])]
    assert d["unregistered"] == []


def test_d8_unregistered_snapshot_detected(tmp_path):
    """任务书原话方向：快照存在但指纹缺失。"""
    import nucleus.audit_problem_pool as P
    root = str(tmp_path)
    ad = os.path.join(root, "audit")
    P.persist_pool(_report("a"), root, ad, now=1792740000.0)
    rogue = os.path.join(ad, "audit_20260101.json")
    with io.open(rogue, "w", encoding="utf-8") as f:
        f.write('{"entries": []}')
    d = P.detect_pool_loss(root, ad, 1792740000.0)
    assert d["unregistered"] == ["audit_20260101.json"]


def test_d8_quiet_negative_control(tmp_path):
    """什么都没动 ⇒ 0 告警（不许假报）。"""
    import nucleus.audit_problem_pool as P
    root = str(tmp_path)
    ad = os.path.join(root, "audit")
    P.persist_pool(_report("a"), root, ad, now=1792740000.0)
    d = P.detect_pool_loss(root, ad, 1792740000.0)
    assert d["alarms"] == 0


def test_d8_ledger_unreadable_is_not_absent(tmp_path):
    """台账写坏 ≠ 首次运行：必须报不可判，不得给出 0 的假清白。"""
    import nucleus.audit_problem_pool as P
    root = str(tmp_path)
    ad = os.path.join(root, "audit")
    P.persist_pool(_report("a"), root, ad, now=1792740000.0)
    with io.open(P.ledger_path(root, ad), "w", encoding="utf-8") as f:
        f.write("{ not json")
    d = P.detect_pool_loss(root, ad, 1792740000.0)
    assert d["state"] == "ledger_unreadable"
    assert d["missing"] is None and d["unregistered"] is None


# ============================ 刀10 撤单与协议豁免 ============================
def test_d10_wont_fix_legal_transition():
    from nucleus.evolution.patch_lifecycle import (
        HISTORY_STATUS_WONT_FIX, mark_history_wont_fix, PATCH_HISTORY_STATUSES)
    assert HISTORY_STATUS_WONT_FIX in PATCH_HISTORY_STATUSES
    p = {"id": "x", "status": "approved", "o3_auto_dispatch_ready": True}
    r = mark_history_wont_fix(p, reason="协议接口", by="human:test")
    assert r["ok"] is True and r["changed"] is True
    assert p["status"] == "wont_fix"
    assert p["o3_auto_dispatch_ready"] is False
    assert p["wont_fix_reason"] == "协议接口"


def test_d10_wont_fix_illegal_transition_not_written():
    from nucleus.evolution.patch_lifecycle import mark_history_wont_fix
    p = {"id": "y", "status": "applied"}
    r = mark_history_wont_fix(p, reason="r", by="t")
    assert r["ok"] is False
    assert p["status"] == "applied"


def test_d10_protocol_iface_exempt_and_negative_control():
    """74号裁定：协议/接口/桥接声明 ⇒ 不判死；负控保证判据不是恒真。"""
    import nucleus.self_inspector as SI
    assert SI._m348_protocol_iface_declared(
        {"file": "nucleus/CompanionBridge.py", "method": "handshake"}) is True
    # 负控：一个普通工具函数不得被赦免
    assert SI._m348_protocol_iface_declared(
        {"file": "nucleus/proc_gen.py", "method": "proc_gen_str"}) is False


def test_d10_tier_is_protocol_iface():
    import nucleus.self_inspector as SI
    tier, _ev = SI._m330_classify_hard_tier(
        {"file": "nucleus/CompanionBridge.py", "method": "handshake"})
    assert tier == "protocol_iface"


# ============================ 刀2② 入池额度豁免 ============================
def _o3(tag, status=None, extra=None, ts=None):
    e = {"id": tag, "file": "%s.py" % tag, "method": "m_" + tag,
         "issue_type": "dead_code_candidate", "o3_dead_code": True,
         "generated_at": float(1792740000.0 if ts is None else ts)}
    if status:
        e["status"] = status
    if extra:
        e.update(extra)
    return e


def _intake(tmp_path, entries):
    import nucleus.self_inspector as SI
    d = os.path.join(str(tmp_path), "r")
    os.makedirs(os.path.join(d, "data", "patches"), exist_ok=True)
    with io.open(os.path.join(d, "data", "patches", "patch_history.json"),
                 "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)
    return SI._m333_intake_rate(project_root=d, now=1792740000.0)


def test_d2_terminal_states_do_not_consume_quota(tmp_path):
    r = _intake(tmp_path, [_o3("ok1"), _o3("ok2"),
                           _o3("w", "wont_fix"), _o3("r", "rolled_back")])
    assert r["count"] == 2
    assert r["quota_exempt"] == 2
    assert r["quota_exempt_by"]["status:wont_fix"] == 1


def test_d2_normal_entries_not_exempted(tmp_path):
    """负控：豁免不得误伤正常条目。"""
    r = _intake(tmp_path, [_o3("n%d" % i) for i in range(4)])
    assert r["count"] == 4
    assert r["quota_exempt"] == 0


def test_d2_exempt_flag_and_history_paths(tmp_path):
    r = _intake(tmp_path, [
        _o3("f1", extra={"revoked": True}),
        _o3("h1", extra={"status_history": [{"status": "open"},
                                            {"status": "restored"}]}),
    ])
    assert r["count"] == 0
    assert r["quota_exempt"] == 2
    assert "flag:revoked" in r["quota_exempt_by"]
    assert "history:restored" in r["quota_exempt_by"]


# ============================ 刀1③ 日志代次戳 ============================
def test_d1_log_line_carries_pid_and_gen(monkeypatch):
    from nucleus.logger import PulseFormatter
    monkeypatch.delenv("PULSE_LOG_NO_PROC_STAMP", raising=False)
    PulseFormatter._m348_gen = ""
    rec = logging.LogRecord("pulse.organ.PulseHeart", logging.WARNING,
                            __file__, 1, "hello", None, None)
    line = PulseFormatter(
        fmt="%(asctime)s [%(organ_tag)s] %(levelname)s: %(message)s").format(rec)
    assert "pid=%d" % os.getpid() in line
    assert "gen=" in line
    # ★零回归：行尾追加不得破坏 organ 正则（标签与级别必须仍相邻）
    from nucleus.evolution.LogAnalyzer import _LOG_ORGAN_RE
    m = _LOG_ORGAN_RE.search(line)
    assert m is not None and m.group(1) == "Heart"


def test_d1_log_stamp_kill_switch(monkeypatch):
    from nucleus.logger import PulseFormatter
    monkeypatch.setenv("PULSE_LOG_NO_PROC_STAMP", "1")
    rec = logging.LogRecord("pulse.organ.PulseHeart", logging.WARNING,
                            __file__, 1, "hello", None, None)
    line = PulseFormatter(
        fmt="%(asctime)s [%(organ_tag)s] %(levelname)s: %(message)s").format(rec)
    assert "pid=" not in line


# ============================ 刀4④ 网络探测降频 ============================
def test_d4_net_throttle_constants_and_tiers():
    """源码级断言：TTL≥30min、慢阈值=3s、三档留痕都在（不实例化器官）。"""
    import inspect

    from organs.motor.PulseController import PulseController
    assert float(PulseController._M348_NET_TTL_SEC) >= 1800.0
    assert float(PulseController._M348_NET_SLOW_SEC) == 3.0
    src = inspect.getsource(PulseController._check_domestic_network)
    for _tier in ("[347网] 缓存命中", "[347网] 探测", "[347网] 慢降级"):
        assert _tier in src, "缺少留痕档：%s" % _tier


def test_d4_net_cache_prevents_second_probe():
    """两次调用只探测一次（缓存生效）。socket 全部打桩，零网络。"""
    import socket as _socket

    from organs.motor.PulseController import PulseController

    calls = {"n": 0}

    def _fake_create_connection(*a, **kw):
        calls["n"] += 1
        raise OSError("stub: 拒绝建连")

    _orig = _socket.create_connection
    _socket.create_connection = _fake_create_connection
    try:
        obj = object.__new__(PulseController)
        obj._permission_config = {"network_check_hosts": [("stub.invalid", 1)]}
        obj._log = lambda *a, **kw: None
        first = obj._check_domestic_network()
        second = obj._check_domestic_network()
    finally:
        _socket.create_connection = _orig
    assert first is False and second is False
    assert calls["n"] == 1, "缓存未生效：探测了 %d 次" % calls["n"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
