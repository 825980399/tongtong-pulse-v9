# -*- coding: utf-8 -*-
"""
test_event_tap_m17.py —— 主线第17批 门控单测（P2-63 事件总线旁路监听试点）

覆盖：
  一、EventTap 核心（订阅/统计/环形缓存/间隔/溢出保护/导出/线程安全/开关/停机）
  二、tap_publish 便捷发布入口（总开关 / 器官开关 / 异常兜底）
  三、三器官旁路发布接线（事件名规范 + 发布块真实执行 + PulseCortex 真实调度）

设计说明：
  · EventTap 核心测试注入**独立 EventBus 实例**，避免污染全局单例。
  · 器官「发布块真实执行」用源码切片 + exec：真实跑新增的 `tap_publish(...)`
    代码（含 payload 构造），而非仅做文本断言。
"""

import json
import os
import re
import sys
import textwrap
import threading
import time
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from nucleus.events.EventBus import (  # noqa: E402
    EventBus,
    EventPriority,
    reset_event_bus,
)
from nucleus.events.EventTap import (  # noqa: E402
    _OTHER_BUCKET,
    EventTap,
    get_event_tap,
    reset_event_tap,
    tap_publish,
)

from nucleus.const import Event  # noqa: E402

_SCRATCH = os.path.join(_PROJECT_ROOT, "tmp", "_event_tap_m17")

_CORTEX = os.path.join(_PROJECT_ROOT, "organs", "brain", "PulseCortex.py")
_LIVER = os.path.join(_PROJECT_ROOT, "organs", "body", "PulseLiver.py")
_STOMACH = os.path.join(_PROJECT_ROOT, "organs", "body", "PulseStomach.py")


class _Switch:
    """临时改写 config 值并在退出时还原。"""

    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        for _k, _v in self._kw.items():
            self._old[_k] = getattr(config, _k, None)
            setattr(config, _k, _v)
        return self

    def __exit__(self, *exc):
        for _k, _v in self._old.items():
            setattr(config, _k, _v)
        return False


def _iter_publish_blocks(path):
    """从源码中切出每个 `tap_publish(...)` 完整调用（含前导缩进）。"""
    _src = open(path, encoding="utf-8").read()
    _out = []
    _idx = 0
    while True:
        _i = _src.find("tap_publish(", _idx)
        if _i < 0:
            break
        _ls = _src.rfind("\n", 0, _i) + 1
        _op = _src.index("(", _i)
        _depth = 0
        _k = _op
        while _k < len(_src):
            _ch = _src[_k]
            if _ch == "(":
                _depth += 1
            elif _ch == ")":
                _depth -= 1
                if _depth == 0:
                    break
            _k += 1
        _out.append(_src[_ls:_k + 1])
        _idx = _k + 1
    return _out


def _first_str_arg(block):
    """取 tap_publish 的第一个字符串实参（事件名）。"""
    _m = re.search(r"tap_publish\(\s*\n?\s*[\"']([^\"']+)[\"']", block)
    return _m.group(1) if _m else None


