# -*- coding: utf-8 -*-
"""★主线第91批 T-91c：灰度开关**正式登记**进 config.py 的门控单测。

任务书 T-91c（P1）
------------------
把三个开关 `ENABLE_M89_PATCH_SIM_THRESHOLD` / `ENABLE_M90_PATCH_FIELD_CONTRACT` /
`ENABLE_M90_LOG_LOCATE_V2` 由「模块内联默认值 + `getattr` 兜底」改为**正式写入
config.py**，默认值保持 True。验收：config.py 中能查到三个开关；默认值与当前一致。
本批另登记 T-91a/T-91b 新增的两个开关（`ENABLE_M91_LOG_LOCATE_V3` /
`ENABLE_M91_LLM_INDENT_REPAIR`），共 5 个。

★跨批影响（必须在报告里披露）
----------------------------
第89/90批的**批内红线**是「不改 config.py」⇒ 当时有三条测试断言
`assertNotIn(开关名, config.py)`：
    test_llm_patch_similarity_m89.py::test_42
    test_patch_field_contract_m90.py::test_51
    test_log_locate_fix_m90.py::test_71
本批任务书 T-91c 明确解除该约束（只登记默认值、不改任何既有开关的值）
⇒ 三条断言按新契约**反转**为 `assertIn` + 默认值 True，本文件 E 组做护栏。

覆盖六组：
  A. 5 个开关在 config.py 中**唯一登记**且值均为 True（AST 级，防重复追加）；
  B. 各读取函数在当前配置下均返回 True（生效值与登记前一致）；
  C. 登记本身零行为变化 —— 与 `.bak_batch91/config.py.bak`（登记前）对比：
     登记前该属性**不存在**（靠 getattr 兜底 True），登记后为 True，**生效值相同**；
  D. config.py 文件完整性（可编译、末段 marker、开关段落成组出现）；
  E. 三处既有「开关契约」断言已按 T-91c 同步反转（旧 assertNotIn 文案不再存在）；
  F. 运行时生效 —— 读取点每次重新 import config，改属性即时生效（无需重启）。
"""
import ast
import importlib
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

_CFG_REL = "config.py"
_CFG_PATH = os.path.join(ROOT, _CFG_REL)
_BAK_CFG = os.path.join(ROOT, ".bak_batch91", "config.py.bak")
_MARK = "# [M91-CFG]"
_MARK_T = "_m91_t91c_switch_registered"

# 本次登记的 5 个开关（顺序 = config.py 中的登记顺序）
_SWITCHES = (
    "ENABLE_M89_PATCH_SIM_THRESHOLD",
    "ENABLE_M90_PATCH_FIELD_CONTRACT",
    "ENABLE_M90_LOG_LOCATE_V2",
    "ENABLE_M91_LOG_LOCATE_V3",
    "ENABLE_M91_LLM_INDENT_REPAIR",
)

# 反转后的三处既有断言：文件 → 原方法名 / 新方法名
_INVERTED_TESTS = (
    ("tests/test_llm_patch_similarity_m89.py",
     "test_42_switch_not_written_into_config_py",
     "test_42_switch_since_m91_registered_in_config_py"),
    ("tests/test_patch_field_contract_m90.py",
     "test_51_config_py_has_no_m90_switch",
     "test_51_config_py_has_m90_switch_registered_by_m91"),
    ("tests/test_log_locate_fix_m90.py",
     "test_71_config_py_untouched",
     "test_71_config_py_registered_by_m91"),
)


def _read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace").read()


def _module_assign_counts():
    """AST 级统计 config.py 各开关的赋值次数（防重复追加导致后者覆盖前者）。"""
    _tree = ast.parse(_read(_CFG_REL))
    _cnt = {}
    for _node in _tree.body:
        if isinstance(_node, ast.Assign):
            for _t in _node.targets:
                if isinstance(_t, ast.Name):
                    _cnt[_t.id] = _cnt.get(_t.id, 0) + 1
    return _cnt


