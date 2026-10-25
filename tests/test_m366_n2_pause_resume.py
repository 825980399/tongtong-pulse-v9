# -*- coding: utf-8 -*-
"""366 刀N2 验收：暂停→解除双向留痕（同一 schema）＋live_gate 类别暂停成对历史。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus.mnemosyne.live_gate as lg  # noqa: E402
import nucleus.runtime_metrics as rm  # noqa: E402


def _ctrl():
    _c = rm.AdaptiveFrequencyController()
    _c.register("stomach_digest", 30.0)
    _c.register("organ_scan", 300.0)
    return _c


class TestN2PauseResume(unittest.TestCase):
    def setUp(self):
        self._c = _ctrl()

    def _keys(self):
        return [e["event"] for e in self._c.get_pause_resume_events(50)]

    # ① 进 CRITICAL ⇒ 暂停事件（WARNING 级 + 统一 schema）
    def test_01_pause_event(self):
        self._c.set_level("CRITICAL")
        _ev = self._c.get_pause_resume_events()
        self.assertEqual(_ev[-1]["event"], "pause")
        self.assertEqual(_ev[-1]["scope"], "level")
        self.assertEqual(_ev[-1]["to_level"], "CRITICAL")
        for _k in ("ts", "event", "scope", "name", "from_level", "to_level",
                   "duration_s", "reason"):
            self.assertIn(_k, _ev[-1])

    # ② 解除：等级回落 ⇒ resume；操作再执行 ⇒ resume_exec（两路径）
    def test_02_resume_two_paths(self):
        with mock.patch.object(rm, "_adaptive_enabled", lambda: True):
            self._c.set_level("CRITICAL")
            self.assertFalse(self._c.should_execute("stomach_digest"))
            self._c.set_level("LOW")
            self.assertIn("resume", self._keys())
            self.assertTrue(self._c.should_execute("stomach_digest"))
        self.assertIn("resume_exec", self._keys())
        _re = [e for e in self._c.get_pause_resume_events() if e["event"] == "resume_exec"][-1]
        self.assertEqual(_re["name"], "stomach_digest")
        self.assertIsNotNone(_re["duration_s"])

    # ③ 重复暂停不刷屏（同等级再 set 不产生新事件）
    def test_03_repeat_pause_not_spam(self):
        self._c.set_level("CRITICAL")
        _n1 = len(self._c.get_pause_resume_events(50))
        self._c.set_level("CRITICAL")
        self._c.set_level("CRITICAL")
        self.assertEqual(len(self._c.get_pause_resume_events(50)), _n1)

    # ④ 解除事件不受 300s 节流压制（低频关键事件必须立即出现）
    def test_04_resume_not_throttled(self):
        with mock.patch.object(rm, "_adaptive_enabled", lambda: True):
            self._c.set_level("CRITICAL")
            self.assertFalse(self._c.should_execute("organ_scan"))
            self._c.set_level("MEDIUM")
            # 恢复后第一次必执行（解除事件此刻落），随后按 MEDIUM 间隔正常节流
            self.assertTrue(self._c.should_execute("organ_scan"))
            self.assertFalse(self._c.should_execute("organ_scan"))
        _ev = [e for e in self._c.get_pause_resume_events() if e["event"] == "resume_exec"]
        self.assertEqual(len(_ev), 1, _ev)

    # ⑤ 统计出口：计数与"当前仍挂起"分得开
    def test_05_stats(self):
        with mock.patch.object(rm, "_adaptive_enabled", lambda: True):
            self._c.set_level("CRITICAL")
            self._c.should_execute("stomach_digest")
        _st = self._c.pause_resume_stats()
        self.assertEqual(_st["paused_ops"], ["stomach_digest"])
        self.assertEqual(_st["events"].get("pause"), 1)


class TestN2LiveGate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "live_gate_state.json")

    def tearDown(self):
        self._tmp.cleanup()

    # ⑥ 类别暂停/解除成对进 history，解除带时长与原原因
    def test_06_pause_resume_history_pair(self):
        lg.pause_category("dead_code", why="误释放", state_path=self.path, now=1000.0)
        r = lg.resume_category("dead_code", state_path=self.path, now=1180.0)
        self.assertTrue(r.get("changed"))
        self.assertEqual(r.get("duration_s"), 180.0)
        _h = lg.pause_history(10, state_path=self.path)
        self.assertEqual([x["action"] for x in _h], ["pause", "resume"])
        self.assertEqual(_h[0]["category"], "dead_code")
        self.assertEqual(_h[1]["duration_s"], 180.0)
        self.assertEqual(_h[1]["why"], "误释放")

    # ⑦ 未暂停时解除：不写历史（不制造假事件）
    def test_07_resume_not_paused_no_history(self):
        r = lg.resume_category("nope", state_path=self.path, now=1.0)
        self.assertFalse(r.get("changed"))
        self.assertEqual(lg.pause_history(10, state_path=self.path), [])


if __name__ == "__main__":
    unittest.main()
