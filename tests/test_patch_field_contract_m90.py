# -*- coding: utf-8 -*-
"""★主线第90批 T-90a：LLM 补丁「字段契约」（关0）门控单测。

背景（★与任务书前提的偏差，见交付报告 §1）：
    任务书 T-90a 的前提是「主链路根本没传 original_code」。
    第90批 T0 用三路独立证据实测**证伪**了该前提：
      ① `.bak_batch86`（=第85批末）起，`_llm_patch` 构造点就**已含**
         `original_code` + `modified_code`；逐批次 keys 恒含两字段；
      ② `git log -S '"original_code": _snippet'` 只命中初始提交 ⇒ 从未缺失过；
      ③ 对生产补丁库 75 条真补丁实调 `_check_llm_patch_completeness`：
         通过 75 / 被拒 0，相似度分布 [0.5006, 0.9994] —— 若关3 读到空
         `original_code`，`SequenceMatcher("", x).ratio()` 恒 0.0，绝无可能
         出现 0.5~1.0 ⇒ **关3 一直在生效**。
    故本批落地的是「**缺字段校验**」（关0），而不是「补字段」：
    把「字段没传」与「内容残缺」**分开报因**，因为改前「空 original_code」
    会被关3 以「与原文相似度过低(0.00 < 0.3)」的文案**伪装成内容残缺**。

被测对象 = **真实源码**（`PatchManager._check_llm_patch_completeness`），
不复刻任何逻辑；`.bak_batch90` 仅用于「改前行为对照」，缺失时 skipTest。

覆盖五组：
  A. 关0 语义 —— 缺 original_code / 缺 modified_code / 双缺 / 纯空白；
  B. 零回归证明 —— 空字段补丁在改前/改后**结论一致**（都拒），只改「理由」；
  C. 真实补丁库零回归 —— 生产 75 条补丁「改前判定 == 改后判定」逐条相等；
  D. 字段契约全库穷举 —— 生产源码里每个补丁 dict 字面量都必须同时含两字段；
  E. 开关契约 —— 默认值内联本模块、config.py 不含开关名、关闭即逐字回退。
"""
import ast
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from nucleus.reasoning.PatchManager import (
    _M90_PATCH_REQUIRED_FIELDS,
    PatchManager,
    _m90_missing_patch_fields,
    _m90_patch_field_contract_on,
)

_SWITCH = "ENABLE_M90_PATCH_FIELD_CONTRACT"
_BAK_PM = os.path.join(ROOT, ".bak_batch90", "nucleus", "reasoning", "PatchManager.py.bak")
_PENDING = os.path.join(ROOT, "data", "patches", "pending_patches.json")
_HISTORY = os.path.join(ROOT, "data", "patches", "patch_history.json")

_SKIP_DIRS = {"__pycache__", ".git", "venv", ".venv", "node_modules", "logs",
              "data", "models", "backups", "dist", "build", ".pytest_cache",
              "tmp", "tests"}


# ------------------------------------------------------------------ 被测封装
def _new_pm():
    """轻量实例（不跑 __init__）——被测方法不依赖任何实例状态。"""
    return PatchManager.__new__(PatchManager)


def _check(pm, patch: dict) -> dict:
    return pm._check_llm_patch_completeness(patch)


def _mk(original, modified):
    _p = {}
    if original is not None:
        _p["original_code"] = original
    if modified is not None:
        _p["modified_code"] = modified
    return _p


def _load_legacy_pm():
    """载入 `.bak_batch90` 里的**改前** PatchManager（缺失时返回 None）。

    ★`.bak_batch90/` 是 git-ignored 的 scratch 目录（可能被外部清理），
    故调用方必须在缺失时 skipTest，不得让用例永久失败。
    """
    if not os.path.isfile(_BAK_PM):
        return None
    _ld = importlib.machinery.SourceFileLoader("m90_legacy_patchmanager", _BAK_PM)
    _sp = importlib.util.spec_from_loader(_ld.name, _ld)
    _mod = importlib.util.module_from_spec(_sp)
    _ld.exec_module(_mod)
    return _mod


def _load_patch_library():
    """从真实补丁库递归抽取所有补丁 dict（pending + history）。"""
    _out = []
    for _p in (_PENDING, _HISTORY):
        if not os.path.isfile(_p):
            continue
        try:
            _d = json.loads(io.open(_p, encoding="utf-8").read())
        except Exception:
            continue
        _stack = [_d]
        _seen = set()
        while _stack:
            _o = _stack.pop()
            if isinstance(_o, dict):
                if id(_o) in _seen:
                    continue
                _seen.add(id(_o))
                if "modified_code" in _o:
                    _out.append(_o)
                _stack.extend(_o.values())
            elif isinstance(_o, list):
                _stack.extend(_o)
    return _out