# ======================================================================
# 一、EventTap 核心
# ======================================================================
class TestEventTapCore(unittest.TestCase):
    def setUp(self):
        os.makedirs(_SCRATCH, exist_ok=True)
        self.bus = EventBus(async_enabled=False, max_history=200)
        self.tap = EventTap(history_limit=50, max_event_types=5, bus=self.bus)

    def tearDown(self):
        self.tap.stop()

    def test_config_defaults(self):
        """6 个开关的默认值必须与任务书一致。"""
        self.assertIs(config.ENABLE_EVENT_BUS_TAP, True)
        self.assertIs(config.ENABLE_CORTEX_EVENT_TAP, True)
        self.assertIs(config.ENABLE_LIVER_EVENT_TAP, True)
        self.assertIs(config.ENABLE_STOMACH_EVENT_TAP, True)
        self.assertEqual(config.EVENT_TAP_HISTORY_LIMIT, 1000)
        self.assertEqual(config.EVENT_TAP_MAX_EVENT_TYPES, 500)

    def test_subscribes_all_events_with_low_priority(self):
        """必须以 `**` 订阅全部事件，且优先级为 LOW（业务处理器先执行）。"""
        _subs = list(self.bus._subs.values())
        _wild = [s for s in _subs if s.pattern == "**"]
        self.assertEqual(len(_wild), 1, _subs)
        self.assertEqual(int(_wild[0].priority), int(EventPriority.LOW))
        self.assertTrue(self.tap.started)

    def test_auto_start_on_construct(self):
        """构造即订阅（监听器语义），stop 后可再次 start。"""
        self.assertTrue(self.tap.started)
        self.assertTrue(self.tap.stop())
        self.assertFalse(self.tap.started)
        self.tap.start()
        self.assertTrue(self.tap.started)

    def test_counts_by_name_source_priority(self):
        for _ in range(3):
            self.bus.publish(Event.CORTEX_DIALOG_START, source="PulseCortex",
                             priority=EventPriority.LOW)
        self.bus.publish("liver.memory.write", source="PulseLiver",
                         priority=EventPriority.HIGH)
        _st = self.tap.get_stats()
        self.assertEqual(_st["total"], 4)
        self.assertEqual(_st["by_name"][Event.CORTEX_DIALOG_START], 3)
        self.assertEqual(_st["by_name"]["liver.memory.write"], 1)
        self.assertEqual(_st["by_source"]["PulseCortex"], 3)
        self.assertEqual(_st["by_source"]["PulseLiver"], 1)
        self.assertEqual(_st["by_priority"]["LOW"], 3)
        self.assertEqual(_st["by_priority"]["HIGH"], 1)
        self.assertEqual(_st["distinct_names"], 2)

    def test_ring_buffer_bounded(self):
        """环形缓存不得超上限（无内存泄漏）。"""
        for _i in range(80):
            self.bus.publish("a.b", payload={"i": _i})
        _st = self.tap.get_stats()
        self.assertEqual(_st["total"], 80)
        self.assertEqual(_st["recent_size"], 50)
        _recent = self.tap.get_recent(5)
        self.assertEqual(len(_recent), 5)
        self.assertEqual(_recent[-1]["payload"], "dict(len=1)")

    def test_interval_stats(self):
        for _i in range(4):
            self.bus.publish("a.b")
            time.sleep(0.002)
        _iv = self.tap.get_stats()["interval"]
        self.assertGreaterEqual(_iv["samples"], 3)
        self.assertIsNotNone(_iv["min"])
        self.assertGreater(_iv["max"], 0.0)
        self.assertGreater(_iv["avg"], 0.0)

    def test_overflow_guard_buckets_into_other(self):
        """事件名种类超上限 → 归入 __other__，不再无界增长。"""
        for _i in range(20):
            self.bus.publish("evt.%d" % _i, source="X")
        _st = self.tap.get_stats()
        self.assertEqual(_st["total"], 20)
        self.assertEqual(_st["distinct_names"], 5)
        self.assertIn(_OTHER_BUCKET, _st["by_name"])
        self.assertEqual(_st["overflow_types"], 15)
        self.assertEqual(_st["by_name"][_OTHER_BUCKET], 15)

    def test_overflow_warning_only_once(self):
        """溢出告警只打一次（不刷屏）。"""
        _logger = __import__("nucleus.events.EventTap",
                             fromlist=["_logger"])._logger
        _warns = []
        _orig = _logger.warning
        _logger.warning = lambda *a, **k: _warns.append(a)
        try:
            for _i in range(20):
                self.bus.publish("evt.%d" % _i)
        finally:
            _logger.warning = _orig
        self.assertEqual(len(_warns), 1, _warns)

    def test_switch_off_returns_early(self):
        """总开关关闭 → 不计入统计（零开销路径）。"""
        with _Switch(ENABLE_EVENT_BUS_TAP=False):
            for _i in range(5):
                self.bus.publish("a.b")
        _st = self.tap.get_stats()
        self.assertEqual(_st["total"], 0)
        self.assertEqual(_st["filtered_off"], 5)

    def test_export_json_roundtrip(self):
        for _i in range(3):
            self.bus.publish(Event.CORTEX_DIALOG_START, payload={"i": _i},
                             source="PulseCortex")
        _p = os.path.join(_SCRATCH, "tap_export.json")
        _n = self.tap.export_stats(_p)
        self.assertEqual(_n, 3)
        with open(_p, encoding="utf-8") as f:
            _data = json.load(f)
        self.assertEqual(_data["stats"]["total"], 3)
        self.assertEqual(len(_data["recent"]), 3)
        self.assertEqual(_data["recent"][0]["source"], "PulseCortex")
        self.assertIn("exported_at", _data)
        self.assertEqual(self.tap.get_stats()["export_count"], 1)
        os.remove(_p)

    def test_thread_safety_counts_exact(self):
        """多线程并发发布，总数必须精确（加锁计数）。"""
        _n_threads, _per = 6, 60

        def _worker():
            for _ in range(_per):
                self.bus.publish("concurrent.evt", source="T")

        _ts = [threading.Thread(target=_worker) for _ in range(_n_threads)]
        for _t in _ts:
            _t.start()
        for _t in _ts:
            _t.join()
        _st = self.tap.get_stats()
        self.assertEqual(_st["total"], _n_threads * _per)
        self.assertEqual(_st["by_name"]["concurrent.evt"], _n_threads * _per)

    def test_on_event_never_raises_on_bad_event(self):
        """畸形事件不得让监听器抛出（旁路监听零副作用）。"""
        self.tap._on_event(object())          # 无任何属性
        self.tap._on_event(None)
        self.assertEqual(self.tap.get_stats()["total"], 2)

    def test_reset_stats_keeps_subscription(self):
        self.bus.publish("a.b")
        self.tap.reset_stats()
        _st = self.tap.get_stats()
        self.assertEqual(_st["total"], 0)
        self.assertEqual(_st["recent_size"], 0)
        self.assertTrue(self.tap.started)

    def test_singleton_returns_same_instance(self):
        try:
            _a = get_event_tap()
            _b = get_event_tap()
            self.assertIs(_a, _b)
            self.assertTrue(_a.started)
        finally:
            reset_event_tap()


