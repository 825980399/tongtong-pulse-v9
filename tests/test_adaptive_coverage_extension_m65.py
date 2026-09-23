# -*- coding: utf-8 -*-
"""主线第65批 T2/P1：自适应覆盖扩展 门控单测（6例）。

覆盖：统一负载接口 runtime_metrics.get_system_load / 代码学习自适应开关+负载 /
      冷存 compaction 高负载暂停 / 后台消化自适应开关+负载 / 降级安全。

隔离：负载等级通过 monkeypatch 单例 get_system_load 注入；不触发真实重操作。
"""
import logging
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config                                                     # noqa: E402
from nucleus.runtime_metrics import get_runtime_metrics          # noqa: E402
from organs.brain import PulseCodeLearner as _pcl                # noqa: E402
from nucleus.mnemosyne import PulseNodePool as _pnp_mod          # noqa: E402
from organs.body import PulseLung as _pl_mod                      # noqa: E402

_LEVELS = ("low", "medium", "high", "critical")


def _patch_load(level):
    _rt = get_runtime_metrics()
    _orig = _rt.get_system_load
    _rt.get_system_load = lambda: {
        "cpu_percent": 0.0, "queue_depth": 0, "memory_percent": 0.0, "load_level": level,
    }
    return _orig


class _CfgSwitch:
    def __init__(self, **kw):
        self._kw, self._old = kw, {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._old.items():
            if v is None:
                if hasattr(config, k):
                    delattr(config, k)
            else:
                setattr(config, k, v)
        return False


# ============================================================ 统一负载接口
class TestSystemLoad(unittest.TestCase):
    def setUp(self):
        self._orig = get_runtime_metrics().get_system_load

    def tearDown(self):
        get_runtime_metrics().get_system_load = self._orig

    def test_10_shape_and_levels(self):
        for _lv in _LEVELS:
            _patch_load(_lv)
            _r = get_runtime_metrics().get_system_load()
            self.assertIn("cpu_percent", _r)
            self.assertIn("queue_depth", _r)
            self.assertIn("memory_percent", _r)
            self.assertEqual(_r["load_level"], _lv)

    def test_11_load_level_threshold(self):
        """CPU 70% → high；45% → medium；10% → low；队列 6000 → high（口径与任务书一致）。"""
        import psutil
        _rt = get_runtime_metrics()
        _o_cpu, _o_vm, _o_qd = psutil.cpu_percent, psutil.virtual_memory, _rt.get_queue_depth
        try:
            psutil.cpu_percent = lambda interval=None: 75.0
            psutil.virtual_memory = lambda: type("M", (), {"percent": 0.0})()
            _rt.get_queue_depth = lambda: 0
            self.assertEqual(_rt.get_system_load()["load_level"], "high")
            psutil.cpu_percent = lambda interval=None: 45.0
            self.assertEqual(_rt.get_system_load()["load_level"], "medium")
            psutil.cpu_percent = lambda interval=None: 10.0
            self.assertEqual(_rt.get_system_load()["load_level"], "low")
            # 队列阈值：qd>5000 → high（即便 CPU 很低）
            psutil.cpu_percent = lambda interval=None: 0.0
            _rt.get_queue_depth = lambda: 6000
            self.assertEqual(_rt.get_system_load()["load_level"], "high")
        finally:
            psutil.cpu_percent, psutil.virtual_memory, _rt.get_queue_depth = _o_cpu, _o_vm, _o_qd


# ============================================================ 代码学习自适应
class TestCodeLearningAdaptive(unittest.TestCase):
    def setUp(self):
        self._orig = get_runtime_metrics().get_system_load

    def tearDown(self):
        get_runtime_metrics().get_system_load = self._orig

    def test_20_switch_reflects_config(self):
        _cl = _pcl.PulseCodeLearner.__new__(_pcl.PulseCodeLearner)
        with _CfgSwitch(ENABLE_CODE_LEARNING_ADAPTIVE=True):
            self.assertTrue(_cl._m65_code_learning_adaptive_enabled())
        with _CfgSwitch(ENABLE_CODE_LEARNING_ADAPTIVE=False):
            self.assertFalse(_cl._m65_code_learning_adaptive_enabled())

    def test_21_get_load_level(self):
        _cl = _pcl.PulseCodeLearner.__new__(_pcl.PulseCodeLearner)
        for _lv in _LEVELS:
            _patch_load(_lv)
            self.assertEqual(_cl._m65_get_load_level(), _lv)


# ============================================================ 冷存 compaction 自适应
class TestColdCompactionAdaptive(unittest.TestCase):
    def test_30_high_load_paused(self):
        """★核心：高负载 → compaction 暂停（reason=high_load_paused），不执行重 IO。"""
        _pool = _pnp_mod.PulseNodePool.__new__(_pnp_mod.PulseNodePool)
        _pool._cold_storage_enabled = True
        _pool._module_logger = logging.getLogger("test.m65.cold")
        _pool._get_system_load_level = lambda: "high"
        _res = _pool.compact_cold_storage()
        self.assertEqual(_res.get("reason"), "high_load_paused")
        self.assertIs(_res.get("success"), False)

    def test_31_switch_reflects_config(self):
        _pool = _pnp_mod.PulseNodePool.__new__(_pnp_mod.PulseNodePool)
        with _CfgSwitch(ENABLE_COLD_COMPACTION_ADAPTIVE=True):
            self.assertTrue(_pool._cold_compaction_adaptive_enabled())
        with _CfgSwitch(ENABLE_COLD_COMPACTION_ADAPTIVE=False):
            self.assertFalse(_pool._cold_compaction_adaptive_enabled())


# ============================================================ 后台消化自适应
class TestDigestAdaptive(unittest.TestCase):
    def setUp(self):
        self._orig = get_runtime_metrics().get_system_load

    def tearDown(self):
        get_runtime_metrics().get_system_load = self._orig

    def test_40_switch_reflects_config(self):
        _lung = _pl_mod.PulseLung.__new__(_pl_mod.PulseLung)
        with _CfgSwitch(ENABLE_BACKGROUND_DIGEST_ADAPTIVE=True):
            self.assertTrue(_lung._m65_digest_adaptive_enabled())
        with _CfgSwitch(ENABLE_BACKGROUND_DIGEST_ADAPTIVE=False):
            self.assertFalse(_lung._m65_digest_adaptive_enabled())

    def test_41_get_load_level(self):
        _lung = _pl_mod.PulseLung.__new__(_pl_mod.PulseLung)
        for _lv in _LEVELS:
            _patch_load(_lv)
            self.assertEqual(_lung._m65_get_load_level(), _lv)

    def test_42_source_wired(self):
        """★零回归：消化路径确实植入了高负载跳过逻辑。"""
        _src = io___read(_pl_mod.__file__)
        self.assertIn("_m65_digest_adaptive_enabled()", _src)
        self.assertIn('"high", "critical"', _src)


def io___read(path):
    import io as _io
    return _io.open(path, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
