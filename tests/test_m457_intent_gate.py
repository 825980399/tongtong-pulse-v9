# -*- coding: utf-8 -*-
"""457 刀1/刀2：假设生成的意图闸 + 链级超时预算。

★本文件守四件事（对应任务书验收）
  1. 刀1：代码/事实/时间类意图**绝不生成** —— 不调 LLM、status=skipped_intent；
  2. 刀1：假设类/自我报告类意图放行；**意图未知时降级关键词判据**（不静默全灭）；
  3. 刀2：单次墙钟硬上限 8s；同一请求的**整条链** ≤10s，超了不再发第二次 HTTP；
  4. 刀2：超时**只调用一次**，绝不重试；降级后返回空假设（答案不受影响）。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import config
from nucleus import evidence_injection as EI
from nucleus.reasoning import hypothesize as H

EP = ("https://unit.test/v1/chat/completions", "sk-unit", "unit-model", "unit")
OK_BODY = {"speculative": True,
           "hypotheses": [{"claim": "缓存层去掉后延迟会先降后升",
                           "assumptions": ["读多写少"],
                           "evidence_ids": ["E1"],
                           "confidence": 0.62,
                           "test": "压测对比 p99"}]}


def _resp(content):
    return {"choices": [{"finish_reason": "stop",
                         "message": {"content": content, "reasoning_content": ""}}],
            "usage": {"completion_tokens": 64}}


def _ev(n=1):
    return [{"id": f"E{i + 1}", "text": f"证据正文 {i + 1}", "source": "qa"}
            for i in range(n)]


class _Stub:
    def __init__(self, result=None):
        self.calls = []
        self.result = result if result is not None else (True, _resp(json.dumps(OK_BODY)))

    def __call__(self, url, **kw):
        self.calls.append({"url": url, "timeout": kw.get("timeout")})
        return self.result


@pytest.fixture(autouse=True)
def _fresh():
    EI.fresh_state()
    H.end_request()
    H.end_chain()
    yield
    EI.fresh_state()
    H.end_request()
    H.end_chain()


def _run(stub, **kw):
    kw.setdefault("question", "如果负载翻十倍会怎样")
    kw.setdefault("evidence", _ev())
    return H.hypothesize(kw.pop("question"), kw.pop("evidence"),
                         deadline=time.time() + 30.0, origin="user_query",
                         endpoint_resolver=lambda: EP, http_json=stub, **kw)


# ---------------------------------------------------------------- 刀1 · 纯函数
@pytest.mark.parametrize("intent,expect,kind", [
    ("技术推理", False, "code_reasoning 的中文落名"),
    ("code_reasoning", False, "英文契约"),
    ("知识查询", False, "fact_query 的中文落名"),
    ("fact_query", False, "英文契约"),
    ("状态查询", False, "time_query 邻域"),
    ("time_query", False, "英文契约"),
    ("元认知报告", True, "self_report 的中文落名"),
    ("self_report", True, "英文契约"),
    ("hypothesis", True, "白名单"),
    ("what_if", True, "白名单"),
    ("一般对话", None, "未登记 ⇒ 降级关键词判据"),
    ("", None, "空 ⇒ 降级关键词判据"),
])
def test_intent_gate_table(intent, expect, kind):
    _allow, _why = H.intent_gate(intent)
    assert _allow is expect, (intent, kind, _allow, _why)
    assert isinstance(_why, str) and _why


def test_intent_gate_off_allows_everything(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M457_INTENT_WHITELIST", False, raising=False)
    assert H.intent_gate("技术推理")[0] is True


# ---------------------------------------------------------------- 刀1 · 接线
@pytest.mark.parametrize("intent", ["技术推理", "code_reasoning", "知识查询",
                                    "fact_query", "time_query"])
def test_denied_intent_never_calls_llm(intent):
    _s = _Stub()
    _out = _run(_s, intent_type=intent)
    assert _out["status"] == H.STATUS_SKIPPED_INTENT, _out
    assert _s.calls == [], "意图被拦时绝不能发 HTTP"
    assert _out["hypotheses"] == []
    assert _out["intent_gate"] == "deny"
    assert "不生成假设" in _out["reason"]


def test_allowed_intent_generates():
    _s = _Stub()
    _out = _run(_s, intent_type="元认知报告")
    assert _out["status"] == H.STATUS_OK, _out
    assert len(_s.calls) == 1
    assert _out["intent_gate"] == "allow"


def test_unknown_intent_still_generates():
    """★回归护栏：实盘 qica_intent_type 多为空/「一般对话」——
    若把未知当拒绝，假设功能会被静默全灭（457 T0 的核心风险）。"""
    _s = _Stub()
    _out = _run(_s, intent_type="")
    assert _out["status"] == H.STATUS_OK, _out
    assert _out["intent_gate"] == "unknown"
    assert len(_s.calls) == 1


def test_gate_off_restores_456_behavior(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M457_INTENT_WHITELIST", False, raising=False)
    _s = _Stub()
    _out = _run(_s, intent_type="技术推理")
    assert _out["status"] == H.STATUS_OK, _out
    assert len(_s.calls) == 1


# ---------------------------------------------------------------- 刀2 · 超时
def test_single_call_hard_cap_is_8s():
    _s = _Stub()
    _run(_s)
    assert _s.calls[0]["timeout"] == 8, _s.calls


def test_hard_cap_never_exceeds_8s_even_if_configured_higher(monkeypatch):
    monkeypatch.setattr(config, "M457_HYPOTHESIZE_HARD_TIMEOUT_SEC", 30.0,
                        raising=False)
    monkeypatch.setattr(config, "M455_HYPOTHESIZE_TIMEOUT_SEC", 30.0, raising=False)
    _s = _Stub()
    _run(_s, intent_type="元认知报告")
    # ★上限取 min(457, 455)，两者都被调高也不会超过「任务书 8s」这一物理约定
    assert _s.calls[0]["timeout"] <= 8, _s.calls


def test_timeout_is_not_retried():
    _s = _Stub(result=(False, "Timeout 8000ms exceeded"))
    _out = _run(_s, intent_type="元认知报告")
    assert _out["status"] == H.STATUS_TIMEOUT, _out
    assert len(_s.calls) == 1, "超时绝不重试（456 事故的直接成因）"
    assert _out["hypotheses"] == []
    assert _out["attempts"] == 1


def test_chain_budget_blocks_second_call():
    """同一请求里，链已耗时 9.5s ⇒ 剩余 <1s ⇒ 直接跳过，不发第二次 HTTP。"""
    H.begin_chain(time.time() - 9.5)
    _s = _Stub()
    _out = _run(_s, intent_type="元认知报告")
    assert _out["status"] == H.STATUS_NO_BUDGET, _out
    assert "chain_budget" in _out["reason"]
    assert _s.calls == []
    assert _out["chain_elapsed_ms"] >= 9000


def test_chain_budget_caps_single_timeout():
    H.begin_chain(time.time() - 4.0)
    _s = _Stub()
    _run(_s, intent_type="元认知报告")
    assert _s.calls[0]["timeout"] <= 6, _s.calls


def test_chain_budget_total_never_exceeds_10s():
    """两次调用累加：第二次的超时被压到剩余链预算内（≤10s 总账）。"""
    H.begin_chain(time.time() - 3.0)
    _s = _Stub()
    _run(_s, intent_type="元认知报告")
    _first = _s.calls[0]["timeout"]
    EI.fresh_state()            # 只换券，不换链起点 —— 模拟同一请求的第二次调用
    H.begin_chain(time.time() - 3.0 - _first)
    _out2 = _run(_s, intent_type="元认知报告")
    assert _out2["status"] == H.STATUS_NO_BUDGET, _out2
    assert "chain_budget" in _out2["reason"]
    assert len(_s.calls) == 1


def test_chain_not_registered_keeps_455_behavior():
    """没登记链起点 ⇒ 不启用链预算（455 的 29 条既有单测走这条）。"""
    assert H.chain_elapsed() is None
    _s = _Stub()
    _out = _run(_s, intent_type="元认知报告")
    assert _out["status"] == H.STATUS_OK, _out


# ---------------------------------------------------------------- 配置口径
def test_config_defaults():
    assert config.ENABLE_M457_INTENT_WHITELIST is True
    assert float(config.M457_HYPOTHESIZE_HARD_TIMEOUT_SEC) == 8.0
    assert float(config.M457_HYPOTHESIZE_CHAIN_BUDGET_SEC) == 10.0
    assert config.ENABLE_INJECT_CURRENT_TIME is True
    # ★止血状态：本批**不得**把它改回 True（456 事故后的人工决策）
    assert config.ENABLE_M455_HYPOTHESIZE is False