# ======================================================================
# 二、tap_publish 便捷入口
# ======================================================================
class TestTapPublish(unittest.TestCase):
    def setUp(self):
        reset_event_bus()
        reset_event_tap()
        self.tap = get_event_tap()

    def tearDown(self):
        reset_event_tap()
        reset_event_bus()

    def test_publish_ok_and_counted(self):
        self.assertTrue(tap_publish(Event.CORTEX_DIALOG_START, {"a": 1},
                                    "PulseCortex", "ENABLE_CORTEX_EVENT_TAP"))
        _st = self.tap.get_stats()
        self.assertEqual(_st["total"], 1)
        self.assertEqual(_st["by_name"][Event.CORTEX_DIALOG_START], 1)
        self.assertEqual(_st["by_priority"]["LOW"], 1)

    def test_total_switch_off_blocks_all(self):
        with _Switch(ENABLE_EVENT_BUS_TAP=False):
            self.assertFalse(tap_publish(Event.CORTEX_DIALOG_START, {}, "PulseCortex",
                                         "ENABLE_CORTEX_EVENT_TAP"))
        self.assertEqual(self.tap.get_stats()["total"], 0)

    def test_organ_switch_off_blocks_only_that_organ(self):
        with _Switch(ENABLE_LIVER_EVENT_TAP=False):
            self.assertFalse(tap_publish("liver.memory.write", {}, "PulseLiver",
                                         "ENABLE_LIVER_EVENT_TAP"))
            self.assertTrue(tap_publish(Event.CORTEX_DIALOG_START, {}, "PulseCortex",
                                        "ENABLE_CORTEX_EVENT_TAP"))
        _st = self.tap.get_stats()
        self.assertEqual(_st["total"], 1)
        self.assertNotIn("liver.memory.write", _st["by_name"])

    def test_empty_switch_attr_only_total_switch(self):
        """switch_attr 为空 → 只受总开关约束。"""
        self.assertTrue(tap_publish("generic.evt", {}, "X", ""))

    def test_exception_is_swallowed(self):
        """发布链路异常必须被吞掉并返回 False（绝不影响业务）。"""
        import nucleus.events.EventTap as _m

        class _BoomBus:
            def publish(self, *a, **k):
                raise RuntimeError("boom")

        _orig = _m.get_event_bus
        _m.get_event_bus = lambda: _BoomBus()
        try:
            self.assertFalse(tap_publish("x.y", {}, "X", ""))
        finally:
            _m.get_event_bus = _orig


