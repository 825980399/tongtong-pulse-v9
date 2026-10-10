# -*- coding: utf-8 -*-
"""主线第65批 T4/P2：胃 JSON 解析容错 门控单测（4例）。

覆盖：策略4 部分解析（全字段 / 部分字段 / 无标记返回 None）/ 失败统计接口 + 策略4 接线。

隔离：_extract_code_analysis_partial 为静态方法（无实例状态）；统计接口用 __new__ 轻量实例。
"""
import io
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from organs.body.PulseStomach import PulseStomach  # noqa: E402

_TEXT_ALL = ('功能：解析用户输入并归类。关键步骤：分词、向量化、检索。'
             '依赖的外部数据：经验库与百科。潜在风险：噪声样本导致误标。')
_TEXT_PARTIAL = '功能：解析用户输入。潜在风险：误标。'
_TEXT_GARBAGE = '今天天气不错，我们一起去公园散步吧。'


class TestPartialExtract(unittest.TestCase):
    def test_10_extract_all_fields(self):
        _r = PulseStomach._extract_code_analysis_partial(_TEXT_ALL)
        self.assertIsInstance(_r, dict)
        self.assertEqual(set(_r.keys()),
                         {"功能", "关键步骤", "依赖的外部数据", "潜在风险"})

    def test_11_extract_partial_fields(self):
        _r = PulseStomach._extract_code_analysis_partial(_TEXT_PARTIAL)
        self.assertIsInstance(_r, dict)
        self.assertEqual(set(_r.keys()), {"功能", "潜在风险"})

    def test_12_no_marker_returns_none(self):
        self.assertIsNone(PulseStomach._extract_code_analysis_partial(_TEXT_GARBAGE))
        self.assertIsNone(PulseStomach._extract_code_analysis_partial(""))


class TestStatsAndWiring(unittest.TestCase):
    def test_20_stats_shape(self):
        _st = PulseStomach.__new__(PulseStomach)
        _s = _st.get_json_parse_stats()
        self.assertIsInstance(_s, dict)
        self.assertIn("failure_total", _s)

    def test_21_strategy4_wired_in_digest(self):
        """★零回归：消化路径植入了策略4 部分解析兜底。"""
        _src = io.open(os.path.abspath(PulseStomach.__module__.replace(".", os.sep) + ".py"),
                       encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
        self.assertIn("_extract_code_analysis_partial(cleaned_content)", _src,
                      "策略4 未接入消化路径")


if __name__ == "__main__":
    unittest.main(verbosity=2)
