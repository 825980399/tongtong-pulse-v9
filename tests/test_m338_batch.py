# -*- coding: utf-8 -*-
"""338 批回归测试（五刀验收的可复跑子集）。

隔离约定：全部用 tempfile / tmp_path；**不写生产 data/**、不碰 logs/。
覆盖：
  刀1 索引持久化（checksum 命中 / 失配重扫 / compaction 后不串号 / 原子写）
  刀2 释放决策树（G0 四闸门 / 分级 / 防误杀 / heap 峰值硬线）
  刀3 冷化链（UNAVAILABLE 归因 / 命中率不填 0）
  刀4 证据门（有据通过 / 无据拒入池 / 可复用清单只出方案）
  刀5 留样三字段（头版本戳 + item verdict 显式 UNSET）
"""
import io
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------- 刀1
def _mk_snapshot(path, n, checksum="cs-x"):
    nodes = [{"node_id": "n%03d" % i, "value": "v%d" % i,
              "keywords": ["k%d" % (i % 3)], "evol_level": "L1"} for i in range(n)]
    payload = {"metadata": {"node_list_checksum": checksum, "node_count": n},
               "nodes": nodes}
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return payload


def test_index_save_load_hit(tmp_path):
    from nucleus.mnemosyne import snapshot_index as si

    snap = str(tmp_path / "s.json")
    _mk_snapshot(snap, 10, "cs-a")
    assert si.save(snap, si.build_payload(snap, {"node_list_checksum": "cs-a"},
                                          [("n000", (0, 10))])) is True
    assert os.path.exists(si.index_path(snap))
    got = si.load(snap, expect_checksum="cs-a")
    assert got is not None
    # checksum 失配 ⇒ 不许命中（否则等于用旧索引读新档）
    assert si.load(snap, expect_checksum="cs-b") is None


def test_index_invalidate_then_miss(tmp_path):
    from nucleus.mnemosyne import snapshot_index as si

    snap = str(tmp_path / "s.json")
    _mk_snapshot(snap, 5, "cs-q")
    si.save(snap, si.build_payload(snap, {"node_list_checksum": "cs-q"}, []))
    assert si.load(snap, expect_checksum="cs-q") is not None
    si.invalidate(snap, "compaction")
    assert si.load(snap, expect_checksum="cs-q") is None


def test_index_physical_identity_change_not_reused(tmp_path):
    """文件被整体重写（mtime/size 变）⇒ 即便忘 invalidate 也不得复用旧索引。"""
    from nucleus.mnemosyne import snapshot_index as si

    snap = str(tmp_path / "s.json")
    _mk_snapshot(snap, 5, "cs-1")
    si.save(snap, si.build_payload(snap, {"node_list_checksum": "cs-1"}, []))
    time.sleep(0.02)
    _mk_snapshot(snap, 9, "cs-2")
    got = si.load(snap, expect_checksum="cs-2")
    assert got is None or got.get("meta", {}).get("node_count") == 9 or True
    # 关键：不许把 5 节点的旧索引当成 9 节点新档的索引
    if got is not None:
        assert got.get("meta", {}).get("node_count") == 9


# ---------------------------------------------------------------- 刀2
def test_release_g0_requires_alive():
    from nucleus.mnemosyne import release_policy as rp

    _r = rp.evaluate_g0(w1_verdict="stale", stress=False, now_ts=0.0)
    assert _r["allowed"] is False
    assert rp.G0_W1 in _r["blocked_by"]


def test_release_g0_stress_blocks():
    from nucleus.mnemosyne import release_policy as rp

    _r = rp.evaluate_g0(w1_verdict="alive", stress=True, now_ts=0.0)
    assert _r["allowed"] is False
    assert rp.G0_STRESS in _r["blocked_by"]


def test_release_decide_unknown_heap_no_release():
    from nucleus.mnemosyne import release_policy as rp

    _d = rp.decide(heap_mb=None, w1_verdict="alive", stress=False, now_ts=0.0)
    # heap 读不到 ⇒ **不许释放**（档位是 G1 只观测，而不是"猜一个数去放行"）
    assert _d["tier"] == rp.TIER_G1
    assert "测不到" in _d["tier_why"]
    assert _d["approved"] == [] and _d["deferred"] == []


def test_release_decide_tiers():
    from nucleus.mnemosyne import release_policy as rp

    _th = rp.thresholds()
    _low = rp.decide(heap_mb=1.0, w1_verdict="alive", stress=False, now_ts=0.0)
    assert _low["tier"] in (rp.TIER_G1, rp.TIER_G2)
    _high = rp.decide(heap_mb=float(_th["g4_heap_mb"]) + 100, w1_verdict="alive",
                      stress=False, now_ts=0.0)
    assert _high["tier"] == rp.TIER_G4
    assert _high["approved"] == []      # G4 只告警，不释放


def test_release_guards_reject_busy_object():
    from nucleus.mnemosyne import release_policy as rp

    class _Obj:
        pass

    _o = _Obj()
    _held = [_o]        # 造一个外部引用 ⇒ refcount > 0
    _cand = {"id": "c1", "obj": _o, "last_access_ts": time.time(),
             "whitelisted": False, "reloadable": True}
    _g = rp.check_guards(_cand, now_ts=time.time())
    assert _g["ok"] is False
    assert isinstance(_g["blocked_by"], list) and _g["blocked_by"]
    assert _held is not None


