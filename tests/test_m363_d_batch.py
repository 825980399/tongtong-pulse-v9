# -*- coding: utf-8 -*-
"""363 批 D 组验收（D1 质量门 9 信号接线）。

纪律：只守判据语义与接线点存在性；不锁易变总行数、不写生产 data/。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from organs.body.PulseLung import PulseLung  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _lung() -> PulseLung:
    p = PulseLung.__new__(PulseLung)
    p._log = lambda *a, **k: None
    return p


class TestD1QualitySignals(unittest.TestCase):
    def test_01_nine_signals_declared(self):
        """任务书点名的七类缺信号 + 既有两类 = 九类齐。"""
        for k in ("empty_reply", "truncated", "integrity_low", "def_name_mismatch",
                  "guard_template_missing", "parse_failed", "retry_exceeded",
                  "low_confidence", "precheck_failed"):
            self.assertIn(k, PulseLung.M363_QUALITY_SIGNALS, f"缺信号 {k}")
        self.assertEqual(len(PulseLung.M363_QUALITY_SIGNALS), 9)

    def test_02_unknown_kind_not_silently_dropped(self):
        """未知信号必须可见（记 unknown:），不许静默丢弃。"""
        p = _lung()
        p._m363_note_quality_signal("totally_new_signal", "probe")
        self.assertIn("unknown:totally_new_signal", p.m363_quality_signals()["counts"])

    def test_03_counts_and_last_detail(self):
        p = _lung()
        p._m363_note_quality_signal("empty_reply", "ch=zhipu")
        p._m363_note_quality_signal("empty_reply", "ch=zhipu2")
        s = p.m363_quality_signals()
        self.assertEqual(s["counts"]["empty_reply"], 2)
        self.assertEqual(s["last"]["empty_reply"]["detail"], "ch=zhipu2")

    def test_04_read_export_is_copy(self):
        p = _lung()
        s = p.m363_quality_signals()
        s["counts"]["tampered"] = 99
        self.assertNotIn("tampered", p.m363_quality_signals()["counts"])

    def test_05_signal_hit_releases_paid(self):
        """任一质量门信号命中 ⇒ 免费档产出不达标 ⇒ 放行付费。"""
        p = _lung()
        p._m360_is_paid_channel = lambda ch: True
        p._m360_adv_free_first_on = lambda: True
        p._m360_adv_free_min = lambda: 2
        p._m360_reply_truncated = False
        p._m360_patch_precheck_failed = False
        self.assertTrue(p._m360_adv_paid_blocked({"name": "deepseek"}, 0),
                        "无任何信号且免费未试够 ⇒ 应拦住付费")
        p._m363_note_quality_signal("empty_reply", "probe")
        self.assertFalse(p._m360_adv_paid_blocked({"name": "deepseek"}, 0),
                         "有质量门信号 ⇒ 应放行付费兜底")

    def test_06_no_attribute_error_before_init(self):
        """`_m363_quality_counts` 只在 _call_via_channels 初始化 ⇒ 三口都必须 getattr 兜底。"""
        p = PulseLung.__new__(PulseLung)
        p._log = lambda *a, **k: None
        self.assertEqual(p.m363_quality_signals()["counts"], {})
        self.assertFalse(p._m363_quality_hit())
        p._m363_note_quality_signal("truncated", "late-init")
        self.assertTrue(p._m363_quality_hit())

    def test_07_parse_failure_wired(self):
        src = open(os.path.join(_ROOT, "organs", "body", "PulseLung.py"),
                   encoding="utf-8").read()
        self.assertIn('_m363_note_quality_signal("parse_failed"', src)
        self.assertIn("原样 raise", src, "parse 失败必须原样抛出，不改控制流")

    def test_08_no_duplicate_contract_check(self):
        """完整性<0.85 / def 名 —— 360 刀6 契约校验层已落，本刀**不重复施工**。"""
        src = open(os.path.join(_ROOT, "organs", "body", "PulseLung.py"),
                   encoding="utf-8").read()
        self.assertNotIn("check_method_contract(", src,
                         "校验不该搬到 PulseLung（保持生成器单点）")
        gen = open(os.path.join(_ROOT, "nucleus", "evolution", "patch_generator.py"),
                   encoding="utf-8").read()
        self.assertIn("def check_method_contract", gen, "契约校验必须还在生成器")


if __name__ == "__main__":
    unittest.main(verbosity=2)
