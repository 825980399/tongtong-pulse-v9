# -*- coding: utf-8 -*-
"""★主线第89批 T-89a：LLM 补丁完整性「相似度阈值 0.5→0.3 + 长度下限」门控单测。

被测对象 = **真实源码**（`PatchManager._check_llm_patch_completeness`），
不复刻任何逻辑：用例直接构造 `PatchManager.__new__(PatchManager)` 轻量实例并调用真方法。

覆盖四组：
  A. 阈值语义 —— [0.3, 0.5) 区间的「合法局部改写」由拒到收；
  B. 安全护栏 —— 真截断与「长度不足」仍被拒；长度下限的零安全回归证明；
  C. 真实数据回归 —— 补丁库里的 LLM 补丁在改前/改后判定不劣化；
  D. 开关契约 —— 默认值内联在本模块、config.py 不含开关名、关闭即逐字回退。

样本构造全部取自**真实源码**（`organs/body/PulseLiver.py` 的真方法），
并动态复算 ratio/长度比（不写死魔数），源码漂移时用例会自己暴露。
"""
import ast
import difflib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from nucleus.reasoning.PatchManager import (
    PatchManager,
    _m89_len_floor,
    _m89_min_similarity,
)

_PULSE_LIVER = os.path.join(ROOT, "organs", "body", "PulseLiver.py")
_BAK_PM = os.path.join(ROOT, ".bak_batch89", "nucleus", "reasoning", "PatchManager.py.bak")
_PENDING = os.path.join(ROOT, "data", "patches", "pending_patches.json")
_HISTORY = os.path.join(ROOT, "data", "patches", "patch_history.json")

_SWITCH = "ENABLE_M89_PATCH_SIM_THRESHOLD"
_LONG_METHOD = "_periodic_purity_check"      # 长方法（截断样本载体，17000+ 字符）
_SMALL_METHOD = "_m70_kal_node_count"        # 常用修复方法（局部改写样本载体）


# --------------------------------------------------------------- 真实源码取样
def _fn_node(src: str, name: str):
    for _n in ast.walk(ast.parse(src)):
        if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _n.name == name:
            return _n
    raise AssertionError("源码中找不到方法 {}".format(name))


def _method_source(path: str, name: str) -> str:
    """真实方法的完整原文（与 get_method_body 同源：带原缩进）。"""
    _src = io.open(path, encoding="utf-8").read()
    _lines = _src.splitlines(keepends=True)
    _fn = _fn_node(_src, name)
    _end = getattr(_fn, "end_lineno", _fn.lineno)
    return "".join(_lines[_fn.lineno - 1:_end])


def _statement_prefix(path: str, name: str, n_stmt: int) -> str:
    """取「def 行 + 前 n_stmt 条完整语句」——**语法合法**的截断样本。

    （按字符硬切会切在 try/except 中间 → 关2 语法关先拦，测不到关3/关3b。）
    """
    _src = io.open(path, encoding="utf-8").read()
    _lines = _src.splitlines(keepends=True)
    _fn = _fn_node(_src, name)
    _parts = [_lines[_fn.lineno - 1]]
    for _st in _fn.body[:n_stmt]:
        _parts.append("".join(_lines[_st.lineno - 1:getattr(_st, "end_lineno", _st.lineno)]))
    return "".join(_parts)


def _append_only_sample(path: str, name: str, lo: float = 0.30, hi: float = 0.50):
    """构造「原文 + 追加新代码」样本 —— 复现真实形态（694→1232 字符，ratio≈0.5006）。

    ratio = 2*L1/(L1+L2)（修改后完全包含原文），随追加行数单调下降，
    故迭代找到落入 [lo, hi) 的个数即停。
    """
    _full = _method_source(path, name)
    _unit = "\n        _m89_added_field = 1"
    for _k in range(1, 800):
        _mod = _full + _unit * _k + "\n"
        _r = _ratio(_full, _mod)
        if lo <= _r < hi:
            return _full, _mod, _r
    raise AssertionError("构造失败：追加式样本无法落入 [{:.2f}, {:.2f})".format(lo, hi))


