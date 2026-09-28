# -*- coding: utf-8 -*-
"""
test_frequency_codec_cache_m16.py —— 主线第16批 任务2 门控单测（P2-94 Cython 缓存）

复现根因：日志守卫用**实例属性** `self._cython_loaded`，而 FrequencyCodec() 被反复
新建（子进程 encode_batch 每次都 new）→ 每个实例都重打一次 INFO，刷屏 70+ 行。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
import nucleus.pulse.FrequencyCodec as FC  # noqa: E402
from nucleus.pulse.FrequencyCodec import FrequencyCodec  # noqa: E402

_SRC = os.path.join(_PROJECT_ROOT, "nucleus", "pulse", "FrequencyCodec.py")


class _Switch:
    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self._old.items():
            if v is None:
                if hasattr(config, k):
                    delattr(config, k)
            else:
                setattr(config, k, v)
        return False


class TestCythonCache(unittest.TestCase):
    def setUp(self):
        FC._reset_cy_load_state()

    def tearDown(self):
        FC._reset_cy_load_state()

    def test_config_default(self):
        self.assertIs(getattr(config, "ENABLE_FREQUENCY_CODEC_CACHE", None), True)

    def test_module_level_cache_resolves_once(self):
        """多次调用只真正 import 一次，且返回同一函数对象。"""
        FC._reset_cy_load_state()
        f1 = FC._get_encode_cy()
        f2 = FC._get_encode_cy()
        f3 = FC._get_encode_cy()
        self.assertEqual(FC._CY_LOAD_STATE["import_count"], 1,
                         f"进程级缓存下 import 次数应为 1，实际 "
                         f"{FC._CY_LOAD_STATE['import_count']}")
        self.assertIs(f1, f2)
        self.assertIs(f2, f3)

    def test_many_instances_do_not_reload(self):
        """回归护栏：反复新建实例并真正编码，仍只解析一次。"""
        FC._reset_cy_load_state()
        for _ in range(10):
            FrequencyCodec().encode("重复加载回归测试文本", ["重复", "加载"])
        self.assertEqual(FC._CY_LOAD_STATE["import_count"], 1,
                         f"10 个实例编码后 import 次数应为 1，实际 "
                         f"{FC._CY_LOAD_STATE['import_count']}")

    def test_cache_disabled_reimports_each_call(self):
        """灰度关闭：退回每次重新 import 的行为。"""
        FC._reset_cy_load_state()
        with _Switch(ENABLE_FREQUENCY_CODEC_CACHE=False):
            for _ in range(3):
                FC._get_encode_cy()
        self.assertGreaterEqual(FC._CY_LOAD_STATE["import_count"], 3,
                                "开关关闭时应每次重新 import")

    def test_fallback_when_cython_disabled(self):
        """Cython 总开关关闭 → 返回 None，encode 走 Python 原生实现。"""
        FC._reset_cy_load_state()
        _feat = getattr(config, "FEATURE", {})
        _old = _feat.get("use_cython_extensions")
        try:
            _feat["use_cython_extensions"] = False
            FC._reset_cy_load_state()
            self.assertIsNone(FC._get_encode_cy())
            _v = FrequencyCodec().encode("回退测试文本", ["回退", "测试"])
            self.assertIsInstance(_v, float)
        finally:
            if _old is not None:
                _feat["use_cython_extensions"] = _old
            FC._reset_cy_load_state()

    def test_warn_threshold_is_regression_sentinel(self):
        """超阈值告警逻辑（回归哨兵）：import_count 超过阈值即告警。"""
        FC._reset_cy_load_state()
        FC._CY_LOAD_STATE["import_count"] = FC._CY_LOAD_WARN_THRESHOLD + 1
        self.assertGreater(FC._CY_LOAD_STATE["import_count"],
                           FC._CY_LOAD_WARN_THRESHOLD)
        self.assertEqual(FC._CY_LOAD_WARN_THRESHOLD, 3)


class TestSourceWiring(unittest.TestCase):
    def setUp(self):
        with open(_SRC, encoding="utf-8") as f:
            self.src = f.read()

    def test_log_guard_no_longer_instance_attr(self):
        """护栏：日志守卫不得再用实例属性 self._cython_loaded。"""
        self.assertNotIn("self._cython_loaded = True", self.src)

    def test_import_moved_out_of_hot_path(self):
        """护栏：热路径（encode 方法体内）不得再直接 import Cython 模块。"""
        _i = self.src.index("_encode_cy = _get_encode_cy()")   # encode() 内的调用点
        self.assertNotIn("from nucleus.pulse._frequency_codec_cy import",
                         self.src[_i:], "热路径调用点之后不应再有函数体内 import")

    def test_encode_batch_import_cached_too(self):
        """encode_batch 的 Cython 导入同样走进程级缓存（原为热路径内直接 import）。"""
        self.assertIn("_get_encode_batch_cy()", self.src)
        _i = self.src.index("_encode_batch_cy = _get_encode_batch_cy()")
        self.assertNotIn("from nucleus.pulse._frequency_codec_cy import",
                         self.src[_i:])
        FC._reset_cy_load_state()
        b1 = FC._get_encode_batch_cy()
        b2 = FC._get_encode_batch_cy()
        self.assertIs(b1, b2)
        self.assertEqual(FC._CY_LOAD_STATE["batch_import_count"], 1)

    def test_subprocess_debug_branch_present(self):
        self.assertIn("multiprocessing.current_process().name == \"MainProcess\"",
                      self.src)
        self.assertIn("_logger.debug(_msg)", self.src)


if __name__ == "__main__":
    unittest.main()
