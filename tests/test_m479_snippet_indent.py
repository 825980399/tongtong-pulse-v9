# -*- coding: utf-8 -*-
"""★479：补 422 批 `_m422_clip_statement_blocks` 的覆盖缺口 —— 首行带缩进。

## 为什么必须单独守这一条

422 批的核心承诺是「按完整语句块裁剪，不腰斩 try/if/for」，
但它的测试**只用了顶格代码**（`def f():` 前无空格），于是漏掉了一个致命场景：

    _read_snippet_from_file(file, method)
      → 找到 "    def xxx(self):" 所在行（类方法，前有 4 格）
      → _m422_clip_statement_blocks("".join(lines[i:]))
      → 旧实现 `return "".join(_out).strip()`  ← **剥掉那 4 格**
    ⇒ 得到「def 顶格 + 方法体 8 格」
    ⇒ ast.parse: unindent does not match any outer indentation level

实测后果（本机 2026-11-08 21:5x，改前）：
    PulseSystemManager._schedule_health_check        1192 字 / parse FAIL
    PulseSubconscious._schedule_next_exploration     1613 字 / parse FAIL
    PulseSubconscious._schedule_dream_timer           820 字 / parse FAIL
    PulseLegs.start                                  1419 字 / parse OK（模块级，本就无缩进）
⇒ 所有**类方法**的 code_snippet 都是语法不通的残缺代码
⇒ LLM 照它改 → full_method=false → 469 粒度对齐必拦 → 修复率恒 0
⇒ 同一问题下一轮再送进 LLM（22 轮 ALL 协作里 _m374_schedule_ahead ×4 等）

改后同样四个方法：4/4 parse_ok=True / full_method=True / score=100。
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.autonomy.all_collab import score_code
from nucleus.reasoning.SafeEvolutionExecutor import (
    SafeEvolutionExecutor,
    _m422_clip_statement_blocks,
)


def _first_indent(text: str) -> int:
    _l = str(text or "").splitlines()
    return len(_l[0]) - len(_l[0].lstrip()) if _l else -1


class TestClipKeepsFirstLineIndent(unittest.TestCase):
    """核心：首行（方法定义行）的缩进**必须保留**。"""

    def test_01_class_method_indent_kept(self):
        """类方法（首行 4 格）—— 这是修前必然出错的场景。"""
        _s = ("    def f(self):\n"
              "        return 1\n")
        _out = _m422_clip_statement_blocks(_s, 40, 2000)
        self.assertEqual(_first_indent(_out), 4,
                         "首行缩进被剥掉了 ⇒ 方法体与 def 错位 ⇒ SyntaxError")
        self.assertEqual(_out, "    def f(self):\n        return 1")

    def test_02_nested_method_indent_kept(self):
        """嵌套类里的方法（首行 8 格）。"""
        _s = ("        def g(self):\n"
              "            return 2\n")
        _out = _m422_clip_statement_blocks(_s, 40, 2000)
        self.assertEqual(_first_indent(_out), 8)

    def test_03_clipped_class_method_indent_kept(self):
        """★超预算走「第二层」时，首行缩进同样必须保留。"""
        _body = "".join(f"        x{i} = {i}\n" for i in range(60))
        _s = "    def big(self):\n" + _body
        _out = _m422_clip_statement_blocks(_s, 20, 2000)
        self.assertEqual(_first_indent(_out), 4,
                         "第二层（首块超预算）也剥了首行缩进")
        self.assertLess(len(_out.splitlines()), len(_s.splitlines()),
                        "应真的裁剪过（否则这条测不到第二层）")

    def test_04_toplevel_indent_still_zero(self):
        """反证：顶格代码不该被加缩进（不许为修 bug 引入新偏差）。"""
        _s = "def f():\n    return 1\n"
        self.assertEqual(_first_indent(_m422_clip_statement_blocks(_s, 40, 2000)), 0)

    def test_05_no_leading_blank_lines(self):
        """开头的空行要去掉（保留原 fast-path 行为）。"""
        _out = _m422_clip_statement_blocks("\n\n\ndef f():\n    return 1\n", 40, 2000)
        self.assertFalse(_out.startswith("\n"))
        self.assertTrue(_out.startswith("def f():"))

    def test_06_trailing_whitespace_trimmed(self):
        _out = _m422_clip_statement_blocks("def f():\n    return 1\n\n\n", 40, 2000)
        self.assertFalse(_out.endswith("\n"))

    def test_07_class_method_parses_via_flex(self):
        """截出的类方法必须能被 score_code 的 flex 解析认成完整方法。"""
        _s = ("    def f(self):\n"
              "        try:\n"
              "            return 1\n"
              "        except ValueError as _e:\n"
              "            silent_exc(_e, where='x')\n"
              "            return 0\n")
        _out = _m422_clip_statement_blocks(_s, 40, 2000)
        _d = score_code(_out, original=_out, method="f")
        self.assertTrue(_d["parse_ok"], _d.get("parse_error"))
        self.assertTrue(_d["full_method"])


class TestReadSnippetEndToEnd(unittest.TestCase):
    """端到端：真实文件 + 真实类方法 ⇒ 必须是可用的完整方法。"""

    def _inst(self):
        _o = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        _o._project_root = ROOT
        return _o

    def test_10_real_class_method_is_usable(self):
        """★核心不变量：读出来的片段必须 parse_ok 且 full_method。"""
        _o = self._inst()
        _sn = _o._read_snippet_from_file(
            "organs/core/PulseSystemManager.py", "_schedule_health_check")
        self.assertTrue(_sn, "片段为空")
        _d = score_code(_sn, original="", method="_schedule_health_check")
        self.assertTrue(_d["parse_ok"],
                        "片段语法不通 ⇒ LLM 拿到残缺代码: " + str(_d.get("parse_error")))
        self.assertTrue(_d["full_method"], "片段不是完整方法")
        self.assertEqual(_first_indent(_sn), 4, "首行缩进必须是 4（类方法）")

    def test_11_multiple_real_methods_all_usable(self):
        _o = self._inst()
        for _f, _m in (("organs/brain/PulseSubconscious.py",
                        "_schedule_next_exploration"),
                       ("organs/brain/PulseSubconscious.py",
                        "_schedule_dream_timer"),
                       ("organs/motor/PulseLegs.py", "start")):
            _sn = _o._read_snippet_from_file(_f, _m)
            self.assertTrue(_sn, f"{_f}:{_m} 片段为空")
            _d = score_code(_sn, original="", method=_m)
            self.assertTrue(_d["parse_ok"],
                            f"{_f}::{_m} 语法不通: {_d.get('parse_error')}")
            self.assertTrue(_d["full_method"], f"{_f}::{_m} 不是完整方法")

    def test_12_file_head_read_still_works(self):
        """无 method 名 ⇒ 读文件头，必须无前导缩进（不许把整文件当方法）。"""
        _o = self._inst()
        _sn = _o._read_snippet_from_file("organs/core/PulseSystemManager.py", "")
        self.assertTrue(_sn)
        self.assertEqual(_first_indent(_sn), 0)
        self.assertTrue(ast.parse(_sn) is not None, "文件头应能裸解析")

    def test_13_missing_method_falls_back_to_head(self):
        """方法不存在 ⇒ 退回文件头（既有行为，不许改成返回空）。"""
        _o = self._inst()
        _sn = _o._read_snippet_from_file("organs/core/PulseSystemManager.py",
                                         "_no_such_method_xyz_479")
        self.assertTrue(_sn, "应退回文件头而不是返回空")
        self.assertEqual(_first_indent(_sn), 0)

    def test_14_source_has_no_lstrip_on_first_line(self):
        """接线防回潮：`_m422_clip_statement_blocks` 体内不许再出现 `.strip()`。

        ★只查该函数体（用 AST 定位行范围）—— 文件其它地方的 `_t.strip()`
        是无关函数的正常用法，全局 grep 会误报（第一次写这条就误报了 4978 行）。
        """
        _p = os.path.join(ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
        with open(_p, encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        _tree = ast.parse(_src)
        _fn = None
        for _n in _tree.body:
            if (isinstance(_n, ast.FunctionDef)
                    and _n.name == "_m422_clip_statement_blocks"):
                _fn = _n
                break
        self.assertIsNotNone(_fn, "函数不见了")
        _body = "".join(_src.splitlines(True)[_fn.lineno - 1:_fn.end_lineno])
        self.assertNotIn(".strip()", _body,
                         "clip 函数体内又用了 .strip() ⇒ 会剥掉类方法首行缩进")
        self.assertIn(".rstrip().lstrip(", _body, "两条返回路径都应保留首行缩进")


class TestPathBoundary:
    """★479 收尾③：越界判据必须用路径语义，不能用字符串前缀。

    旧写法 `_p.startswith(_root)` 会把 `project-root-other/x.py` 判成「在根内」
    （前缀字符串相同）⇒ 能读到项目外的文件。改为 `os.path.relpath` + 检查 `..`。
    """

    def _inst(self):
        _o = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        _o._project_root = ROOT
        return _o

    def test_20_inside_readable(self):
        _sn = self._inst()._read_snippet_from_file(
            os.path.join(ROOT, "organs", "core", "PulseSystemManager.py"), "")
        assert _sn, "项目内文件应可读"

    def test_21_prefix_sibling_rejected(self):
        """★核心：同前缀兄弟目录必须拒绝（旧实现会放行）。"""
        _sn = self._inst()._read_snippet_from_file(ROOT + "-other/x.py", "")
        assert _sn == "", "同前缀兄弟目录被放行 ⇒ 能读项目外文件"

    def test_22_parent_escape_rejected(self):
        _sn = self._inst()._read_snippet_from_file(
            os.path.join(ROOT, "..", "..", "Windows", "win.ini"), "")
        assert _sn == ""

    def test_23_cross_drive_rejected(self):
        _sn = self._inst()._read_snippet_from_file(r"C:\Windows\win.ini", "")
        assert _sn == ""

    def test_24_dotdot_inside_root_allowed(self):
        """经 `..` 再回来、归一后仍在根内 ⇒ 应允许（不许因噎废食）。"""
        _p = os.path.join(ROOT, "..", os.path.basename(ROOT),
                          "organs", "core", "PulseSystemManager.py")
        _sn = self._inst()._read_snippet_from_file(_p, "")
        assert _sn, "归一后在根内的路径应可读"

    def test_25_source_uses_relpath(self):
        with open(os.path.join(ROOT, "nucleus", "reasoning",
                               "SafeEvolutionExecutor.py"),
                  encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        assert 'os.path.relpath(_p, _root)' in _src
        assert "os.pardir" in _src
        # 旧写法不许回来
        assert "if not _p.startswith(_root):" not in _src, \
            "字符串前缀判据回来了 ⇒ 同前缀兄弟目录又能绕过"


if __name__ == "__main__":
    unittest.main()
