# -*- coding: utf-8 -*-
"""
test_l3_backpressure_m16.py —— 主线第16批 任务5 门控单测（P2-100 L3 背压）

注：「提前扩容」（70/85/95 分档）与「水位告警」（80%）第5批已实现，本批不重复，
    只测本批新增的「生产端过滤 + 告警升级 + 统计」。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from nucleus.field.InfoField import InfoField  # noqa: E402


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


def _field():
    f = InfoField.__new__(InfoField)
    f._l3_filter_stats = {"low_value": 0, "dedup": 0, "backpressure": 0,
                          "backpressure_streak_peak": 0}
    f._l3_dedup_seen = {}
    f._l3_backpressure_streak = 0
    f._layer_rejected_count = {"L3": 0, "L1": 0}
    return f


class TestL3Filter(unittest.TestCase):
    def test_config_defaults(self):
        self.assertIs(getattr(config, "ENABLE_L3_BACKPRESSURE_OPTIMIZE", None), True)
        self.assertEqual(getattr(config, "L3_DEDUP_WINDOW_SEC", None), 30)
        self.assertEqual(getattr(config, "L3_BACKPRESSURE_WARN_EVERY", None), 3)

    def test_dedup_filters_repeat_within_window(self):
        f = _field()
        p = {"event": "E.TEST", "payload": {"a": 1}}
        self.assertFalse(f._should_filter_l3_pulse(p, "orgA"), "首次放行")
        self.assertTrue(f._should_filter_l3_pulse(p, "orgA"), "窗口内重复应被过滤")
        self.assertEqual(f.get_l3_backpressure_stats()["filtered_dedup"], 1)

    def test_different_organ_not_deduped(self):
        f = _field()
        p = {"event": "E.TEST", "payload": {"a": 1}}
        f._should_filter_l3_pulse(p, "orgA")
        self.assertFalse(f._should_filter_l3_pulse(p, "orgB"),
                         "不同器官的同名事件不算重复")

    def test_explicit_low_value_filtered(self):
        f = _field()
        self.assertTrue(f._should_filter_l3_pulse(
            {"event": "E", "payload": {"diagnostic": True}}, "orgA"))
        self.assertTrue(f._should_filter_l3_pulse(
            {"event": "E", "payload": {"debug_only": True}}, "orgB"))
        self.assertEqual(f.get_l3_backpressure_stats()["filtered_low_value"], 2)

    def test_business_message_never_filtered(self):
        f = _field()
        self.assertFalse(f._should_filter_l3_pulse(
            {"event": "knowledge.written", "payload": {"node_id": "n1"}}, "orgA"),
            "普通业务消息必须放行")

    def test_switch_off_no_filtering(self):
        with _Switch(ENABLE_L3_BACKPRESSURE_OPTIMIZE=False):
            f = _field()
            p = {"event": "E", "payload": {"diagnostic": True}}
            self.assertFalse(f._should_filter_l3_pulse(p, "orgA"))
            self.assertFalse(f._should_filter_l3_pulse(p, "orgA"))
            self.assertEqual(f.get_l3_backpressure_stats()["filtered_total"], 0)


class TestBackpressureStreak(unittest.TestCase):
    def test_streak_increments_and_resets(self):
        f = _field()
        f.note_l3_backpressure("L3")
        f.note_l3_backpressure("L3")
        self.assertEqual(f.get_l3_backpressure_stats()["backpressure_streak"], 2)
        f.note_l3_admitted()
        self.assertEqual(f.get_l3_backpressure_stats()["backpressure_streak"], 0)
        self.assertEqual(f.get_l3_backpressure_stats()["backpressure_streak_peak"], 2)

    def test_stats_shape(self):
        f = _field()
        st = f.get_l3_backpressure_stats()
        for k in ("optimize_enabled", "dedup_window_sec", "warn_every",
                  "filtered_low_value", "filtered_dedup", "filtered_total",
                  "backpressure_count", "backpressure_streak",
                  "backpressure_streak_peak", "l3_rejected"):
            self.assertIn(k, st)


class TestSourceWiring(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(_PROJECT_ROOT, "nucleus", "field",
                                  "InfoField.py"), encoding="utf-8") as f:
            self.src = f.read()

    def test_filter_only_applied_to_l3(self):
        self.assertIn("if layer_tag == _PULSE_LAYER_L3 and self._should_filter_l3_pulse(",
                      self.src)

    def test_existing_early_expand_untouched(self):
        """护栏：既有提前扩容分档不得被本批改动破坏。"""
        self.assertIn("l3_scale_up_thresholds", self.src)

    def test_warn_escalation_wired(self):
        self.assertIn("背压·升级", self.src)


if __name__ == "__main__":
    unittest.main()
