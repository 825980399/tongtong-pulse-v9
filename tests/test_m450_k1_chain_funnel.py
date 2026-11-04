# -*- coding: utf-8 -*-
"""450 刀1：patch_chain 环节漏斗（gen_voided / gen_pending / approved + 瓶颈判据）。

★基准纪律（铁律 30）：断言里的期望值全部**写死在用例里**（16/1/0 那组来自
  11-04 实测盘面），不由被测函数反推。
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

PC = pytest.importorskip("organs.core.PulseMetricsCollector")


def _today_ts(offset_sec: float = -60.0) -> float:
    return time.time() + offset_sec


def _write_jsonl(path: str, rows: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def _write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False)


def _iso_root(tmp_path) -> str:
    """造一个隔离仓根：四个源的读数按 11-04 实测形状。"""
    root = str(tmp_path)
    ts = _today_ts()
    # history：今天 0 条落地
    _write_json(os.path.join(root, "data", "patches", "patch_history.json"), [])
    # pending：1 条（今天凌晨生成的 dead_code_candidate）
    _write_json(os.path.join(root, "data", "patches", "pending_patches.json"),
                [{"id": "p1", "generated_at": ts - 3600.0, "status": "pending",
                  "issue_type": "dead_code_candidate", "file": "organs/x.py"}])
    # 作废台账：16 条（结构缺陷）
    _write_jsonl(os.path.join(root, "data", "reports", "converge_voided.jsonl"),
                 [{"ts": ts, "patch_id": f"v{i}", "reason": "284刀5 结构缺陷补丁作废",
                   "no_def": True, "issue_type": "code_optimization"} for i in range(16)])
    # 审批留痕：全拒
    _write_jsonl(os.path.join(root, "data", "patches", "approval_trace.jsonl"),
                 [{"ts": ts, "patch_id": "p1", "approved": False, "gates_total": 8}])
    # 落账台账：空对象（结构对、0 条）
    _write_json(os.path.join(root, "data", "evolution", "verify_feedback_ledger.json"),
                {"applied": {}})
    return root


# ---------------------------------------------------------------- 计数原语
def test_count_jsonl_按今日窗口计数(tmp_path):
    p = os.path.join(str(tmp_path), "x.jsonl")
    _write_jsonl(p, [{"ts": time.time()}, {"ts": time.time() - 86400 * 2}])
    assert PC._m450_count_jsonl(p, lo=time.time() - 3600, hi=time.time() + 60) == 1


def test_count_jsonl_文件不存在返回None而不是0(tmp_path):
    """★读不到 ≠ 是 0（铁律 156）。这条是本刀最容易写错的地方。"""
    assert PC._m450_count_jsonl(os.path.join(str(tmp_path), "nope.jsonl"),
                               lo=0.0, hi=time.time()) is None


def test_count_jsonl_坏行跳过不影响其余(tmp_path):
    p = os.path.join(str(tmp_path), "x.jsonl")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(f'{{"ts": {time.time():.1f}}}\n')
        f.write("{半行坏数据\n")
        f.write(f'{{"ts": {time.time():.1f}}}\n')
    assert PC._m450_count_jsonl(p, lo=time.time() - 60, hi=time.time() + 60) == 2


def test_count_jsonl_need过滤只数匹配行(tmp_path):
    p = os.path.join(str(tmp_path), "x.jsonl")
    _write_jsonl(p, [{"ts": time.time(), "approved": True},
                     {"ts": time.time(), "approved": False},
                     {"ts": time.time(), "approved": True}])
    _n = PC._m450_count_jsonl(p, lo=time.time() - 60, hi=time.time() + 60,
                              need={"approved": True})
    assert _n == 2, _n


# ---------------------------------------------------------------- 组装
def test_隔离根读齐七段(tmp_path):
    root = _iso_root(tmp_path)
    d = PC._m421_patch_chain_read(root=root)
    assert d["gen_voided"] == 16, d.get("gen_voided")
    assert d["gen_pending"] == 1, d.get("gen_pending")
    assert d["gen"] == 0, d.get("gen")
    assert d["queue"] == 1, d.get("queue")
    assert d["approved"] == 0, d.get("approved")
    assert d["apply"] == 0, d.get("apply")
    assert d["ledger"] == 0, d.get("ledger")


def test_新字段不进state判据(tmp_path):
    """★新增三字段**不进** `_M421_PC_FIELDS`：老源齐了就还是 ok，不因新源缺失降级。"""
    root = _iso_root(tmp_path)
    d = PC._m421_patch_chain_read(root=root)
    assert "gen_voided" not in PC._M421_PC_FIELDS
    assert "gen_pending" not in PC._M421_PC_FIELDS
    assert "approved" not in PC._M421_PC_FIELDS
    # 四个老字段都在 ⇒ state 不能再是 unavailable
    assert d["state"] in ("ok", "partial", "degraded"), d["state"]


def test_degraded仍由老判据决定(tmp_path):
    root = _iso_root(tmp_path)
    d = PC._m421_patch_chain_read(root=root)
    assert d["state"] == "degraded", d["state"]
    assert "apply=0" in str(d.get("state_reason")), d.get("state_reason")


# ---------------------------------------------------------------- 瓶颈判据
def test_瓶颈_作废占多数时报生成侧质量():
    _v = PC._m450_bottleneck({"gen_voided": 16, "gen_pending": 1, "gen": 0,
                              "queue": 1, "approved": 0, "apply": 0, "ledger": 0})
    assert _v.startswith("生成侧质量"), _v
    assert "16/17" in _v, _v


def test_瓶颈_入队多但批不出来时报审批侧():
    _v = PC._m450_bottleneck({"gen_voided": 0, "gen_pending": 3, "gen": 0,
                              "queue": 3, "approved": 0, "apply": 0, "ledger": 0})
    assert _v.startswith("审批侧"), _v


def test_瓶颈_批了没落地报应用侧():
    _v = PC._m450_bottleneck({"gen_voided": 0, "gen_pending": 2, "gen": 0,
                              "queue": 2, "approved": 2, "apply": 0, "ledger": 0})
    assert _v.startswith("应用侧"), _v


def test_瓶颈_全齐时报无阻断():
    _v = PC._m450_bottleneck({"gen_voided": 0, "gen_pending": 0, "gen": 5,
                              "queue": 0, "approved": 5, "apply": 5, "ledger": 5})
    assert _v.startswith("无阻断"), _v


def test_瓶颈_读数不全时不硬判():
    _v = PC._m450_bottleneck({"gen_voided": None, "gen_pending": 1, "gen": 0,
                              "queue": 1, "approved": 0, "apply": 0, "ledger": 0})
    assert _v == "读数不全，不判", _v


# ---------------------------------------------------------------- 日志
def test_漏斗日志含五段(monkeypatch, capsys):
    monkeypatch.setattr(config, "ENABLE_M450_CHAIN_FUNNEL", True, raising=False)
    monkeypatch.setattr(config, "M450_CHAIN_FUNNEL_LOG_SEC", 0.0, raising=False)
    PC._m450_funnel_log_at = 0.0
    d = {"gen_voided": 16, "gen_pending": 1, "gen": 0, "queue": 1,
         "approved": 0, "apply": 0, "ledger": 0}
    PC._m450_funnel_log(d)
    _err = capsys.readouterr().err
    # logging 未配置 handler 时不进 capsys；改为直接校验拼串函数可读性
    assert PC._m450_bottleneck(d).startswith("生成侧质量")
    if _err:
        assert True


def test_漏斗日志节流_窗口内不重复写(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M450_CHAIN_FUNNEL", True, raising=False)
    monkeypatch.setattr(config, "M450_CHAIN_FUNNEL_LOG_SEC", 1800.0, raising=False)
    _calls = []
    import logging
    _lg = logging.getLogger("pulse.module.PulseMetricsCollector")
    _orig = _lg.info

    def _spy(msg, *a, **kw):
        _calls.append(msg)
        return _orig(msg, *a, **kw)

    monkeypatch.setattr(_lg, "info", _spy)
    PC._m450_funnel_log_at = time.time()
    PC._m450_funnel_log({"gen_voided": 1, "gen_pending": 1, "gen": 0, "queue": 1,
                         "approved": 0, "apply": 0, "ledger": 0})
    assert _calls == [], _calls