def _ratio(a: str, b: str) -> float:
    """与 `_check_llm_patch_completeness` 关3 **同口径**（dedent + strip）。"""
    return difflib.SequenceMatcher(None, textwrap.dedent(a or "").strip(),
                                   textwrap.dedent(b or "").strip()).ratio()


def _syntax_ok(m: str) -> bool:
    """与关2 同口径（dedent + 虚拟函数包装）。"""
    try:
        ast.parse("def _wrap():\n" + textwrap.indent(textwrap.dedent(m).strip(), "    "),
                  filename="<llm-patch>")
        return True
    except SyntaxError:
        return False


# ------------------------------------------------------------------ 被测封装
def _new_pm():
    """轻量实例（不跑 __init__）——被测方法不依赖任何实例状态。"""
    return PatchManager.__new__(PatchManager)


def _check(pm, original: str, modified: str) -> dict:
    return pm._check_llm_patch_completeness(
        {"original_code": original, "modified_code": modified})


def _load_legacy_pm():
    """载入 .bak_batch89 里的**改前** PatchManager（缺失时返回 None）。

    ★`.bak_batch89/` 是 git-ignored 的 scratch 目录（可能被外部清理），
    故调用方必须在缺失时 skipTest，不得让用例永久失败。
    """
    if not os.path.isfile(_BAK_PM):
        return None
    _loader = importlib.machinery.SourceFileLoader("m89_legacy_patchmanager", _BAK_PM)
    _spec = importlib.util.spec_from_loader(_loader.name, _loader)
    _mod = importlib.util.module_from_spec(_spec)
    _loader.exec_module(_mod)
    return _mod


def _load_llm_patches():
    """从真实补丁库抽取 LLM 补丁（pending + history 两处，递归）。"""
    _out = []
    for _p in (_PENDING, _HISTORY):
        if not os.path.isfile(_p):
            continue
        try:
            _d = json.loads(io.open(_p, encoding="utf-8").read())
        except Exception:
            continue
        _stack = [_d]
        while _stack:
            _o = _stack.pop()
            if isinstance(_o, dict):
                if ("modified_code" in _o or "original_code" in _o) \
                        and str(_o.get("source", "")) == "llm":
                    _out.append(_o)
                _stack.extend(_o.values())
            elif isinstance(_o, list):
                _stack.extend(_o)
    return _out


class _Switch:
    """临时改写 config 属性（被测函数每次调用重新 import config）。"""

    def __init__(self, value):
        self._value = value
        self._old = None
        self._had = False

    def __enter__(self):
        self._had = hasattr(config, _SWITCH)
        self._old = getattr(config, _SWITCH, None)
        setattr(config, _SWITCH, self._value)
        return self

    def __exit__(self, *a):
        if self._had:
            setattr(config, _SWITCH, self._old)
        else:
            try:
                delattr(config, _SWITCH)
            except AttributeError:
                pass
        return False


