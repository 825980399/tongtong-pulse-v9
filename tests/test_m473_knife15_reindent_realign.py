# -*- coding: utf-8 -*-
"""第473批 刀15 门控单测：入队时把完整方法对齐回原文基础缩进。

病灶（2026-11-07 实测）
------------------------
`PatchManager._m323_reindent` 只做「剥公共缩进」，对**含 def 的完整方法**也照剥
（`    def` → `def`）。而入队前 `original_code` 仍是文件里的 4/8 格，于是存盘后

    base(modified) = 0  ≠  base(original) = 4/8

验证侧（`_verify_in_copy`）与应用侧（`apply_all_pending`）各有一道
`_m92_base_indent_guard`，两侧都会拒。`patch_history.json` 里 4 条 `approved`
**全部** `applied=False`，其中 3 条的 `apply_error` 就是
「基础缩进不一致（未替换）: original=8/4 modified=0」。

判据
----
* 两侧都含 def（整方法替换）且 base 不等 ⇒ 整体平移到 original 的 base；
* 片段（无 def）/ 原文无 def（O3 删除类）⇒ 不处理；
* 平移**跳过三引号字符串的内部续行**（那里是字符串内容，平移等于改语义）；
* 平移后必须能通过 AST 校验（套类壳，4 格 def 单独 parse 必失败）；
* 灰度 `ENABLE_M473_REINDENT_REALIGN=False` ⇒ 逐字回到刀15 之前。
"""

from __future__ import annotations

import ast
import io
import os
import sys
import tokenize
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
from nucleus.reasoning.PatchManager import PatchManager, _m92_base_indent

#: 一个「类里有多个方法」的自包含样本（不依赖生产仓库，避免数据变动导致脆测）
_CLS_BODY = '''class Demo:
    def before(self):
        return 1

{target}

    def after(self):
        return 3
'''

_ORIG_TARGET = '''    def target(self):
        """落盘。

        Returns: 路径。
        """
        try:
            with open("a.json", "w") as f:
                f.write("{}")
            return "a.json"
        except Exception:
            return ""
'''

_MOD_TARGET_FLAT = '''def target(self):
    try:
        with open("a.json", "w") as f:
            f.write("{}")
        return "a.json"
    except Exception:
        return ""
'''

_MOD_TARGET_DOC = '''def target(self):
    """落盘。

        缩进说明写在第二行，右移时必须逐字不变。
    """
    return 1
'''


def _set_switch(on: bool | None):
    """设置灰度开关；None 表示删除属性（回落 getattr 默认值）。"""
    _old = getattr(config, "ENABLE_M473_REINDENT_REALIGN", None)
    if on is None:
        if hasattr(config, "ENABLE_M473_REINDENT_REALIGN"):
            delattr(config, "ENABLE_M473_REINDENT_REALIGN")
    else:
        config.ENABLE_M473_REINDENT_REALIGN = on
    return _old


def _restore_switch(old):
    if old is None:
        if hasattr(config, "ENABLE_M473_REINDENT_REALIGN"):
            delattr(config, "ENABLE_M473_REINDENT_REALIGN")
    else:
        config.ENABLE_M473_REINDENT_REALIGN = old


