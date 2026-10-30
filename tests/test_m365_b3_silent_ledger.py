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
        """★399刀1 起守**不变量**，不再钉 364 那一刻的 64/4。

        364 的结论是"新判据是旧判据的**严格超集**，且新增只有 cont/break 两形态
        （没有 pass/ret/assign）"—— 这条**结构**结论仍然成立、仍然值得守。
        但 cont/break 的条数会随修复进度下降（399 实测 68 → 10，58 条已修），
        钉死 `== 64` 就是拿历史快照当判据（铁律 268），必然红。
        """
        _bk = self.led["by_kind"]
        _allowed = set(("cont", "break"))
        self.assertTrue(set(_bk) <= _allowed,
                        f"出现了 pass/ret/assign 形态：{sorted(set(_bk) - _allowed)}")
        # 自报计数必须与 rows 同源（不能另写一份）
        _real = {}
        for _r in self.led["rows"]:
            _real[_r["kind"]] = int(_real.get(_r["kind"], 0)) + 1
        self.assertEqual({k: int(v) for k, v in _bk.items()}, _real)
        # 只降不升：修复只会让待办变少，涨了就说明新判据抓到了新的静默 handler
        self.assertLessEqual(int(_bk.get("cont", 0)), 64)
        self.assertLessEqual(int(_bk.get("break", 0)), 4)

    def test_04_identity_recomputable(self):
        """结构未脱节：**在册**条目必须仍在现仓 AST 里；**结案**条目的归因必须能复算。

        ★399刀1 重写（原实现按"抽样 3 个文件里找带 silent_exc 的 handler"校验）：
        重跑 emit 后在册只剩 10 条，且**全部**是 `manual_review` / `exemption` 类
        （本就不要求 silent_exc 留痕）⇒ 旧口径的 `_checked` 恒为 0，判据变成空跑。
        改为两条各自成立的口径：
          ① 在册行：qualname 仍在对应文件的函数枚举里（台账没记到不存在的东西上）；
          ② 结案行：`retired_reason == "fixed"` 的必须真的在函数体内调了 `silent_exc`
             —— 否则"已修好"就是猜的。
        """
        _scan = self.tool._load_scanner()
        _rows = self.led["rows"]
        self.assertGreater(len(_rows), 0)

        # ① 在册行的结构存在性
        _trees = {}
        _miss = []
        for _r in _rows:
            _rel = _r["path"]
            if _rel not in _trees:
                _trees[_rel] = ast.parse(_scan.read_src(_rel))
            _names = set()
            for _n in ast.walk(_trees[_rel]):
                if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _names.add(_n.name)
            if str(_r.get("qualname") or "").split(".")[-1] not in _names:
                _miss.append((_rel, _r.get("qualname")))
        self.assertEqual(_miss, [], "台账在册条目在现仓找不到对应函数（结构已脱节）")

        # ② 结案行的归因复算（抽样 ≤5 条，够证明"reason 不是拍脑袋填的"）
        #    ★399刀1：退役行**仍在 `rows` 里**（`retired_by` 非空），另有 `retired` 数组
        _fixed = [r for r in self.led["rows"]
                  if r.get("retired_reason") == "fixed"][:5]
        self.assertTrue(_fixed, "没有 retired_reason=fixed 的样本，这条判据无法验证")
        _bad = []
        for _r in _fixed:
            _tree = ast.parse(_scan.read_src(_r["path"]))
            _fn = self.tool._find_func_node(_tree, str(_r.get("qualname") or ""))
            if _fn is None or not self.tool._calls_name(_fn, "silent_exc"):
                _bad.append((_r["path"], _r.get("qualname")))
        self.assertEqual(_bad, [], "标了 fixed 但函数体内找不到 silent_exc（归因不实）")

    def test_05_diff_ignores_lineno(self):
        _a = [{"path": "a.py", "qualname": "f", "nth": 0, "tname": "OSError",
               "kind": "cont", "sha16": "x" * 16, "lineno": 10}]
        _b = [dict(_a[0], lineno=99)]
        _d = self.tool.diff_ledger(_a, _b)
        self.assertEqual((len(_d["added"]), len(_d["removed"])), (0, 0))


if __name__ == "__main__":
    unittest.main()