# ============================================================ A 阈值语义
class TestT89aThresholdSemantics(unittest.TestCase):
    """阈值下调后「合法局部改写」由拒到收。"""

    def setUp(self):
        self.pm = _new_pm()
        self.orig, self.mod_band, self.ratio_band = _append_only_sample(
            _PULSE_LIVER, _SMALL_METHOD)

    def test_10_constructed_sample_is_in_band(self):
        """自检：构造样本 ratio ∈ [0.3,0.5) 且长度比 ≥ 1/3 且语法合法。"""
        self.assertGreaterEqual(self.ratio_band, 0.30, "ratio={:.4f}".format(self.ratio_band))
        self.assertLess(self.ratio_band, 0.50, "ratio={:.4f}".format(self.ratio_band))
        self.assertGreaterEqual(len(self.mod_band), len(self.orig) / 3.0)
        self.assertTrue(_syntax_ok(self.mod_band))

    def test_11_band_patch_passes_after(self):
        """★核心：0.3 阈值下，[0.3,0.5) 的真实代码补丁通过验证。"""
        _r = _check(self.pm, self.orig, self.mod_band)
        self.assertTrue(_r["complete"], "应通过，实际 reason={}".format(_r["reason"]))

    def test_12_band_patch_rejected_before(self):
        """★先红证据：同一补丁在**改前**（0.5）实现下被拒。"""
        _legacy = _load_legacy_pm()
        if _legacy is None:
            self.skipTest(".bak_batch89 不存在（已被外部清理），跳过改前对照")
        _pm = _legacy.PatchManager.__new__(_legacy.PatchManager)
        _r = _pm._check_llm_patch_completeness(
            {"original_code": self.orig, "modified_code": self.mod_band})
        self.assertFalse(_r["complete"], "改前实现应拒绝该补丁")
        self.assertIn("相似度过低", _r["reason"])
        self.assertIn("0.5", _r["reason"], "改前 reason 应带旧阈值 0.5")

    def test_13_reason_carries_effective_threshold(self):
        """reason 串必须带**生效阈值**，便于日志侧区分改前/改后。"""
        _orig = _method_source(_PULSE_LIVER, _LONG_METHOD)
        _mod = _statement_prefix(_PULSE_LIVER, _LONG_METHOD, 1)
        _r = _check(self.pm, _orig, _mod)
        self.assertFalse(_r["complete"])
        self.assertIn("相似度过低", _r["reason"])
        self.assertIn("0.3", _r["reason"], "改后 reason 应带新阈值 0.3")
        with _Switch(False):
            _r2 = _check(self.pm, _orig, _mod)
            self.assertIn("0.5", _r2["reason"], "开关关闭后 reason 应带旧阈值 0.5")


# ============================================================ B 安全护栏
class TestT89aSafetyGuards(unittest.TestCase):
    """阈值下调不得放行截断/残缺。"""

    def setUp(self):
        self.pm = _new_pm()
        self.long_orig = _method_source(_PULSE_LIVER, _LONG_METHOD)

    def test_20_severe_truncation_still_rejected(self):
        """★真截断（与实测 15:25:47 的 0.09 同区）必须仍被拒。"""
        _mod = _statement_prefix(_PULSE_LIVER, _LONG_METHOD, 7)
        _r = _check(self.pm, self.long_orig, _mod)
        self.assertFalse(_r["complete"], "真截断必须被拒（阈值 0.3 也不得放行）")
        self.assertIn("相似度过低", _r["reason"])
        self.assertIn("0.3", _r["reason"])
        self.assertLess(_ratio(self.long_orig, _mod), 0.30)

    def test_21_observed_009_is_below_length_floor(self):
        """★实测反推：ratio=0.09 ⇒ modified 仅剩原文 4.7%，远低于长度下限。

        ratio ≈ 2*L2/(L1+L2)（截断时 M ≈ L2）⇒ L2 = ratio*L1/(2-ratio)。
        """
        for _r_v, _l1, _label in ((0.09, len(self.long_orig), "长方法(实测 15:25:47)"),
                                  (0.03, 158, "ERROR 日志文本(第88批实测)")):
            _l2 = _r_v * _l1 / (2 - _r_v)
            self.assertLess(_l2, _l1 / 3.0,
                            "{}: ratio={:.2f} 反推 L2={:.0f} 应低于 L1/3={:.0f}".format(_label, _r_v, _l2, _l1 / 3.0))

    def test_22_length_floor_blocks_what_similarity_allows(self):
        """★长度下限的唯一价值场景：ratio 已过 0.3 但长度不足原文 1/3 → 仍拒。"""
        _mod = _statement_prefix(_PULSE_LIVER, _LONG_METHOD, 10)
        _r_sim = _ratio(self.long_orig, _mod)
        self.assertGreaterEqual(_r_sim, 0.30, "构造应已过相似度关（ratio={:.4f}）".format(_r_sim))
        self.assertLess(len(_mod), len(self.long_orig) / 3.0, "构造应长度不足")
        self.assertTrue(_syntax_ok(_mod))
        _r = _check(self.pm, self.long_orig, _mod)
        self.assertFalse(_r["complete"], "长度不足必须被长度下限拦下")
        self.assertIn("长度不足", _r["reason"])

    def test_23_length_floor_is_provably_zero_regression(self):
        """★零安全回归证明：原 0.5 阈值下任何过闸补丁恒满足 len(mod) ≥ len(orig)/3。

        若 ratio ≥ 0.5 ⇒ 2*len(mod)/(L1+len(mod)) ≥ 0.5 ⇒ len(mod) ≥ L1/3。
        逐样本穷举验证该数学上界。
        """
        _bad = []
        for _n in (1, 3, 5, 7, 8, 10, 14, 20, 34):
            _mod = _statement_prefix(_PULSE_LIVER, _LONG_METHOD, _n)
            if _ratio(self.long_orig, _mod) >= 0.5 and \
                    len(_mod) < len(self.long_orig) / 3.0:
                _bad.append(_n)
        self.assertEqual(_bad, [], "存在「相似度≥0.5 却长度<1/3」的样本 ⇒ 证明不成立: {}".format(_bad))

    def test_24_error_log_material_far_below_new_threshold(self):
        """误判率不上升：ERROR 日志文本素材的相似度远低于 0.3（第88批 0.03 型）。"""
        _log_text = ("2026-09-20 15:25:47 [代码学习] WARNING: 补丁验证失败已丢弃: "
                     "patch_llm_178988... → ['补丁完整性检查失败: 与原文相似度过低']")
        _mod = _method_source(_PULSE_LIVER, _SMALL_METHOD)
        _r = difflib.SequenceMatcher(None, _log_text, _mod).ratio()
        self.assertLess(_r, 0.3, "日志文本 vs 代码的 ratio={:.4f} 应远低于 0.3".format(_r))
        self.assertFalse(_check(self.pm, _log_text, _mod)["complete"])


