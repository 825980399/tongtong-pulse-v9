# -*- coding: utf-8 -*-
"""第52批 T2（P1-370 部分）门控测试：PulseLiver（肝）单元测试。

覆盖 4 类（初始化 / 核心方法 / 异常降级 / 边界），共 24 例。
★mock 隔离全部外部依赖（节点池 / 知识树 / 代码学习器 / 频段编码器 /
  共振引擎 / 快照 / 推理池）—— **不写生产数据**。

肝的职责：知识代谢（L1→L2→L3 分层、矛盾检测、噪声过滤）、
本能升降级、状态快照。
"""
import os
import sys
import threading
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from organs.body.PulseLiver import PulseLiver  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.o = PulseLiver(organ_name="肝")
        self.o.info_field = mock.MagicMock()
        self.o.pulse_core = mock.MagicMock()
        for _s in ("set_node_pool", "set_knowledge_tree", "set_code_learner",
                   "set_frequency_codec", "set_resonance_engine",
                   "set_snapshot", "set_reasoning_pool"):
            _f = getattr(self.o, _s, None)
            if callable(_f):
                _f(mock.MagicMock())
        self.addCleanup(self._safe_stop)

    def _safe_stop(self):
        try:
            self.o.stop()
        except Exception:      # 清理失败不得影响用例结论
            pass


class TestInit(_Base):
    def test_01_organ_name(self):
        self.assertEqual(self.o.organ_name, "肝")

    def test_02_default_organ_name(self):
        self.assertTrue(PulseLiver().organ_name)

    def test_03_custom_organ_name(self):
        self.assertEqual(PulseLiver(organ_name="X").organ_name, "X")

    def test_04_log_callable(self):
        self.assertTrue(callable(getattr(self.o, "_log", None)))

    def test_05_stats_has_organ(self):
        self.assertEqual(self.o.get_stats().get("organ"), "肝")


class TestCoreMethods(_Base):
    def test_10_get_stats_shape(self):
        _st = self.o.get_stats()
        self.assertIsInstance(_st, dict)
        for _k in ("organ", "l1_to_l2_count", "l2_to_l3_count"):
            self.assertIn(_k, _st)

    def test_11_stats_counters_numeric(self):
        _st = self.o.get_stats()
        for _k in ("l1_to_l2_count", "l2_to_l3_count",
                   "noise_detected_count", "consolidation_count"):
            self.assertIsInstance(_st[_k], int)

    def test_12_resonance_shape(self):
        _rc = self.o.get_resonance_conditions()
        self.assertIsInstance(_rc, list)
        self.assertTrue(_rc)
        for _c in _rc:
            self.assertIn("organ_name", _c)
            self.assertIn("event_types", _c)

    def test_13_resonance_organ_name(self):
        _rc = self.o.get_resonance_conditions()
        self.assertEqual(_rc[0]["organ_name"], self.o.organ_name)

    def test_14_refresh_no_raise(self):
        self.o.refresh_runtime_params()

    def test_15_state_snapshot_shape(self):
        _s = self.o.get_state_snapshot()
        self.assertIsInstance(_s, dict)

    def test_16_load_state_snapshot_roundtrip(self):
        _s = self.o.get_state_snapshot()
        self.o.load_state_snapshot(_s)

    def test_17_load_empty_state(self):
        self.o.load_state_snapshot({})

    def test_18_start_stop_lifecycle(self):
        self.o.start()
        self.o.stop()

    def test_19_on_pulse_status_request(self):
        self.o.on_pulse({"event_type": "system.status.request", "payload": {}})

    def test_20_on_pulse_heart_beat(self):
        self.o.on_pulse({"event_type": "heart.beat", "payload": {}})

    def test_21_on_pulse_knowledge_written(self):
        self.o.on_pulse({"event_type": "knowledge.written", "payload": {}})


class TestDegradation(_Base):
    def test_30_on_pulse_empty(self):
        self.assertIsNone(self.o.on_pulse({}))

    def test_31_on_pulse_unknown(self):
        self.assertIsNone(self.o.on_pulse({"event_type": "no.such"}))

    def test_32_on_pulse_missing_payload(self):
        self.o.on_pulse({"event_type": "heart.beat"})

    def test_33_on_pulse_without_deps(self):
        """裸构造（不注入）时未知事件仍应安全返回。"""
        _bare = PulseLiver(organ_name="肝")
        self.assertIsNone(_bare.on_pulse({"event_type": "no.such"}))

    def test_34_set_none_dependencies(self):
        for _s in ("set_node_pool", "set_knowledge_tree", "set_code_learner",
                   "set_frequency_codec", "set_resonance_engine",
                   "set_snapshot", "set_reasoning_pool"):
            _f = getattr(self.o, _s, None)
            if callable(_f):
                _f(None)

    def test_35_refresh_on_broken_config(self):
        class _Boom:
            def __contains__(self, _k):
                raise RuntimeError("boom")

        with mock.patch("config.RUNTIME_PARAMS", _Boom()):
            self.o.refresh_runtime_params()

    def test_36_stop_without_start(self):
        self.o.stop()


class TestBoundary(_Base):
    def test_50_many_unknown_events(self):
        for _i in range(200):
            self.o.on_pulse({"event_type": "e%d" % _i})

    def test_51_concurrent_on_pulse(self):
        _errs = []

        def _w():
            try:
                for _ in range(30):
                    self.o.on_pulse({"event_type": "heart.beat",
                                     "payload": {}})
            except Exception as _e:
                _errs.append("{}: {}".format(type(_e).__name__, _e))

        _ts = [threading.Thread(target=_w) for _ in range(4)]
        for _t in _ts:
            _t.start()
        for _t in _ts:
            _t.join()
        self.assertEqual(_errs, [])

    def test_52_double_start_stop(self):
        for _ in range(3):
            self.o.start()
            self.o.stop()

    def test_53_stats_repeatable(self):
        self.assertEqual(sorted(self.o.get_stats()), sorted(self.o.get_stats()))

    def test_54_snapshot_repeatable(self):
        self.assertEqual(sorted(self.o.get_state_snapshot()),
                         sorted(self.o.get_state_snapshot()))

    def test_55_load_none_state(self):
        """载入 None → 不得崩（应视为空状态或明确降级）。"""
        try:
            self.o.load_state_snapshot(None)
        except (TypeError, AttributeError):
            pass          # 允许显式类型错误（非静默吞异常）


if __name__ == "__main__":
    unittest.main()
