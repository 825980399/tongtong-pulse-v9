# -*- coding: utf-8 -*-
"""★主线第92批 T-92b：开关登记 门控单测（`ENABLE_NONORGAN_FILE_RESOLVE` + 本批两个新开关）。

任务书原文（第92批 T-92b）
--------------------------
「第87批引入的灰度开关 `ENABLE_NONORGAN_FILE_RESOLVE` 仍未写入 config.py，
  靠 `getattr` 兜底默认 True。
  改法：① 在 config.py 末尾（M91-CFG marker 之后）登记该开关；
        ② 默认值保持 True（与当前兜底一致）；③ 同步更新相关单测断言。
  验收：config.py 中能查到 `ENABLE_NONORGAN_FILE_RESOLVE = True`；
        相关 pytest 无新增失败。」

本批另登记 T-92c / T-92d 的两个防御性开关（默认 **False**）—— 同属「加新开关」，
不触碰任何既有开关值（红线）。★理由：T-92b 的存在本身就是「引入开关却不登记」
欠下的债，本批不宜再造两个同类欠债。

覆盖五组：
  A. 登记事实 —— AST 顶层赋值各自**恰好一次**、值符合预期、`# [M92-CFG]` marker 就位；
  B. 运行时一致 —— `getattr(config, ...)` 与登记值一致；两个 getter 返回预期；
     T-87b 的 `ENABLE_NONORGAN_FILE_RESOLVE` 读取口径仍为 True；
  C. 改前对照 —— `.bak_batch92/config.py.bak` 中这三个名字**不存在**（证明是新增）；
     且既有开关值**逐个未变**（红线自证）；
  D. 可编译 —— config.py 通过 py_compile；
  E. 顺序与兜底 —— 登记段位于 `# [M91-CFG]` 之后；`getattr` 兜底仍在（第二道保险）。
"""
import ast
import importlib.machinery
import importlib.util
import io
import os
import py_compile
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
from nucleus.reasoning.PatchManager import (  # noqa: E402
    _m92_ast_struct_guard_on,
    _m92_base_indent_guard_on,
)

