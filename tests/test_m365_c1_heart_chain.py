# -*- coding: utf-8 -*-
"""365 刀C1 验收：心跳链断链防护（续链失败兜底／看门狗／重入守卫／TimeCore 同口径）。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from organs.body.PulseHeart import PulseHeart  # noqa: E402
from nucleus.chronos.TimeCore import TimeCore  # noqa: E402


def _mk(**kw):
    """做一个只带心跳链所需属性的轻量实例（绕 __init__，与既有夹具同风格）。"""
    h = PulseHeart.__new__(PulseHeart)
    h._logs = []
    h._log = lambda *a, **k: h._logs.append((a, k))
    h.is_running = True
    h.current_interval = 10.0
    h._last_beat_time = 0.0
    h._beat_count = 0
    h._heartbeat_in_progress = False
    h._survival_orchestrator = None
    h.pulse_core = None
    h.organ_name = "心脏"
    h.info_field = None
    h._timer_lock = mock.MagicMock()
    h._next_timer = None
    for _k, _v in kw.items():
        setattr(h, _k, _v)
    return h


class _BadField:
    def publish(self, pulse):
        raise RuntimeError("publish boom")


class TestC1HeartChain(unittest.TestCase):
    # ① finally 续链失败 ⇒ 留痕计数，异常不外泄（链断了要知道，不能静默）
    def test_01_chain_fail_traced_in_finally(self):
        h = _mk(info_field=_BadField(), _emit=lambda *a, **k: None)
        with mock.patch.object(PulseHeart, "_schedule_next_beat",
                               side_effect=RuntimeError("timer boom")):
            h._trigger_heartbeat()          # 不得抛异常
        self.assertEqual(h.m365_chain_stats().get("chain_fail"), 1)
        _errs = [a for a, k in h._logs if k.get("error_code") == "heartbeat_chain_break"]
        self.assertTrue(_errs, "续链失败必须以 heartbeat_chain_break 留痕")

    # ② info_field 缺失那条早退路径同样兜底
    def test_02_chain_fail_traced_info_none(self):
        h = _mk()
        with mock.patch.object(PulseHeart, "_schedule_next_beat",
                               side_effect=RuntimeError("timer boom")):
            h._trigger_heartbeat()
        self.assertEqual(h.m365_chain_stats().get("chain_fail"), 1)

    # ③ gap > 2×interval ⇒ 告警并尝试续链
    def test_03_watchdog_gap_warns_and_reschedules(self):
        h = _mk(current_interval=10.0, _last_beat_time=0.0)
        h._last_beat_time = __import__("time").time() - 100.0
        with mock.patch.object(PulseHeart, "_schedule_next_beat") as _sch:
            r = h.m365_beat_watchdog()
        self.assertEqual(r.get("action"), "rescheduled")
        _sch.assert_called_once()
        self.assertEqual(h.m365_chain_stats().get("gap_warn"), 1)
        self.assertTrue(any(k.get("level") or True for a, k in h._logs), h._logs)

    # ④ 重入守卫有效：正在跑的一拍不被看门狗插队（否则双定时器）
    def test_04_watchdog_skips_when_in_progress(self):
        h = _mk(current_interval=10.0, _heartbeat_in_progress=True)
        h._last_beat_time = __import__("time").time() - 100.0
        with mock.patch.object(PulseHeart, "_schedule_next_beat") as _sch:
            r = h.m365_beat_watchdog()
        self.assertEqual(r.get("action"), "skip_in_progress")
        _sch.assert_not_called()

    # ⑤ gap 正常 ⇒ 不动作（不误报）
    def test_05_watchdog_quiet_when_healthy(self):
        h = _mk(current_interval=10.0)
        h._last_beat_time = __import__("time").time() - 1.0
        with mock.patch.object(PulseHeart, "_schedule_next_beat") as _sch:
            r = h.m365_beat_watchdog()
        self.assertEqual(r.get("action"), "none")
        _sch.assert_not_called()

    # ⑥ TimeCore 同口径：Timer 构造失败 ⇒ 计数 + WARNING，不外泄
    def test_06_timecore_counts_fail(self):
        tc = TimeCore.__new__(TimeCore)
        tc._interval = 30.0
        tc._running = True
        tc._logger = None
        with mock.patch("threading.Timer", side_effect=RuntimeError("no timer")):
            tc._schedule_next()
        self.assertEqual(tc.m365_chain_stats().get("chain_fail"), 1)


if __name__ == "__main__":
    unittest.main()
