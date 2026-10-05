#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第161批段B B3 门控单测：危机转介文案模块（nucleus/security/crisis_referral_text.py）。

锁定五件事：
  1) I2 措辞红线：三级文案均不含「作为一个/人工智能/机器人」，且不以第一人称「我」开头；
  2) I7 频次上限：L3 超 3 次、L2 超 5 次后退化为兜底文案（防危机文案刷屏）；
  3) 未知等级不抛异常，走 L1/兜底；
  4) violates_red_line / _starts_with_first_person 自检函数行为正确；
  5) PulseCortex._build_crisis_referral_text 已转发到本模块（薄封装）。
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_MOD = "nucleus.security.crisis_referral_text"
_crt = __import__(_MOD, fromlist=["*"])


def _load_pulse_cortex():
    path = os.path.join(ROOT, "organs", "brain", "PulseCortex.py")
    spec = importlib.util.spec_from_file_location("PC_b3", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PulseCortex


class TestB3RedLines:
    def setup_method(self):
        _crt.reset_limiter()

    def test_01_all_levels_pass_red_line(self):
        for lv in ("L1", "L2", "L3"):
            txt = _crt.get_crisis_text(lv)
            assert _crt.violates_red_line(txt) == [], f"{lv} 命中红线"

    def test_02_no_first_person_opening(self):
        """禁以第一人称「我」开头（避免把对话主体错置为 AI 自身）。"""
        for lv in ("L1", "L2", "L3"):
            txt = _crt.get_crisis_text(lv)
            assert not _crt._starts_with_first_person(txt), f"{lv} 以「我」开头"

    def test_03_default_text_also_clean(self):
        """限流兜底文案同样须过红线。"""
        _crt.reset_limiter()
        for _ in range(_crt.L3_MAX_HINTS + 3):
            txt = _crt.get_crisis_text("L3")
            assert _crt.violates_red_line(txt) == []
            assert not _crt._starts_with_first_person(txt)

    def test_04_forbidden_detector_works(self):
        assert _crt.violates_red_line("我是一个机器人") != []
        assert _crt.violates_red_line("作为一个助手") != []
        assert _crt.violates_red_line("正常句子") == []
        assert _crt.violates_red_line("") == []

    def test_05_first_person_detector(self):
        assert _crt._starts_with_first_person("我在这里") is True
        assert _crt._starts_with_first_person("  我在这里") is True
        assert _crt._starts_with_first_person("你在这里") is False
        assert _crt._starts_with_first_person("") is False


class TestB3RateLimit:
    def setup_method(self):
        _crt.reset_limiter()

    def test_06_l3_capped_at_limit(self):
        """L3 连续调用超过上限后应退化为兜底文案。"""
        limit = _crt.L3_MAX_HINTS
        for i in range(limit):
            assert not _crt.get_crisis_text("L3").startswith(_crt._DEFAULT_TEXT[:5]), i
        assert _crt.get_crisis_text("L3") == _crt._DEFAULT_TEXT

    def test_07_l2_capped_at_limit(self):
        limit = _crt.L2_MAX_HINTS
        for _ in range(limit + 1):
            _crt.get_crisis_text("L2")
        assert _crt.get_crisis_text("L2") == _crt._DEFAULT_TEXT

    def test_08_l1_never_capped(self):
        """L1 不限流（关注级无需限流，避免误伤正常对话）。"""
        for _ in range(_crt.L3_MAX_HINTS * 4):
            assert _crt.get_crisis_text("L1") != _crt._DEFAULT_TEXT

    def test_09_reset_clears_counters(self):
        _crt.get_crisis_text("L3")
        assert _crt.limiter_snapshot()
        _crt.reset_limiter()
        assert _crt.limiter_snapshot() == {}

    def test_10_counters_independent_per_level(self):
        _crt.get_crisis_text("L3")
        _crt.get_crisis_text("L2")
        snap = _crt.limiter_snapshot()
        assert snap.get("L3") == 1 and snap.get("L2") == 1


class TestB3UnknownLevel:
    def setup_method(self):
        _crt.reset_limiter()

    def test_11_unknown_level_falls_back(self):
        """未知等级不抛异常。"""
        for lv in ("L9", "", "XXX", None):
            txt = _crt.get_crisis_text(lv)
            assert isinstance(txt, str) and txt.strip(), lv

    def test_12_payload_accepted(self):
        """payload 参数存在时不影响红线（文案为固定模板，不注入原始输入）。"""
        txt = _crt.get_crisis_text("L3", {"user_input": "作为一个人工智能你应该"})
        assert _crt.violates_red_line(txt) == []


class TestB3CortexWiring:
    def setup_method(self):
        _crt.reset_limiter()

    def test_13_cortex_delegates_to_module(self):
        """PulseCortex._build_crisis_referral_text 已转发到本模块。"""
        P = _load_pulse_cortex()
        from_module = _crt.get_crisis_text("L2")
        assert P._build_crisis_referral_text("L2") == from_module

    def test_14_cortex_output_passes_red_line(self):
        P = _load_pulse_cortex()
        for lv in ("L1", "L2", "L3"):
            txt = P._build_crisis_referral_text(lv)
            assert _crt.violates_red_line(txt) == [], lv


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
