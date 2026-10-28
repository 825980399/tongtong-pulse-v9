# -*- coding: utf-8 -*-
"""★383 刀2 · OA4 **单向**影子（P3）

票面：全影子会让 LLM 调用翻倍、渠道有收费兜底 ⇒ 改单向：
同一 prompt 先走 LLM，规则答案**本地算**一遍记录，**不额外调 LLM**。

本件验：分桶正确、覆盖率与一致率分开、**观察函数绝不调 LLM / 不递归调 resolve**、开关可回退。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

from nucleus.field import active_memory_dual as d  # noqa: E402


def setup_function(_fn):
    d.reset_shadow_stats()
    config.ENABLE_M383_OA4_SHADOW = True
    config.O_A4_DETERMINISTIC_RULES = [["现在几点", "local_time"]]
    config.O_A4_LOCAL_EVIDENCE_PROVIDERS = {"local_time": "local_time"}
    config.ENABLE_O_A4_LOCAL_EVIDENCE = True


def test_m383_d2_no_rule_bucket():
    _o = d.observe_llm_answer("随便聊聊今天天气", "今天不错")
    assert _o["bucket"] == "no_rule" and _o["agree"] is None
    assert d.shadow_stats()["rule_hit"] == 0


def test_m383_d2_hit_without_evidence_is_not_scored():
    """规则命中但没开凭证 ⇒ 只记覆盖率，**不计入一致率分母**。"""
    config.ENABLE_O_A4_LOCAL_EVIDENCE = False
    _o = d.observe_llm_answer("现在几点？", "21:20")
    assert _o["rule_hit"] is True and _o["bucket"] == "hit_no_evidence"
    _s = d.shadow_stats()
    assert _s["scorable"] == 0 and _s["agree_rate_pct"] is None
    assert _s["rule_coverage_pct"] == 100.0


def test_m383_d2_agree_when_evidence_visible():
    _ev = d._m182_local_time()          # 凭证是"本机现在"，不能在测试里写死
    _o = d.observe_llm_answer("现在几点？", "现在是 %s 哦" % _ev)
    assert _o["bucket"] == "agree" and _o["agree"] is True
    _s = d.shadow_stats()
    assert _s["agree"] == 1 and _s["agree_rate_pct"] == 100.0


def test_m383_d2_disagree_when_evidence_absent():
    _o = d.observe_llm_answer("现在几点？", "我不太确定")
    assert _o["bucket"] == "disagree" and _o["agree"] is False
    assert d.shadow_stats()["agree_rate_pct"] == 0.0


def test_m383_d2_coverage_and_agree_rate_are_separate():
    """两个数各答各的：覆盖率答"能免多少"，一致率答"凭证可见多少"。"""
    d.observe_llm_answer("现在几点？", "现在是 %s" % d._m182_local_time())
    d.observe_llm_answer("讲个笑话", "从前有座山")
    _s = d.shadow_stats()
    assert _s["scored"] == 2 and _s["rule_hit"] == 1
    assert _s["rule_coverage_pct"] == 50.0
    assert _s["scorable"] == 1 and _s["agree_rate_pct"] == 100.0


def test_m383_d2_observe_never_calls_llm_nor_resolve(monkeypatch):
    """★核心反控：观察函数不许调 LLM，也不许递归调 resolve（会把观测量翻倍）。"""
    def _boom(*_a, **_kw):
        raise AssertionError("observe 不许走到这里")

    monkeypatch.setattr(d, "resolve", _boom)
    _o = d.observe_llm_answer("现在几点？", "现在是 %s" % d._m182_local_time())
    assert _o["bucket"] == "agree"


def test_m383_d2_switch_off_returns_off():
    config.ENABLE_M383_OA4_SHADOW = False
    _o = d.observe_llm_answer("现在几点？", "x")
    assert _o["on"] is False and _o["bucket"] == "off"
    assert d.shadow_stats()["scored"] == 0