# ============================================================ C 真实数据回归
class TestT89aRealData(unittest.TestCase):
    """真实补丁库：改后判定不得比改前更严。"""

    def setUp(self):
        self.pm = _new_pm()
        self.patches = _load_llm_patches()
        if not self.patches:
            self.skipTest("补丁库无 LLM 补丁样本（数据缺失/被清理）")

    def test_30_all_real_llm_patches_still_pass(self):
        """真实 LLM 补丁在改后实现下仍全部通过（护住既有成果）。"""
        _fail = []
        for _p in self.patches:
            _r = _check(self.pm, _p.get("original_code", ""), _p.get("modified_code", ""))
            if not _r["complete"]:
                _fail.append((_p.get("id"), _r["reason"]))
        self.assertEqual(_fail, [], "真实 LLM 补丁被改后实现拒绝: {}".format(_fail))

    def test_31_no_patch_regressed_vs_legacy(self):
        """★零回归（真实数据）：改前接受的集合 ⊆ 改后接受的集合。"""
        _legacy = _load_legacy_pm()
        if _legacy is None:
            self.skipTest(".bak_batch89 不存在（已被外部清理），跳过改前对照")
        _lpm = _legacy.PatchManager.__new__(_legacy.PatchManager)
        _regressed = []
        for _p in self.patches:
            _o, _m = _p.get("original_code", ""), _p.get("modified_code", "")
            _old = _lpm._check_llm_patch_completeness(
                {"original_code": _o, "modified_code": _m})["complete"]
            _new = _check(self.pm, _o, _m)["complete"]
            if _old and not _new:
                _regressed.append(_p.get("id"))
        self.assertEqual(_regressed, [], "改前通过、改后被拒 ⇒ 安全回归: {}".format(_regressed))

    def test_32_tightest_real_sample_is_real_code_and_passes(self):
        """记录最紧样本：其原文必须是**代码**（非日志文本）且改后过闸。"""
        _rows = []
        for _p in self.patches:
            _o = textwrap.dedent(_p.get("original_code") or "").strip()
            _m = textwrap.dedent(_p.get("modified_code") or "").strip()
            if _o and _m:
                _rows.append((difflib.SequenceMatcher(None, _o, _m).ratio(),
                              _p.get("id"), _o[:24]))
        self.assertTrue(_rows, "应至少有一个可复算样本")
        _min = min(_rows, key=lambda x: x[0])
        self.assertLess(_min[0], 0.62, "最紧样本 ratio 应接近阈值区（实测 {:.4f}）".format(_min[0]))
        self.assertNotRegex(_min[2], r"^\d{4}-\d{2}-\d{2}",
                            "最紧样本的原文应是代码而非日志文本（实际 {!r}）".format(_min[2]))


