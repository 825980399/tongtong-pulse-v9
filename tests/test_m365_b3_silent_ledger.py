# -*- coding: utf-8 -*-
"""365 刀B3 验收：静默 except 三形态 68 条渐进留痕台账（裁决丙）。

只守不变量：台账字段齐／分档合法／身份可由现仓复算（抽样 3 文件，不锁总行数）。
"""
from __future__ import annotations

import ast
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
        # ★371A5 订正：366 N3（`da1f0ad1`）已把台账升为 v2（6 条 A 档人工确认＋
        #   控制流型豁免元数据＋emit 元数据保真），本断言当时未同步 ⇒ 长期假红。
        self.assertEqual(self.led.get("schema"), "m365.silent_except_ledger.v2")
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
        """抽样 3 个文件：台账条目必须能在现仓找到对应 handler（结构未脱节）。

        ★371A5 订正（既有缺陷，非本笔引入）：原实现用 identity（含 body sha16）复算，
        但 369 刀4 给台账每条都补了 `silent_exc(...)` 留痕 ⇒
          ① body 变 ⇒ sha16 必变；
          ② 含 silent_exc ⇒ `_has_report` 判为已上报 ⇒ 不在 extract_handlers_v2 结果里。
        两者叠加使**已留痕条目必然复算不到**；369 当时抽样恰好没命中这类文件，长期假绿。
        改为与 test_02 同口径：按 (qualname, tname) 在完整 AST 枚举里定位，
        并要求该 handler 确实带 silent_exc 留痕（等于是"台账所说的留痕仍在"）。
        """
        _scan = self.tool._load_scanner()
        _checked = 0
        _paths = sorted({r["path"] for r in self.led["rows"]
                         if r["path"].endswith(".py") and not r.get("retired_by")})[:3]
        self.assertTrue(_paths)
        for _rel in _paths:
            _src = _scan.read_src(_rel)
            _tree = ast.parse(_src)
            _funcs = [(n.lineno, n.end_lineno or n.lineno, n.name)
                      for n in ast.walk(_tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            _all = [n for n in ast.walk(_tree) if isinstance(n, ast.ExceptHandler)]
            # 排除退役与豁免类：exemption/manual_review 条目本就不要求 silent_exc 留痕
            _rows = [r for r in self.led["rows"]
                     if r["path"] == _rel
                     and not r.get("retired_by")
                     and not (r.get("manual_review") or r.get("exemption"))]
            if not _rows:
                # 该文件条目全部为退役/豁免类 ⇒ 无可校验项，跳过不算缺失
                continue
            _checked += len(_rows)
            _miss = []
            for r in _rows:
                _hit = False
                for h in _all:
                    if _scan._enclosing_func(_funcs, h.lineno) != r["qualname"]:
                        continue
                    if (ast.unparse(h.type) if h.type else "bare") != r["tname"]:
                        continue
                    _names = set()
                    for c in ast.walk(h):
                        if isinstance(c, ast.Call):
                            _f = c.func
                            _names.add(_f.attr if isinstance(_f, ast.Attribute)
                                       else (_f.id if isinstance(_f, ast.Name) else ""))
                    if "silent_exc" not in _names:
                        continue
                    _hit = True
                    break
                if not _hit:
                    _miss.append((r["qualname"], r["tname"]))
            self.assertEqual(_miss, [], "%s 有台账条目在现仓找不到带留痕的 handler" % _rel)
        self.assertGreater(_checked, 0, "抽样未覆盖任何可校验条目（防空跑假通过）")

    def test_05_diff_ignores_lineno(self):
        _a = [{"path": "a.py", "qualname": "f", "nth": 0, "tname": "OSError",
               "kind": "cont", "sha16": "x" * 16, "lineno": 10}]
        _b = [dict(_a[0], lineno=99)]
        _d = self.tool.diff_ledger(_a, _b)
        self.assertEqual((len(_d["added"]), len(_d["removed"])), (0, 0))


if __name__ == "__main__":
    unittest.main()
