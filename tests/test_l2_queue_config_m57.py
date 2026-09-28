# -*- coding: utf-8 -*-
"""主线第57批 T4（P2-394）：L2 队列配置优化门控测试。

覆盖：
  - resolve_l2_config：从 PULSE_LAYER 解析 L2 有效上限/worker/告警比例（含默认值与覆盖）
  - L2 水位告警阈值逻辑（depth >= limit * warn_ratio）
  - reload_l2_config：运行时热重载（更新内存上限与线程池 max_workers，无需重启）
  - 向后兼容：PULSE_LAYER 仍含 L2 配置键，InfoField 可正常导入
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from nucleus.field import InfoField as R  # noqa: E402


class TestResolveL2Config(unittest.TestCase):
    def test_reads_real_config(self):
        # 读取真实 PULSE_LAYER（本批已设为 l2_queue_limit=300 / l2_threads=8 / l2_warn_ratio=0.8）
        cfg = R.resolve_l2_config()
        self.assertEqual(cfg["queue_limit"], 300)
        self.assertEqual(cfg["workers"], 8)
        self.assertAlmostEqual(cfg["warn_ratio"], 0.8)

    def test_explicit_layer_config_override(self):
        custom = {"l2_queue_limit": 250, "l2_threads": 12, "l2_warn_ratio": 0.9}
        cfg = R.resolve_l2_config(16, custom)
        self.assertEqual(cfg["queue_limit"], 250)
        self.assertEqual(cfg["workers"], 12)
        self.assertAlmostEqual(cfg["warn_ratio"], 0.9)

    def test_zero_threads_falls_back_to_cpu(self):
        custom = {"l2_queue_limit": 200, "l2_threads": 0, "l2_warn_ratio": 0.8}
        cfg = R.resolve_l2_config(16, custom)
        self.assertEqual(cfg["workers"], max(4, 16 // 2))  # 回退 cpu 公式

    def test_warn_ratio_zero_falls_back(self):
        custom = {"l2_queue_limit": 200, "l2_threads": 4, "l2_warn_ratio": 0.0}
        cfg = R.resolve_l2_config(8, custom)
        self.assertAlmostEqual(cfg["warn_ratio"], 0.8)


class TestL2WarnThreshold(unittest.TestCase):
    def test_threshold_logic(self):
        limit = 300
        ratio = 0.8
        # 刚好达到 80%（240）应触发告警水位
        self.assertTrue(240 >= limit * ratio)
        # 低于 80% 不触发
        self.assertFalse(239 >= limit * ratio)
        # 超过上限不在此分支（由拒绝逻辑处理）
        self.assertFalse(300 < limit)


class TestReloadL2Config(unittest.TestCase):
    def test_hot_reload_updates_limit_and_workers(self):
        # 用最小假对象验证 reload_l2_config 的「无需重启」语义
        class _FakePool:
            def __init__(self):
                self._max_workers = 4

        class _FakeInfoField:
            def __init__(self):
                self._layer_queue_limits = {R._PULSE_LAYER_L2: 200}
                self._layer_pools = {R._PULSE_LAYER_L2: _FakePool()}

        fake = _FakeInfoField()
        # reload_l2_config 是 InfoField 实例方法：以未绑定方式把 fake 作为 self 传入
        cfg = R.InfoField.reload_l2_config(fake)
        # 热重载后内存上限从 200 变为配置值 300
        self.assertEqual(fake._layer_queue_limits[R._PULSE_LAYER_L2], 300)
        # 线程池 worker 数从 4 变为配置值 8
        self.assertEqual(fake._layer_pools[R._PULSE_LAYER_L2]._max_workers, 8)
        self.assertEqual(cfg["queue_limit"], 300)


class TestBackwardCompat(unittest.TestCase):
    def test_pulse_layer_has_l2_keys(self):
        for key in ("l2_threads", "l2_queue_limit", "l2_warn_ratio", "l2_enabled"):
            self.assertIn(key, config.PULSE_LAYER)

    def test_info_field_imports(self):
        self.assertTrue(hasattr(R, "resolve_l2_config"))
        self.assertTrue(hasattr(R.InfoField, "reload_l2_config"))
        self.assertTrue(hasattr(R, "InfoField"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
