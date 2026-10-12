# -*- coding: utf-8 -*-
"""199刀2 隔离单测：层②分级带单一真源 health_bands.py + engine/v2 两处改引用。

验收：health_bands.py 单一真源（critical<40/warning<70/attention<90/healthy≥90
＋ legacy v1 表）；engine._health_level 与 quality_score_v2.quality_level 均改引用
常量表且零行为（取值与旧实现完全一致）；词表断言单测。
"""
import unittest

from nucleus.self_awareness import health_bands
from nucleus.self_awareness.SelfAwarenessEngine import _health_level
from nucleus.self_awareness import quality_score_v2


class TestM199HealthBands(unittest.TestCase):
    def test_v1_table_preserves_legacy_semantics(self):
        # v1（legacy engine）口径零行为：既有 _health_level 测试断言必须继续成立
        self.assertEqual(_health_level(80), "healthy")
        self.assertEqual(_health_level(100), "healthy")
        self.assertEqual(_health_level(79.99), "moderate")
        self.assertEqual(_health_level(60), "moderate")
        self.assertEqual(_health_level(59.99), "concerning")
        self.assertEqual(_health_level(40), "concerning")
        self.assertEqual(_health_level(39.99), "critical")
        self.assertEqual(_health_level(0), "critical")
        self.assertEqual(_health_level(None), "unknown")
        self.assertEqual(_health_level("x"), "unknown")
        self.assertEqual(_health_level(True), "unknown")

    def test_v2_table_preserves_new_caliber(self):
        self.assertEqual(health_bands.level_v2(None), "unknown")
        self.assertEqual(health_bands.level_v2(39.99), "critical")
        self.assertEqual(health_bands.level_v2(40), "warning")
        self.assertEqual(health_bands.level_v2(69.99), "warning")
        self.assertEqual(health_bands.level_v2(70), "attention")
        self.assertEqual(health_bands.level_v2(89.99), "attention")
        self.assertEqual(health_bands.level_v2(90), "healthy")
        self.assertEqual(health_bands.level_v2(100), "healthy")

    def test_engine_and_v2_reference_constant_table(self):
        # _health_level 委托 health_bands.level_v1（改引用常量表，零行为）
        for _s in (None, 0, 39.99, 40, 59.99, 60, 79.99, 80, 100, "x", True):
            self.assertEqual(_health_level(_s), health_bands.level_v1(_s))
        # v2 quality_level 委托 health_bands.level_v2
        for _s in (None, 39.99, 40, 69.99, 70, 89.99, 90, 100):
            self.assertEqual(quality_score_v2.quality_level(_s),
                             health_bands.level_v2(_s))

    def test_band_constants_present(self):
        self.assertEqual(health_bands.V2_CRITICAL_MAX, 40)
        self.assertEqual(health_bands.V2_WARNING_MAX, 70)
        self.assertEqual(health_bands.V2_ATTENTION_MAX, 90)
        self.assertEqual(health_bands.V1_HEALTHY_MIN, 80)
        self.assertEqual(health_bands.V1_MODERATE_MIN, 60)
        self.assertEqual(health_bands.V1_CONCERNING_MIN, 40)


if __name__ == "__main__":
    unittest.main()
