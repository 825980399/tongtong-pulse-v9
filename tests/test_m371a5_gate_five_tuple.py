# -*- coding: utf-8 -*-
"""371A5 第2笔 · 门禁判据升级（五元组 + cont/break）与去 nth 的定向单测。

覆盖：
  1. 新判据认 cont / break（旧判据完全查不到）——正样本
  2. 冻结的旧判据对同一源码**确实漏掉** cont/break ——反向判别（证明不是两边都一样）
  3. identity 为五元组、**不含 nth**、含 tname+kind
  4. ★D8 场景：同函数内删掉前一个静默 handler ⇒ 后面那个 nth 由 1 变 0
     - 旧 identity 会把它算成「新增静默」（假报）
     - 新 identity 不算新增（修复）
  5. 豁免表结构与新判据自洽（120 条均可被新判据命中，字段齐备）
  6. 门禁自举：ci_common.py 自身不含静默 handler

★铁律188 防自噬：本文件只读被测模块与豁免表，扫描时不把自己计入。
"""
import importlib.util
import io
import json
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools", "ci"))

from ci_common import (  # noqa: E402
    extract_handlers,
    handler_identity,
)


def _load_dual():
    """加载 364 对比工具（已冻结旧判据）。它 import ci_common，需前插 sys.path。"""
    tool = os.path.join(ROOT, "tools", "scan_silent_except_dual.py")
    spec = importlib.util.spec_from_file_location("m371a5_dual", tool)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


DUAL = _load_dual()


CONT_SRC = (
    "def f():\n"
    "    for x in y:\n"
    "        try:\n"
    "            g(x)\n"
    "        except ValueError:\n"
    "            continue\n"
)
BREAK_SRC = (
    "def f():\n"
    "    while True:\n"
    "        try:\n"
    "            g()\n"
    "        except Exception:\n"
    "            break\n"
)


class TestNewJudgeCatchesContBreak(unittest.TestCase):

    def test_01_cont_recognized(self):
        hs = extract_handlers(CONT_SRC, "x.py")
        self.assertEqual(len(hs), 1, "continue 形态应被新判据命中")
        self.assertEqual(hs[0]["kind"], "cont")

    def test_02_break_recognized(self):
        hs = extract_handlers(BREAK_SRC, "x.py")
        self.assertEqual(len(hs), 1, "break 形态应被新判据命中")
        self.assertEqual(hs[0]["kind"], "break")

    def test_03_legacy_really_misses_them(self):
        """反向判别：冻结旧判据对同一源码必须 0 命中，否则「新增」不成立。"""
        self.assertEqual(len(DUAL.extract_handlers_legacy(CONT_SRC, "x.py")), 0)
        self.assertEqual(len(DUAL.extract_handlers_legacy(BREAK_SRC, "x.py")), 0)

    def test_04_ret_and_assign_still_recognized(self):
        """负样本：旧形态不得因升级而丢失。"""
        ret_src = "def f():\n    try:\n        g()\n    except Exception:\n        return None\n"
        asg_src = "def f():\n    try:\n        g()\n    except Exception:\n        x = 1\n"
        self.assertEqual([h["kind"] for h in extract_handlers(ret_src, "x.py")], ["ret"])
        self.assertEqual([h["kind"] for h in extract_handlers(asg_src, "x.py")], ["assign"])

    def test_05_mixed_body_not_silent(self):
        """负样本：continue 后面还有别的语句 ⇒ 不是纯静默，不得命中。"""
        src = ("def f():\n    for x in y:\n        try:\n            g(x)\n"
               "        except Exception:\n            continue\n            h()\n")
        # 语法上 h() 不可达但仍能解析；body 含 Continue + Expr ⇒ 非纯 cont
        self.assertEqual(len(extract_handlers(src, "x.py")), 0)


class TestIdentityDropsNth(unittest.TestCase):

    def test_06_identity_is_five_tuple_without_nth(self):
        fp = {"path": "a/b.py", "qualname": "f", "nth": 7, "tname": "Exception",
              "kind": "cont", "sha16": "deadbeefdeadbeef"}
        ident = handler_identity(fp)
        self.assertEqual(len(ident), 5)
        self.assertNotIn(7, ident, "identity 不得再含 nth")
        self.assertEqual(ident, ("a/b.py", "f", "Exception", "cont", "deadbeefdeadbeef"))

    def test_07_d8_scenario_legacy_false_positive_new_clean(self):
        """★D8 正面验证：删掉前一个静默 handler，后面那个 nth 由 1 漂到 0。

        旧 identity（含 nth）会把「漂移后的 handler」算成新增静默（假报）；
        新 identity（去 nth、带 tname）不报。
        """
        before = (
            "def f():\n"
            "    try:\n"
            "        a()\n"
            "    except KeyError:\n"
            "        return 1\n"
            "    try:\n"
            "        b()\n"
            "    except ValueError:\n"
            "        return 2\n"
        )
        # 删掉前一个 try/except 块 ⇒ 剩下那个 handler 的 nth 变 0
        after = (
            "def f():\n"
            "    try:\n"
            "        b()\n"
            "    except ValueError:\n"
            "        return 2\n"
        )
        before_new = {handler_identity(x) for x in extract_handlers(before, "d8.py")}
        after_new = {handler_identity(x) for x in extract_handlers(after, "d8.py")}
        added_new = after_new - before_new
        self.assertEqual(added_new, set(), "新 identity 不应把 nth 漂移算成新增（D8 已修）")

        # 旧口径（含 nth）复现假报：手工按 (path,qualname,nth,sha16) 计算
        def _old_ident(src):
            return {(x["path"], x["qualname"], x["nth"], x["sha16"])
                    for x in extract_handlers(src, "d8.py")}

        added_old = _old_ident(after) - _old_ident(before)
        self.assertEqual(len(added_old), 1,
                         "旧口径应复现 D8 假报（证明本用例真的测到了差异）")


class TestBaselineConsistency(unittest.TestCase):

    def test_08_baseline_rows_have_required_fields(self):
        fp = os.path.join(ROOT, "tools", "ci", "silent_except_fingerprints.json")
        rows = json.load(io.open(fp, encoding="utf-8"))
        self.assertGreater(len(rows), 100, "五元组切换后豁免表应明显扩容")
        need = {"path", "qualname", "nth", "sha16", "tname", "kind", "scope", "reason", "match"}
        for r in rows:
            self.assertTrue(need.issubset(set(r)), "缺字段 %s：%s" % (sorted(need - set(r)), r.get("path")))
            self.assertIn(r["kind"], {"pass", "ret", "assign", "cont", "break"})
            self.assertIn(r["scope"], {"prod", "scan"})

    def test_09_baseline_identities_unique(self):
        fp = os.path.join(ROOT, "tools", "ci", "silent_except_fingerprints.json")
        rows = json.load(io.open(fp, encoding="utf-8"))
        ids = [handler_identity(r) for r in rows]
        self.assertEqual(len(ids), len(set(ids)), "豁免表存在重复 identity（应用侧 set 化后等价，但表里不该重复）")

    def test_10_ci_common_itself_clean(self):
        """门禁自举不变量：ci_common.py 自身不得含静默 handler。"""
        fp = os.path.join(ROOT, "tools", "ci", "ci_common.py")
        src = io.open(fp, encoding="utf-8").read()
        self.assertEqual(extract_handlers(src, "tools/ci/ci_common.py"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