def _methods_of(src: str, cls: str = "Demo") -> list[str]:
    """独立基准：用 ast 数类里的方法名（不经过被测函数）。"""
    for _n in ast.parse(src).body:
        if isinstance(_n, ast.ClassDef) and _n.name == cls:
            return [m.name for m in _n.body
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return []


def _string_literals(src: str) -> list[str]:
    """取出全部字符串字面量的**内容**，用于断言平移没碰字符串。"""
    _out = []
    for _t in tokenize.generate_tokens(io.StringIO(src).readline):
        if _t.type == tokenize.STRING:
            _out.append(_t.string)
    return _out


class TestShiftIndent(unittest.TestCase):
    """纯平移函数：只动语句缩进，不动字符串内容。"""

    def test_1_right_shift_keeps_relative_levels(self):
        _src = "def f(self):\n    return 1\n"
        _out = PatchManager._m473_shift_indent(_src, 4)
        self.assertEqual(_out, "    def f(self):\n        return 1\n")

    def test_2_left_shift(self):
        _src = "    def f(self):\n        return 1\n"
        _out = PatchManager._m473_shift_indent(_src, -4)
        self.assertEqual(_out, "def f(self):\n    return 1\n")

    def test_3_blank_lines_get_no_trailing_spaces(self):
        _src = "def f(self):\n    return 1\n\n    x = 2\n"
        _out = PatchManager._m473_shift_indent(_src, 4)
        for _ln in _out.split("\n"):
            self.assertEqual(_ln, _ln.rstrip(), f"空行被塞了尾随空格: {_ln!r}")

    def test_4_multiline_string_body_is_untouched(self):
        """跨行三引号字符串的内部续行**逐字不变**（平移会改语义）。"""
        _before = _string_literals(_MOD_TARGET_DOC)
        _out = PatchManager._m473_shift_indent(_MOD_TARGET_DOC, 4)
        _after = _string_literals(_out)
        self.assertEqual(_before, _after, "字符串内容被平移改动了")
        self.assertTrue(_out.startswith("    def target(self):"), _out[:40])
        ast.parse("class _Shim:\n" + _out)

    def test_5_zero_delta_is_noop(self):
        self.assertEqual(PatchManager._m473_shift_indent(_MOD_TARGET_FLAT, 0),
                         _MOD_TARGET_FLAT)

    def test_6_empty_is_noop(self):
        self.assertEqual(PatchManager._m473_shift_indent("", 4), "")

    def test_7_left_shift_never_goes_negative(self):
        """左移量超过实际缩进 ⇒ 该行拉到 0，不抛异常。"""
        _out = PatchManager._m473_shift_indent("  a = 1\n", -8)
        self.assertEqual(_out, "a = 1\n")


class TestRealignIndent(unittest.TestCase):
    """判据本体：只在「整方法 + base 不等」时补偿。"""

    def setUp(self):
        self._old = _set_switch(True)

    def tearDown(self):
        _restore_switch(self._old)

    def _patch(self, orig, mod):
        return {"original_code": orig, "modified_code": mod}

    def test_10_class_method_zero_to_four(self):
        _p = self._patch(_ORIG_TARGET, _MOD_TARGET_FLAT)
        _r = PatchManager._m473_realign_indent(_p)
        self.assertTrue(_r["changed"], _r)
        self.assertEqual(_r["base_before"], 0)
        self.assertEqual(_r["base_target"], 4)
        self.assertEqual(_r["base_after"], 4)
        self.assertEqual(_m92_base_indent(_p["modified_code"]), 4)
        self.assertEqual(_m92_base_indent(_p["original_code"]), 4)

    def test_11_class_method_zero_to_eight(self):
        _orig = " " * 8 + "def deep(self):\n" + " " * 12 + "return 1\n"
        _mod = "def deep(self):\n    return 1\n"
        _p = self._patch(_orig, _mod)
        _r = PatchManager._m473_realign_indent(_p)
        self.assertTrue(_r["changed"], _r)
        self.assertEqual(_m92_base_indent(_p["modified_code"]), 8)

    def test_12_already_aligned_is_noop(self):
        _p = self._patch(_ORIG_TARGET, "    def target(self):\n        return 1\n")
        _before = _p["modified_code"]
        _r = PatchManager._m473_realign_indent(_p)
        self.assertFalse(_r["changed"])
        self.assertEqual(_r["reason"], "already_aligned")
        self.assertEqual(_p["modified_code"], _before)

    def test_13_fragment_without_def_is_skipped(self):
        """片段（无 def）交给 445 包裹，本判据不碰。"""
        _p = self._patch(_ORIG_TARGET, '    return ""\n')
        _r = PatchManager._m473_realign_indent(_p)
        self.assertFalse(_r["changed"])
        self.assertEqual(_r["reason"], "not_full_method")
        self.assertEqual(_p["modified_code"], '    return ""\n')

    def test_14_original_without_def_is_skipped(self):
        """原文无 def（O3 删除类候选）不适用。"""
        _p = self._patch('    _x = 1\n', _MOD_TARGET_FLAT)
        _r = PatchManager._m473_realign_indent(_p)
        self.assertFalse(_r["changed"])
        self.assertEqual(_r["reason"], "not_full_method")

    def test_15_empty_side_is_skipped(self):
        self.assertEqual(
            PatchManager._m473_realign_indent({"original_code": "", "modified_code": _MOD_TARGET_FLAT})["reason"],
            "empty")
        self.assertEqual(
            PatchManager._m473_realign_indent({"original_code": _ORIG_TARGET, "modified_code": ""})["reason"],
            "empty")

    def test_16_switch_off_is_noop(self):
        _set_switch(False)
        _p = self._patch(_ORIG_TARGET, _MOD_TARGET_FLAT)
        _before = _p["modified_code"]
        _r = PatchManager._m473_realign_indent(_p)
        self.assertFalse(_r["changed"])
        self.assertEqual(_r["reason"], "switch_off")
        self.assertEqual(_p["modified_code"], _before)

    def test_17_switch_default_is_on(self):
        """缺属性时按默认 True（不走 `or 默认值`，False 必须能生效）。"""
        _set_switch(None)
        self.assertIs(PatchManager._m473_realign_on(), True)
        config.ENABLE_M473_REINDENT_REALIGN = False
        self.assertIs(PatchManager._m473_realign_on(), False)

    def test_18_string_content_survives_realign(self):
        """整方法里带跨行 docstring 时，补偿后字符串内容逐字不变。"""
        _p = self._patch("    def target(self):\n        return 1\n", _MOD_TARGET_DOC)
        _before = _string_literals(_p["modified_code"])
        _r = PatchManager._m473_realign_indent(_p)
        self.assertTrue(_r["changed"], _r)
        self.assertEqual(_string_literals(_p["modified_code"]), _before)


class TestEndToEndStructure(unittest.TestCase):
    """闭环判据：补偿后替换进类里，**类结构不能塌**（这是本刀的全部意义）。"""

    def setUp(self):
        self._old = _set_switch(True)

    def tearDown(self):
        _restore_switch(self._old)

    def _normalize_and_apply(self, mod: str):
        _src = _CLS_BODY.format(target=_ORIG_TARGET.rstrip("\n"))
        _before = _methods_of(_src)
        _patch = {"original_code": _ORIG_TARGET, "modified_code": mod,
                  "method": "target", "file": "demo.py"}
        PatchManager._m323_normalize_patch(_patch)
        _new = _src.replace(_patch["original_code"], _patch["modified_code"], 1)
        return _before, _new, _patch

    def test_20_class_structure_survives(self):
        _before, _new, _p = self._normalize_and_apply(_MOD_TARGET_FLAT)
        self.assertEqual(len(_before), 3, _before)
        # 独立基准：重新 parse 数方法，不依赖被测函数的任何输出
        self.assertEqual(_methods_of(_new), _before,
                         "补偿后类方法数变化（结构塌了）:\n" + _new)
        ast.parse(_new)
        _r = (_p.get("m323_truth") or {}).get("realigned") or {}
        self.assertTrue(_r.get("changed"), _p.get("m323_truth"))

    def test_21_without_compensation_structure_would_collapse(self):
        """反证：把补偿关掉，同一份补丁会让类方法数缩水 —— 证明本刀有效。"""
        _set_switch(False)
        _before, _new, _ = self._normalize_and_apply(_MOD_TARGET_FLAT)
        _after = _methods_of(_new)
        self.assertNotEqual(_after, _before,
                            "开关关闭时结构竟然没塌，说明本用例没有判别力")

    def test_22_def_name_still_extracted(self):
        """补偿不回写 `_code`，所以取 def 名这条链不受影响。"""
        _, _, _p = self._normalize_and_apply(_MOD_TARGET_FLAT)
        self.assertEqual(_p.get("method"), "target")
        self.assertTrue((_p.get("m323_truth") or {}).get("parse_ok"))


class TestWiring(unittest.TestCase):
    """AST 接线：补偿必须在语法校验之后，且不得回写 _code。"""

    @classmethod
    def setUpClass(cls):
        _p = os.path.join(_ROOT, "nucleus", "reasoning", "PatchManager.py")
        with open(_p, encoding="utf-8") as _fh:
            cls.src = _fh.read()
        cls.tree = ast.parse(cls.src)

    def _fn(self):
        for _n in self.tree.body:
            if isinstance(_n, ast.ClassDef) and _n.name == "PatchManager":
                for _m in _n.body:
                    if isinstance(_m, ast.FunctionDef) and _m.name == "_m323_normalize_patch":
                        return _m
        return None

    def test_30_realign_runs_after_syntax_check(self):
        _fn = self._fn()
        self.assertIsNotNone(_fn)
        _ok_line = _rl_line = None
        for _n in ast.walk(_fn):
            if isinstance(_n, ast.Assign):
                for _t in _n.targets:
                    if (isinstance(_t, ast.Subscript)
                            and isinstance(_t.slice, ast.Constant)
                            and _t.slice.value == "parse_ok"
                            and isinstance(_n.value, ast.Constant)
                            and _n.value.value is False):
                        _ok_line = _n.lineno
                    if (isinstance(_t, ast.Name) and _t.id == "_m473_rl"
                            and isinstance(_n.value, ast.Call)):
                        _rl_line = _n.lineno
        self.assertIsNotNone(_ok_line, "找不到 parse_ok=False")
        self.assertIsNotNone(_rl_line, "找不到 _m473_rl 赋值")
        self.assertGreater(_rl_line, _ok_line,
                           "补偿跑在语法校验之前 ⇒ 会给正常补丁刷假 WARNING")

    def test_31_code_var_not_rewritten_inside_block(self):
        """补偿块内不得回写 `_code`（否则 def 名提取会多一条 IndentationError 日志）。"""
        _s = self.src
        _i = _s.index("_m473_rl = PatchManager._m473_realign_indent(patch)")
        _j = _s.index('_info["realigned"] = _m473_rl', _i)
        _seg = _s[_i:_j]
        self.assertNotIn("_code =", _seg, "补偿块回写了 _code:\n" + _seg)

    def test_32_helpers_defined_before_use(self):
        _names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        for _x in ("_m473_realign_on", "_m473_shift_indent", "_m473_realign_indent"):
            self.assertIn(_x, _names)

    def test_33_config_switch_present(self):
        with open(os.path.join(_ROOT, "config.py"), encoding="utf-8") as _fh:
            _cfg = _fh.read()
        self.assertIn("ENABLE_M473_REINDENT_REALIGN", _cfg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