def _iter_production_patch_dicts():
    """穷举**生产源码**（排除 tests/ 与 scratch 目录）里的补丁 dict 字面量。

    判据：dict 字面量的**字符串键**里含 `modified_code`。
    `tests/` 被排除是**有意的**：测试用例需要主动构造「残缺补丁」来断言拒绝行为，
    那是被测输入，不是生产者。
    """
    for _dp, _dn, _fn in os.walk(ROOT):
        _dn[:] = [d for d in _dn if d not in _SKIP_DIRS and not d.startswith(".")]
        for _f in _fn:
            if not _f.endswith(".py"):
                continue
            _fp = os.path.join(_dp, _f)
            try:
                _src = io.open(_fp, encoding="utf-8", errors="ignore").read()
                _tree = ast.parse(_src, filename=_fp)
            except Exception:
                continue
            for _n in ast.walk(_tree):
                if not isinstance(_n, ast.Dict):
                    continue
                _keys = []
                for _k in _n.keys:
                    if isinstance(_k, ast.Constant) and isinstance(_k.value, str):
                        _keys.append(_k.value)
                if _keys and "modified_code" in _keys:
                    yield os.path.relpath(_fp, ROOT).replace(os.sep, "/"), \
                        getattr(_n, "lineno", 0), _keys


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


# ============================================================ A 关0 语义
class TestT90aGateZeroSemantics(unittest.TestCase):
    """关0：字段契约必填校验。"""

    def setUp(self):
        self.pm = _new_pm()

    def test_10_required_fields_contract(self):
        """契约本身：必填字段清单就是这两项（顺序稳定，文案要用）。"""
        self.assertEqual(tuple(_M90_PATCH_REQUIRED_FIELDS),
                         ("original_code", "modified_code"))

    def test_11_missing_original_code(self):
        """缺 original_code → 以「缺少必填字段」+ 字段名拒绝（改前是「相似度过低」）。"""
        _r = _check(self.pm, _mk(None, "def f():\n    return 1\n"))
        self.assertFalse(_r["complete"])
        self.assertIn("缺少必填字段", _r["reason"], _r["reason"])
        self.assertIn("original_code", _r["reason"], _r["reason"])
        self.assertNotIn("相似度", _r["reason"],
                         "关0 必须抢在关3 之前，理由不得再是「相似度过低」")

    def test_12_missing_modified_code(self):
        """缺 modified_code → 同样以「缺少必填字段」拒绝。"""
        _r = _check(self.pm, _mk("def f():\n    return 0\n", None))
        self.assertFalse(_r["complete"])
        self.assertIn("缺少必填字段", _r["reason"], _r["reason"])
        self.assertIn("modified_code", _r["reason"], _r["reason"])

    def test_13_both_missing(self):
        """双缺 → 两个字段名都要出现在理由里（可诊断性）。"""
        _r = _check(self.pm, {})
        self.assertFalse(_r["complete"])
        self.assertIn("original_code", _r["reason"], _r["reason"])
        self.assertIn("modified_code", _r["reason"], _r["reason"])

    def test_14_whitespace_only_counts_as_missing(self):
        """纯空白视同缺失（strip 口径，与关1/关3 的 dedent+strip 一致）。"""
        _r = _check(self.pm, _mk("   \n\t  ", "def f():\n    return 1\n"))
        self.assertFalse(_r["complete"])
        self.assertIn("缺少必填字段", _r["reason"], _r["reason"])

    def test_15_missing_patch_fields_helper(self):
        """`_m90_missing_patch_fields` 是关0 的判据函数，逐例校验。"""
        self.assertEqual(_m90_missing_patch_fields({}),
                         ["original_code", "modified_code"])
        self.assertEqual(_m90_missing_patch_fields(_mk("a", "b")), [])
        self.assertEqual(_m90_missing_patch_fields(_mk("", "b")), ["original_code"])
        self.assertEqual(_m90_missing_patch_fields(_mk("a", "  ")), ["modified_code"])

    def test_16_non_string_values_do_not_crash(self):
        """字段可能是 None/int/list —— 关0 必须健壮（不得抛异常）。"""
        for _bad in (None, 0, [], {}, ""):
            _r = _check(self.pm, {"original_code": _bad, "modified_code": "def f():\n    return 1\n"})
            self.assertFalse(_r["complete"], "original_code=%r 应被拒" % (_bad,))


