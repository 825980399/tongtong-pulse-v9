# -*- coding: utf-8 -*-
"""★主线第92批 T-92a：PatchManager「语法错误」报错文案补异常类名 门控单测。

背景（第91批 T0 记录的可观测性缺口）
------------------------------------
关2 的文案原为 `f"语法错误: {_e}"`，**不含异常类名** ⇒ 日志里
`TabError`（tab/空格混用）与 `IndentationError`（缩进漂移）都长成
「语法错误: inconsistent use of ... / unindent does not match ...」，
只能人肉读 message 才能区分异常种类
（`logs/pulse.log` 8 次 `语法错误` 全是 `unindent ...`，无法区分异常种类）。

本批改法（任务书 T-92a）
------------------------
1. `PatchManager._check_llm_patch_completeness` 关2：
   `f"语法错误: {_e}"` → `f"语法错误({type(_e).__name__}): {_e}"`；
2. `PatchManager._verify_in_copy` 第 3 步（整份副本语法校验）同口径补类名
   —— 任务书验收口径是「**日志中出现语法错误时**，能看到异常类名」，
      该处 `result["errors"]` 同样经 `[补丁验证] ... errors=...` 进日志，
      与关2 属**同一缺口**，故一并统一。

覆盖四组：
  A. 文案格式 —— IndentationError / TabError / 其他 SyntaxError 三类都带类名，
     且可用 `语法错误\\(类名\\): ` 正则稳定提取、可区分；
  B. 先红后绿 —— `.bak_batch92` 改前版**不含**类名（红）；当前版**含**（绿）；
     且改前/改后 `complete` 结论逐例相等（零回归）；
  C. 源码接线门禁 —— 两处输出点都必须是 `type(...).__name__` 形式，
     且 PatchManager 内**不得残留**裸 `f"语法错误: {"`；
  D. 端到端 —— `_verify_in_copy` 第 3 步真的跑到、真的带类名
     （合成工程复现「关2 能过、但整份文件语法失败」的补丁）。

被测对象 = **真实源码**；`.bak_batch92` 仅用于改前对照，缺失时 skipTest。
"""
import ast
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.reasoning.PatchManager import PatchManager  # noqa: E402

_PM_SRC_PATH = os.path.join(ROOT, "nucleus", "reasoning", "PatchManager.py")
_BAK_PM = os.path.join(ROOT, ".bak_batch92", "nucleus", "reasoning", "PatchManager.py.bak")
_PENDING = os.path.join(ROOT, "data", "patches", "pending_patches.json")
_HISTORY = os.path.join(ROOT, "data", "patches", "patch_history.json")

# 稳定提取异常类名；改前文案不匹配（无类名），故它同时是「先红」的判据
_LABEL_RE = re.compile(r"^语法错误\(([A-Za-z_][A-Za-z0-9_]*)\): ")

_ORIG = "    def foo(self):\n        x = 1\n        return x"
_DRIFT = "    def foo(self):\n        x = 2\n      return x"      # IndentationError(unindent)
_TAB = "    def foo(self):\n\t    x = 2\n        return x"         # TabError
_SYNTAX = "def f(:\n    return 1"                                  # 其他 SyntaxError


def _pm():
    return PatchManager.__new__(PatchManager)


def _reason(orig, mod):
    return _pm()._check_llm_patch_completeness(
        {"original_code": orig, "modified_code": mod})


def _src():
    return io.open(_PM_SRC_PATH, encoding="utf-8").read()


def _method_src(name):
    _t = ast.parse(_src())
    for _n in ast.walk(_t):
        if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _n.name == name:
            return ast.get_source_segment(_src(), _n)
    raise AssertionError("PatchManager 中找不到方法 %s" % name)


def _load_legacy_pm():
    if not os.path.isfile(_BAK_PM):
        return None
    _ld = importlib.machinery.SourceFileLoader("m92_legacy_pm_label", _BAK_PM)
    _sp = importlib.util.spec_from_loader(_ld.name, _ld)
    _mod = importlib.util.module_from_spec(_sp)
    _ld.exec_module(_mod)
    return _mod


