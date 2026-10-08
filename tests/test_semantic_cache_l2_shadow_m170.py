# -*- coding: utf-8 -*-
"""第170批 C6（T-语义缓存L2校准落地-1）配套断言：

· ``ENABLE_SEMANTIC_CACHE_L2_SHADOW`` 已按 FACE_WELCOME_SHADOW 范式新增，默认 True（影子双跑观测态）；
· ``SEMANTIC_CACHE_THRESHOLD`` 维持第45批校准值 0.85（calibrate_semantic_threshold.py 实测 P=R=F1=1.0）；
· 回退值 ``SEMANTIC_CACHE_THRESHOLD_BEFORE_CALIBRATION`` 仍为 0.92（仅供灰度回退，非生效值）。

★纯配置默认值钉死，不触碰生产链路；与业务提交拆分（门禁隔离守卫）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402


class TestSemanticCacheL2ShadowConfig(unittest.TestCase):
    def test_l2_shadow_key_default_true(self):
        """影子双跑观测键默认开（只观测不返回，安全非影响态）。"""
        self.assertTrue(
            getattr(config, "ENABLE_SEMANTIC_CACHE_L2_SHADOW", None) is True,
            "ENABLE_SEMANTIC_CACHE_L2_SHADOW 必须存在且默认 True（影子观测态）")

    def test_threshold_keeps_calibrated_085(self):
        """0.85 校准阈值保持（本批不调整）。"""
        self.assertEqual(config.SEMANTIC_CACHE_THRESHOLD, 0.85)

    def test_threshold_fallback_092_unchanged(self):
        """灰度回退值仍为 0.92（非生效值，仅供回退）。"""
        self.assertEqual(config.SEMANTIC_CACHE_THRESHOLD_BEFORE_CALIBRATION, 0.92)


if __name__ == "__main__":
    unittest.main()
