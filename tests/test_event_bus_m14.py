# -*- coding: utf-8 -*-
"""
test_event_bus_m14.py —— 主线第14批 任务3 门控单测（事件总线 P2-63）

覆盖：发布/订阅、通配符（* / **）、优先级、异步队列、事件溯源历史、
      单例与灰度开关、异常隔离、性能（同步 <1ms）、历史容量上限。
"""
import json
import os
import shutil
import sys
import threading
import time
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from nucleus.events.EventBus import (  # noqa: E402
    Event,
    EventBus,
    EventPriority,
    _match,
    get_event_bus,
    reset_event_bus,
)

from nucleus.const import Event as PulseEvent  # noqa: E402

_SCRATCH_DIR = os.path.join(_PROJECT_ROOT, "tmp", "_eventbus_m14")


def _purge_scratch():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)


class _Collector:
    """记录被调用的事件（含顺序）。"""

    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def __call__(self, ev):
        with self.lock:
            self.events.append(ev)

    @property
    def names(self):
        return [e.name for e in self.events]


class TestWildcardMatch(unittest.TestCase):
    def test_exact(self):
        self.assertTrue(_match(PulseEvent.HEART_BEAT, PulseEvent.HEART_BEAT))
        self.assertFalse(_match(PulseEvent.HEART_BEAT, "heart.stop"))

    def test_single_segment(self):
        self.assertTrue(_match("heart.*", PulseEvent.HEART_BEAT))
        self.assertFalse(_match("heart.*", "heart.a.b"))

    def test_multi_segment(self):
        self.assertTrue(_match("heart.**", "heart"))
        self.assertTrue(_match("heart.**", "heart.a"))
        self.assertTrue(_match("heart.**", "heart.a.b"))

    def test_all(self):
        self.assertTrue(_match("**", "anything"))
        self.assertTrue(_match("**", "a.b.c"))

    def test_empty_pattern_never_matches(self):
        self.assertFalse(_match("", "a"))


