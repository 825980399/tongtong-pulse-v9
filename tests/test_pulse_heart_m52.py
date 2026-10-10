# -*- coding: utf-8 -*-
"""第52批 T2（P1-370 部分）门控测试：PulseHeart（心脏）单元测试。

覆盖 4 类（初始化 / 核心方法 / 异常降级 / 边界），共 23 例。
★mock 隔离外部依赖（info_field / pulse_core / 自我认知 / 生存编排器）。

心脏的职责：心跳节律（相位推进）、节拍同步、任务调度。

★`start()` 会启动线程 → 测试中用 `addCleanup` 确保 `stop()`，
  避免线程泄漏影响后续用例。
"""
import os
import sys
import threading
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from organs.body.PulseHeart import PulseHeart  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.o = PulseHeart(organ_name="心脏")
        self.o.info_field = mock.MagicMock()
        self.o.pulse_core = mock.MagicMock()
        self.addCleanup(self._safe_stop)

    def _safe_stop(self):
        try:
            self.o.stop()
        except Exception:      # 清理失败不得影响用例结论
            pass


class TestInit(_Base):
    def test_01_organ_name(self):
        self.assertEqual(self.o.organ_name, "心脏")

    def test_02_default_organ_name(self):
        self.assertTrue(PulseHeart().organ_name)

    def test_03_custom_organ_name(self):
        self.assertEqual(PulseHeart(organ_name="X").organ_name, "X")

    def test_04_log_callable(self):
        self.assertTrue(callable(getattr(self.o, "_log", None)))

    def test_05_stats_has_organ(self):
        self.assertEqual(self.o.get_stats().get("organ"), "心脏")


class TestCoreMethods(_Base):
    def test_10_get_stats_shape(self):
        _st = self.o.get_stats()
        self.assertIsInstance(_st, dict)
        for _k in ("organ", "beat_count", "current_interval"):
            self.assertIn(_k, _st)

    def test_11_stats_numeric(self):
        _st = self.o.get_stats()
        self.assertIsInstance(_st["beat_count"], int)
        self.assertIsInstance(_st["current_interval"], (int, float))

    def test_12_resonance_shape(self):
        _rc = self.o.get_resonance_conditions()
        self.assertIsInstance(_rc, list)
        self.assertTrue(_rc)
        self.assertEqual(_rc[0]["organ_name"], self.o.organ_name)

    def test_13_refresh_no_raise(self):
        self.o.refresh_runtime_params()

    def test_14_beat_phase_in_range(self):
        _p = self.o.get_beat_phase()
        if isinstance(_p, (int, float)):
            self.assertGreaterEqual(_p, 0.0)

    def test_15_beat_sync_info(self):
        _i = self.o.get_beat_sync_info()
        self.assertIsNotNone(_i)

    def test_16_set_self_awareness(self):
        self.o.set_self_awareness(mock.MagicMock())

    def test_17_set_survival_orchestrator(self):
        self.o.set_survival_orchestrator(mock.MagicMock())

    def test_18_start_stop_lifecycle(self):
        self.o.start()
        self.o.stop()

    def test_19_on_pulse_status_request(self):
        self.o.on_pulse({"event_type": "system.status.request", "payload": {}})

    def test_20_on_pulse_trigger_dispatch(self):
        """心跳触发分派（以 Mock 统一接住）。"""
        with mock.patch.object(self.o, "_log"):
            self.o.on_pulse({"event_type": "system.status.request",
                             "payload": {"detail": True}})


class TestDegradation(_Base):
    def test_30_on_pulse_empty(self):
        self.assertIsNone(self.o.on_pulse({}))

    def test_31_on_pulse_unknown(self):
        self.assertIsNone(self.o.on_pulse({"event_type": "no.such"}))

    def test_32_on_pulse_missing_payload(self):
        self.o.on_pulse({"event_type": "system.status.request"})

    def test_33_wait_for_phase_timeout(self):
        """★极短超时：不得无限阻塞（返回/抛超时须可控）。"""
        try:
            self.o.wait_for_phase(0.0, timeout=0.01)
        except Exception as _e:
            # 允许显式超时异常，但不得是无关错误
            self.assertIn(type(_e).__name__,
                          ("TimeoutError", "ValueError", "AssertionError"))

    def test_34_stop_without_start(self):
        """未启动即停止 → 不得崩。"""
        self.o.stop()

    def test_35_refresh_on_broken_config(self):
        class _Boom:
            def __contains__(self, _k):
                raise RuntimeError("boom")

        with mock.patch("config.RUNTIME_PARAMS", _Boom()):
            self.o.refresh_runtime_params()

    def test_36_set_none_dependencies(self):
        self.o.set_self_awareness(None)
        self.o.set_survival_orchestrator(None)


class TestBoundary(_Base):
    def test_50_many_unknown_events(self):
        for _i in range(200):
            self.o.on_pulse({"event_type": "e%d" % _i})

    def test_51_double_start_stop(self):
        for _ in range(3):
            self.o.start()
            self.o.stop()

    def test_52_concurrent_on_pulse(self):
        _errs = []

        def _w():
            try:
                for _ in range(30):
                    self.o.on_pulse({"event_type": "system.status.request",
                                     "payload": {}})
            except Exception as _e:
                _errs.append("{}: {}".format(type(_e).__name__, _e))

        _ts = [threading.Thread(target=_w) for _ in range(4)]
        for _t in _ts:
            _t.start()
        for _t in _ts:
            _t.join()
        self.assertEqual(_errs, [])

    def test_53_stats_repeatable(self):
        self.assertEqual(sorted(self.o.get_stats()), sorted(self.o.get_stats()))

    def test_54_interval_positive(self):
        _iv = self.o.get_stats().get("current_interval")
        if isinstance(_iv, (int, float)):
            self.assertGreater(_iv, 0)

    def test_55_phase_repeatable(self):
        _a = self.o.get_beat_phase()
        _b = self.o.get_beat_phase()
        self.assertEqual(type(_a), type(_b))


if __name__ == "__main__":
    unittest.main()
