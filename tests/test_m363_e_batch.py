# -*- coding: utf-8 -*-
"""363 批 E 组验收（E5 视觉流改名占用收口）。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from organs.senses.PulseVisualCortex import PulseVisualCortex  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _vc() -> PulseVisualCortex:
    p = PulseVisualCortex.__new__(PulseVisualCortex)
    p._log = lambda *a, **k: None
    return p


class TestE5VisualRename(unittest.TestCase):
    def test_01_initial_stats_are_zero(self):
        p = _vc()
        self.assertEqual(p.m363_visual_write_stats()["rename_fail"], 0)
        self.assertEqual(p.m363_visual_write_stats()["last"], {})

    def test_02_fail_counted_with_snapshot(self):
        p = _vc()
        _d = tempfile.mkdtemp()
        _f = os.path.join(_d, "vision_stream_log.json")
        with open(_f, "w", encoding="utf-8") as f:
            f.write("[]")
        p._m363_note_rename_fail(_f, OSError("WinError 5 拒绝访问"))
        p._m363_note_rename_fail(_f, OSError("WinError 5 拒绝访问"))
        s = p.m363_visual_write_stats()
        self.assertEqual(s["rename_fail"], 2, "丢几次必须可数")
        self.assertEqual(s["last"]["type"], "OSError")
        self.assertIn("WinError 5", s["last"]["msg"])
        self.assertEqual(s["last"]["path"], _f)

    def test_03_occupancy_probe_reports_false_when_openable(self):
        _d = tempfile.mkdtemp()
        _f = os.path.join(_d, "openable.json")
        with open(_f, "w", encoding="utf-8") as f:
            f.write("[]")
        p = _vc()
        p._m363_note_rename_fail(_f, OSError("x"))
        self.assertFalse(p.m363_visual_write_stats()["last"]["target_locked"],
                         "目标可打开 ⇒ 不得谎报被占用")

    def test_04_occupancy_probe_reports_true_when_unopenable(self):
        """打不开（不存在/被占）⇒ target_locked=True，供判断是否为框架外占用方。"""
        p = _vc()
        p._m363_note_rename_fail(os.path.join(tempfile.gettempdir(), "no_such_363.json"),
                                 OSError("x"))
        self.assertTrue(p.m363_visual_write_stats()["last"]["target_locked"])

    def test_05_read_export_is_copy(self):
        p = _vc()
        s = p.m363_visual_write_stats()
        s["rename_fail"] = 999
        self.assertEqual(p.m363_visual_write_stats()["rename_fail"], 0)

    def test_06_fallback_write_wired(self):
        """rename 被拒必须有回退写，不能让整批条目随临时文件一起消失。"""
        src = open(os.path.join(_ROOT, "organs", "senses", "PulseVisualCortex.py"),
                   encoding="utf-8").read()
        self.assertIn("except OSError as _re363:", src)
        self.assertIn("已回退为整体重写", src)
        self.assertIn("[363E5] 视觉流回退写也失败（本批条目丢失）", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
