# -*- coding: utf-8 -*-
"""主线第39批 T4 门控：top_issues 各维度 severity 语义对齐（统一 5 级）。

覆盖：5 级定义 / `_normalize_severity` 全口径映射（含未知→info，保守）/
`_severity_rank` 排序权重 / 各维度适配层（knowledge error→critical、
fake_loops severe→critical、evolution 保持）/ `original_severity` 保留原始口径 /
`_collect_top_issues` 按归一化 severity 排序 / 报告使用统一标签。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from nucleus.self_awareness.SelfAwarenessEngine import (
    _SEV_LEVELS,
    SelfAwarenessEngine,
    SelfAwarenessProfile,
    _collect_top_issues,
    _dimension_issues,
    _normalize_severity,
    _severity_rank,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestSeverityLevels(unittest.TestCase):
    """T4：统一 5 级定义。"""

    def test_10_levels(self):
        self.assertEqual(_SEV_LEVELS, ("critical", "high", "medium", "low", "info"))

    def test_11_normalize_all(self):
        cases = [
            # critical（致命）
            ("critical", "critical"), ("fatal", "critical"), ("severe", "critical"),
            ("error", "critical"), ("blocker", "critical"), ("p0", "critical"),
            # high
            ("high", "high"), ("major", "high"), ("p1", "high"),
            # medium
            ("medium", "medium"), ("moderate", "medium"), ("warning", "medium"),
            ("warn", "medium"), ("p2", "medium"),
            # low
            ("low", "low"), ("minor", "low"), ("trivial", "low"), ("p3", "low"),
            # info
            ("info", "info"), ("notice", "info"), ("information", "info"),
            ("debug", "info"),
        ]
        for src, exp in cases:
            self.assertEqual(_normalize_severity(src), exp, src)

    def test_12_normalize_case_insensitive(self):
        self.assertEqual(_normalize_severity("ERROR"), "critical")
        self.assertEqual(_normalize_severity("  High  "), "high")

    def test_13_unknown_is_info_conservative(self):
        """无法识别的取值一律归 info（保守：不夸大严重度）。"""
        for s in ("weird", "", None, 123, "unknown", "P9"):
            self.assertEqual(_normalize_severity(s), "info", repr(s))

    def test_14_rank_order(self):
        self.assertEqual(
            [_severity_rank(s) for s in _SEV_LEVELS], [0, 1, 2, 3, 4])
        self.assertEqual(_severity_rank("error"), 0)
        self.assertEqual(_severity_rank("severe"), 0)
        self.assertEqual(_severity_rank(None), 4)


class TestDimensionAdapters(unittest.TestCase):
    """T4：各维度适配层。"""

    def test_20_knowledge_error_to_critical(self):
        out = _dimension_issues("knowledge_health", {"top_issues": [
            {"severity": "error", "detail": "冲突 3 处"}]})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["severity"], "critical")
        self.assertEqual(out[0]["original_severity"], "error")

    def test_21_fake_loops_severe_to_critical(self):
        out = _dimension_issues("fake_loops", {"critical": [
            {"module": "m", "class": "C", "score": 0}]})
        self.assertEqual(out[0]["severity"], "high")     # score 0 → high（第38批口径）

    def test_22_evolution_keeps_levels(self):
        out = _dimension_issues("evolution_health", {"issues": [
            {"severity": "high", "description": "a"},
            {"severity": "medium", "description": "b"},
            {"severity": "low", "description": "c"},
            {"severity": "info", "description": "d"}]})
        self.assertEqual([x["severity"] for x in out],
                         ["high", "medium", "low", "info"])
        self.assertEqual([x["original_severity"] for x in out],
                         ["high", "medium", "low", "info"])

    def test_23_original_severity_always_kept(self):
        out = _dimension_issues("knowledge_health", {"top_issues": [
            {"severity": "warning", "detail": "w"}]})
        self.assertEqual(out[0]["original_severity"], "warning")
        self.assertEqual(out[0]["severity"], "medium")

    def test_24_code_health_summary_marked(self):
        out = _dimension_issues("code_health", {"by_severity": {"warning": 9}})
        self.assertTrue(out)
        self.assertIn("[汇总型]", out[0]["description"])
        self.assertEqual(out[0]["severity"], "medium")

    def test_25_code_health_low_excluded_from_summary(self):
        """info/low 级汇总不进入 top_issues（只取 warning 及以上）。"""
        out = _dimension_issues("code_health",
                                {"by_severity": {"info": 5, "low": 3}})
        self.assertEqual(out, [])


class TestTopIssuesOrdering(unittest.TestCase):
    """T4：top_issues 排序。"""

    def _profile(self):
        p = SelfAwarenessProfile()
        p.knowledge_health = {"top_issues": [
            {"severity": "medium", "detail": "盲区 2 个"},
            {"severity": "error", "detail": "冲突 3 处"}]}
        p.evolution_health = {"issues": [{"severity": "high",
                                          "description": "成功率低"}]}
        p.fake_loops = {"critical": [{"module": "m", "class": "C", "score": 0}]}
        p.code_health = {"by_severity": {"warning": 9}}
        return p

    def test_30_sorted_non_increasing(self):
        ti = _collect_top_issues(self._profile(), 10)
        ranks = [_severity_rank(x["severity"]) for x in ti]
        self.assertEqual(ranks, sorted(ranks))

    def test_31_critical_first(self):
        ti = _collect_top_issues(self._profile(), 10)
        self.assertEqual(ti[0]["severity"], "critical")
        self.assertEqual(ti[0]["original_severity"], "error")

    def test_32_all_have_labels(self):
        ti = _collect_top_issues(self._profile(), 10)
        for x in ti:
            self.assertIn(x["severity"], _SEV_LEVELS)
            self.assertIn("original_severity", x)
            self.assertIn("dimension_label", x)

    def test_33_limit_respected(self):
        self.assertEqual(len(_collect_top_issues(self._profile(), 2)), 2)

    def test_34_report_uses_unified_labels(self):
        p = self._profile()
        p.overall_score = 55.0
        p.health_level = "concerning"
        p.top_issues = _collect_top_issues(p, 5)
        txt = "\n".join(SelfAwarenessEngine._report_overall_section(p))
        self.assertIn("【最严重问题】", txt)
        self.assertIn("/critical]", txt)


if __name__ == "__main__":
    unittest.main()
