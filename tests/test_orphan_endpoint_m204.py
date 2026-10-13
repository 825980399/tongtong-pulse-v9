# -*- coding: utf-8 -*-
"""第204批 刀4：快照类孤儿"报告即终点"白名单 的隔离验证。

断言：
1. 模块级白名单含 EnergyEvent.METABOLISM_SNAPSHOT。
2. 快照类 emit 事件（超龄/无接收方/无订阅方）被白名单放行，不计入孤儿。
3. 非白名单孤儿 emit 事件仍被判为孤儿（白名单不误伤）。
"""
import os
import sys
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from utils.pulse_tracer import _detect_orphans, _REPORT_ENDPOINT_WHITELIST  # noqa: E402
from nucleus.const import EnergyEvent  # noqa: E402


def _mk(event_type, age=10):
    return {
        "type": "emit",
        "timestamp": time.time() - age,
        "event_type": event_type,
        "organ": "Energy",
        "layer": "L3",
        "pulse_id": "p1",
    }


class TestOrphanEndpointM204(unittest.TestCase):
    def test_whitelist_contains_metabolism(self):
        self.assertIn(EnergyEvent.METABOLISM_SNAPSHOT, _REPORT_ENDPOINT_WHITELIST)

    def test_snapshot_whitelisted(self):
        evs = [_mk("energy.metabolism_snapshot")]
        orphans = _detect_orphans(evs, min_age=0.0)
        self.assertEqual(len(orphans), 0, "快照类事件应被白名单放行（不计孤儿）")

    def test_non_whitelist_still_orphan(self):
        evs = [_mk("some.other.unregistered.event")]
        orphans = _detect_orphans(evs, min_age=0.0)
        self.assertEqual(len(orphans), 1, "非白名单孤儿仍应判定为孤儿")

    def test_mixed(self):
        evs = [_mk("energy.metabolism_snapshot"), _mk("another.orphan.event")]
        orphans = _detect_orphans(evs, min_age=0.0)
        types = {o["event_type"] for o in orphans}
        self.assertNotIn("energy.metabolism_snapshot", types)
        self.assertIn("another.orphan.event", types)


if __name__ == "__main__":
    unittest.main()