class TestSyncPubSub(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus(async_enabled=False)

    def tearDown(self):
        self.bus.stop()

    def test_exact_subscribe_publish(self):
        c = _Collector()
        sid = self.bus.subscribe(PulseEvent.HEART_BEAT, c)
        self.assertTrue(sid)
        n = self.bus.publish(PulseEvent.HEART_BEAT, {"bpm": 72})
        self.assertEqual(n, 1)
        self.assertEqual(c.names, [PulseEvent.HEART_BEAT])
        self.assertEqual(c.events[0].payload, {"bpm": 72})

    def test_wildcard_subscription(self):
        c = _Collector()
        self.bus.subscribe("heart.*", c)
        self.bus.publish(PulseEvent.HEART_BEAT)
        self.bus.publish("heart.stop")
        self.bus.publish("lung.breathe")   # 不匹配
        self.assertEqual(c.names, [PulseEvent.HEART_BEAT, "heart.stop"])

    def test_unsubscribe(self):
        c = _Collector()
        sid = self.bus.subscribe("a.b", c)
        self.assertEqual(self.bus.publish("a.b"), 1)
        self.assertTrue(self.bus.unsubscribe(sid))
        self.assertEqual(self.bus.publish("a.b"), 0)
        self.assertFalse(self.bus.unsubscribe(sid), "重复退订应返回 False")

    def test_once_subscription(self):
        c = _Collector()
        self.bus.subscribe("ping", c, once=True)
        self.bus.publish("ping")
        self.bus.publish("ping")
        self.assertEqual(len(c.events), 1)
        self.assertEqual(self.bus.subscription_count(), 0, "once 触发后应自动退订")

    def test_handler_priority_ordering(self):
        order = []
        self.bus.subscribe("x", lambda e: order.append("low"), priority=EventPriority.LOW)
        self.bus.subscribe("x", lambda e: order.append("crit"), priority=EventPriority.CRITICAL)
        self.bus.subscribe("x", lambda e: order.append("normal"), priority=EventPriority.NORMAL)
        self.bus.publish("x")
        self.assertEqual(order, ["crit", "normal", "low"])

    def test_subscribe_validation(self):
        with self.assertRaises(TypeError):
            self.bus.subscribe("a", "not-callable")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            self.bus.subscribe("", lambda e: None)

    def test_handler_exception_isolated(self):
        def _boom(ev):
            raise RuntimeError("handler-fail")
        c = _Collector()
        self.bus.subscribe("e", _boom)
        self.bus.subscribe("e", c)
        n = self.bus.publish("e")
        self.assertEqual(n, 1, "异常处理器不计入成功数，且不得影响其他订阅")
        self.assertEqual(len(c.events), 1)
        st = self.bus.stats()
        self.assertEqual(st["failed"], 1)
        self.assertIn("handler-fail", st["last_error"])

    def test_sync_latency_under_1ms(self):
        """任务书要求：同步事件 <1ms。"""
        self.bus.subscribe("fast", lambda e: None)
        # 预热
        for _ in range(20):
            self.bus.publish("fast")
        t0 = time.perf_counter()
        runs = 200
        for _ in range(runs):
            self.bus.publish("fast")
        avg_ms = (time.perf_counter() - t0) / runs * 1000.0
        self.assertLess(avg_ms, 1.0, f"平均同步发布耗时 {avg_ms:.4f}ms 超过 1ms")


class TestAsyncPubSub(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus(async_enabled=True)

    def tearDown(self):
        self.bus.stop()

    def test_async_delivery(self):
        c = _Collector()
        self.bus.subscribe("async.evt", c)
        self.assertTrue(self.bus.publish_async("async.evt", {"v": 1}))
        self.assertTrue(self.bus.drain(5.0), "异步队列未在超时内排空")
        self.assertEqual(c.names, ["async.evt"])
        st = self.bus.stats()
        self.assertTrue(st["worker_alive"])
        self.assertEqual(st["delivered"], 1)

    def test_async_priority_order(self):
        order = []
        self.bus.subscribe("p", lambda e: order.append(e.payload))
        self.bus.publish_async("p", "low", priority=EventPriority.LOW)
        self.bus.publish_async("p", "normal", priority=EventPriority.NORMAL)
        self.bus.publish_async("p", "crit", priority=EventPriority.CRITICAL)
        self.assertTrue(self.bus.drain(5.0))
        self.assertEqual(order, ["crit", "normal", "low"])

    def test_async_disabled_degrades_to_sync(self):
        bus = EventBus(async_enabled=False)
        try:
            c = _Collector()
            bus.subscribe("s", c)
            bus.publish_async("s")
            self.assertEqual(len(c.events), 1, "异步关闭时应同步投递")
            self.assertFalse(bus.stats()["worker_alive"], "不得创建后台线程")
        finally:
            bus.stop()

    def test_stop_releases_worker(self):
        bus = EventBus(async_enabled=True)
        bus.subscribe("t", lambda e: None)
        bus.publish_async("t")
        self.assertTrue(bus.drain(5.0))
        self.assertTrue(bus.stop())
        self.assertFalse(bus.stats()["worker_alive"])

    def test_concurrent_publish_thread_safe(self):
        c = _Collector()
        self.bus.subscribe("c", c)
        def _worker():
            for _ in range(50):
                self.bus.publish("c")
        threads = [threading.Thread(target=_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(c.events), 200)


class TestEventSourcing(unittest.TestCase):
    def setUp(self):
        _purge_scratch()
        os.makedirs(_SCRATCH_DIR, exist_ok=True)
        self.bus = EventBus(async_enabled=False)

    def tearDown(self):
        self.bus.stop()
        _purge_scratch()

    def test_history_records_and_filters(self):
        self.bus.publish("a.one", 1)
        self.bus.publish("a.two", 2)
        self.bus.publish("b.one", 3)
        self.assertEqual(self.bus.history_size, 3)
        self.assertEqual(len(self.bus.get_history(name="a.one")), 1)
        self.assertEqual(len(self.bus.get_history(pattern="a.*")), 2)
        self.assertEqual(len(self.bus.get_history(pattern="**")), 3)
        self.assertEqual(len(self.bus.get_history(limit=2)), 2)

    def test_history_is_newest_first(self):
        self.bus.publish("e", "first")
        self.bus.publish("e", "second")
        got = self.bus.get_history(as_dict=True)
        self.assertEqual(got[0]["payload"], "second")
        self.assertEqual(got[1]["payload"], "first")

    def test_history_bounded_by_max(self):
        bus = EventBus(async_enabled=False, max_history=5)
        try:
            for i in range(12):
                bus.publish("n", i)
            self.assertEqual(bus.history_size, 5)
            got = bus.get_history()
            self.assertEqual([e.payload for e in got], [11, 10, 9, 8, 7])
        finally:
            bus.stop()

    def test_history_default_max_is_10000(self):
        self.assertEqual(EventBus().max_history, 10000)

    def test_summary_and_clear(self):
        self.bus.publish("x")
        self.bus.publish("x")
        self.bus.publish("y")
        s = self.bus.summary()
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["distinct_names"], 2)
        self.assertEqual(s["top_names"][0], {"name": "x", "count": 2})
        self.assertIsNotNone(s["latest"])
        self.assertEqual(self.bus.clear_history(), 3)
        self.assertEqual(self.bus.history_size, 0)

    def test_export_history_json(self):
        self.bus.publish("exp", {"k": "v"})
        path = os.path.join(_SCRATCH_DIR, "history.json")
        n = self.bus.export_history(path)
        self.assertEqual(n, 1)
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        self.assertEqual(rows[0]["name"], "exp")
        self.assertEqual(rows[0]["payload"], {"k": "v"})


class TestSingletonAndSwitch(unittest.TestCase):
    def tearDown(self):
        reset_event_bus()

    def test_switch_defaults_off_and_async_disabled(self):
        self.assertIs(getattr(config, "ENABLE_EVENT_BUS", None), False,
                      "灰度开关默认必须为 False（不改动现有通信方式）")
        bus = get_event_bus()
        self.assertFalse(bus.stats()["async_enabled"],
                         "开关关闭时单例应为同步退化模式")

    def test_singleton_identity_and_reset(self):
        b1 = get_event_bus()
        self.assertIs(b1, get_event_bus())
        reset_event_bus()
        self.assertIsNot(get_event_bus(), b1)

    def test_switch_on_enables_async(self):
        old = config.ENABLE_EVENT_BUS
        try:
            config.ENABLE_EVENT_BUS = True
            reset_event_bus()
            bus = get_event_bus()
            self.assertTrue(bus.stats()["async_enabled"])
        finally:
            config.ENABLE_EVENT_BUS = old
            reset_event_bus()

    def test_event_to_dict_serializable(self):
        ev = Event.create("n", {"a": 1}, source="test")
        d = ev.to_dict()
        json.dumps(d, ensure_ascii=False)   # 不抛异常即通过
        self.assertEqual(d["name"], "n")
        self.assertEqual(d["source"], "test")
        self.assertEqual(d["priority_name"], "NORMAL")


if __name__ == "__main__":
    unittest.main()
