# -*- coding: utf-8 -*-
"""第360批 D/E 组验收测试（已完成刀 + 核销项）。

已落地：刀9（死广播零匹配率**按事件名分桶**）+ 刀17（EventBus 监控三件）。
核销（前批已落地、本批不重复施工）：
  - 刀13 LogAnalyzer 域过滤  ← 343刀2（Traceback 分支）+ 353刀6（ERROR 分支）
  - 刀16 GPU 探测开关        ← 353刀2（`GPU_PROBE_ENABLED=False` 且探测前判定）
  - 刀9 全局零匹配计数       ← `_zero_match_events` 已存在（本批只补按名分桶）
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
from nucleus._silent_except import silent_exc  # noqa: E402


# ======================================================================
class TestM360D09DeadBroadcast(unittest.TestCase):
    """刀9/刀17：零匹配率按事件名分桶 —— 定位"100% 死广播"。"""

    def _bus(self):
        from nucleus.events.EventBus import EventBus
        return EventBus()

    def test_01_zero_match_rate_by_name(self):
        """严格 100% 死广播必须能被单独认出来（全局计数做不到这点）。"""
        _b = self._bus()
        for _ in range(3):
            _b.publish("pattern_prewarm", {"x": 1})
        _b.subscribe("some.thing", lambda e: None)
        _b.publish("some.thing", {})
        _m = _b.m360_event_metrics()
        self.assertEqual(_m["published"], 4)
        self.assertEqual(_m["zero_match"], 3)
        _dead = {d["event"]: d for d in _m["top_dead_events"]}
        self.assertIn("pattern_prewarm", _dead, "死广播必须按事件名出现")
        self.assertEqual(_dead["pattern_prewarm"]["rate"], 1.0,
                         "3 发 3 零 ⇒ 比率应为 1.0（100% 死广播）")
        self.assertEqual(_dead["pattern_prewarm"]["pub"], 3)

    def test_02_rate_never_exceeds_one(self):
        """防回归：曾因 _deliver 与 publish 各计一次 ⇒ 比率算出 2.0。"""
        _b = self._bus()
        for _ in range(5):
            _b.publish("dead.only", {})
        _m = _b.m360_event_metrics()
        _r = _m["zero_match_rate"]
        self.assertIsNotNone(_r)
        self.assertLessEqual(_r, 1.0, "零匹配率不得 >1（实得 %s）" % _r)
        for _d in _m["top_dead_events"]:
            self.assertLessEqual(_d["rate"], 1.0)

    def test_03_no_sample_rate_is_none_not_zero(self):
        """铁律155：无样本是 None 不是 0（0 会被读成"零死广播"）。"""
        _b = self._bus()
        _m = _b.m360_event_metrics()
        self.assertEqual(_m["published"], 0)
        self.assertIsNone(_m["zero_match_rate"])

    def test_04_sub_hits_per_subscription(self):
        """刀17②：per-sub 命中数可读（识别"订了但从未命中"的僵尸订阅）。"""
        _b = self._bus()
        _b.subscribe("a.*", lambda e: None)
        _b.subscribe("b.*", lambda e: None)
        for _ in range(3):
            _b.publish("a.one", {})
        _m = _b.m360_event_metrics()
        _hits = {h["pattern"]: h["hits"] for h in _m["sub_hits"]}
        self.assertEqual(_hits.get("a.*"), 3)
        self.assertEqual(_hits.get("b.*"), 0, "从未命中的订阅也要可见（=0）")

    def test_05_queue_alarm_threshold(self):
        """刀17③：水位阈值可配置，未超限不告警。"""
        _b = self._bus()
        _m = _b.m360_event_metrics()
        self.assertGreater(_m["queue_alarm_threshold"], 0)
        self.assertFalse(_m["queue_alarm"])
        _r = _b.m360_check_queue_alarm()
        self.assertFalse(_r["alarm"])

    def test_06_alarm_threshold_configurable(self):
        _old = getattr(config, "EVENTBUS_QUEUE_ALARM_DEPTH", None)
        try:
            config.EVENTBUS_QUEUE_ALARM_DEPTH = 1
            _b = self._bus()
            _b.publish_async("q.test", {})
            _m = _b.m360_event_metrics()
            self.assertEqual(_m["queue_alarm_threshold"], 1)
        finally:
            if _old is None:
                try:
                    delattr(config, "EVENTBUS_QUEUE_ALARM_DEPTH")
                except Exception as _e:
                    silent_exc(_e, where="test_m360_d 还原 EVENTBUS_QUEUE_ALARM_DEPTH")
            else:
                config.EVENTBUS_QUEUE_ALARM_DEPTH = _old


# ======================================================================
class TestM360WriteOff(unittest.TestCase):
    """核销项：前批已落地，本批不重复施工（断言其**仍在**，防回退）。"""

    def _src(self, rel):
        with open(os.path.join(_PROJECT_ROOT, *rel.split("/")),
                  "r", encoding="utf-8") as f:
            return f.read()

    def test_07_log_analyzer_domain_filter_present(self):
        """刀13 核销：Traceback 分支（343刀2）+ ERROR 分支（353刀6）都有域过滤。"""
        _s = self._src("nucleus/evolution/LogAnalyzer.py")
        self.assertIn("_m343_in_domain", _s)
        # ERROR 行分支（353刀6）
        self.assertIn("_m353_domain_dropped", _s)
        self.assertIn("丢弃域外ERROR行", _s)

    def test_08_gpu_probe_switch_defaults_off(self):
        """刀16 核销：默认关，且探测**前**判定（不是先加载再判）。"""
        self.assertFalse(getattr(config, "GPU_PROBE_ENABLED", True),
                         "GPU_PROBE_ENABLED 应默认 False（353刀2）")
        _s = self._src("nucleus/GPUCore.py")
        self.assertIn("GPU_PROBE_ENABLED", _s)
        self.assertIn("探测=skipped", _s)

    def test_09_global_zero_match_counter_exists(self):
        """刀9 基础（既有）：全局零匹配计数仍在。"""
        _s = self._src("nucleus/events/EventBus.py")
        self.assertIn("_zero_match_events", _s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
