# -*- coding: utf-8 -*-
"""455 刀2/3/4：hypothesize 独立通道 —— 预算、JSON 契约、请求级算子券。

★本文件守的四件事（任务书验收逐条对应）
  1. 无证据**不调用 LLM**；剩余预算 <12s 直接跳过；单次 timeout ≤8s 且扣掉答案预留；
  2. 严格 JSON 契约：围栏可去、``speculative`` 必须 true、乱码/截断 ⇒ ``parse_error``
     且**不返回任何原文**；``evidence_ids`` 只认本次提供的；无有效 id ⇒ confidence ≤0.3；
  3. 一刀最多 2 条假设；
  4. 请求级「假设券」：同请求第二次必跳过、失败重试**不复位**、并发两请求各领各的。

★为什么每个用例都要 ``fresh_state()``：券是 ContextVar 级请求状态，前一个用例领过
  就会让后一个用例「一张都领不到」——这不是 bug，是本刀的设计（见刀4）。
"""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import config
from nucleus import evidence_injection as EI
from nucleus.reasoning import hypothesize as H

EP = ("https://unit.test/v1/chat/completions", "sk-unit", "unit-model", "unit")


def _ev(n=1):
    return [{"id": f"E{i + 1}", "text": f"证据正文 {i + 1}", "source": "qa"}
            for i in range(n)]


def _resp(content, *, finish="stop", tokens=64, reasoning=""):
    return {"choices": [{"finish_reason": finish,
                         "message": {"content": content,
                                     "reasoning_content": reasoning}}],
            "usage": {"completion_tokens": tokens}}


class _Stub:
    """HTTP 替身：记录调用次数与 timeout，按预设返回。"""

    def __init__(self, result=None):
        self.calls = []
        self.result = result if result is not None else (True, _resp("{}"))

    def __call__(self, url, **kw):
        self.calls.append({"url": url, "timeout": kw.get("timeout"),
                           "data": kw.get("data")})
        return self.result


@pytest.fixture(autouse=True)
def _fresh():
    """每个用例一个「新请求」：清空券。"""
    EI.fresh_state()
    H.end_request()
    yield
    EI.fresh_state()
    H.end_request()


def _run(stub, *, question="如果负载翻十倍会怎样", evidence=None, deadline_in=30.0,
         origin="user_query", **kw):
    return H.hypothesize(question, _ev() if evidence is None else evidence,
                         deadline=time.time() + deadline_in, origin=origin,
                         endpoint_resolver=lambda: EP, http_json=stub, **kw)


# ---------------------------------------------------------------- 配置口径
def test_config_defaults():
    assert config.ENABLE_M455_HYPOTHESIZE_SHADOW is True
    # ★456 刀1：455 批此处钉 False（「本批不进答案」）；456 批按任务书把它打开，
    #   假设由 PulseInnerWorld._m456_attach_hypotheses 统一拼到答案末尾。
    # ★457（2026-11-05 20:xx 实盘事故后）：星轨**手工止血**，把该键改回 False。
    #   这是**有意状态**，不是脏改动 ⇒ 规格断言随之改钉 False。
    #   回滚点：等 457 刀1/刀2 生效并实盘验证后，若决定恢复，
    #   必须**同时**改 config.py 该键与这一行（两处必须一起动），否则本条会立刻红。
    assert config.ENABLE_M455_HYPOTHESIZE is False
    assert config.M455_MAX_LLM_OPS == 1
    assert config.M455_HYPOTHESIZE_TIMEOUT_SEC == 8.0
    assert config.M455_HYPOTHESIZE_MAX_TOKENS == 320
    assert abs(config.M455_HYPOTHESIZE_TEMPERATURE - 0.4) < 1e-9
    assert config.M455_ANSWER_RESERVE_SEC == 10.0
    assert config.M455_MIN_START_SEC == 12.0
    assert H.max_llm_ops() == 1


# ---------------------------------------------------------------- 刀2：预算
def test_no_evidence_never_calls_llm():
    """★刀2 验收：无证据不调 LLM（凭空假设没有价值）。"""
    stub = _Stub()
    out = _run(stub, evidence=[], deadline_in=60.0)
    assert out["status"] == H.STATUS_INSUFFICIENT, out
    assert out["reason"] == "no_evidence"
    assert stub.calls == []
    assert EI.llm_ops_used() == 0, "没调 LLM 就不该消耗券"


def test_empty_question_is_insufficient():
    stub = _Stub()
    out = _run(stub, question="   ")
    assert out["status"] == H.STATUS_INSUFFICIENT
    assert out["reason"] == "empty_question"
    assert stub.calls == []


def test_short_remain_skips_without_calling():
    """剩余 <12s ⇒ 直接 skipped_no_budget，一秒都不花。"""
    stub = _Stub()
    out = _run(stub, deadline_in=5.0)
    assert out["status"] == H.STATUS_NO_BUDGET, out
    assert "remain=" in out["reason"]
    assert stub.calls == []


