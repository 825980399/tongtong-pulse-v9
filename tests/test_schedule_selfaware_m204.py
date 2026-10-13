# -*- coding: utf-8 -*-
"""第204批 刀2：SelfAwareness 纯周期件迁移到 TimeCore 时效闸 的隔离验证。

断言 config.TIMECORE_SCHEDULE_CONFIG 含 self_awareness_{30,60,150} 三项，
interval = N × 标称拍长(13.5s)（D#7），event_type 为裸字面量（类比203批刀3）。
消费侧接线待观察窗，本批仅验证时效闸登记正确。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402

EXPECTED = {
    "self_awareness_30": {"interval": 405, "event_type": "self_awareness.tick.30"},
    "self_awareness_60": {"interval": 810, "event_type": "self_awareness.tick.60"},
    "self_awareness_150": {"interval": 2025, "event_type": "self_awareness.tick.150"},
}

BEAT_SEC = 13.5  # 标称拍长（与203批刀2口径一致，D#7）


class TestSelfAwareScheduleM204(unittest.TestCase):
    def test_entries_present_and_exact(self):
        cfg = config.TIMECORE_SCHEDULE_CONFIG
        for k, v in EXPECTED.items():
            self.assertIn(k, cfg, f"时效闸缺失项 {k}")
            self.assertEqual(cfg[k].get("interval"), v["interval"],
                             f"{k} interval 不符")
            self.assertEqual(cfg[k].get("event_type"), v["event_type"],
                             f"{k} event_type 不符")

    def test_beat_basis(self):
        # 时效闸主案：interval = N × 标称拍长
        cfg = config.TIMECORE_SCHEDULE_CONFIG
        self.assertEqual(cfg["self_awareness_30"]["interval"], 30 * BEAT_SEC)
        self.assertEqual(cfg["self_awareness_60"]["interval"], 60 * BEAT_SEC)
        self.assertEqual(cfg["self_awareness_150"]["interval"], 150 * BEAT_SEC)

    def test_no_duplicate_keys(self):
        keys = list(config.TIMECORE_SCHEDULE_CONFIG.keys())
        self.assertEqual(len(keys), len(set(keys)), "TIMECORE_SCHEDULE_CONFIG 存在重复键")

    def test_self_awareness_event_type_namespace(self):
        # 三个周期件使用 self_awareness.tick.* 命名空间，互不冲突
        evs = {config.TIMECORE_SCHEDULE_CONFIG[k]["event_type"] for k in EXPECTED}
        self.assertEqual(len(evs), 3)


if __name__ == "__main__":
    unittest.main()
