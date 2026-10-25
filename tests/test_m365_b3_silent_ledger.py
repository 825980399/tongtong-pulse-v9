# -*- coding: utf-8 -*-
"""365 刀B3 验收：静默 except 三形态 68 条渐进留痕台账（裁决丙）。

只守不变量：台账字段齐／分档合法／身份可由现仓复算（抽样 3 文件，不锁总行数）。
"""
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


def _load_tool():
    _p = os.path.join(_ROOT, "tools", "ci", "m365_silent_ledger.py")
    _spec = importlib.util.spec_from_file_location("m365_ledger_tool", _p)
    _m = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_m)
    return _m


class TestB3SilentLedger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(_LEDGER, encoding="utf-8") as f:
            cls.led = json.load(f)
        cls.tool = _load_tool()

    def test_01_schema_and_fields(self):
        self.assertEqual(self.led.get("schema"), "m365.silent_except_ledger.v1")
        self.assertGreater(len(self.led.get("rows") or []), 0)
        self.assertEqual(int(self.led["total"]), len(self.led["rows"]))
        for _r in self.led["rows"]:
            for _k in ("path", "qualname", "nth", "tname", "kind", "sha16",
                       "lineno", "tier_suggestion", "grade", "evidence_anchor"):
                self.assertIn(_k, _r, _k)
            self.assertIn(_r["kind"], ("pass", "ret", "assign", "cont", "break"))
            self.assertIn(_r["tier_suggestion"], ("A", "B", "C"))
            self.assertIn(_r["grade"], ("INFO", "WARNING"))
            self.assertEqual(len(_r["sha16"]), 16)

    def test_02_grade_rule(self):
        for _r in self.led["rows"]:
            self.assertEqual(_r["grade"], self.tool.grade_of(_r["tier_suggestion"]))

    def test_03_kind_counts_match_364(self):
        """364 前置实测：新增 cont 64 / break 4（严格超集，无 pass/ret/assign 新增）。"""
        self.assertEqual(int(self.led["by_kind"].get("cont", 0)), 64)
        self.assertEqual(int(self.led["by_kind"].get("break", 0)), 4)
        self.assertEqual(set(self.led["by_kind"]), {"cont", "break"})

    def test_04_identity_recomputable(self):
        """抽样 3 个文件：台账里的身份必须能由现仓重算出来（指纹稳定，非空中楼阁）。"""
        _scan = self.tool._load_scanner()
        _paths = sorted({r["path"] for r in self.led["rows"] if r["path"].endswith(".py")})[:3]
        self.assertTrue(_paths)
        for _rel in _paths:
            _src = _scan.read_src(_rel)
            _live = {self.tool.identity_of({
                "path": x["path"], "qualname": x["qualname"], "nth": x["nth"],
                "tname": x["tname"], "kind": x["kind"], "sha16": x["sha16"]})
                for x in _scan.extract_handlers_v2(_src, _rel)}
            _rows = [r for r in self.led["rows"] if r["path"] == _rel]
            self.assertTrue(_rows, _rel)
            _miss = [self.tool.identity_of(r) for r in _rows
                     if self.tool.identity_of(r) not in _live]
            self.assertEqual(_miss, [], "%s 有身份在现仓复算不到" % _rel)

    def test_05_diff_ignores_lineno(self):
        _a = [{"path": "a.py", "qualname": "f", "nth": 0, "tname": "OSError",
               "kind": "cont", "sha16": "x" * 16, "lineno": 10}]
        _b = [dict(_a[0], lineno=99)]
        _d = self.tool.diff_ledger(_a, _b)
        self.assertEqual((len(_d["added"]), len(_d["removed"])), (0, 0))


if __name__ == "__main__":
    unittest.main()