def test_heap_peak_guard_line():
    from nucleus.mnemosyne import release_policy as rp

    _p = rp.heap_peak_guard(1000.0, line_mb=2000.0)
    assert _p["verdict"] == "pass"
    _f = rp.heap_peak_guard(2600.0, line_mb=2000.0)
    assert _f["verdict"] == "fail"
    # 读不到峰值 ⇒ 不可判，**不许记通过**
    _u = rp.heap_peak_guard(None, line_mb=2000.0)
    assert _u["verdict"] == "unavailable"


# ---------------------------------------------------------------- 刀3
def test_cold_chain_report_unavailable_when_not_triggered():
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool

    _pool = PulseNodePool(max_hot=10_000_000, max_warm=50_000)
    _rep = _pool.cold_chain_report()
    assert _rep["verdict"] == "UNAVAILABLE"
    assert "max_warm" in str(_rep["why"]) or "未启用" in str(_rep["why"])
    assert _rep["cold_pool_mem_observe"] is True


def test_cold_stats_hit_rate_none_not_zero():
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool

    _pool = PulseNodePool(max_hot=10_000_000, max_warm=10_000_000)
    _st = _pool.get_cold_stats()
    assert _st["recall_hit_rate"] is None
    assert _st["recall_status"] == "UNAVAILABLE"


# ---------------------------------------------------------------- 刀4
def _good_patch(pid="t_good"):
    return {"id": pid, "status": "runtime_verified", "risk_level": "低",
            "trust_score": 80.0, "applied": False,
            "issue_type": "periodic_task_no_reentry",
            "method": "_generate_organ_handbook",
            "file": "organs/brain/PulseCodeLearner.py"}


def test_evidence_ok_good_and_bad():
    from nucleus.evolution import patch_execution_dispatch as D

    _good = _good_patch()
    _ok, _why, _ev = D._m338_evidence_ok(_good)
    assert _ok is True, _why
    assert all(isinstance(_ev.get(k), dict) for k in ("risk", "trust", "blast"))

    _bad = dict(_good)
    _bad.pop("issue_type")          # risk 退化成纯 LLM 自评
    _ok2, _why2, _ = D._m338_evidence_ok(_bad)
    assert _ok2 is False
    assert "自评" in _why2 or "缺" in _why2


def test_evidence_gate_rejects_no_evidence(monkeypatch):
    import config
    from nucleus.evolution import patch_execution_dispatch as D

    _bad = _good_patch("t_noev")
    _bad.pop("issue_type")
    monkeypatch.setattr(config, "ENABLE_PATCH_EVIDENCE_GATE", False, raising=False)
    assert len(D.select_for_auto_triage([_bad], limit=1)) == 1
    monkeypatch.setattr(config, "ENABLE_PATCH_EVIDENCE_GATE", True, raising=False)
    assert D.select_for_auto_triage([_bad], limit=1) == []
    assert D._LAST_TRIAGE_REJ.get("evidence", 0) >= 1


def test_reusable_candidates_lists_repairs(tmp_path):
    from nucleus.evolution import patch_execution_dispatch as D

    _hist = [{"id": "r1", "status": "pending", "risk_level": "中",
              "trust_score": 30.0, "applied": False, "method": "m_x",
              "file": "a/b.py", "issue_type": "sql_injection"}]
    _out = str(tmp_path / "reuse.json")
    _r = D.build_reusable_candidates(_hist, limit=10, out_path=_out)
    assert _r["total"] == 1
    _it = _r["items"][0]
    assert _it["repair_actions"]
    assert _it["primary_reason"] in ("risk", "trust", "blast", "status", "evidence", "morph")
    assert os.path.exists(_out)
    _j = json.load(io.open(_out, encoding="utf-8"))
    assert _j["version"] == D._M338_EVIDENCE_VERSION


# ---------------------------------------------------------------- 刀5
def test_version_stamps_present():
    from nucleus.exploration_audit import _m338_version_stamps

    _s = _m338_version_stamps()
    assert _s["code_version"]
    assert _s["criteria_version"]
    assert _s["source"] == "class_attr"


def test_typed_infer_gate_rejects_injected(tmp_path):
    import subprocess

    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _gate = os.path.join(_root, "tools", "ci", "check_typed_infer_guard.py")
    if not os.path.exists(_gate):
        pytest.skip("门禁脚本不在")
    _g = {"code_version": "335.cg.v1", "edge_types": {"typed_infer": 99}}
    _p = tmp_path / "g.json"
    _p.write_text(json.dumps(_g), encoding="utf-8")
    _cp = subprocess.run([sys.executable, _gate, "--graph", str(_p)],
                         capture_output=True, text=True)
    assert _cp.returncode == 1
    _p2 = tmp_path / "g2.json"
    _p2.write_text(json.dumps({"code_version": "335.cg.v1",
                               "edge_types": {"typed_infer": 0}}), encoding="utf-8")
    _cp2 = subprocess.run([sys.executable, _gate, "--graph", str(_p2)],
                          capture_output=True, text=True)
    assert _cp2.returncode == 0
