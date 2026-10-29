# -*- coding: utf-8 -*-
"""397 刀5 验收：DL3 从影子升级为仲裁（过渡期）。

票面：
  1. 在 shadow.py 之上加一层排序/否决：复用动机压力 + 风险等级 + 预算，输出 DecideResult 的真实 action；
  2. 先只对**资源/风险**两域，不动 chat 域（LLM 调用，风险高）；
  3. 影子→仲裁的过渡：先输出 `action=DEFER` 但加 `would_be=APPROVE/REJECT` 字段；
  4. 验证：账本里开始出现非 DEFER 的 action（哪怕是 would_be 字段）。

★T0 口径修正：契约 `SCOPES` 闭集是 `chat/patch/resource/schedule`，**没有 "risk" 域**。
  票面的"风险"不是独立 scope —— 风险体现为 **patch 域候选的 `risk` 等级**。
  本批**不开新域**（改闭集会连带影响 validate 与其它域），
  "资源域 + 风险域" 落到实现上 = `resource` + `patch` 两域。

★过渡期红线（本批守的第一条）：`action` **必须仍是 DEFER**，真裁决只在
  `arbitration.would_be` 里。否则账本看起来像"L3 已经在下指令"，而执行器必须忽略它。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from nucleus.decision import shadow  # noqa: E402
from nucleus.decision.arbitrate import (  # noqa: E402
    ARBITRATION_SCOPES, RISK_REJECT_AT, RSS_CRITICAL_MB, RSS_HIGH_MB,
    arbitrate, enabled_for,
)
from nucleus.decision.contracts import (  # noqa: E402
    ACTIONS, DecisionRequest, make_request_id, shadow_result,
)

#: 让"导入即自检"：这两个常量是本批判据的锚，改了它们测试就得跟着动。
assert RISK_REJECT_AT == 3, "风险否决线变了 ⇒ 本文件 test_05/09 的前提要重核"
assert "DEFER" in ACTIONS and "REJECT" in ACTIONS, "动作闭集变了 ⇒ 过渡期红线要重核"


def _req(scope: str, candidates: list, signals: dict | None = None,
         constraints: dict | None = None) -> DecisionRequest:
    return DecisionRequest(
        request_id=make_request_id(scope, "t"), ts=1.0, proc_gen="g", scope=scope,
        candidates=candidates, signals=dict(signals or {}),
        constraints=dict(constraints or {"llm_budget": 0, "forbidden_files": []}),
    )


# ---------------------------------------------------------------- ① 域范围
def test_01_only_resource_and_patch():
    """只放行资源/风险两域，chat 与 schedule 不动。"""
    assert enabled_for("resource") is True
    assert enabled_for("patch") is True
    assert enabled_for("chat") is False, "chat 域要调 LLM，本批绝不能走仲裁"
    assert enabled_for("schedule") is False
    assert set(ARBITRATION_SCOPES) == {"resource", "patch"}


# ---------------------------------------------------------------- ② 各闸
def test_02_resource_normal_approves():
    _r = arbitrate(_req("resource", [{"id": "keep", "cost": {}, "risk": None}],
                        {"resource": {"rss_mb": 1000.0}}))
    assert _r["would_be"] == "APPROVE", _r
    assert _r["reason_code"] == "INTENT_MATCH"


def test_03_resource_high_defers():
    _r = arbitrate(_req("resource", [{"id": "keep", "cost": {}, "risk": None}],
                        {"resource": {"rss_mb": RSS_HIGH_MB + 1}}))
    assert _r["would_be"] == "DEFER", _r
    assert _r["reason_code"] == "RESOURCE_PRESSURE"
    assert _r["degrade_policy"] == "DEFER_ONE_TICK"


def test_04_resource_critical_rejects():
    _r = arbitrate(_req("resource", [{"id": "keep", "cost": {}, "risk": None}],
                        {"resource": {"rss_mb": RSS_CRITICAL_MB + 1}}))
    assert _r["would_be"] == "REJECT", _r
    assert _r["vetoes"], "REJECT 必须留下否决理由（没理由的否决是猜）"


def test_05_high_risk_patch_rejected():
    _r = arbitrate(_req("patch", [{"id": "p1", "cost": {}, "risk": "high"}]))
    assert _r["would_be"] == "REJECT", _r
    assert _r["reason_code"] == "POLICY_BLOCK"


def test_06_forbidden_file_rejected():
    _r = arbitrate(_req("patch",
                        [{"id": "p1", "cost": {}, "risk": "low", "evidence_ref": "a.py"}],
                        constraints={"llm_budget": 0, "forbidden_files": ["a.py"]}))
    assert _r["would_be"] == "REJECT", _r
    assert _r["reason_code"] == "POLICY_BLOCK"


def test_07_user_override_wins():
    """用户覆盖优先于一切闸（连高风险也放行），且**不算否决**。"""
    _r = arbitrate(_req("patch", [{"id": "p1", "cost": {}, "risk": "high"}],
                        constraints={"llm_budget": 0, "forbidden_files": ["p1"],
                                     "user_override": True}))
    assert _r["would_be"] == "APPROVE", _r
    assert _r["reason_code"] == "USER_OVERRIDE"
    assert _r["vetoes"] == [], "用户覆盖下的命中不该进否决清单"


def test_08_budget_exhausted_defers():
    _r = arbitrate(_req("patch", [{"id": "p1", "cost": {"llm_calls": 3}, "risk": "low"}],
                        constraints={"llm_budget": 0, "forbidden_files": []}))
    assert _r["would_be"] == "DEFER", _r
    assert _r["reason_code"] == "BUDGET_EXHAUSTED"


def test_09_unknown_risk_is_not_vetoed():
    """★未知风险等级按 0 处理，**不**按最危险 —— 否则一次字段写法变更
    （如 risk 从 'high' 变成 'HIGH' 之外的第三种写法）会把整个域 REJECT 掉。"""
    for _rk in ("weird", "", None, "UNKNOWN_LEVEL"):
        _r = arbitrate(_req("patch", [{"id": "p1", "cost": {}, "risk": _rk}]))
        assert _r["would_be"] == "APPROVE", f"risk={_rk!r} 被误否决：{_r}"


def test_10_no_candidates_is_evidence_missing():
    _r = arbitrate(_req("patch", []))
    assert _r["would_be"] == "DEFER"
    assert _r["reason_code"] == "EVIDENCE_MISSING"


def test_11_rss_missing_is_not_zero():
    """★`rss_mb` 没采到 ⇒ 不得当成"RSS 很低"放行（铁律 276）。"""
    _r = arbitrate(_req("resource", [{"id": "keep", "cost": {}, "risk": None}], {}))
    assert _r["reason_code"] != "RESOURCE_PRESSURE", "没采到 RSS 不该走压力闸"


# ---------------------------------------------------------------- ③ 排序
def test_12_ranking_vetoed_last():
    """排序：被否决的沉底，其余按风险升序、成本升序。"""
    _r = arbitrate(_req("patch", [
        {"id": "hi", "cost": {"llm_calls": 1}, "risk": "high"},
        {"id": "lo", "cost": {"llm_calls": 9}, "risk": "low"},
        {"id": "mid", "cost": {"llm_calls": 5}, "risk": "medium"},
    ], constraints={"llm_budget": 5, "forbidden_files": []}))
    _ids = [x["id"] for x in _r["ranking"]]
    assert _ids[0] == "lo", _ids          # 风险最低排第一（哪怕成本最高）
    assert _ids[-1] == "hi", "被否决的必须沉底"
    assert [x["rank"] for x in _r["ranking"]] == [1, 2, 3]
    assert _r["would_be"] == "APPROVE", "首选未被否决 ⇒ APPROVE"


def test_13_ranking_breaks_ties_by_cost():
    _r = arbitrate(_req("patch", [
        {"id": "cheap", "cost": {"llm_calls": 1}, "risk": "low"},
        {"id": "pricey", "cost": {"llm_calls": 8}, "risk": "low"},
    ], constraints={"llm_budget": 50, "forbidden_files": []}))
    assert [x["id"] for x in _r["ranking"]] == ["cheap", "pricey"]


# ---------------------------------------------------------------- ④ 契约接线
def test_14_result_carries_arbitration():
    _req1 = _req("resource", [{"id": "keep", "cost": {}, "risk": None}],
                 {"resource": {"rss_mb": 1000.0}})
    _res = shadow_result(_req1, evidence=["resource:rss=1000"],
                         arbitration=arbitrate(_req1))
    _d = _res.to_dict()
    assert _d["arbitration"]["would_be"] == "APPROVE"
    assert _d["action"] == "DEFER", "★过渡期红线：action 必须仍是 DEFER"
    assert _res.validate() == [], f"契约校验不通过：{_res.validate()}"


def test_15_no_arbitration_is_empty_dict_not_none():
    _res = shadow_result(_req("chat", [{"id": "a", "cost": {}, "risk": None}]))
    assert _res.arbitration == {}, "未仲裁应为 {}（不是 None —— 后者会让 `or {}` 兜底掩盖）"


def test_16_would_be_values_are_closed_set():
    for _r in (arbitrate(_req("patch", [{"id": "p", "cost": {}, "risk": "high"}])),
               arbitrate(_req("patch", [{"id": "p", "cost": {}, "risk": "low"}])),
               arbitrate(_req("resource", [{"id": "k", "cost": {}, "risk": None}],
                              {"resource": {"rss_mb": RSS_HIGH_MB + 1}}))):
        assert _r["would_be"] in ("APPROVE", "REJECT", "DEFER"), _r


# ---------------------------------------------------------------- ⑤ 端到端（真写账本）
@pytest.fixture
def _shadow_on(tmp_path, monkeypatch):
    import config as _cfg

    monkeypatch.setattr(_cfg, "ENABLE_DEC_SHADOW", True, raising=False)
    shadow.reset_stats()
    yield tmp_path
    shadow.reset_stats()


def test_17_ledger_has_would_be(_shadow_on):
    """票面验收：账本里出现非 DEFER 的裁决（在 would_be 字段里）。"""
    _p = str(_shadow_on / "dl3.jsonl")
    shadow.observe_resource(rss_mb=1000.0, tick=30, path=_p, explicit=True)
    shadow.observe_patch(patch_id="p1", risk="high", file="a.py", dispatched=False,
                         path=_p, explicit=True)

    with open(_p, encoding="utf-8") as _f:
        _lines = [json.loads(x) for x in _f if x.strip()]
    assert _lines, "账本一行都没写"
    # ★账本行结构是 {"schema","input_digest","request_id","recorded_at","result"} ——
    #   裁决在 `result` 里，不是顶层（洞鉴 §6.4）。
    _res = [(l.get("result") or {}) for l in _lines]
    _wbs = [(r.get("arbitration") or {}).get("would_be") for r in _res]
    assert "APPROVE" in _wbs, f"账本里没有 APPROVE：{_wbs}"
    assert "REJECT" in _wbs, f"账本里没有 REJECT：{_wbs}"
    # ★过渡期红线：真 action 一列必须全是 DEFER
    assert all(r.get("action") == "DEFER" for r in _res), \
        f"过渡期 action 必须仍为 DEFER：{[r.get('action') for r in _res]}"


def test_18_chat_domain_has_no_arbitration(_shadow_on):
    """chat 域只写不判 —— 账本里它的 arbitration 必须是空字典。"""
    _p = str(_shadow_on / "dl3_chat.jsonl")
    shadow.observe_chat(intent_type="qa", route="local", tick=100, path=_p, explicit=True)
    with open(_p, encoding="utf-8") as _f:
        _lines = [json.loads(x) for x in _f if x.strip()]
    assert _lines, "chat 域没写账本"
    _r0 = _lines[0].get("result") or {}
    assert (_r0.get("arbitration") or {}) == {}, \
        f"chat 域不该有仲裁结论：{_r0.get('arbitration')}"


def test_19_stats_counts_would_be(_shadow_on):
    """`by_would_be` 是独立键 —— 不能和 `by_status`（写盘状态）混在一个键里。"""
    _p = str(_shadow_on / "dl3.jsonl")
    shadow.observe_resource(rss_mb=1000.0, tick=30, path=_p, explicit=True)
    shadow.observe_patch(patch_id="p1", risk="high", path=_p, explicit=True)
    _st = shadow.stats()
    assert _st["by_would_be"].get("APPROVE") == 1, _st["by_would_be"]
    assert _st["by_would_be"].get("REJECT") == 1, _st["by_would_be"]
    assert "by_status" in _st, "写盘状态读数必须仍在"


def test_20_shadow_off_still_writes_nothing(tmp_path, monkeypatch):
    """开关关 ⇒ 零写入（385 票面单测②，本批不得破坏）。"""
    import config as _cfg

    monkeypatch.setattr(_cfg, "ENABLE_DEC_SHADOW", False, raising=False)
    shadow.reset_stats()
    _p = str(tmp_path / "dl3_off.jsonl")
    assert shadow.observe_resource(rss_mb=1000.0, tick=30, path=_p) is None
    assert shadow.observe_patch(patch_id="p1", path=_p) is None
    assert not os.path.exists(_p), "开关关时不得落盘"
