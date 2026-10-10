# -*- coding: utf-8 -*-
"""第52批 T2（P1-370 部分）门控测试：PulseKidney（肾）单元测试。

覆盖 4 类（初始化 / 核心方法 / 异常降级 / 边界），共 22 例。
★mock 隔离外部依赖（节点池 / 知识树 / 共振引擎）—— **不写生产数据**。

肾的职责：知识过滤与排泄（降级 / 清除偏移分支）。

★注：`kidney.purge.check` 事件会与依赖返回的数值比较，
  故本文件用**数值型 mock**（`MagicMock` 属性返回 int）避免误报；
  通用事件路径用普通 mock 即可。
"""
import os
import sys
import threading
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from organs.body.PulseKidney import PulseKidney  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.o = PulseKidney(organ_name="肾")
        self.o.info_field = mock.MagicMock()
        self.o.pulse_core = mock.MagicMock()
        # 数值型 mock 依赖（避免 MagicMock 与 int 比较）
        _pool = mock.MagicMock()
        _pool.count.return_value = 0
        _pool.get_all_nodes.return_value = []
        self.o.set_node_pool(_pool)
        self.o.set_knowledge_tree(mock.MagicMock())
        self.o.set_resonance_engine(mock.MagicMock())


class TestInit(_Base):
    def test_01_organ_name(self):
        self.assertEqual(self.o.organ_name, "肾")

    def test_02_default_organ_name(self):
        self.assertTrue(PulseKidney().organ_name)

    def test_03_custom_organ_name(self):
        self.assertEqual(PulseKidney(organ_name="X").organ_name, "X")

    def test_04_log_callable(self):
        self.assertTrue(callable(getattr(self.o, "_log", None)))

    def test_05_stats_has_organ(self):
        self.assertEqual(self.o.get_stats().get("organ"), "肾")


class TestCoreMethods(_Base):
    def test_10_get_stats_shape(self):
        _st = self.o.get_stats()
        self.assertIsInstance(_st, dict)
        for _k in ("organ", "total_purged", "total_downgraded"):
            self.assertIn(_k, _st)

    def test_11_stats_counters_numeric(self):
        _st = self.o.get_stats()
        for _k in ("total_purged", "total_downgraded"):
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

    def test_15_on_pulse_status_request(self):
        _r = self.o.on_pulse({"event_type": "system.status.request",
                              "payload": {}})
        self.assertIsInstance(_r, dict)

    def test_16_on_pulse_purge_check(self):
        """清除检查事件（数值型 mock 依赖）不得抛异常。"""
        self.o.on_pulse({"event_type": "kidney.purge.check", "payload": {}})

    def test_17_purge_check_with_threshold(self):
        self.o.on_pulse({"event_type": "kidney.purge.check",
                         "payload": {"threshold": 0.5}})

    def test_18_stats_purged_monotonic(self):
        _a = self.o.get_stats()["total_purged"]
        self.o.on_pulse({"event_type": "kidney.purge.check", "payload": {}})
        _b = self.o.get_stats()["total_purged"]
        self.assertGreaterEqual(_b, _a)


class TestDegradation(_Base):
    def test_30_on_pulse_empty(self):
        self.assertIsNone(self.o.on_pulse({}))

    def test_31_on_pulse_unknown(self):
        self.assertIsNone(self.o.on_pulse({"event_type": "no.such"}))

    def test_32_on_pulse_missing_payload(self):
        self.o.on_pulse({"event_type": "kidney.purge.check"})

    def test_33_on_pulse_without_deps(self):
        """裸构造（不注入）时未知事件仍应安全返回。"""
        _bare = PulseKidney(organ_name="肾")
        self.assertIsNone(_bare.on_pulse({"event_type": "no.such"}))

    def test_34_set_none_dependencies(self):
        self.o.set_node_pool(None)
        self.o.set_knowledge_tree(None)
        self.o.set_resonance_engine(None)

    def test_35_refresh_on_broken_config(self):
        class _Boom:
            def __contains__(self, _k):
                raise RuntimeError("boom")

        with mock.patch("config.RUNTIME_PARAMS", _Boom()):
            self.o.refresh_runtime_params()

    def test_36_purge_without_node_pool_safe(self):
        """node_pool 置 None 后未知事件仍安全（不冒泡）。"""
        self.o.set_node_pool(None)
        self.o.on_pulse({"event_type": "no.such"})


class TestBoundary(_Base):
    def test_50_many_unknown_events(self):
        for _i in range(200):
            self.o.on_pulse({"event_type": "e%d" % _i})

    def test_51_empty_payload_purge(self):
        self.o.on_pulse({"event_type": "kidney.purge.check", "payload": {}})
        self.o.on_pulse({"event_type": "kidney.purge.check",
                         "payload": {"threshold": 0}})

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

    def test_54_double_injection_idempotent(self):
        for _ in range(3):
            self.o.set_node_pool(mock.MagicMock())
        self.assertIsInstance(self.o.get_stats(), dict)

    def test_55_biased_branches_key_present(self):
        _st = self.o.get_stats()
        self.assertTrue(any("biased" in _k or "purge" in _k for _k in _st),
                        "get_stats 应含偏移/清除相关计数: {}".format(sorted(_st)))


if __name__ == "__main__":
    unittest.main()