class TestSwitchRegistryM91(unittest.TestCase):
    """★第91批 T-91c：开关登记契约。"""

    # --------------------------------------------------------- A 登记唯一 + True
    def test_A1_registered_exactly_once_with_true(self):
        """★验收：config.py 中能查到 5 个开关，且只出现一次、值均为 True。"""
        _cnt = _module_assign_counts()
        for _s in _SWITCHES:
            self.assertEqual(_cnt.get(_s, 0), 1,
                             "★{} 在 config.py 中的顶层赋值次数应为 1（实际 {}）".format(_s, _cnt.get(_s, 0)))
            self.assertIn("{} = True".format(_s), _read(_CFG_REL),
                          "★{} 的登记默认值必须为 True".format(_s))

    def test_A2_runtime_values_are_true(self):
        """★验收：运行时读取到的默认值与登记前一致（均为 True）。"""
        for _s in _SWITCHES:
            self.assertTrue(hasattr(config, _s), "config 缺少属性 {}".format(_s))
            self.assertIs(getattr(config, _s), True, "{} 运行时值应为 True".format(_s))

    # --------------------------------------------------------- B 各读取函数口径
    def test_B1_reader_functions_return_true(self):
        """各读取函数在当前配置下均返回 True（登记未改变生效值）。"""
        _pm = importlib.import_module("nucleus.reasoning.PatchManager")
        _la = importlib.import_module("nucleus.evolution.LogAnalyzer")
        _si = importlib.import_module("nucleus.self_inspector")
        _se = importlib.import_module("nucleus.reasoning.SafeEvolutionExecutor")
        _checks = (
            ("_m89_patch_sim_switch_on", getattr(_pm, "_m89_patch_sim_switch_on")),
            ("_m90_patch_field_contract_on", getattr(_pm, "_m90_patch_field_contract_on")),
            ("LogAnalyzer._m90_log_locate_v2_on", getattr(_la, "_m90_log_locate_v2_on")),
            ("self_inspector._m90_locate_v2_on", getattr(_si, "_m90_locate_v2_on")),
            ("self_inspector._m91_log_locate_v3_on", getattr(_si, "_m91_log_locate_v3_on")),
            ("SafeEvolutionExecutor._m91_indent_repair_on",
             getattr(_se, "_m91_indent_repair_on")),
        )
        for _name, _fn in _checks:
            self.assertTrue(_fn(), "{}() 应为 True".format(_name))

    def test_B2_m89_threshold_values(self):
        """登记后阈值语义不变（0.3 + 长度下限 1/3）。"""
        _pm = importlib.import_module("nucleus.reasoning.PatchManager")
        self.assertEqual(getattr(_pm, "_m89_min_similarity")(), 0.3)
        self.assertAlmostEqual(getattr(_pm, "_m89_len_floor")(), 1.0 / 3.0, places=6)

    # --------------------------------------------------------- C 登记零行为变化
    def test_C1_registration_is_behavior_neutral(self):
        """★登记零行为变化：登记前该属性**不存在**（兜底 True），登记后为 True。"""
        if not os.path.isfile(_BAK_CFG):
            self.skipTest("`.bak_batch91/config.py.bak` 不存在（scratch 目录可被清理）")
        import importlib.machinery
        import importlib.util
        _ld = importlib.machinery.SourceFileLoader("m91_legacy_config", _BAK_CFG)
        _sp = importlib.util.spec_from_loader(_ld.name, _ld)
        _old = importlib.util.module_from_spec(_sp)
        _ld.exec_module(_old)
        for _s in _SWITCHES:
            _old_val = getattr(_old, _s, None)
            _new_val = getattr(config, _s, None)
            # 登记前两个 M91 开关与 M90/M89 开关都不在 config.py ⇒ 靠 getattr 兜底 True
            self.assertIsNone(_old_val,
                              "★登记前 {} 本不应存在于 config.py（实测 {!r}）".format(_s, _old_val))
            # 生效值 = 登记前 getattr 兜底值 == 登记后的显式值
            self.assertEqual(_new_val, True)
            self.assertEqual(_new_val, bool(getattr(_old, _s, True)),
                             "★登记默认值必须与登记前的 getattr 兜底值一致")

    # --------------------------------------------------------- D 文件完整性
    def test_D1_config_py_still_compiles(self):
        """config.py 仍可编译（登记是纯追加，未破坏语法）。"""
        _src = _read(_CFG_REL)
        compile(_src, _CFG_REL, "exec")

    def test_D2_section_marker_present(self):
        """登记段落带幂等 marker（便于二次追加检测与回溯）。"""
        self.assertIn(_MARK, _read(_CFG_REL))

    # --------------------------------------------------------- E 跨批断言已同步
    def test_E1_legacy_assertnotin_tests_inverted(self):
        """★三条既有「红线 assertNotIn」断言已按 T-91c 同步反转（跨批影响护栏）。

        若不反转，登记 config.py 会让这三个用例永久转红。
        """
        for _rel, _old_name, _new_name in _INVERTED_TESTS:
            _src = _read(_rel)
            self.assertNotIn("def {}(".format(_old_name), _src,
                             "{} 仍保留旧方法名（应已更名）".format(_rel))
            self.assertIn("def {}(".format(_new_name), _src,
                          "{} 缺少反转后的新用例".format(_rel))
            self.assertIn(_MARK_T, _src, "{} 缺少 T-91c 变更说明标记".format(_rel))
            self.assertIn("assertIn(_SWITCH, _src", _src,
                          "{} 的反转断言应为 assertIn(_SWITCH, _src...)".format(_rel))

    # --------------------------------------------------------- F 运行时生效
    def test_F1_change_takes_effect_without_restart(self):
        """读取点每次重新 import config ⇒ 改属性即时生效（无需重启框架）。"""
        _si = importlib.import_module("nucleus.self_inspector")
        _fn = getattr(_si, "_m91_log_locate_v3_on")
        _had = hasattr(config, "ENABLE_M91_LOG_LOCATE_V3")
        _old = getattr(config, "ENABLE_M91_LOG_LOCATE_V3", None)
        try:
            config.ENABLE_M91_LOG_LOCATE_V3 = False
            self.assertFalse(_fn(), "★置 False 后应立即生效")
            config.ENABLE_M91_LOG_LOCATE_V3 = True
            self.assertTrue(_fn())
        finally:
            if _had:
                config.ENABLE_M91_LOG_LOCATE_V3 = _old
            else:
                delattr(config, "ENABLE_M91_LOG_LOCATE_V3")


if __name__ == "__main__":
    unittest.main()
