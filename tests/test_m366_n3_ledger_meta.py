# -*- coding: utf-8 -*-
"""366 刀N3 验收：A5 主体前置收口（6 条 A 档人工确认／控制流型豁免元数据／零漂移／元数据保真）。"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LEDGER = os.path.join(_ROOT, "tools", "ci", "baselines",
                       "m365_silent_except_ledger.json")
_TOOL = os.path.join(_ROOT, "tools", "ci", "m365_silent_ledger.py")

A_SIX = {
    ("nucleus/evolution/criteria_window.py", "write_ledger_conclusions"),
    ("nucleus/evolution/zero_break_judge.py", "write_calibration_ledger"),
    ("nucleus/llm/call_recorder.py", "_rotate"),
    ("nucleus/llm/data_quality_evaluator.py", "load_records"),
    ("nucleus/mnemosyne/PulseSnapshot.py", "cleanup_temp_snapshot_residue"),
    ("nucleus/self_awareness/daily_reader.py", "_read_last_queue_stats"),
}


def _load_tool():
    _spec = importlib.util.spec_from_file_location("m366_ledger_tool", _TOOL)
    _m = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_m)
    return _m


class TestN3LedgerMeta(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(_LEDGER, encoding="utf-8") as f:
            cls.led = json.load(f)
        cls.tool = _load_tool()
        cls.by_key = {(r.get("path"), r.get("qualname")): r for r in cls.led["rows"]}

    # ① 6 条 A 档人工结论齐备（含 verdict/basis/复核人/时点/HEAD 行号）
    def test_01_six_manual_reviews(self):
        for _k in A_SIX:
            _r = self.by_key.get(_k)
            self.assertIsNotNone(_r, "台账缺条目：%s" % (_k,))
            _mr = _r.get("manual_review") or {}
            self.assertIn(_mr.get("verdict"), ("A", "B", "C"), _k)
            self.assertTrue(str(_mr.get("basis") or "").strip(), _k)
            self.assertEqual(_mr.get("reviewed_by"), "路灯", _k)
            self.assertEqual(_mr.get("reviewed_at"), "2026-10-25", _k)
            self.assertIsInstance(_mr.get("head_lineno"), int, _k)
        # 台账自报计数与实测一致
        self.assertEqual(int(self.led.get("manual_review_n") or 0), len(A_SIX))

    # ② 控制流型豁免元数据字段齐（kind/reason/dep/reviewed_at）
    def test_02_control_flow_exemptions(self):
        _cf = [r for r in self.led["rows"] if r.get("exemption")]
        self.assertGreater(len(_cf), 0)
        for _r in _cf:
            _e = _r["exemption"]
            self.assertEqual(_e.get("kind"), "control_flow")
            self.assertTrue(str(_e.get("reason") or "").strip())
            self.assertTrue(str(_e.get("dep") or "").strip())
            self.assertEqual(_e.get("reviewed_at"), "2026-10-25")
        self.assertEqual(int(self.led.get("control_flow_exempt_n") or 0), len(_cf))

    # ③ 零漂移：现仓身份集与台账完全一致（added=0 / removed=0）
    def test_03_zero_drift(self):
        _live = self.tool.build_ledger(preserve=False)
        _d = self.tool.diff_ledger(self.led["rows"], _live["rows"])
        self.assertEqual(_d["added"], [])
        self.assertEqual(_d["removed"], [])
        # ★399刀1：`diff_ledger` 两侧都跳过已退役行（`retired_by` 非空）⇒
        #   `same` 等于**在册**条数，不是全集条数。退役行的正确性由
        #   `test_m369_blade4` / `test_m399_k1` 按 `retired_reason` 单独验。
        _live_n = len([r for r in self.led["rows"] if not r.get("retired_by")])
        self.assertEqual(_d["same"], _live_n)

    # ④ 元数据保真：重算（--emit 同径）不得抹掉人工结论
    def test_04_meta_preserved_on_rebuild(self):
        _live = self.tool.build_ledger(preserve=True)
        _keys = {(r.get("path"), r.get("qualname")) for r in _live["rows"]
                 if r.get("manual_review")}
        self.assertTrue(A_SIX.issubset(_keys), A_SIX - _keys)
        _cf = [r for r in _live["rows"] if r.get("exemption")]
        self.assertGreater(len(_cf), 0)


if __name__ == "__main__":
    unittest.main()
