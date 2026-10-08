# -*- coding: utf-8 -*-
"""169批停窗段 门控单测：否定语境豁免（修 C7' 扩面引入的既有红）。

背景
----
C7' 扩面加入「想死」后，`tests/test_knife5_crisis_l3_m161lower.py::
test_07_no_false_positive_on_normal_text` 变红：
「我不想死，我还想看着曈曈长大」被子串「想死」误判 self_harm。
风险感知侧无否定语义豁免 ⇒ 误报。

本测试锁定 `is_negated_harm_mention` 的精确口径（三次实测踩坑后定稿）：
  - 词表词**自带否定**（「不想活」）→ **绝不豁免**（指「不想活了」＝真危机）；
  - 其余词（想死/自杀/割腕…）处于否定结构中 → 豁免；
  - 字面重叠（否定前缀「不想」与关键词「想死」共享「想」字）须正确处理。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.security.crisis_keywords import (  # noqa: E402
    CRISIS_SELF_HARM_KEYWORDS, is_negated_harm_mention as neg)


class TestNegatedHarmMention:
    def test_negated_pure_forms(self):
        """否定结构 ⇒ 豁免。"""
        for t, k in [("我不想死，我还想看着曈曈长大", "想死"),
                     ("我不要死", "死"),
                     ("别死", "死")]:
            assert neg(t, k) is True, "应豁免: %s / %s" % (t, k)

    def test_real_crisis_not_exempt(self):
        """真危机 ⇒ 不豁免（漏检代价 > 误报代价，从严）。"""
        for t, k in [("我想死", "想死"),
                     ("我想自杀", "想自杀"),
                     ("我要跳楼", "跳楼"),
                     ("我想割腕", "割腕"),
                     ("活着没意思", "活着没意思"),
                     ("我不想活了，真的想死", "想死"),
                     ("我不是很想死", "想死")]:   # 双重否定⇒仍属倾向，从严不豁免
            assert neg(t, k) is False, "误豁免: %s / %s" % (t, k)

    def test_keyword_with_own_negation_never_exempt(self):
        """★词表词自带否定（「不想活」）⇒ 绝不豁免。"""
        assert "不想活" in CRISIS_SELF_HARM_KEYWORDS
        assert neg("我不想活了", "不想活") is False
        assert neg("我不想活了", "不想活") is False

    def test_literal_overlap_handling(self):
        """字面重叠：否定前缀「不想」与关键词「想死」共享「想」字，须正确豁免。"""
        # 「不想」+「死」连续成子串 ⇒ 豁免
        assert neg("我不想死", "想死") is True
        # 无否定前缀时不得豁免
        assert neg("我想死", "想死") is False

    def test_empty_inputs(self):
        assert neg("", "想死") is False
        assert neg("我想死", "") is False


class TestExistingRedFixed:
    def test_knife5_negative_cases_all_pass(self):
        """★C7' 引入的既有红必须已修复（6 条反例全不误判）。"""
        from organs.brain.PulseRiskPerception import PulseRiskPerception
        o = PulseRiskPerception.__new__(PulseRiskPerception)
        o.SELF_HARM_PATTERNS = list(CRISIS_SELF_HARM_KEYWORDS)
        cases = ("人终有一死，所以要把每一天过好",
                 "生命的意义是什么？",
                 "我不想死，我还想看着曈曈长大",
                 "今天加班累死了",
                 "这部电影里主角最后死了，很感人",
                 "死亡是哲学里经常讨论的话题")
        for text in cases:
            hits = o._match_patterns(text, o.SELF_HARM_PATTERNS, "self_harm")
            keep = [w for w in hits if not neg(text, w)]
            assert not keep, "仍误判危机: %s -> %s" % (text, keep)