def _patch_library(limit=40):
    _out = []
    for _p in (_PENDING, _HISTORY):
        if not os.path.isfile(_p):
            continue
        try:
            _d = json.load(io.open(_p, encoding="utf-8"))
        except Exception:
            continue
        _ls = _d.get("patches") if isinstance(_d, dict) else _d
        for _x in (_ls or []):
            if isinstance(_x, dict) and _x.get("original_code") and _x.get("modified_code"):
                _out.append(_x)
    return _out[:limit]


def _make_project():
    _root = tempfile.mkdtemp(prefix="m92_lbl_")
    os.makedirs(os.path.join(_root, "organs", "brain"), exist_ok=True)
    _fp = os.path.join(_root, "organs", "brain", "PulseInnerWorld.py")
    io.open(_fp, "w", encoding="utf-8").write(
        "# synthetic\nclass C:\n    def m(self):\n        return 1\n")
    return _root, _fp


# 「关2 能过、但整份文件语法失败」的补丁样本
_S3_OC = "    def m(self):\n        return 1"
_S3_MC = "return 1\nx = 2"


class TestT92aLabelFormat(unittest.TestCase):
    """A 文案格式：三类语法异常都带类名，且可稳定提取、可区分。"""

    def test_A1_all_three_kinds_carry_class_name(self):
        for _tag, _mod, _want in (("缩进漂移", _DRIFT, "IndentationError"),
                                  ("tab/空格混用", _TAB, "TabError"),
                                  ("其他语法错", _SYNTAX, "SyntaxError")):
            _r = _reason(_ORIG, _mod)
            self.assertFalse(_r["complete"], "%s 应被关2 拒" % _tag)
            _m = _LABEL_RE.match(_r["reason"])
            self.assertIsNotNone(_m, "[%s] 文案不含异常类名: %r" % (_tag, _r["reason"]))
            self.assertEqual(_want, _m.group(1), "[%s] 异常类名不符" % _tag)

    def test_A2_original_message_preserved(self):
        """★零信息损失：原有 message 必须逐字保留在类名之后。"""
        _r = _reason(_ORIG, _TAB)
        self.assertIn("inconsistent use of tabs and spaces in indentation", _r["reason"])
        _r2 = _reason(_ORIG, _DRIFT)
        self.assertIn("unindent does not match any outer indentation level", _r2["reason"])

    def test_A3_two_kinds_now_distinguishable(self):
        """★需求本体：TabError 与 IndentationError 从日志即可区分。"""
        _a = _LABEL_RE.match(_reason(_ORIG, _DRIFT)["reason"]).group(1)
        _b = _LABEL_RE.match(_reason(_ORIG, _TAB)["reason"]).group(1)
        self.assertNotEqual(_a, _b)
        self.assertIn("TabError", (_a, _b))


class TestT92bRedThenGreen(unittest.TestCase):
    """B 先红后绿：改前无类名（红），改后有（绿），结论零回归。"""

    def test_B1_legacy_reason_has_no_class_name(self):
        _leg = _load_legacy_pm()
        if _leg is None:
            self.skipTest(".bak_batch92 缺失（scratch 目录可被外部清理），跳过改前对照")
        for _tag, _mod in (("漂移", _DRIFT), ("tab", _TAB), ("语法", _SYNTAX)):
            _r = _leg.PatchManager.__new__(_leg.PatchManager)._check_llm_patch_completeness(
                {"original_code": _ORIG, "modified_code": _mod})
            self.assertFalse(_r["complete"], _tag)
            self.assertIsNone(_LABEL_RE.match(_r["reason"]),
                              "[%s] 改前文案不应含类名: %r" % (_tag, _r["reason"]))
            self.assertTrue(_r["reason"].startswith("语法错误: "),
                            "[%s] 改前文案应为裸 message: %r" % (_tag, _r["reason"]))

    def test_B2_current_reason_has_class_name(self):
        for _tag, _mod in (("漂移", _DRIFT), ("tab", _TAB), ("语法", _SYNTAX)):
            _r = _reason(_ORIG, _mod)
            self.assertIsNotNone(_LABEL_RE.match(_r["reason"]),
                                 "[%s] 改后文案应含类名: %r" % (_tag, _r["reason"]))

    def test_B3_verdicts_identical_to_legacy(self):
        """零回归：改前/改后对同一输入的 `complete` 结论必须**逐例相等**。"""
        _leg = _load_legacy_pm()
        if _leg is None:
            self.skipTest(".bak_batch92 缺失，跳过改前对照")
        _cases = [("漂移", _ORIG, _DRIFT), ("tab", _ORIG, _TAB), ("语法", _ORIG, _SYNTAX),
                  ("合法", _ORIG, _ORIG.replace("x = 1", "x = 2")),
                  ("与原文相同", _ORIG, _ORIG)]
        for _x in _patch_library():
            _cases.append((str(_x.get("id")), _x["original_code"], _x["modified_code"]))
        _leg_pm = _leg.PatchManager.__new__(_leg.PatchManager)
        _drift = []
        for _tag, _o, _m in _cases:
            _old = _leg_pm._check_llm_patch_completeness(
                {"original_code": _o, "modified_code": _m})
            _new = _pm()._check_llm_patch_completeness(
                {"original_code": _o, "modified_code": _m})
            if _old["complete"] != _new["complete"]:
                _drift.append((_tag, _old["complete"], _new["complete"]))
        self.assertEqual([], _drift, "结论漂移：%s" % _drift[:5])


