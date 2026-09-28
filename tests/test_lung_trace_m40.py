# -*- coding: utf-8 -*-
"""第40批 T2 门控测试：PulseLung 埋点（调用对留存 + SCENE_LUNG 依赖度补全）。

★测试隔离：注入临时目录 recorder + 假依赖度计数器，**绝不写生产 data/**。
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import nucleus.LLMDependencyMetrics as dm  # noqa: E402
import nucleus.llm.call_recorder as cr  # noqa: E402
from organs.body.PulseLung import PulseLung  # noqa: E402


def _make_lung(reply="模拟回复"):
    """轻量实例 + 打桩（绕过 __init__，只挂被测路径需要的属性）。"""
    _l = PulseLung.__new__(PulseLung)
    _l._log = lambda *a, **k: None
    _l._gateway_channel = lambda: None
    _l._is_advanced_task = lambda m: False
    _l._prefer_cheap_channels = lambda c, is_background=False: c
    _l._prefer_paid_channels = lambda c, caller=None: c
    _l._get_channel_health = lambda: None
    _l._channel_concurrency = lambda: None
    _l._update_channel_health = lambda *a, **k: None
    _l._record_model_result = lambda *a, **k: None
    _l._adjust_channel_concurrency = lambda *a, **k: None
    _l._m32_apply_quota_policy = lambda c: c
    _l._current_call_is_background = False
    _l._call_channel = lambda ch, prompt, **kw: reply
    return _l


_CHANNELS = [{"name": "test-ch", "model": "m1",
              "api_url": "http://x", "api_key": "k"}]


class _Base(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="m40_t2_")
        self._rec = cr.LLMCallRecorder(base_dir=self._dir)
        self._orig_get = cr.get_call_recorder
        cr.get_call_recorder = lambda: self._rec
        self._seen = []

        class _FakeM:
            def record_llm_call(self, scene=dm.SCENE_OTHER, n=1):
                self._outer._seen.append((scene, n))

        _fk = _FakeM()
        _fk._outer = self
        self._orig_metrics = dm.get_llm_dependency_metrics
        dm.get_llm_dependency_metrics = lambda: _fk
        self._orig_ch = config.get_active_channels
        config.get_active_channels = lambda: list(_CHANNELS)
        # 开关全部置默认开
        self._s1 = getattr(config, "ENABLE_LUNG_CALL_TRACE", True)
        self._s2 = getattr(config, "ENABLE_LUNG_DEPENDENCY_TRACKING", True)
        config.ENABLE_LUNG_CALL_TRACE = True
        config.ENABLE_LUNG_DEPENDENCY_TRACKING = True

    def tearDown(self):
        cr.get_call_recorder = self._orig_get
        dm.get_llm_dependency_metrics = self._orig_metrics
        config.get_active_channels = self._orig_ch
        config.ENABLE_LUNG_CALL_TRACE = self._s1
        config.ENABLE_LUNG_DEPENDENCY_TRACKING = self._s2
        shutil.rmtree(self._dir, ignore_errors=True)

    def _lines(self):
        _fp = self._rec._path_for(time.time())
        if not os.path.isfile(_fp):
            return []
        with open(_fp, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]


class TestTraceRecording(_Base):
    def test_01_success_recorded(self):
        _l = _make_lung("成功回答")
        self.assertEqual(_l._call_via_channels("问题", "m1", caller="user_dialog"),
                         "成功回答")
        _ls = self._lines()
        self.assertEqual(len(_ls), 1)
        self.assertEqual(_ls[0]["status"], "success")
        self.assertEqual(_ls[0]["response"], "成功回答")
        self.assertEqual(_ls[0]["channel"], "test-ch")
        self.assertEqual(_ls[0]["model"], "m1")

    def test_02_failure_recorded(self):
        _l = _make_lung(None)
        self.assertIsNone(_l._call_via_channels("问题", "m1", caller="user_dialog"))
        _ls = self._lines()
        self.assertEqual(len(_ls), 1)
        self.assertEqual(_ls[0]["status"], "failed")
        self.assertEqual(_ls[0]["response"], "")

    def test_03_duration_positive(self):
        _l = _make_lung("x")
        _l._call_via_channels("q", "m1")
        self.assertGreaterEqual(self._lines()[0]["duration"], 0.0)

    def test_04_tokens_from_usage(self):
        _l = _make_lung("x")
        _l._last_llm_usage = {"prompt_tokens": 7, "completion_tokens": 3,
                              "total_tokens": 10}
        _l._call_via_channels("q", "m1")
        self.assertEqual(self._lines()[0]["tokens"], 10)

    def test_05_tokens_fallback_sum(self):
        _l = _make_lung("x")
        _l._last_llm_usage = {"prompt_tokens": 7, "completion_tokens": 3}
        _l._call_via_channels("q", "m1")
        self.assertEqual(self._lines()[0]["tokens"], 10)

    def test_06_usage_cleared_after_use(self):
        _l = _make_lung("x")
        _l._last_llm_usage = {"total_tokens": 10}
        _l._call_via_channels("q", "m1")
        self.assertIsNone(getattr(_l, "_last_llm_usage", None))


class TestOriginPropagation(_Base):
    def test_20_user_dialog_maps_to_user_query(self):
        _l = _make_lung("x")
        _l._call_via_channels("q", "m1", caller="user_dialog")
        self.assertEqual(self._lines()[0]["origin"], cr.ORIGIN_USER_QUERY)

    def test_21_background_maps_to_system_internal(self):
        _l = _make_lung("x")
        _l._call_via_channels("q", "m1", caller="background_learning")
        self.assertEqual(self._lines()[0]["origin"], cr.ORIGIN_SYSTEM_INTERNAL)

    def test_22_evolution_maps(self):
        _l = _make_lung("x")
        _l._call_via_channels("q", "m1", caller="evolution")
        self.assertEqual(self._lines()[0]["origin"], cr.ORIGIN_EVOLUTION_TASK)

    def test_23_unknown_caller_default(self):
        _l = _make_lung("x")
        # caller='unknown' → 自动推断为 user_dialog（非后台）
        _l._call_via_channels("q", "m1")
        self.assertEqual(self._lines()[0]["origin"], cr.ORIGIN_USER_QUERY)

    def test_24_explicit_origin_wins(self):
        _l = _make_lung("x")
        _l._call_via_channels("q", "m1", caller="evolution",
                              origin=cr.ORIGIN_DIGESTION)
        self.assertEqual(self._lines()[0]["origin"], cr.ORIGIN_DIGESTION)

    def test_25_resolve_helper(self):
        self.assertEqual(PulseLung._m40_resolve_origin("user_dialog", None),
                         cr.ORIGIN_USER_QUERY)
        self.assertEqual(PulseLung._m40_resolve_origin("x", "digestion"),
                         "digestion")
        self.assertEqual(PulseLung._m40_resolve_origin("nope", None),
                         cr.ORIGIN_SYSTEM_INTERNAL)


class TestDependencyTracking(_Base):
    def test_30_scene_lung_recorded(self):
        _l = _make_lung("x")
        _l._call_via_channels("q", "m1", caller="user_dialog")
        self.assertEqual(self._seen, [(dm.SCENE_LUNG, 1)])

    def test_31_count_once_per_call(self):
        _l = _make_lung("x")
        for _i in range(3):
            _l._call_via_channels("q%d" % _i, "m1", caller="user_dialog")
        self.assertEqual(len(self._seen), 3)
        self.assertTrue(all(s == dm.SCENE_LUNG for s, _ in self._seen))

    def test_32_no_double_count_on_failure(self):
        _l = _make_lung(None)
        _l._call_via_channels("q", "m1", caller="user_dialog")
        # 失败也计一次（与既有 4 处埋点风格一致：方法入口记一次）
        self.assertEqual(len(self._seen), 1)

    def test_33_tracking_switch_off(self):
        config.ENABLE_LUNG_DEPENDENCY_TRACKING = False
        _l = _make_lung("x")
        _l._call_via_channels("q", "m1")
        self.assertEqual(self._seen, [])


class TestSwitches(_Base):
    def test_40_trace_switch_off(self):
        config.ENABLE_LUNG_CALL_TRACE = False
        _l = _make_lung("x")
        _l._call_via_channels("q", "m1", caller="user_dialog")
        self.assertEqual(self._lines(), [])
        # 依赖度埋点仍生效（两个开关独立）
        self.assertEqual(len(self._seen), 1)

    def test_41_recorder_unavailable_silent(self):
        cr.get_call_recorder = lambda: None
        _l = _make_lung("x")
        self.assertEqual(_l._call_via_channels("q", "m1", caller="user_dialog"),
                         "x")

    def test_42_no_channel(self):
        config.get_active_channels = lambda: []
        _l = _make_lung("x")
        self.assertIsNone(_l._call_via_channels("q", "m1"))
        # 依赖度仍记一次（进入方法即计）；无调用对
        self.assertEqual(self._lines(), [])

    def test_43_helpers_never_raise(self):
        self.assertIsInstance(PulseLung._m40_trace_enabled(), bool)
        self.assertIsInstance(PulseLung._m40_dep_tracking_enabled(), bool)


if __name__ == "__main__":
    unittest.main()