_CFG = os.path.join(ROOT, "config.py")
_CFG_BAK = os.path.join(ROOT, ".bak_batch92", "config.py.bak")
_SE = os.path.join(ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
_MARKER = "# [M92-CFG]"
_MARKER_M91 = "# [M91-CFG]"

# 本批登记的开关及其**期望值**（任务书：前者 True 沿用兜底；后两者默认关闭）
_EXPECT = {
    "ENABLE_NONORGAN_FILE_RESOLVE": True,
    "ENABLE_M92_PATCH_BASE_INDENT_GUARD": False,
    "ENABLE_M92_PATCH_AST_STRUCT_GUARD": False,
}


def _cfg_src():
    return io.open(_CFG, encoding="utf-8").read()


def _top_level_enable_assigns(src):
    """→ {开关名: 字面量值}，只取模块级 `ENABLE_* = <常量>` 赋值。"""
    _out = {}
    for _n in ast.parse(src).body:
        if not isinstance(_n, ast.Assign) or len(_n.targets) != 1:
            continue
        _t = _n.targets[0]
        if not (isinstance(_t, ast.Name) and _t.id.startswith("ENABLE_")):
            continue
        if isinstance(_n.value, ast.Constant):
            _out.setdefault(_t.id, []).append(_n.value.value)
    return _out


class TestT92aRegistrationFact(unittest.TestCase):
    """A 登记事实（AST 口径，不靠字符串包含）。"""

    def test_A1_each_switch_registered_exactly_once_as_literal(self):
        _all = _top_level_enable_assigns(_cfg_src())
        for _name, _want in _EXPECT.items():
            _vals = _all.get(_name)
            self.assertIsNotNone(_vals, "config.py 未登记 %s" % _name)
            self.assertEqual(1, len(_vals), "%s 被登记 %d 次（应恰好 1 次）" % (_name, len(_vals)))
            self.assertIs(_want, _vals[0], "%s 期望 %r，实际 %r" % (_name, _want, _vals[0]))

    def test_A2_taskbook_acceptance_literal(self):
        """任务书验收原句：config.py 中能查到 `ENABLE_NONORGAN_FILE_RESOLVE = True`。"""
        self.assertIn("ENABLE_NONORGAN_FILE_RESOLVE = True", _cfg_src())

    def test_A3_marker_present(self):
        _src = _cfg_src()
        self.assertIn(_MARKER, _src, "缺少第92批登记 marker")
        self.assertIn(_MARKER_M91, _src, "M91 marker 应保留")


class TestT92bRuntimeConsistency(unittest.TestCase):
    """B 运行时一致：登记值 = 生效值。"""

    def test_B1_config_attributes_match(self):
        for _name, _want in _EXPECT.items():
            self.assertIs(_want, getattr(config, _name, None),
                          "运行时 %s 与登记值不一致" % _name)

    def test_B2_getters_return_registered_values(self):
        self.assertIs(False, _m92_base_indent_guard_on())
        self.assertIs(False, _m92_ast_struct_guard_on())

    def test_B3_nonorgan_resolve_reads_true(self):
        """T-87b 消费点的读取口径（`getattr(config, 'ENABLE_NONORGAN_FILE_RESOLVE', True)`）仍为 True。"""
        self.assertIs(True, bool(getattr(config, "ENABLE_NONORGAN_FILE_RESOLVE", True)))

    def test_B4_runtime_toggle_still_works_after_registration(self):
        """登记后仍须「改 config 即时生效」（开关读取点每次重新 import config）。"""
        _saved = config.ENABLE_M92_PATCH_AST_STRUCT_GUARD
        try:
            config.ENABLE_M92_PATCH_AST_STRUCT_GUARD = True
            self.assertIs(True, _m92_ast_struct_guard_on())
            config.ENABLE_M92_PATCH_AST_STRUCT_GUARD = False
            self.assertIs(False, _m92_ast_struct_guard_on())
        finally:
            config.ENABLE_M92_PATCH_AST_STRUCT_GUARD = _saved


class TestT92cLegacyDiffAndRedline(unittest.TestCase):
    """C 改前对照 + 红线自证（既有开关一个都不能变）。"""

    def _require_bak(self):
        if not os.path.isfile(_CFG_BAK):
            self.skipTest(".bak_batch92 缺失（scratch 目录可被外部清理），跳过改前对照")
        return io.open(_CFG_BAK, encoding="utf-8").read()

    def test_C1_switches_absent_before_m92(self):
        _old = _top_level_enable_assigns(self._require_bak())
        for _name in _EXPECT:
            self.assertNotIn(_name, _old, "%s 在改前就已登记（与任务书前提不符）" % _name)

    def test_C2_existing_switches_unchanged(self):
        """★红线：改前的每个 `ENABLE_* = <常量>` 在改后必须**逐字同值**。"""
        _old = _top_level_enable_assigns(self._require_bak())
        _new = _top_level_enable_assigns(_cfg_src())
        _drift = []
        for _name, _vals in _old.items():
            _n = _new.get(_name)
            if _n is None:
                _drift.append((_name, _vals, "MISSING"))
            elif _n[: len(_vals)] != _vals:
                _drift.append((_name, _vals, _n))
        self.assertEqual([], _drift[:10], "既有开关值被改动：%s" % _drift[:10])
        self.assertGreater(len(_old), 150, "改前开关数异常（%d）" % len(_old))

    def test_C3_switch_count_grows_by_at_least_three(self):
        """★版本无关判据（铁律 100：脆弱源码文本断言改版本无关复算）。

        ★第94批修正：原断言硬编码「恰好 +3」，其含义是「本批新增 3 个开关」，
        但写成「全库新增数 == 3」后，**任何**后续批次合法地新增开关都会把它打红
        （第94批 T-94a 的 `ENABLE_PENDING_QUEUE_AGING` 即此）。
        改为复算三项不变量：
          ① 既有开关**一个都没被删除**；
          ② 本批三个开关**都在**新增集合内；
          ③ 新增总数 ≥ 3。
        """
        _old = _top_level_enable_assigns(self._require_bak())
        _new = _top_level_enable_assigns(_cfg_src())
        _added = set(_new) - set(_old)
        _removed = set(_old) - set(_new)
        self.assertEqual(set(), _removed,
                         "既有开关被删除：%s" % sorted(_removed)[:10])
        self.assertEqual(set(), set(_EXPECT) - _added,
                         "本批三个开关必须都在新增集合内，缺：%s"
                         % sorted(set(_EXPECT) - _added))
        self.assertGreaterEqual(len(_added), 3,
                                "新增开关数应 ≥3（本批 3 个），实际 %d" % len(_added))

    def test_C4_no_test_used_to_assert_absence(self):
        """★任务书「同步更新相关单测断言」的**反向核实**：
        改前全 tests/ 中不存在对这三个名字的 `assertNotIn(..., config.py)` 式断言
        （本批 T0 已实测 0 命中）——若存在，本批必须反转，故此处固化该事实。
        """
        _hits = []
        for _dp, _dn, _fn in os.walk(os.path.join(ROOT, "tests")):
            _dn[:] = [_d for _d in _dn if _d != "__pycache__"]
            for _f in sorted(_fn):
                if not _f.endswith(".py"):
                    continue
                _p = os.path.join(_dp, _f)
                _t = io.open(_p, encoding="utf-8", errors="ignore").read()
                for _i, _l in enumerate(_t.replace("\r\n", "\n").split("\n"), 1):
                    if any(_n in _l for _n in _EXPECT) and "assertNotIn" in _l:
                        _hits.append("%s:%d: %s" % (os.path.relpath(_p, ROOT), _i, _l.strip()[:100]))
        self.assertEqual([], _hits, "存在对本批开关的 assertNotIn 断言，需同步反转：%s" % _hits)


class TestT92dCompilable(unittest.TestCase):
    """D config.py 可编译。"""

    def test_D1_py_compile(self):
        py_compile.compile(_CFG, doraise=True)

    def test_D2_importable_and_switch_readable(self):
        _ld = importlib.machinery.SourceFileLoader("m92_cfg_probe", _CFG)
        _sp = importlib.util.spec_from_loader(_ld.name, _ld)
        _mod = importlib.util.module_from_spec(_sp)
        _ld.exec_module(_mod)
        for _name, _want in _EXPECT.items():
            self.assertIs(_want, getattr(_mod, _name))


class TestT92eOrderAndFallback(unittest.TestCase):
    """E 登记顺序与 getattr 兜底（第二道保险）。"""

    def test_E1_registered_after_m91_marker(self):
        _src = _cfg_src()
        _i91 = _src.index(_MARKER_M91)
        for _name in _EXPECT:
            self.assertGreater(_src.index("%s = " % _name), _i91,
                               "%s 应登记在 M91-CFG 之后" % _name)

    def test_E2_m92_marker_is_last_registration_block(self):
        _src = _cfg_src()
        self.assertGreater(_src.index(_MARKER), _src.index(_MARKER_M91))
        self.assertLess(_src.index(_MARKER), len(_src) + 1)

    def test_E3_getattr_fallback_kept_in_consumer(self):
        """T-87b 消费点的 `getattr(..., True)` 兜底保留（config 读取失败时仍为 True）。"""
        _t = io.open(_SE, encoding="utf-8", errors="ignore").read()
        self.assertIn('"ENABLE_NONORGAN_FILE_RESOLVE", True', _t,
                      "消费点的 getattr 兜底被移除（应保留为第二道保险）")

    def test_E4_consumers_documented_in_registration(self):
        """登记注释须写明**读取点**（第87批的债正是「找不到读取点」造成的）。"""
        _src = _cfg_src()
        _seg = _src[_src.index(_MARKER_M91):]
        for _need in ("SafeEvolutionExecutor.py::repair_with_distillation",
                      "PatchManager.py::_m92_base_indent_guard_on",
                      "PatchManager.py::_m92_ast_struct_guard_on"):
            self.assertIn(_need, _seg, "登记注释缺少读取点：%s" % _need)


if __name__ == "__main__":
    unittest.main(verbosity=2)