class TestT92cWiringGate(unittest.TestCase):
    """C 源码接线门禁：两处输出点都必须改写，且不得残留无类名形式。"""

    def test_C1_gate2_output_rewritten(self):
        _seg = _method_src("_check_llm_patch_completeness")
        self.assertIn('f"语法错误({type(_e).__name__}): {_e}"', _seg,
                      "关2 输出点未补异常类名")

    def test_C2_verify_in_copy_step3_output_rewritten(self):
        _seg = _method_src("_verify_in_copy")
        self.assertIn('f"语法错误({type(e).__name__}): {e}"', _seg,
                      "_verify_in_copy 第 3 步输出点未补异常类名")

    def test_C3_no_bare_label_left_in_patchmanager(self):
        """★防漏改：PatchManager 内不得再出现裸 `f"语法错误: {`。"""
        _bad = [i for i, _l in enumerate(_src().split("\n"), 1)
                if re.search(r'f"语法错误: \{', _l)]
        self.assertEqual([], _bad, "PatchManager 内仍有裸文案 @ 行 %s" % _bad)

    def test_C4_label_is_derived_not_hardcoded(self):
        """类名必须由 `type(...).__name__` **推导**，不得硬编码字符串（否则会失真）。"""
        for _m in ("_check_llm_patch_completeness", "_verify_in_copy"):
            _seg = _method_src(_m)
            self.assertRegex(_seg, r"语法错误\(\{type\(_?\w+\)\.__name__\}\): ", _m)


class TestT92dEndToEnd(unittest.TestCase):
    """D 端到端：`_verify_in_copy` 第 3 步真跑到、真带类名。"""

    def _run(self, pm_obj, tag):
        _root, _fp = _make_project()
        try:
            pm_obj._project_root = _root
            return pm_obj._verify_in_copy({
                "id": "m92-" + tag, "source": "llm", "file": _fp, "method": "m",
                "original_code": _S3_OC, "modified_code": _S3_MC})
        finally:
            shutil.rmtree(_root, ignore_errors=True)

    def test_D1_current_version_labels_step3_error(self):
        _r = self._run(_pm(), "cur")
        self.assertEqual("syntax_check_failed", _r.get("stage"), _r)
        _errs = [str(_e) for _e in _r.get("errors", [])]
        _hit = [_e for _e in _errs if _LABEL_RE.match(_e)]
        self.assertTrue(_hit, "step3 报错未带异常类名: %s" % _errs)

    def test_D2_legacy_version_had_no_label(self):
        """★先红证据（永久可复现）：改前版走到同一 stage，但文案无类名。"""
        _leg = _load_legacy_pm()
        if _leg is None:
            self.skipTest(".bak_batch92 缺失，跳过改前对照")
        _r = self._run(_leg.PatchManager.__new__(_leg.PatchManager), "leg")
        self.assertEqual("syntax_check_failed", _r.get("stage"), _r)
        _errs = [str(_e) for _e in _r.get("errors", [])]
        self.assertTrue(_errs)
        self.assertTrue(all(_LABEL_RE.match(_e) is None for _e in _errs),
                        "改前文案不应含类名: %s" % _errs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