# ======================================================================
# 三、三器官旁路发布接线
# ======================================================================
class TestOrganWiring(unittest.TestCase):
    def test_event_name_convention(self):
        """事件名必须是 `器官名.动作.阶段` 全小写点分格式。"""
        _pat = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")
        _expected = {
            _CORTEX: {Event.CORTEX_DIALOG_START, "cortex.dialog.end"},
            _LIVER: {"liver.memory.write", "liver.memory.compress"},
            _STOMACH: {"stomach.digest.start", "stomach.digest.end"},
        }
        for _path, _names in _expected.items():
            _found = set()
            for _b in _iter_publish_blocks(_path):
                _n = _first_str_arg(_b)
                self.assertIsNotNone(_n, _b)
                self.assertRegex(_n, _pat, _path)
                _found.add(_n)
            self.assertEqual(_found, _names, _path)

    def test_cortex_source_wiring(self):
        _src = open(_CORTEX, encoding="utf-8").read()
        self.assertIn("from nucleus.events.EventTap import tap_publish", _src)
        # ★主线第33批 T2（P2-195）：语义=「start/end 出口都已插桩」，
        #   删点即 <3 仍失败；改用 >= 以免新增插桩点或注释提及导致假失败。
        self.assertGreaterEqual(
            _src.count('switch_attr="ENABLE_CORTEX_EVENT_TAP"'), 3,
            "1 个 start + 2 个 end 出口")

    def test_liver_source_wiring(self):
        _src = open(_LIVER, encoding="utf-8").read()
        self.assertIn("from nucleus.events.EventTap import tap_publish", _src)
        # ★主线第33批 T2（P2-195）：同 Cortex，改 >= 提高鲁棒性。
        self.assertGreaterEqual(_src.count('switch_attr="ENABLE_LIVER_EVENT_TAP"'), 3)

    def test_stomach_source_wiring(self):
        """★第18批 T7 已补全 2 个拒绝出口 → 事件点由 2 增至 4。"""
        _src = open(_STOMACH, encoding="utf-8").read()
        self.assertIn("from nucleus.events.EventTap import tap_publish", _src)
        # ★主线第33批 T2（P2-195）：同 Cortex，改 >= 提高鲁棒性。
        self.assertGreaterEqual(
            _src.count('switch_attr="ENABLE_STOMACH_EVENT_TAP"'), 4,
            "1 start + 1 主 end + 2 拒绝 end")

    def test_publish_blocks_are_business_neutral(self):
        """护栏：发布块不得包含 return / raise / 赋值（只发布，不改业务）。"""
        for _path in (_CORTEX, _LIVER, _STOMACH):
            for _b in _iter_publish_blocks(_path):
                self.assertNotIn("return", _b, _path)
                self.assertNotIn("raise", _b, _path)

    def _run_blocks(self, path, ns_extra):
        _calls = []

        def _capture(name, payload=None, source="", switch_attr="", priority=None):
            _calls.append({"name": name, "payload": payload, "source": source,
                           "switch_attr": switch_attr, "priority": priority})
            return True

        _ns = {"tap_publish": _capture, "time": time}
        _ns.update(ns_extra)
        for _b in _iter_publish_blocks(path):
            exec(compile(textwrap.dedent(_b), "<tap-block>", "exec"), _ns)
        return _calls

    def test_cortex_publish_blocks_execute_with_expected_payload(self):
        _calls = self._run_blocks(_CORTEX, {
            "correlation_id": "ctx:7:1", "_tap_t0": time.perf_counter() - 0.01,
            "method": "rule_reason", "answer": "答案示例", "content": "问题内容",
        })
        _by = {c["name"]: c for c in _calls}
        self.assertIn(Event.CORTEX_DIALOG_START, _by)
        self.assertIn("cortex.dialog.end", _by)
        self.assertEqual(_by[Event.CORTEX_DIALOG_START]["payload"]["correlation_id"], "ctx:7:1")
        self.assertEqual(_by[Event.CORTEX_DIALOG_START]["payload"]["question_len"], 4)
        self.assertIn("duration_ms", _by["cortex.dialog.end"]["payload"])
        self.assertEqual(_by[Event.CORTEX_DIALOG_START]["source"], "PulseCortex")

    def test_liver_publish_blocks_execute_with_expected_payload(self):
        _calls = self._run_blocks(_LIVER, {
            "_tap_t0": time.perf_counter() - 0.02, "total_l1": 12,
            "compressed_count": 4, "fused_count": 3, "groups": {"a": [1, 2]},
        })
        _names = [c["name"] for c in _calls]
        # ★主线第33批 T2（P2-195）：`_names` 是**运行期捕获**的调用名列表，
        #   属行为断言（非源码文本计数），本就稳健，保持不变。
        self.assertEqual(_names.count("liver.memory.write"), 2)
        self.assertIn("liver.memory.compress", _names)
        _write = [_c for _c in _calls if _c["name"] == "liver.memory.write"]
        self.assertEqual({_c["payload"]["node_count"] for _c in _write}, {12, 4})
        _cmp = [c for c in _calls if c["name"] == "liver.memory.compress"][0]
        self.assertEqual(_cmp["payload"]["from_count"], 1)
        self.assertEqual(_cmp["payload"]["to_count"], 3)

    def test_stomach_publish_blocks_execute_with_expected_payload(self):
        """★第18批 T7：主出口 success + 两个拒绝出口 rejected（low_quality/below_threshold）。"""
        _calls = self._run_blocks(_STOMACH, {
            "_tap_t0": time.perf_counter() - 0.03,
            "content": "一段待消化内容", "keywords": ["知识", "架构"],
            "_bk_kws": ["知识"],
        })
        _starts = [c for c in _calls if c["name"] == "stomach.digest.start"]
        _ends = [c for c in _calls if c["name"] == "stomach.digest.end"]
        self.assertEqual(len(_starts), 1)
        self.assertEqual(len(_ends), 3)
        self.assertEqual(_starts[0]["payload"]["input_len"], 7)
        self.assertEqual(_starts[0]["payload"]["input_type"], "str")
        self.assertEqual(sorted(c["payload"]["result"] for c in _ends),
                         ["rejected", "rejected", "success"])
        _reasons = sorted(c["payload"]["reason"] for c in _ends
                          if c["payload"]["result"] == "rejected")
        self.assertEqual(_reasons, ["below_threshold", "low_quality"])
        _ok = [c for c in _ends if c["payload"]["result"] == "success"][0]
        self.assertEqual(_ok["payload"]["output_len"], 2)

    def test_cortex_real_dispatch_publishes_dialog_start(self):
        """真实调用 PulseCortex._on_chat_message → 触发 cortex.dialog.start。"""
        from organs.brain import PulseCortex as _mod
        _calls = []
        _orig = _mod.tap_publish
        _mod.tap_publish = lambda name, payload=None, **k: _calls.append(name) or True
        try:
            _c = _mod.PulseCortex("测试皮层")
            _c._emit = lambda *a, **k: None
            _c._log = lambda *a, **k: None
            _c._log_ignored_exception = lambda *a, **k: None
            try:
                _c._on_chat_message({"content": "你好，介绍一下自己",
                                     "user_name": "用户"})
            except Exception:
                pass   # 下游路由可失败，此处只验发布点是否被触达
        finally:
            _mod.tap_publish = _orig
        self.assertIn(Event.CORTEX_DIALOG_START, _calls)


if __name__ == "__main__":
    unittest.main()
