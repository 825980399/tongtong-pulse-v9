# -*- coding: utf-8 -*-
"""365 刀C2 验收：语音启动失败五档分类留痕（＋内存类跳过入口）。"""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import organs.senses.PulseEars as pe  # noqa: E402


class _Sink:
    def __init__(self):
        self.rows = []

    def _log(self, level, msg, **kw):
        self.rows.append((level, msg))


class TestC2VoiceFailure(unittest.TestCase):
    def setUp(self):
        pe._M365_VOICE_FAIL.clear()
        pe._M365_VOICE_LAST.clear()
        pe._M365_VOICE_SKIP_UNTIL["ts"] = 0.0

    def test_01_five_buckets(self):
        self.assertEqual(pe.m365_classify_voice_failure(ImportError("x")), "dependency")
        self.assertEqual(pe.m365_classify_voice_failure(FileNotFoundError("m")), "model")
        self.assertEqual(pe.m365_classify_voice_failure(
            OSError("No Default Input Device")), "no_mic")
        self.assertEqual(pe.m365_classify_voice_failure(
            OSError("Device unavailable / busy")), "device_busy")
        self.assertEqual(pe.m365_classify_voice_failure(MemoryError()), "memory")
        self.assertEqual(pe.m365_classify_voice_failure(ValueError("??")), "unknown")

    def test_02_counts_and_report(self):
        _s = _Sink()
        pe.m365_note_voice_failure(_s, ImportError("no sounddevice"), "sd")
        pe.m365_note_voice_failure(_s, ImportError("no vosk"), "vosk")
        rep = pe.m365_voice_failure_report()
        self.assertEqual(rep["counts"].get("dependency"), 2)
        self.assertEqual(rep["total"], 2)
        self.assertTrue(_s.rows, "必须留痕")

    def test_03_memory_sets_skip_window(self):
        _s = _Sink()
        pe.m365_note_voice_failure(_s, MemoryError(), "vosk.Model")
        rep = pe.m365_voice_failure_report()
        self.assertTrue(rep["skip_active"], rep)
        self.assertGreater(rep["skip_until"], time.time())

    def test_04_quiet_window_downgrades(self):
        """同类失败在抑制窗口内降 DEBUG（不刷 WARNING）。"""
        _s = _Sink()
        pe.m365_note_voice_failure(_s, ImportError("a"), "sd")
        pe.m365_note_voice_failure(_s, ImportError("b"), "sd")
        self.assertEqual(len(_s.rows), 2)
        _lv1, _lv2 = str(_s.rows[0][0]), str(_s.rows[1][0])
        self.assertIn("WARNING", _lv1)
        self.assertIn("DEBUG", _lv2)

    def test_05_skip_entry_when_window_active(self):
        """窗口内调用语音入口 ⇒ 只留一条"跳过"DEBUG 就返回（不加载模型、不重试）。"""
        pe._M365_VOICE_SKIP_UNTIL["ts"] = time.time() + 60
        _s = _Sink()
        pe.PulseEars._voice_listen_loop(_s)   # 只传 self 的轻量对象
        self.assertEqual(len(_s.rows), 1, _s.rows)
        self.assertIn("跳过", _s.rows[0][1])
        self.assertIn("DEBUG", str(_s.rows[0][0]))


if __name__ == "__main__":
    unittest.main()