# ============================================================ D 开关契约
class TestT89aSwitchContract(unittest.TestCase):
    """灰度开关契约。"""

    def test_40_defaults(self):
        self.assertEqual(_m89_min_similarity(), 0.3, "默认相似度阈值应为 0.3")
        self.assertAlmostEqual(_m89_len_floor(), 1.0 / 3.0, places=6)

    def test_41_switch_off_reverts_to_legacy(self):
        """关闭开关 → 阈值逐字回到 0.5、长度下限关闭（零回归）。"""
        with _Switch(False):
            self.assertEqual(_m89_min_similarity(), 0.5)
            self.assertEqual(_m89_len_floor(), 0.0)

    def test_42_switch_since_m91_registered_in_config_py(self):
        """★契约变更（第91批 T-91c）：本开关已**正式登记**进 config.py，默认 True。  # _m91_t91c_switch_registered

        ★历史：第89批的批内红线是「不改 config.py」⇒ 当时断言 `assertNotIn`；
        第91批任务书 T-91c 明确解除该约束，要求把本开关（连同 M90/M91 共 5 个）
        正式登记到 config.py 且**默认值保持 True 不变** ⇒ 原断言按新契约反转。
        """
        _src = io.open(os.path.join(ROOT, "config.py"), encoding="utf-8").read()
        self.assertIn(_SWITCH, _src, "第91批 T-91c 起开关必须登记在 config.py")
        self.assertIn("{} = True".format(_SWITCH), _src, "登记默认值必须为 True（与登记前一致）")
        import config as _cfg
        self.assertTrue(getattr(_cfg, _SWITCH), "登记后的生效默认值应为 True")

    def test_43_length_floor_gone_when_switch_off(self):
        """开关关闭时，判定回退到旧语义（只有相似度关，无长度关）。"""
        _orig = _method_source(_PULSE_LIVER, _LONG_METHOD)
        _mod = _statement_prefix(_PULSE_LIVER, _LONG_METHOD, 10)
        with _Switch(False):
            _r = _check(_new_pm(), _orig, _mod)
            self.assertFalse(_r["complete"], "开关关闭后按旧阈值仍应拒绝该补丁")
            self.assertNotIn("长度不足", _r.get("reason", ""),
                             "开关关闭时不得出现长度下限判定")
            self.assertIn("0.5", _r.get("reason", ""), "开关关闭后 reason 应带旧阈值")

    def test_44_missing_switch_falls_back_to_enabled(self):
        """开关缺失（config 未定义）时按默认启用 —— 不得抛异常。"""
        _had = hasattr(config, _SWITCH)
        _old = getattr(config, _SWITCH, None)
        try:
            if _had:
                delattr(config, _SWITCH)
            self.assertEqual(_m89_min_similarity(), 0.3)
            self.assertAlmostEqual(_m89_len_floor(), 1.0 / 3.0, places=6)
        finally:
            if _had:
                setattr(config, _SWITCH, _old)

    def test_45_case_count_guard(self):
        """护栏：整文件用例数不得低于 16（防后续误删用例导致覆盖缩水）。"""
        _n = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]).countTestCases()
        self.assertGreaterEqual(_n, 16, "整文件用例数不得少于 16（实际 %d）" % _n)


if __name__ == "__main__":
    unittest.main(verbosity=2)