def test_timeout_is_own_and_capped_at_8s():
    """★不复用 inner_world_chat 的 45s：远端 deadline 再宽也只给 8s。"""
    stub = _Stub()
    _run(stub, deadline_in=3600.0)
    assert stub.calls, "应当已发出调用"
    assert stub.calls[0]["timeout"] == 8, stub.calls[0]


def test_timeout_subtracts_answer_reserve():
    """deadline 只剩 ~16s ⇒ timeout ≈ 16-10 = 6s（给最终回答留 10s）。"""
    stub = _Stub()
    _run(stub, deadline_in=16.0)
    assert stub.calls
    assert 3 <= stub.calls[0]["timeout"] <= 6, stub.calls[0]
    assert stub.calls[0]["timeout"] < 8, "必须被 answer_reserve 压下来"


def test_deadline_registry():
    _s = time.time()
    _d = H.begin_request(_s, total=34.0)
    assert abs(_d - (_s + 34.0)) < 1e-6
    assert abs(H.request_deadline() - _d) < 1e-6
    H.end_request()
    assert H.request_deadline() > time.time() + 30.0


# ---------------------------------------------------------------- 刀2：调用形态
def test_payload_contract():
    """temperature/max_tokens 按任务书；system prompt 要求 JSON；证据带 id。"""
    stub = _Stub()
    _run(stub)
    body = json.loads(stub.calls[0]["data"].decode("utf-8"))
    assert body["temperature"] == pytest.approx(0.4)
    assert body["max_tokens"] == 320
    assert body["model"] == "unit-model"
    assert "JSON" in body["messages"][0]["content"]
    assert "[E1]" in body["messages"][1]["content"]


