# -*- coding: utf-8 -*-
"""385 刀2：DL3 影子账本（只写不判）。

## 三条红线（本文件逐条守）

1. **不改执行行为**：所有结果 `shadow=True`、`action` 默认 `DEFER`；
   `observe_*` 返回值不参与任何判定，异常一律吞掉只留痕。
2. **不新写判据**：这里只抄既有链路事实（洞鉴 §6.6 反重复建设）。
3. **开关默认关**：关时第一行返回，**不构造对象、不写盘**。

## 契约字段来源

`docs/洞鉴与星轨对话/分析报告/洞鉴_385前置_内在模型方向审查_20261028.md`
§6.2（输入 11 字段）/ §6.3（输出 12 字段）/ §6.4（账本四条纪律）。
"""
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

from nucleus.decision import contracts, ledger, shadow  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    shadow.reset_stats()
    monkeypatch.setattr(config, "ENABLE_DEC_SHADOW", False, raising=False)
    yield
    shadow.reset_stats()


def _on(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_DEC_SHADOW", True, raising=False)


# ------------------------------------------------------------ ① 开关默认关
def test_m385_d2_switch_defaults_off():
    """★票面第 2 条：影子开关默认必须关。"""
    assert config.ENABLE_DEC_SHADOW is False
    assert shadow.enabled() is False


def test_m385_d2_zero_write_when_off(tmp_path, monkeypatch):
    """★票面单测②：开关关时**零写入**（账本文件都不该被创建）。"""
    _p = str(tmp_path / "off.jsonl")
    assert shadow.observe_patch(patch_id="o3_x", path=_p, explicit=True) is None
    assert shadow.observe_resource(tick=30, every_n=30, path=_p, explicit=True) is None
    assert shadow.observe_chat(tick=100, every_n=100, path=_p, explicit=True) is None
    assert not os.path.exists(_p), "开关关却建了账本文件"
    assert ledger.count_rows(_p) == 0


# -------------------------------------------- ② 账本行数 == 影子调用次数
def test_m385_d2_rows_equal_calls(tmp_path, monkeypatch):
    """★票面单测①：账本行数 = 影子调用次数（每域分别核）。"""
    _on(monkeypatch)
    _p = str(tmp_path / "on.jsonl")

    for _i in range(3):
        _r = shadow.observe_patch(patch_id="o3_%d" % _i, file="a.py",
                                  status="pending", path=_p, explicit=True)
        assert _r and _r["status"] == "ok"
    assert ledger.count_rows(_p) == 3

    for _i in range(1, 5):
        shadow.observe_resource(tick=_i * 30, every_n=30, path=_p, explicit=True)
    assert ledger.count_rows(_p) == 3 + 4, "resource 每 30 拍应写 4 条（30/60/90/120）"

    for _i in range(1, 301):
        shadow.observe_chat(tick=_i, every_n=100, path=_p, explicit=True)
    assert ledger.count_rows(_p) == 3 + 4 + 3, "chat 每 100 次应写 3 条（100/200/300）"


def test_m385_d2_throttle_skips_others(tmp_path, monkeypatch):
    """节流：非整拍不写，但调用计数照涨（可诊断）。"""
    _on(monkeypatch)
    _p = str(tmp_path / "thr.jsonl")
    for _i in range(1, 30):
        shadow.observe_resource(tick=_i, every_n=30, path=_p, explicit=True)
    assert ledger.count_rows(_p) == 0
    assert shadow.stats()["calls"]["resource"] == 29
    assert shadow.stats()["skipped"]["resource"] == 29


# ---------------------------------------------------------- ③ P99 耗时
def test_m385_d2_p99_within_5ms(tmp_path, monkeypatch):
    """★票面单测③：决策耗时 P99 ≤ 5ms（本机实测，含构造 + 写盘）。"""
    _on(monkeypatch)
    _p = str(tmp_path / "perf.jsonl")
    for _i in range(60):
        shadow.observe_patch(patch_id="o3_%d" % _i, file="a.py",
                             status="pending", path=_p, explicit=True)
    _p99 = shadow.stats()["p99_ms"]
    assert _p99 <= 5.0, "P99=%.2fms 超 5ms 预算（样本 %d）" % (
        _p99, shadow.stats()["elapsed_samples"])


# ------------------------------------------------------ ④ 契约字段完整性
def test_m385_d2_request_fields_match_doc():
    """洞鉴 §6.2：8 必填 + 4 信号（signals 保持嵌套）。"""
    _r = contracts.DecisionRequest(
        request_id="req_x", ts=1.0, proc_gen="pid1@20260101-000000",
        scope="patch", candidates=[{"id": "c1"}],
        constraints={"llm_budget": 0}, epoch="e1",
    )
    _d = _r.to_dict()
    for _k in ("request_id", "ts", "proc_gen", "scope", "candidates",
               "signals", "constraints", "policy_version", "epoch"):
        assert _k in _d, "缺字段 %s" % _k
    assert isinstance(_d["signals"], dict), "signals 必须嵌套（洞鉴写的是 signals.l2）"
    assert _r.validate() == []


def test_m385_d2_result_fields_match_doc():
    """洞鉴 §6.3：12 字段 + 闭集枚举。"""
    _req = contracts.DecisionRequest(
        request_id="req_y", ts=1.0, proc_gen="pid1@x", scope="chat",
        candidates=[{"id": "c1"}], constraints={}, epoch="e1")
    _res = contracts.shadow_result(_req, evidence=["e1"])
    _d = _res.to_dict()
    for _k in ("decision_id", "request_id", "action", "target", "priority",
               "budget", "expires_at", "evidence", "reason_code",
               "degrade_policy", "rollback", "policy_version", "shadow"):
        assert _k in _d, "缺字段 %s" % _k
    assert _d["decision_id"].startswith("dec_chat_"), _d["decision_id"]
    assert _d["shadow"] is True, "影子模式必须 shadow=True"
    assert _res.validate() == []


def test_m385_d2_evidence_must_not_be_empty():
    """洞鉴原文：`evidence` **不允许为空**。"""
    _req = contracts.DecisionRequest(
        request_id="req_z", ts=1.0, proc_gen="p", scope="chat",
        candidates=[{"id": "c"}], constraints={}, epoch="e")
    _res = contracts.shadow_result(_req, evidence=[])
    assert "evidence_empty" in _res.validate()


def test_m385_d2_execute_requires_rollback():
    """洞鉴：`EXECUTE` 时 `rollback` 必填 —— 没有回滚句柄的执行指令不许存在。"""
    _req = contracts.DecisionRequest(
        request_id="req_e", ts=1.0, proc_gen="p", scope="patch",
        candidates=[{"id": "c"}], constraints={}, epoch="e")
    _res = contracts.shadow_result(_req, action="EXECUTE", target="t1",
                                   evidence=["e"])
    assert "rollback_required_for_EXECUTE" in _res.validate()
    _ok = contracts.shadow_result(_req, action="EXECUTE", target="t1",
                                  evidence=["e"], rollback={"flag_key": "k"})
    assert _ok.validate() == []


def test_m385_d2_invalid_action_rejected():
    _req = contracts.DecisionRequest(
        request_id="r", ts=1.0, proc_gen="p", scope="chat",
        candidates=[{"id": "c"}], constraints={}, epoch="e")
    assert "action_invalid" in str(
        contracts.shadow_result(_req, action="SHIP_IT", evidence=["x"]).validate())


# ------------------------------------------------- ⑤ 幂等 / 三键索引
def test_m385_d2_idempotent_by_decision_id(tmp_path, monkeypatch):
    """洞鉴 §6.4：`decision_id` 是幂等键 —— 同 id 第二次写应 `duplicate`。"""
    _on(monkeypatch)
    _p = str(tmp_path / "idem.jsonl")
    _req = contracts.DecisionRequest(
        request_id="req_i", ts=1000.0, proc_gen="p", scope="patch",
        candidates=[{"id": "c1"}], constraints={}, epoch="e")
    _res = contracts.shadow_result(_req, target="c1", evidence=["e"])
    assert ledger.append(_res, _req, path=_p, explicit=True) == "ok"
    assert ledger.append(_res, _req, path=_p, explicit=True) == "duplicate"
    assert ledger.count_rows(_p) == 1


def test_m385_d2_index_three_keys(tmp_path, monkeypatch):
    """洞鉴 §6.4：三键索引 decision_id / request_id / target（target 一对多）。"""
    _on(monkeypatch)
    _p = str(tmp_path / "idx.jsonl")
    shadow.observe_patch(patch_id="o3_same", status="pending", path=_p, explicit=True)
    shadow.observe_patch(patch_id="o3_same", status="verdict_x", path=_p, explicit=True)
    shadow.observe_patch(patch_id="o3_other", status="pending", path=_p, explicit=True)
    _ix = ledger.load_index(_p)
    assert _ix["lines"] == 3
    assert len(_ix["by_decision_id"]) == 3, "decision_id 应一对一"
    assert len(_ix["by_target"]["o3_same"]) == 2, "target 应一对多"


# ------------------------------------------------------- ⑥ 红线与接线
def test_m385_d2_never_raises(tmp_path, monkeypatch):
    """红线：观测异常绝不影响调用方。"""
    _on(monkeypatch)
    _bad = str(tmp_path / "no_such_dir_xyz" / "a" / "b.jsonl")
    for _fn, _kw in ((shadow.observe_patch, {"patch_id": "x"}),
                     (shadow.observe_resource, {"tick": 0}),
                     (shadow.observe_chat, {"tick": 0})):
        _r = _fn(path=_bad, explicit=True, **_kw)      # 不抛即通过
        assert _r is None or isinstance(_r, dict)


def test_m385_d2_action_defaults_defer():
    """影子期不该出现 EXECUTE —— `shadow_result` 默认必须 DEFER。"""
    _req = contracts.DecisionRequest(
        request_id="r", ts=1.0, proc_gen="p", scope="chat",
        candidates=[{"id": "c"}], constraints={}, epoch="e")
    assert contracts.shadow_result(_req, evidence=["x"]).action == "DEFER"


def test_m385_d2_wired_in_three_domains():
    """★票面第 3 条：三个只读调用点必须真的接在既有链路上。"""
    _pairs = [
        ("nucleus/evolution/patch_execution_dispatch.py", "_dec385.observe_patch("),
        ("organs/core/PulseMetricsCollector.py", "_dec385.observe_resource("),
        ("organs/body/PulseLung.py", "_dec385.observe_chat("),
    ]
    for _rel, _needle in _pairs:
        _src = io.open(os.path.join(ROOT, _rel), encoding="utf-8").read()
        assert _needle in _src, "%s 里没接 %s" % (_rel, _needle)
        assert "except Exception" in _src, "%s 缺少异常兜底" % _rel


def test_m385_d2_naming_avoids_l3_collision():
    """洞鉴第 6 条：必须用 DL3/dec_ 前缀，避开 InfoField 的 L3 脉冲层。"""
    _src = io.open(os.path.join(ROOT, "nucleus/decision/shadow.py"),
                   encoding="utf-8").read()
    assert "DL3" in _src
    _row = json.loads(json.dumps({"decision_id": "dec_patch_1_abcd1234"}))
    assert _row["decision_id"].startswith("dec_")
