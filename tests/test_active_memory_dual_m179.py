# -*- coding: utf-8 -*-
"""179批 刀1 门控单测：Active Memory 双路三态（O-A4，隔离单跑）。

验证：
  - 总开关关闭 → passthrough，绝不调用 LLM；
  - DETERMINISTIC 命中规则 → 落 matched_rule_id，全程不调 LLM；
  - DETERMINISTIC 未命中 → 回落 NONE，不调 LLM；
  - SUBAGENT（关/开）→ 不调 LLM（关→NONE；开→deferred，真实子代理推后续批）；
  - 三态词面（DETERMINISTIC/SUBAGENT/NONE）可检。
不依赖框架运行（曈曈停机态亦可跑）。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.field import active_memory_dual as am  # noqa: E402


class _FakeLLM:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return "llm-result"


@pytest.fixture(autouse=True)
def _reset():
    yield
    config.ENABLE_O_A4_DUAL_PATH = False
    config.O_A4_MODE = "NONE"
    config.O_A4_SUBAGENT_ENABLED = False


def test_disabled_is_passthrough_no_llm():
    config.ENABLE_O_A4_DUAL_PATH = False
    fake = _FakeLLM()
    r = am.resolve("现在几点", llm_call=fake)
    assert r["state"] == "NONE"
    assert r["action"] == "passthrough"
    assert r["llm_used"] is False
    assert fake.calls == 0


def test_deterministic_rule_hit_no_llm():
    config.ENABLE_O_A4_DUAL_PATH = True
    config.O_A4_MODE = "DETERMINISTIC"
    fake = _FakeLLM()
    r = am.resolve("现在几点了", llm_call=fake)
    assert r["state"] == "DETERMINISTIC"
    assert r["matched_rule_id"] == "rule_current_time"
    assert r["llm_used"] is False
    assert fake.calls == 0


def test_deterministic_no_match_no_llm():
    config.ENABLE_O_A4_DUAL_PATH = True
    config.O_A4_MODE = "DETERMINISTIC"
    fake = _FakeLLM()
    r = am.resolve("讲个冷笑话", llm_call=fake)
    assert r["state"] == "NONE"
    assert r["matched_rule_id"] == ""
    assert fake.calls == 0


def test_subagent_disabled_no_llm():
    config.ENABLE_O_A4_DUAL_PATH = True
    config.O_A4_MODE = "SUBAGENT"
    config.O_A4_SUBAGENT_ENABLED = False
    fake = _FakeLLM()
    r = am.resolve("规划明天的任务", llm_call=fake)
    assert r["state"] == "NONE"
    assert fake.calls == 0


def test_subagent_enabled_deferred_no_llm():
    config.ENABLE_O_A4_DUAL_PATH = True
    config.O_A4_MODE = "SUBAGENT"
    config.O_A4_SUBAGENT_ENABLED = True
    fake = _FakeLLM()
    r = am.resolve("规划明天的任务", llm_call=fake)
    assert r["state"] == "SUBAGENT"
    assert r["action"] == "deferred"
    assert fake.calls == 0


def test_three_states_keyword_present():
    src = open(am.__file__, "r", encoding="utf-8").read()
    for _s in ("DETERMINISTIC", "SUBAGENT", "NONE"):
        assert _s in src, f"三态词面缺失: {_s}"