# ============================================================ B 零回归证明
class TestT90aZeroRegression(unittest.TestCase):
    """关0 只改「拒绝的理由」，不改「通过/拒绝的结论」。"""

    def setUp(self):
        self.pm = _new_pm()

    def _cases(self):
        return [
            ("缺 original", _mk(None, "def f():\n    return 1\n")),
            ("缺 modified", _mk("def f():\n    return 0\n", None)),
            ("双缺", {}),
            ("original 纯空白", _mk("   ", "def f():\n    return 1\n")),
            ("完整且合法", _mk("def f():\n    return 0\n", "def f():\n    return 1\n")),
            ("完整但与原文相同", _mk("def f():\n    return 0\n", "def f():\n    return 0\n")),
            ("完整但语法错误", _mk("def f():\n    return 0\n", "def f(:\n    return 1\n")),
        ]

    def test_20_verdicts_identical_to_legacy(self):
        """改前/改后对同一输入的 complete 结论必须**逐例相等**。"""
        _leg = _load_legacy_pm()
        if _leg is None:
            self.skipTest(".bak_batch90 缺失（scratch 目录可被外部清理），跳过改前对照")
        _leg_pm = _leg.PatchManager.__new__(_leg.PatchManager)
        for _name, _patch in self._cases():
            _old = _leg_pm._check_llm_patch_completeness(dict(_patch))
            _new = _check(self.pm, dict(_patch))
            self.assertEqual(_old["complete"], _new["complete"],
                             "[%s] 结论漂移：改前=%r 改后=%r" % (_name, _old, _new))
        self.assertTrue(True)

    def test_21_legacy_reported_wrong_reason(self):
        """★先红证据（永久可复现）：改前把「字段缺失」报成「内容残缺」。"""
        _leg = _load_legacy_pm()
        if _leg is None:
            self.skipTest(".bak_batch90 缺失，跳过改前对照")
        _leg_pm = _leg.PatchManager.__new__(_leg.PatchManager)
        _patch = _mk(None, "def f():\n    return 1\n")
        _old = _leg_pm._check_llm_patch_completeness(dict(_patch))
        _new = _check(self.pm, dict(_patch))
        # 改前：理由错误（伪装成内容残缺）
        self.assertNotIn("必填字段", _old["reason"], _old["reason"])
        # 改后：理由正确（点名字段）
        self.assertIn("必填字段", _new["reason"], _new["reason"])
        # 但两者都拒 —— 这正是「零回归」的含义
        self.assertFalse(_old["complete"])
        self.assertFalse(_new["complete"])


# ============================================================ C 真实补丁库
class TestT90aProductionLibrary(unittest.TestCase):
    """生产补丁库：关0 不得改判任何一条既有补丁。"""

    def setUp(self):
        self.pm = _new_pm()
        self.patches = _load_patch_library()

    def test_30_library_not_empty(self):
        """自检：真的读到了补丁（否则后面的断言是空转）。"""
        self.assertGreater(len(self.patches), 0,
                           "补丁库为空：%s / %s" % (_PENDING, _HISTORY))

    def test_31_no_verdict_drift_on_real_patches(self):
        """★核心零回归：75 条真实补丁「改前判定 == 改后判定」逐条相等。"""
        _leg = _load_legacy_pm()
        if _leg is None:
            self.skipTest(".bak_batch90 缺失，跳过改前对照")
        _leg_pm = _leg.PatchManager.__new__(_leg.PatchManager)
        _drift = []
        _rej_gate0 = 0
        for _p in self.patches:
            _old = _leg_pm._check_llm_patch_completeness(dict(_p))
            _new = _check(self.pm, dict(_p))
            if bool(_old["complete"]) != bool(_new["complete"]):
                _drift.append((_p.get("id", "?"), _old, _new))
            if not _new["complete"] and "必填字段" in _new["reason"]:
                _rej_gate0 += 1
        self.assertEqual(_drift, [], "存在判定漂移：%s" % (_drift[:3],))
        # 真实数据里不应有关0 拒绝（生产者一直传齐字段）—— 这是 T0 结论的回归护栏
        self.assertEqual(_rej_gate0, 0,
                         "生产补丁被关0 拒绝 %d 条 ⇒ 存在字段契约不完整的生产者" % _rej_gate0)

    def test_32_library_pass_rate_unchanged(self):
        """改后通过率必须与改前一致（用两边的通过数比对，避免写死魔数）。"""
        _leg = _load_legacy_pm()
        if _leg is None:
            self.skipTest(".bak_batch90 缺失，跳过改前对照")
        _leg_pm = _leg.PatchManager.__new__(_leg.PatchManager)
        _old_pass = sum(1 for _p in self.patches
                        if _leg_pm._check_llm_patch_completeness(dict(_p))["complete"])
        _new_pass = sum(1 for _p in self.patches
                        if _check(self.pm, dict(_p))["complete"])
        self.assertEqual(_old_pass, _new_pass,
                         "通过数漂移：改前=%d 改后=%d（共 %d 条）"
                         % (_old_pass, _new_pass, len(self.patches)))


