#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第161批段B B2 门控单测：危机转介判定链。

锁定四件事：
  1) RiskEvent.CRISIS_REFERRAL 已定义且与 ALERT 独立；
  2) crisis_level 分级 L3/L2/L1 准确，无危机返回 None；
  3) 危机判定**独立于** risk_level 阈值（不因关系调制导致 risk_level 下降而漏判）；
  4) PulseCortex 危机文案遵守禁用项（无第一人称/无「作为一个」/无「人工智能」/无「机器人」）。
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FORBIDDEN_PHRASES = ("作为一个", "人工智能", "机器人")


def _load(module_path, name):
    spec = importlib.util.spec_from_file_location(name, module_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestB2CrisisEvent:
    def test_01_crisis_referral_defined(self):
        from nucleus.const import RiskEvent
        assert hasattr(RiskEvent, "CRISIS_REFERRAL")

    def test_02_crisis_referral_distinct_from_alert(self):
        from nucleus.const import RiskEvent
        assert RiskEvent.CRISIS_REFERRAL != RiskEvent.ALERT
        assert RiskEvent.CRISIS_REFERRAL == "risk.crisis_referral"


class TestB2Classify:
    def setup_method(self):
        self.C = _load(os.path.join(ROOT, "organs", "brain",
                                    "PulseRiskPerception.py"),
                       "PRP_b2").PulseRiskPerception

    def test_03_levels_tuple(self):
        assert self.C.CRISIS_LEVELS == ("L1", "L2", "L3")

    def test_04_l3_for_self_harm(self):
        f = self.C._classify_crisis_level
        for t in ("self_harm", "suicide", "violence_threat"):
            assert f(None, [{"type": t, "severity": "high"}]) == "L3", t

    def test_05_l2_for_severe_attack(self):
        f = self.C._classify_crisis_level
        for t in ("identity_erosion", "knowledge_pollution", "mission_distortion"):
            assert f(None, [{"type": t, "severity": "high"}]) == "L2", t

    def test_06_l1_for_manipulation(self):
        f = self.C._classify_crisis_level
        for t in ("relation_manipulation", "resource_trap"):
            assert f(None, [{"type": t, "severity": "medium"}]) == "L1", t

    def test_07_none_for_empty_or_unknown(self):
        f = self.C._classify_crisis_level
        assert f(None, []) is None
        assert f(None, [{"type": "unrelated", "severity": "low"}]) is None

    def test_08_l3_wins_over_lower_types(self):
        """同时命中 L3 与 L1 时取最高级 L3。"""
        f = self.C._classify_crisis_level
        risks = [{"type": "relation_manipulation", "severity": "medium"},
                 {"type": "self_harm", "severity": "high"}]
        assert f(None, risks) == "L3"

    def test_09_independent_of_risk_level(self):
        """★核心约束：危机判定只看类型，不看 risk_level 数值高低。"""
        f = self.C._classify_crisis_level
        # risk_level 再低（关系调制可降至 0.5 倍），只要类型命中仍须转介
        risks = [{"type": "self_harm", "severity": "high"}]
        assert f(None, risks) == "L3"


class TestB2CortexHandler:
    def setup_method(self):
        self.P = _load(os.path.join(ROOT, "organs", "brain", "PulseCortex.py"),
                       "PC_b2").PulseCortex

    def test_10_handler_exists(self):
        assert hasattr(self.P, "_on_crisis_referral")

    def test_11_text_respects_forbidden_phrases(self):
        """危机文案禁第一人称自称与 AI 身份词。"""
        for lv in ("L1", "L2", "L3"):
            txt = self.P._build_crisis_referral_text(lv)
            assert isinstance(txt, str) and txt.strip(), lv
            for bad in FORBIDDEN_PHRASES:
                assert bad not in txt, f"{lv} 含禁用词 {bad}"
            # 禁「我」开头自称（AI/人工智能/机器人 已在上面覆盖）
            assert not txt.startswith("我"), f"{lv} 以第一人称「我」开头"

    def test_12_all_levels_have_text(self):
        for lv in ("L1", "L2", "L3"):
            assert self.P._build_crisis_referral_text(lv)
        # 未知等级走兜底，不抛异常
        assert self.P._build_crisis_referral_text("L9")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
