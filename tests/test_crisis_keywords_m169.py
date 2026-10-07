# -*- coding: utf-8 -*-
"""169批 C7'（T-紧急词表扩面-1）门控测试。

锁定：
  1. **单一来源**：PulseEthics 的危机集与共享常量是**同一对象**；
     PulseRiskPerception 的 SELF_HARM_PATTERNS **包含**该常量
     （config 提供更丰富列表 -> 取并集而非覆盖，强制等同会丢 18 条模式）；
  2. 扩面词命中 → 走 crisis_referral（安抚+转介），普通禁词维持裸 BLOCKED；
  3. 既有「自杀」行为零变更；开关关闭可回退裸拦截；
  4. 13 个危机词**全部可检出**（含 jieba 词边界会漏的「想死/不想活」等）。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

from nucleus.security.crisis_keywords import (CRISIS_SELF_HARM_KEYWORDS,
                                              crisis_self_harm_keywords)
from organs.identity.PulseEthics import PulseEthics
from organs.brain.PulseRiskPerception import PulseRiskPerception


class _Rec:
    def __init__(self):
        self.events = []

    def __call__(self, event, payload=None, **kw):
        self.events.append(str(event))
        return None


def _run(content):
    _e = PulseEthics(organ_name="伦理")
    _r = _Rec()
    _e._emit = _r
    return _e._on_review({"content": content, "correlation_id": "c1"}), _r.events


class TestSingleSource(unittest.TestCase):
    """① 两处词表指向同一常量。"""

    def test_10_ethics_is_same_object(self):
        self.assertIs(PulseEthics._CRISIS_FORBIDDEN_KEYWORDS,
                      CRISIS_SELF_HARM_KEYWORDS,
                      "PulseEthics 危机集必须与共享常量是同一对象")

    def test_11_risk_perception_contains_shared(self):
        """★取并集：共享常量必须**全部包含**（config 列表更丰富，不强制等同）。"""
        _pr = PulseRiskPerception.__new__(PulseRiskPerception)
        _pr._load_crisis_patterns()
        _have = set(_pr.SELF_HARM_PATTERNS)
        self.assertTrue(set(CRISIS_SELF_HARM_KEYWORDS) <= _have,
                        "缺失: %s" % (set(CRISIS_SELF_HARM_KEYWORDS) - _have))

    def test_12_original_words_preserved(self):
        """两侧原有词必须仍在（零行为变更）。"""
        for _w in ("自杀", "活着没意思", "想自杀", "自残"):
            self.assertIn(_w, CRISIS_SELF_HARM_KEYWORDS)

    def test_13_expanded(self):
        self.assertGreater(len(CRISIS_SELF_HARM_KEYWORDS), 4, "应已扩面")
        for _w in ("自残", "自伤", "想死", "活不下去", "割腕"):
            self.assertIn(_w, CRISIS_SELF_HARM_KEYWORDS)


class TestRouting(unittest.TestCase):
    """② 危机词 -> 转介；普通禁词 -> 裸拦截。"""

    def test_20_existing_suicide_unchanged(self):
        _out, _ = _run("我想自杀")
        self.assertEqual(_out["status"], "crisis_referral")

    def test_21_expanded_words_route_to_referral(self):
        for _w in ("割腕", "自残", "轻生", "跳楼", "寻死", "自伤"):
            _out, _ = _run("我想%s" % _w)
            self.assertEqual(_out["status"], "crisis_referral",
                             "扩面词 %s 应走转介，实际 %s" % (_w, _out))

    def test_22_phrase_words_detectable(self):
        """★jieba 词边界会漏的长短语，靠危机集子串兜底检出。"""
        for _c in ("活着没意思", "我不想活了", "我想死", "我想结束生命",
                   "活不下去"):
            _out, _ = _run(_c)
            self.assertEqual(_out["status"], "crisis_referral",
                             "%s 应被检出并转介，实际 %s" % (_c, _out))

    def test_23_normal_forbidden_still_blocked(self):
        """普通禁词维持裸 BLOCKED（不走转介）。"""
        for _c in ("这里有点暴力", "色情内容", "诈骗手段"):
            _out, _ = _run(_c)
            self.assertEqual(_out["status"], "forbidden", _c)

    def test_24_switch_off_rolls_back(self):
        """ENABLE_CRISIS_REFERRAL=False -> 回退裸拦截（零回归）。"""
        with mock.patch.object(config, "ENABLE_CRISIS_REFERRAL", False,
                               create=True):
            _out, _ = _run("我想自杀")
        self.assertEqual(_out["status"], "forbidden")


class TestCoverage(unittest.TestCase):
    """③ 全部危机词可检出（无漏检）。"""

    def test_30_no_missed_crisis_word(self):
        _e = PulseEthics(organ_name="伦理")
        _miss = []
        for _w in crisis_self_harm_keywords():
            if not any(_e._check_forbidden(_t % _w)["blocked"]
                       for _t in ("%s", "我想%s", "我觉得%s", "我打算%s")):
                _miss.append(_w)
        self.assertEqual(_miss, [], "以下危机词漏检: %s" % _miss)


if __name__ == "__main__":
    unittest.main()