def test_shadow_off_skips(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M455_HYPOTHESIZE_SHADOW", False, raising=False)
    stub = _Stub()
    out = _run(stub)
    assert out["status"] == H.STATUS_NO_BUDGET
    assert out["reason"] == "shadow_off"
    assert stub.calls == []


def test_non_user_origin_skips():
    """进化/内部任务不付这笔钱（烛微 H5）。"""
    stub = _Stub()
    out = _run(stub, origin="evolution_task")
    assert out["status"] == H.STATUS_SKIPPED_ORIGIN, out
    assert stub.calls == []


def test_no_endpoint_returns_no_endpoint():
    out = H.hypothesize("如果…会怎样", _ev(), deadline=time.time() + 60,
                        origin="user_query", endpoint_resolver=lambda: None,
                        http_json=_Stub())
    assert out["status"] == H.STATUS_NO_ENDPOINT, out


# ---------------------------------------------------------------- 刀3：JSON 契约
_GOOD = json.dumps({"speculative": True, "hypotheses": [
    {"claim": "延迟会线性上升", "assumptions": ["瓶颈是串行队列"],
     "evidence_ids": ["E1"], "confidence": 0.72, "test": "压测 QPS 曲线"}]},
    ensure_ascii=False)


def test_ok_path_parses():
    out = _run(_Stub((True, _resp(_GOOD))))
    assert out["status"] == H.STATUS_OK, out
    assert out["hypothesis_count"] == 1
    assert out["hypotheses"][0]["confidence"] == pytest.approx(0.72)
    assert out["hypotheses"][0]["evidence_ids"] == ["E1"]
    assert out["avg_confidence"] == pytest.approx(0.72)
    assert out["evidence_count"] == 1
    assert out["elapsed_ms"] >= 0


def test_fence_is_stripped():
    out = _run(_Stub((True, _resp("```json\n" + _GOOD + "\n```"))))
    assert out["status"] == H.STATUS_OK, out


def test_garbage_is_parse_error_and_never_echoed():
    """★解析失败**绝不把原文当结论**塞回调用方。"""
    out = _run(_Stub((True, _resp("我觉得大概会崩吧，因为……"))))
    assert out["status"] == H.STATUS_PARSE_ERROR, out
    assert out["hypotheses"] == []
    assert "我觉得" not in json.dumps(out, ensure_ascii=False)


def test_truncated_json_is_parse_error():
    out = _run(_Stub((True, _resp(_GOOD[:40]))))
    assert out["status"] == H.STATUS_PARSE_ERROR, out
    assert out["hypotheses"] == []


def test_not_speculative_rejected():
    bad = json.dumps({"speculative": False, "hypotheses": [
        {"claim": "这是事实", "evidence_ids": ["E1"], "confidence": 0.9}]},
        ensure_ascii=False)
    out = _run(_Stub((True, _resp(bad))))
    assert out["status"] == H.STATUS_PARSE_ERROR, out
    assert out["dropped"].get("not_speculative") == 1


def test_missing_speculative_key_rejected():
    bad = json.dumps({"hypotheses": [{"claim": "x", "confidence": 0.9}]})
    out = _run(_Stub((True, _resp(bad))))
    assert out["status"] == H.STATUS_PARSE_ERROR


def test_unknown_evidence_id_dropped_and_capped():
    """引用了本次没给的 id ⇒ 丢弃 + confidence 封顶 0.3。"""
    bad = json.dumps({"speculative": True, "hypotheses": [
        {"claim": "外部事实支撑的假设", "evidence_ids": ["E9"], "confidence": 0.95}]},
        ensure_ascii=False)
    out = _run(_Stub((True, _resp(bad))))
    assert out["status"] == H.STATUS_OK, out
    h = out["hypotheses"][0]
    assert h["evidence_ids"] == []
    assert h["confidence"] <= 0.3, h
    assert out["dropped"].get("unknown_evidence_id") == 1


def test_no_evidence_ids_caps_confidence():
    bad = json.dumps({"speculative": True, "hypotheses": [
        {"claim": "无依据的猜测", "confidence": 0.88, "assumptions": ["a"]}]},
        ensure_ascii=False)
    out = _run(_Stub((True, _resp(bad))))
    assert out["status"] == H.STATUS_OK
    assert out["hypotheses"][0]["confidence"] == pytest.approx(0.3)
    assert out["avg_confidence"] == pytest.approx(0.3)


def test_at_most_two_hypotheses():
    many = json.dumps({"speculative": True, "hypotheses": [
        {"claim": f"h{i}", "evidence_ids": ["E1"], "confidence": 0.5}
        for i in range(5)]}, ensure_ascii=False)
    out = _run(_Stub((True, _resp(many))))
    assert out["status"] == H.STATUS_OK
    assert out["hypothesis_count"] == 2
    assert out["dropped"].get("over_limit") == 3


def test_confidence_clamped():
    assert H.parse_hypotheses(json.dumps({"speculative": True, "hypotheses": [
        {"claim": "x", "evidence_ids": ["E1"], "confidence": 7}]}),
        ["E1"])["hypotheses"][0]["confidence"] == 1.0
    assert H.parse_hypotheses(json.dumps({"speculative": True, "hypotheses": [
        {"claim": "x", "evidence_ids": ["E1"], "confidence": -3}]}),
        ["E1"])["hypotheses"][0]["confidence"] == 0.0


def test_empty_content_records_meta():
    """★86/87 批教训：推理模型吃光 max_tokens ⇒ 留痕 finish_reason/tokens/字数。"""
    out = _run(_Stub((True, _resp("", finish="length", tokens=320, reasoning="想" * 88))))
    assert out["status"] == H.STATUS_PARSE_ERROR, out
    assert "empty_content" in out["reason"]
    assert "length" in out["reason"] and "320" in out["reason"]


def test_http_failure_is_timeout_status():
    out = _run(_Stub((False, "request timeout after 8s")))
    assert out["status"] == H.STATUS_TIMEOUT, out
    assert out["hypotheses"] == []


def test_http_exception_is_timeout_status():
    def _boom(url, **kw):
        raise RuntimeError("socket hang up")
    out = _run(_boom)
    assert out["status"] == H.STATUS_TIMEOUT, out
    assert "RuntimeError" in out["reason"]


# ---------------------------------------------------------------- 刀4：算子券
def test_second_call_in_same_request_is_blocked():
    """★刀4 验收：同一请求两次 hypothesize 只执行一次。"""
    stub = _Stub((True, _resp(_GOOD)))
    first = _run(stub)
    second = _run(stub)
    assert first["status"] == H.STATUS_OK
    assert second["status"] == H.STATUS_NO_BUDGET, second
    assert "llm_ops" in second["reason"]
    assert len(stub.calls) == 1, "第二次不该真的发出去"
    assert EI.llm_ops_used() == 1


def test_retry_does_not_reset_ticket_count():
    """★失败重试复用同一计数：第一次超时也要算数，第二次不许再领。"""
    bad = _Stub((False, "timeout"))
    assert _run(bad)["status"] == H.STATUS_TIMEOUT
    assert EI.llm_ops_used() == 1
    again = _run(_Stub((True, _resp(_GOOD))))
    assert again["status"] == H.STATUS_NO_BUDGET, again


def test_new_request_gets_new_ticket():
    assert _run(_Stub((True, _resp(_GOOD))))["status"] == H.STATUS_OK
    EI.fresh_state()                      # 新请求
    assert _run(_Stub((True, _resp(_GOOD))))["status"] == H.STATUS_OK


def test_reserve_llm_op_contract():
    EI.fresh_state()
    a = EI.reserve_llm_op(1)
    b = EI.reserve_llm_op(1)
    assert a == {"ok": True, "used": 1, "limit": 1}
    assert b["ok"] is False and b["used"] == 1
    assert EI.get_request_state().get("llm_ops") == 1


def test_concurrent_requests_are_independent():
    """★刀4 验收：并发两请求各自独立计数（ContextVar 隔离）。"""
    out = {}

    def _worker(tag):
        EI.fresh_state(request_id=tag)
        out[tag] = (EI.reserve_llm_op(1)["ok"], EI.reserve_llm_op(1)["ok"])

    ts = [threading.Thread(target=_worker, args=(f"r{i}",)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)
    assert out == {"r0": (True, False), "r1": (True, False)}, out
