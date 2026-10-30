# -*- coding: utf-8 -*-
"""★403 刀3 判据：心跳快照带 `prev_beat_events`（两次心跳之间发生了哪些事件）。

守什么
------
1. **字段是事件名列表**，不是 payload 字典堆——快照 5s 一写，塞 payload 会爆体积。
2. **去重保序**：同一事件在 5s 内发 20 次，列表里只出现一次。
3. **只看增量**：早于起点的事件不得混进来（否则列表永远是"开机以来全部事件"）。
4. **首拍不撒谎**：没有上一次时刻时返回空表且 `prev_beat_span_s=None`，
   不能拿"0 个事件"冒充"这 5 秒什么都没发生"。
5. **不改变心跳频率**：本刀只加字段，不动 `_snapshot_interval` 与节拍逻辑。
"""
import collections
import importlib.util
import io
import os
import threading
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    _p = os.path.join(_ROOT, rel)
    _spec = importlib.util.spec_from_file_location(name, _p)
    _m = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_m)
    return _m


class _FakeField:
    """只带 `_history` + `_lock` 的替身（方法体来自真的 InfoField）。"""

    def __init__(self, rows):
        self._history = collections.deque(rows)
        self._lock = threading.Lock()


class _StubField:
    """PMC 构建快照时会用到的 info_field 最小面（除 recent_event_names 全部空实现）。"""

    node_pool = None
    _conditions = {}

    def get_stats(self):
        return {}

    def get_history(self, limit=20, event_type=None):
        return []

    def get_current(self, key):
        return None

    def recent_event_names(self, since_ns, limit=50):
        return []


def _rec(ns, event_type, payload=None):
    return {"timestamp_ns": ns, "event_type": event_type, "payload": payload or {}}


class TestRecentEventNames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from nucleus.field.InfoField import InfoField
        cls.IF = InfoField

    def _call(self, rows, since_ns, limit=50):
        return self.IF.recent_event_names(_FakeField(rows), since_ns, limit=limit)

    def test_10_only_after_since(self):
        rows = [_rec(100, "a.old"), _rec(200, "b.new"), _rec(300, "c.new")]
        self.assertEqual(self._call(rows, 150), ["c.new", "b.new"])

    def test_11_dedup_keeps_first_seen_order(self):
        rows = [_rec(100, "a.one"), _rec(110, "b.two"), _rec(120, "a.one"), _rec(130, "b.two")]
        self.assertEqual(self._call(rows, 0 + 1).count("a.one"), 1)

    def test_12_limit_caps(self):
        rows = [_rec(100 + i, "e%d" % i) for i in range(80)]
        self.assertEqual(len(self._call(rows, 1, limit=10)), 10)

    def test_13_zero_since_returns_empty(self):
        rows = [_rec(100, "a.one")]
        self.assertEqual(self._call(rows, 0), [])
        self.assertEqual(self._call(rows, -5), [])

    def test_14_empty_history(self):
        self.assertEqual(self._call([], 100), [])

    def test_15_returns_names_not_payload(self):
        rows = [_rec(100, "a.one", {"big": "x" * 100})]
        out = self._call(rows, 1)
        self.assertEqual(out, ["a.one"])
        self.assertTrue(all(isinstance(x, str) for x in out))


class TestSnapshotHeartbeat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from organs.core.PulseMetricsCollector import PulseMetricsCollector
        cls.C = PulseMetricsCollector()

    def test_20_first_snapshot_empty_and_span_none(self):
        c = self.C
        c._m403_prev_snap_ns = 0
        snap = c._build_observability_snapshot()
        hb = snap["heartbeat"]
        self.assertEqual(hb["prev_beat_events"], [])
        self.assertEqual(hb["prev_beat_event_count"], 0)
        self.assertIsNone(hb["prev_beat_span_s"], "首拍没有上一次，不得编一个跨度")

    def test_21_second_snapshot_has_events(self):
        c = self.C

        class _F(_StubField):
            def __init__(self):
                _StubField.__init__(self)
                self.calls = []

            def recent_event_names(self, since_ns, limit=50):
                self.calls.append((since_ns, limit))
                return ["heart.beat", "memory.probe"]

        f = _F()
        c.info_field = f
        c._m403_prev_snap_ns = time.time_ns() - 5_000_000_000
        snap = c._build_observability_snapshot()
        hb = snap["heartbeat"]
        self.assertEqual(hb["prev_beat_events"], ["heart.beat", "memory.probe"])
        self.assertEqual(hb["prev_beat_event_count"], 2)
        self.assertAlmostEqual(hb["prev_beat_span_s"], 5.0, delta=1.0)
        self.assertEqual(len(f.calls), 1)

    def test_22_field_is_list_of_str(self):
        c = self.C
        c._m403_prev_snap_ns = time.time_ns() - 1_000_000_000
        snap = c._build_observability_snapshot()
        hb = snap["heartbeat"]
        self.assertIsInstance(hb["prev_beat_events"], list)
        for _x in hb["prev_beat_events"]:
            self.assertIsInstance(_x, str)

    def test_23_frequency_unchanged(self):
        """本刀只加字段：快照间隔仍应为 1（每次心跳）。"""
        self.assertEqual(self.C._snapshot_interval, 1)

    def test_24_error_does_not_break_snapshot(self):
        c = self.C

        class _Boom(_StubField):
            def recent_event_names(self, since_ns, limit=50):
                raise RuntimeError("boom")

        c.info_field = _Boom()
        c._m403_prev_snap_ns = time.time_ns() - 1_000_000_000
        snap = c._build_observability_snapshot()   # 不许抛
        self.assertEqual(snap["heartbeat"]["prev_beat_events"], [])


class TestSourceWiring(unittest.TestCase):
    def test_30_pmc_sets_prev_ns(self):
        src = io.open(os.path.join(_ROOT, "organs", "core",
                                   "PulseMetricsCollector.py"), encoding="utf-8").read()
        self.assertIn("_m403_prev_snap_ns = _m403_now_ns", src)
        self.assertIn('_hb["prev_beat_events"]', src)

    def test_31_infofield_method_exists(self):
        src = io.open(os.path.join(_ROOT, "nucleus", "field", "InfoField.py"),
                      encoding="utf-8").read()
        self.assertIn("def recent_event_names(self, since_ns: int, limit: int = 50)", src)
        b = io.open(os.path.join(_ROOT, "nucleus", "field", "InfoField.py"), "rb").read()
        self.assertEqual(b.count(b"\r\n"), b.count(b"\n"), "InfoField 必须保持纯 CRLF")


if __name__ == "__main__":
    unittest.main(verbosity=2)