# ============================================================ D 全库契约穷举
class TestT90aFieldContractSweep(unittest.TestCase):
    """把「全库补丁 dict 构造点必须字段齐」固化成断言，源码漂移时自动暴露。"""

    def test_40_no_production_patch_dict_lacks_original_code(self):
        _offenders = []
        _total = 0
        for _rel, _ln, _keys in _iter_production_patch_dicts():
            _total += 1
            if "original_code" not in _keys:
                _offenders.append("%s:%d" % (_rel, _ln))
        self.assertGreater(_total, 0, "穷举到 0 个构造点 ⇒ 扫描逻辑失效，断言无效")
        self.assertEqual(_offenders, [],
                         "以下补丁 dict 字面量缺 original_code：%s" % (_offenders,))

    def test_41_safe_evolution_executor_gate_present(self):
        """生产者侧同型闸 `_llm_no_snippet` 必须存在且参与构造条件。"""
        _p = os.path.join(ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
        _src = io.open(_p, encoding="utf-8", errors="ignore").read()
        self.assertIn("_llm_no_snippet", _src, "缺少 _llm_no_snippet 闸")
        self.assertIn("and not _llm_no_snippet", _src,
                      "闸未接入构造条件（存在但不生效 = 伪闸）")
        self.assertIn('"original_code": _snippet', _src,
                      "主链路构造点未传 original_code")

    def test_42_test_generator_fixture_completed(self):
        """`TestGenerator.__main__` 自测夹具历史上是唯一缺字段的构造点，已补齐。"""
        _p = os.path.join(ROOT, "nucleus", "evolution", "TestGenerator.py")
        _src = io.open(_p, encoding="utf-8", errors="ignore").read()
        _tree = ast.parse(_src, filename=_p)
        _found = []
        for _n in ast.walk(_tree):
            if isinstance(_n, ast.Dict):
                _keys = [_k.value for _k in _n.keys
                         if isinstance(_k, ast.Constant) and isinstance(_k.value, str)]
                if "modified_code" in _keys:
                    _found.append(sorted(_keys))
        self.assertTrue(_found, "TestGenerator 里找不到补丁夹具")
        for _keys in _found:
            self.assertIn("original_code", _keys, "夹具字段不全：%s" % (_keys,))


# ============================================================ E 开关契约
class TestT90aSwitchContract(unittest.TestCase):
    """灰度开关的完整性：默认开、config.py 无残留、关闭即逐字回退。"""

    def setUp(self):
        self.pm = _new_pm()

    def test_50_default_is_on(self):
        """未显式配置时默认启用（默认值内联在 PatchManager，不写 config.py）。"""
        self.assertTrue(_m90_patch_field_contract_on())

    def test_51_config_py_has_m90_switch_registered_by_m91(self):
        """★契约变更（第91批 T-91c）：本开关已**正式登记**进 config.py，默认 True。  # _m91_t91c_switch_registered

        ★历史：第90批的批内红线是「不改 config.py」⇒ 当时断言 `assertNotIn`；
        第91批任务书 T-91c 解除该约束（登记默认值不变，仍为 True）⇒ 断言反转。
        """
        _cfg = os.path.join(ROOT, "config.py")
        _src = io.open(_cfg, encoding="utf-8", errors="ignore").read()
        self.assertIn(_SWITCH, _src,
                      "第91批 T-91c 起 %s 必须正式登记在 config.py" % _SWITCH)
        self.assertIn("%s = True" % _SWITCH, _src, "登记默认值必须为 True（与登记前一致）")
        import config as _cfg_mod
        self.assertTrue(getattr(_cfg_mod, _SWITCH))

    def test_52_switch_off_rolls_back_verbatim(self):
        """关闭 → 逐字回到改前：理由重新变成关3/关1 的文案。"""
        _patch = _mk(None, "def f():\n    return 1\n")
        with _Switch(False):
            self.assertFalse(_m90_patch_field_contract_on())
            _r = _check(self.pm, dict(_patch))
            self.assertFalse(_r["complete"], "关闭后仍应被拒（改前行为）")
            self.assertNotIn("必填字段", _r["reason"],
                             "关闭后仍在走关0：%s" % _r["reason"])
        # 退出上下文 → 恢复默认（开）
        self.assertTrue(_m90_patch_field_contract_on())

    def test_53_switch_on_again_after_off(self):
        """开关可反复切换（读取侧每次重新 import config）。"""
        _patch = _mk(None, "def f():\n    return 1\n")
        for _v in (False, True, False, True):
            with _Switch(_v):
                _r = _check(self.pm, dict(_patch))
                self.assertEqual("必填字段" in _r["reason"], _v,
                                 "开关=%r 时理由=%r" % (_v, _r["reason"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
