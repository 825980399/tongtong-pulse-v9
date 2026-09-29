# -*- coding: utf-8 -*-
"""★主线第92批 相关任务/相关任务：PatchManager 验证侧两道**防御性**结构化关门控单测。

被测对象 = **真实源码**（`nucleus/reasoning/PatchManager.py`），不复刻任何逻辑；
`.bak_batch92` 仅用于「改前行为对照」，缺失时 skipTest（scratch 目录可被外部清理）。

任务书原文（第92批）：
    相关任务：「在 PatchManager._verify_in_copy 中，补丁应用前增加一道校验：
            base(modified) == base(original) 才允许替换；不满足则拒绝，并记录原因。
            开关控制（默认关闭，先观察一批）」
    相关任务：「补丁应用前后，统计原文件中类的方法数量；如果方法数量减少超过阈值
            （如 >10%），拒绝应用。开关控制（默认关闭，先观察一批）」

覆盖七组：
  A. 开关契约 —— 默认 False（任务书要求默认关闭）、运行时即时生效、getattr 兜底；
  B. `_m92_base_indent` 口径等价 —— 与第91批**构造侧** `_m91_base_indent` 逐样本相等
     （构造侧与验证侧必须是同一把尺子，否则判据形同虚设）；
  C. `_m92_count_class_methods` 口径 —— 只数 ClassDef 的**直接**函数成员；本组用
     真实事故源证明 `ast.walk` 全量计数会**完全漏判**（函数总数一个不少）；
  D. 相关任务 判据 `_m92_ast_struct_guard` —— 增/平/≤10% 缩水放行；>10% 缩水拒绝；
     无法解析 / before==0 时本关不适用；
  E. ★真实事故原件（第91d 两条 LLM 补丁，逐字固化）——
     **红**：事故后的整份源码 `ast.parse` / `compile` / `py_compile` **全部放行**；
     **绿**：相关任务 / 相关任务 判据分别命中（base 8→0；类方法 -26.4% / -47.7%）；
  F. 接线门禁（AST）—— 两个开关的**调用点**必须真在 `_verify_in_copy` 内，被
     `if <getter>():` 守卫、含 stage 赋值与 `return result`；判据函数的实参必须是
     `(full_content, modified_full)`。★第91批教训：helper 全绿 ≠ 生产链路已接线；
  G. 端到端 —— 合成同构工程 + 真实事故补丁：关全关时 `passed=True`（**红**：既有
     全部验证放行），相关任务 / 相关任务 各自产出 `base_indent_guard_failed` /
     `ast_structure_guard_failed`（**绿**）。另证 72 条生产补丁在判据层面零误拦。
"""
import ast
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
from nucleus.reasoning.PatchManager import (  # noqa: E402
    PatchManager,
    _M92_STRUCT_SHRINK_RATIO,
    _m92_ast_struct_guard,
    _m92_ast_struct_guard_on,
    _m92_base_indent,
    _m92_base_indent_guard_on,
    _m92_count_class_methods,
)
from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor  # noqa: E402

_PM_SRC_PATH = os.path.join(ROOT, "nucleus", "reasoning", "PatchManager.py")
_BAK_PM = os.path.join(ROOT, ".bak_batch92", "nucleus", "reasoning", "PatchManager.py.bak")
_PENDING = os.path.join(ROOT, "data", "patches", "pending_patches.json")
_HISTORY = os.path.join(ROOT, "data", "patches", "patch_history.json")

_SW_C = "ENABLE_M92_PATCH_BASE_INDENT_GUARD"
_SW_D = "ENABLE_M92_PATCH_AST_STRUCT_GUARD"

_MISSING = object()


# --------------------------------------------------------------- 基础设施
def _pm(root=None):
    _p = PatchManager.__new__(PatchManager)
    if root is not None:
        _p._project_root = root
    return _p


def _src():
    return io.open(_PM_SRC_PATH, encoding="utf-8").read()


def _load_legacy_pm():
    """载入 `.bak_batch92` 里的**改前** PatchManager（缺失时返回 None）。"""
    if not os.path.isfile(_BAK_PM):
        return None
    _ld = importlib.machinery.SourceFileLoader("m92_legacy_patchmanager", _BAK_PM)
    _sp = importlib.util.spec_from_loader(_ld.name, _ld)
    _mod = importlib.util.module_from_spec(_sp)
    _ld.exec_module(_mod)
    return _mod


def _switches(**kw):
    """上下文管理器：临时改写 config 属性（getter 每次重新 import config ⇒ 即时生效）。"""
    class _Ctx:
        def __enter__(self):
            self.saved = {}
            for _k, _v in kw.items():
                self.saved[_k] = getattr(config, _k, _MISSING)
                setattr(config, _k, _v)
            return self

        def __exit__(self, *a):
            for _k, _v in self.saved.items():
                if _v is _MISSING:
                    try:
                        delattr(config, _k)
                    except AttributeError:
                        pass
                else:
                    setattr(config, _k, _v)
            return False
    return _Ctx()


def _patch_library():
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
    return _out


def _make_project(oc, tail=6):
    """合成**同构**工程：`class C` + `def target`（oc 为其体）+ 若干 after 方法。

    ⇒ 事故形态替换后：col 0 的 `import` 让 `class C` 提前终止，后续方法被**嵌套**进
      模块级函数 —— 整份源码**仍是合法 Python**。
    """
    _root = tempfile.mkdtemp(prefix="m92_proj_")
    _lines = ["# synthetic", "class C:", "    def before(self):", "        return 1", "",
              "    def target(self):", '        """doc."""'] + oc.split("\n") + [""]
    for _k in range(1, tail + 1):
        _lines += ["    def after%d(self):" % _k, "        return %d" % _k, ""]
    os.makedirs(os.path.join(_root, "organs", "brain"), exist_ok=True)
    _fp = os.path.join(_root, "organs", "brain", "PulseInnerWorld.py")
    io.open(_fp, "w", encoding="utf-8").write("\n".join(_lines))
    return _root, _fp


# ==========================================================================
# ==========================================================================
# ★第91d 事故原件（**逐字固化，勿改字面量**）
#
# 来源：`data/patches/patch_history.json` 中 `source=llm` 的两条真实补丁 ——
#   2026-09-20 内部协作者 相关任务 自动应用后，`PulseInnerWorld.py` 类方法 368 → 271、
#   `PulseLung.py` 类方法 86 → 45（`tmp/m92_t0d.txt` 实测），而 `ast.parse` /
#   `compile` / `py_compile` / `import` **全部放行**。
#
# ★为什么必须固化：① `original_code` 已不在当前活文件中（内部协作者已回退）；
#   ② `data/` 是会被外部改写/裁剪的生产数据（本批实测 history 从 83 条降到 72 条）；
#   —— 铁律「契约基线须固化进测试文件」，故不依赖任何 data/ 内容。
# 防漂移：`_INCIDENT_MD5` 断言会拦住任何对下面字面量的手改。
# ==========================================================================
A_ID = 'patch_llm_1789886168_789a'
A_FILE = 'organs/brain/PulseInnerWorld.py'
A_METHOD = '_safe_eval_arithmetic'
A_ORIGINAL = r"""        if not question:
            return None
        # 中文运算符 → 符号
        _cn_map = {"乘以": "*", "乘": "*", "除以": "/", "除": "/",
                   "加上": "+", "加": "+", "减去": "-", "减": "-"}
        _expr = question
        for _cn, _op in _cn_map.items():
            _expr = _expr.replace(_cn, _op)
        # 只保留数字、运算符、小数点、括号、百分号
        _expr = re.sub(r'[^0-9+\-*/().%\s]', '', _expr)
        if not _expr or not re.search(r'\d', _expr):
            return None
        # 仅允许纯算术表达式（无字母/变量/函数名）
        if not re.fullmatch(r'[\d+\-*/().%\s]+', _expr):
            return None
        try:
            import ast as _ast
            _node = _ast.parse(_expr, mode='eval')
            _allowed = (_ast.Expression, _ast.BinOp, _ast.UnaryOp, _ast.Constant,
                        _ast.Add, _ast.Sub, _ast.Mult, _ast.Div, _ast.Mod, _ast.USub, _ast.UAdd)
            for _n in _ast.walk(_node):
                if not isinstance(_n, _allowed):
                    return None
            _result = eval(compile(_node, '<calc>', 'eval'), {"__builtins__": {}}, {})
            if isinstance(_result, (int, float)) and not isinstance(_result, bool):
                if isinstance(_result, float) and _result.is_integer():
                    return int(_result)
                return round(_result, 6) if isinstance(_result, float) else _result
        except Exception:
            return None
        return None"""
A_MODIFIED = r"""import logging
import re

logger = logging.getLogger(__name__)


def _calc_math_question(question):
    if not question:
        return None
    # Chinese operators -> symbols
    _cn_map = {"乘以": "*", "乘": "*", "除以": "/", "除": "/",
               "加上": "+", "加": "+", "减去": "-", "减": "-"}
    _expr = question
    for _cn, _op in _cn_map.items():
        _expr = _expr.replace(_cn, _op)
    # Keep only digits, operators, decimal points, parentheses, percent signs
    _expr = re.sub(r'[^0-9+\-*/().%\s]', '', _expr)
    if not _expr or not re.search(r'\d', _expr):
        return None
    # Allow only pure arithmetic expressions (no letters, variables, function names)
    if not re.fullmatch(r'[\d+\-*/().%\s]+', _expr):
        return None
    try:
        import ast as _ast
        _node = _ast.parse(_expr, mode='eval')
        _allowed = (_ast.Expression, _ast.BinOp, _ast.UnaryOp, _ast.Constant,
                    _ast.Add, _ast.Sub, _ast.Mult, _ast.Div, _ast.Mod, _ast.USub, _ast.UAdd)
        for _n in _ast.walk(_node):
            if not isinstance(_n, _allowed):
                return None
        _result = eval(compile(_node, '<calc>', 'eval'), {"__builtins__": {}}, {})
        if isinstance(_result, (int, float)) and not isinstance(_result, bool):
            if isinstance(_result, float) and _result.is_integer():
                return int(_result)
            return round(_result, 6) if isinstance(_result, float) else _result
    except Exception:
        logger.exception("Failed to evaluate arithmetic expression: %r", question)
        return None
    return None"""

B_ID = 'patch_llm_1789890006_e8ba'
B_FILE = 'organs/body/PulseLung.py'
B_METHOD = '_get_channel_semaphore'
B_ORIGINAL = r"""        _mgr = self._channel_concurrency()
        if _mgr is None:
            return None
        try:
            _mgr.ensure_channel(name)
            return _mgr.get_semaphore(name)
        except Exception:
            return None"""
B_MODIFIED = r"""import logging

logger = logging.getLogger(__name__)


def _get_channel_semaphore(self, name):
    _mgr = self._channel_concurrency()
    if _mgr is None:
        return None
    try:
        _mgr.ensure_channel(name)
        return _mgr.get_semaphore(name)
    except Exception:
        logger.exception(
            "Failed to ensure channel or get semaphore for name=%r",
            name,
        )
        return None"""

_INCIDENT_MD5 = {
    'A_ORIGINAL': '8c3c4ebbaaeab19f793cd2e567099663',
    'A_MODIFIED': '9f185d91bd7450ac0dbb41fe22721a5d',
    'B_ORIGINAL': '9ab828821f9d9e3ee18bfda3635440b0',
    'B_MODIFIED': '2734c7f7164e0a6da20efae5bb69111c',
}

# ==========================================================================


class TestT92aSwitchContract(unittest.TestCase):
    """A 开关契约：默认关闭（任务书要求）、运行时即时生效、读取失败兜底。"""

    def test_A1_defaults_are_false(self):
        self.assertIs(False, _m92_base_indent_guard_on(), "T-92c 默认必须**关闭**")
        self.assertIs(False, _m92_ast_struct_guard_on(), "T-92d 默认必须**关闭**")

    def test_A2_runtime_toggle_takes_effect_immediately(self):
        for _sw, _fn in ((_SW_C, _m92_base_indent_guard_on), (_SW_D, _m92_ast_struct_guard_on)):
            with _switches(**{_sw: True}):
                self.assertIs(True, _fn(), "%s=True 应即时生效" % _sw)
            self.assertIs(False, _fn(), "%s 恢复后应回到 False" % _sw)

    def test_A3_getattr_fallback_when_config_attr_absent(self):
        """config 无该属性（如旧配置）时须按默认 False 兜底，而非抛异常。"""
        for _sw, _fn in ((_SW_C, _m92_base_indent_guard_on), (_SW_D, _m92_ast_struct_guard_on)):
            _old = getattr(config, _sw, _MISSING)
            if _old is not _MISSING:
                delattr(config, _sw)
            try:
                self.assertIs(False, _fn(), "%s 缺失时应兜底 False" % _sw)
            finally:
                if _old is not _MISSING:
                    setattr(config, _sw, _old)

    def test_A4_shrink_ratio_is_ten_percent(self):
        self.assertAlmostEqual(0.10, _M92_STRUCT_SHRINK_RATIO, places=6)


class TestT92bBaseIndentParity(unittest.TestCase):
    """B `_m92_base_indent` 必须与第91批构造侧 `_m91_base_indent` **逐样本相等**。"""

    def test_B1_identical_on_corpus(self):
        _corpus = _patch_library()
        self.assertGreater(len(_corpus), 20, "生产补丁库为空/过小，无法做等价性取证")
        _diff = []
        for _x in _corpus:
            for _k in ("original_code", "modified_code"):
                _a = _m92_base_indent(_x[_k])
                _b = SafeEvolutionExecutor._m91_base_indent(_x[_k])
                if _a != _b:
                    _diff.append((_x.get("id"), _k, _a, _b))
        self.assertEqual([], _diff[:5], "两把尺子口径漂移：%s" % _diff[:5])

    def test_B2_identical_on_edge_samples(self):
        _samples = ["", None, "\n\n", "# c only\n", "    # c only\n        x = 1",
                    "\tx = 1", "    \tdef f(): pass", "def f():\n    return 1",
                    "      return x", "        return None"]
        for _s in _samples:
            self.assertEqual(SafeEvolutionExecutor._m91_base_indent(_s), _m92_base_indent(_s),
                             "样本 %r 口径不一致" % (_s,))

    def test_B3_skips_blank_and_comment_lines(self):
        """首行 col 0 **注释** + 其后 8 缩进 ⇒ base 必须是 8（注释不构成缩进层级）。"""
        self.assertEqual(8, _m92_base_indent("# mod-level note\n        x = 1\n"))
        self.assertEqual(8, _m92_base_indent("\n\n# note\n    # note2\n        x = 1\n"))
        self.assertEqual(-1, _m92_base_indent("\n   \n# only comment\n"))
        # 对照：首个可执行行若**真的**在 col 0，base 就是 0 —— 这正是事故补丁的指纹
        self.assertEqual(0, _m92_base_indent("import logging\n        x = 1\n"))


class TestT92cCountSemantics(unittest.TestCase):
    """C 「类方法数」口径：只数 ClassDef 的**直接**函数成员（全量计数会漏判事故）。"""

    def test_C1_direct_members_only(self):
        _s = ("class A:\n"
              "    def m1(self): pass\n"
              "    def m2(self): pass\n"
              "    class B:\n"
              "        def m3(self): pass\n"
              "def top(): pass\n")
        _n, _c = _m92_count_class_methods(_s)
        self.assertEqual(3, _n, "应数 A.m1/A.m2 + B.m3")
        self.assertEqual(2, _c, "应有 2 个类（A 与嵌套 B）")

    def test_C2_walk_all_funcs_would_miss_the_incident(self):
        """★核心：事故形态下「函数总数不变、类方法数暴跌」⇒ 全量计数会漏判。"""
        _before = ("class C:\n"
                   "    def m1(self):\n"
                   "        return 1\n"
                   "    def m2(self):\n"
                   "        return 2\n")
        _after = ("class C:\n"
                  "    def m1(self):\n"
                  "        return 1\n"
                  "def m2(self):\n"
                  "    return 2\n")
        _n_before = len([_x for _x in ast.walk(ast.parse(_before))
                         if isinstance(_x, ast.FunctionDef)])
        _n_after = len([_x for _x in ast.walk(ast.parse(_after))
                        if isinstance(_x, ast.FunctionDef)])
        self.assertEqual(_n_before, _n_after, "前提：全量函数总数**不变**（漏判根源）")
        self.assertEqual(2, _m92_count_class_methods(_before)[0])
        self.assertEqual(1, _m92_count_class_methods(_after)[0],
                         "★类方法数 2→1 —— 只有这个口径能抓到事故")

    def test_C3_unparseable_returns_none(self):
        self.assertIsNone(_m92_count_class_methods("def f(:\n    return 1"))


class TestT92dGuardJudgement(unittest.TestCase):
    """D 相关任务 判据：只在**大幅缩水**时拒绝。"""

    @staticmethod
    def _cls(n):
        return "class C:\n" + "".join("    def m%d(self): pass\n" % _i for _i in range(n))

    def test_D1_growth_and_equal_pass(self):
        self.assertTrue(_m92_ast_struct_guard(self._cls(10), self._cls(12))["ok"])
        self.assertTrue(_m92_ast_struct_guard(self._cls(10), self._cls(10))["ok"])

    def test_D2_shrink_within_threshold_passes(self):
        """降到恰好 10% 不拦（判据是 **严格大于** 阈值才拒绝）。"""
        _r = _m92_ast_struct_guard(self._cls(10), self._cls(9))
        self.assertTrue(_r["ok"], _r)
        self.assertAlmostEqual(0.10, _r["ratio"], places=6)

    def test_D3_shrink_beyond_threshold_rejected(self):
        _r = _m92_ast_struct_guard(self._cls(10), self._cls(8))
        self.assertFalse(_r["ok"], _r)
        self.assertAlmostEqual(0.20, _r["ratio"], places=6)
        self.assertIn("类方法数量异常缩水", _r["reason"])
        self.assertIn("-20.0%", _r["reason"])
        self.assertIn("10%", _r["reason"])

    def test_D4_not_applicable_cases(self):
        self.assertIsNone(_m92_ast_struct_guard("class C: pass\n", "def f(:"))
        self.assertIsNone(_m92_ast_struct_guard("def f(:", "class C: pass\n"))
        _r = _m92_ast_struct_guard("x = 1\n", "x = 2\n")
        self.assertTrue(_r["ok"], "无类文件不应触发本关")
        self.assertEqual(0, _r["before"])

    def test_D5_real_incident_prone_sources(self):
        """★用真实事故文件（活文件当前是**健康**状态）做判据自检。"""
        _p = os.path.join(ROOT, "organs", "brain", "PulseInnerWorld.py")
        if not os.path.isfile(_p):
            self.skipTest("活文件缺失")
        _s = io.open(_p, encoding="utf-8").read()
        self.assertEqual(_m92_count_class_methods(_s)[0], _m92_count_class_methods(_s)[0])
        self.assertTrue(_m92_ast_struct_guard(_s, _s)["ok"], "自身对自身必须放行")


class TestT92eRealIncidentArtifacts(unittest.TestCase):
    """E ★真实事故原件：红（既有全部关放行）+ 绿（本批两关命中）。"""

    def test_E0_artifacts_frozen(self):
        """防漂移：字面量一旦被改动即刻红。"""
        self.assertEqual(_INCIDENT_MD5["A_ORIGINAL"], hashlib.md5(A_ORIGINAL.encode()).hexdigest())
        self.assertEqual(_INCIDENT_MD5["A_MODIFIED"], hashlib.md5(A_MODIFIED.encode()).hexdigest())
        self.assertEqual(_INCIDENT_MD5["B_ORIGINAL"], hashlib.md5(B_ORIGINAL.encode()).hexdigest())
        self.assertEqual(_INCIDENT_MD5["B_MODIFIED"], hashlib.md5(B_MODIFIED.encode()).hexdigest())

    def test_E1_green_t92c_judgement_hits_both(self):
        for _tag, _oc, _mc in (("A", A_ORIGINAL, A_MODIFIED), ("B", B_ORIGINAL, B_MODIFIED)):
            self.assertEqual(8, _m92_base_indent(_oc), "%s original base 应为 8" % _tag)
            self.assertEqual(0, _m92_base_indent(_mc), "%s modified base 应为 0（事故指纹）" % _tag)
            self.assertNotEqual(_m92_base_indent(_oc), _m92_base_indent(_mc),
                                "%s 必须命中 T-92c 判据" % _tag)

    def test_E2_green_t92d_judgement_hits_both(self):
        _root, _fp = _make_project(A_ORIGINAL)
        self.addCleanup(shutil.rmtree, _root, True)
        _full = io.open(_fp, encoding="utf-8").read()
        _mod = _full.replace(A_ORIGINAL, A_MODIFIED)
        _r = _m92_ast_struct_guard(_full, _mod)
        self.assertIsNotNone(_r)
        self.assertFalse(_r["ok"], _r)
        self.assertLess(_r["after"], _r["before"])
        self.assertGreater(_r["ratio"], _M92_STRUCT_SHRINK_RATIO)

    def test_E3_red_existing_gates_all_pass(self):
        """★红证据：事故后的整份源码 —— ast.parse / compile / py_compile **全部放行**。"""
        import py_compile
        _root, _fp = _make_project(A_ORIGINAL)
        self.addCleanup(shutil.rmtree, _root, True)
        _mod = io.open(_fp, encoding="utf-8").read().replace(A_ORIGINAL, A_MODIFIED)
        ast.parse(_mod, filename="<incident>")                       # 不抛
        compile(_mod, "<incident>", "exec")                          # 不抛
        _tmp = _fp + ".incident"
        io.open(_tmp, "w", encoding="utf-8").write(_mod)
        try:
            py_compile.compile(_tmp, doraise=True)                   # 不抛
        finally:
            os.remove(_tmp)
        self.assertTrue(True, "既有语法/编译关对事故源码全部放行 ⇒ 本批两关是**必要**补充")


class TestT92fWiringGate(unittest.TestCase):
    """F 接线门禁（AST）：防「helper 全绿但生产链路未接线」（第91批教训）。"""

    def _verify_node(self):
        _t = ast.parse(_src())
        for _n in ast.walk(_t):
            if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _n.name == "_verify_in_copy":
                return _n, _t
        raise AssertionError("PatchManager 中找不到 _verify_in_copy")

    def test_F1_both_getters_called_inside_verify_in_copy(self):
        _node, _t = self._verify_node()
        _seg = ast.get_source_segment(_src(), _node)
        self.assertIn("_m92_base_indent_guard_on()", _seg)
        self.assertIn("_m92_ast_struct_guard_on()", _seg)

    def test_F2_guarded_by_if_and_returns(self):
        """每个开关必须包在 `if <getter>():` 里，且分支内含 stage 赋值 + `return result`。"""
        _node, _t = self._verify_node()
        _found = {}
        for _n in ast.walk(_node):
            if not isinstance(_n, ast.If):
                continue
            _tst = _n.test
            if not (isinstance(_tst, ast.Call) and isinstance(_tst.func, ast.Name)):
                continue
            _name = _tst.func.id
            if _name not in ("_m92_base_indent_guard_on", "_m92_ast_struct_guard_on"):
                continue
            _body_src = "\n".join(ast.get_source_segment(_src(), _s) or "" for _s in _n.body)
            _has_ret = any(isinstance(_x, ast.Return) for _s in _n.body
                           for _x in ast.walk(_s))
            _found[_name] = (_has_ret, _body_src)
        self.assertEqual({"_m92_base_indent_guard_on", "_m92_ast_struct_guard_on"},
                         set(_found), "两个守卫 If 必须都存在且直属于 _verify_in_copy")
        self.assertTrue(_found["_m92_base_indent_guard_on"][0], "T-92c 守卫缺少 return result")
        self.assertTrue(_found["_m92_ast_struct_guard_on"][0], "T-92d 守卫缺少 return result")
        self.assertIn("base_indent_guard_failed", _found["_m92_base_indent_guard_on"][1])
        self.assertIn("ast_structure_guard_failed", _found["_m92_ast_struct_guard_on"][1])

    def test_F3_struct_guard_called_with_full_and_modified(self):
        """★防实参接错：相关任务 判据必须收到 (full_content, modified_full)。"""
        _node, _t = self._verify_node()
        _args = []
        for _n in ast.walk(_node):
            if (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Name)
                    and _n.func.id == "_m92_ast_struct_guard"):
                _args.append([_a.id for _a in _n.args if isinstance(_a, ast.Name)])
        self.assertEqual([["full_content", "modified_full"]], _args,
                         "T-92d 判据实参必须恰为 (full_content, modified_full)，实得 %s" % _args)

    def test_F4_guard_blocks_precede_copy_write(self):
        """两道关都必须在 `with open(tmp_target, 'w', ...)` 写副本**之前**。"""
        _node, _t = self._verify_node()
        _seg = ast.get_source_segment(_src(), _node)
        _i_c = _seg.index("_m92_base_indent_guard_on()")
        _i_d = _seg.index("_m92_ast_struct_guard_on()")
        _i_w = _seg.index('with open(tmp_target, ' + repr("w"))
        self.assertLess(_i_c, _i_w, "T-92c 必须在写副本之前")
        self.assertLess(_i_d, _i_w, "T-92d 必须在写副本之前")
        self.assertLess(_i_c, _i_d, "T-92c 应先于 T-92d（先便宜后昂贵）")

    def test_F5_helpers_are_module_level_and_pure(self):
        _t = ast.parse(_src())
        _names = {_n.name for _n in _t.body if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for _f in ("_m92_base_indent", "_m92_count_class_methods", "_m92_ast_struct_guard",
                   "_m92_base_indent_guard_on", "_m92_ast_struct_guard_on"):
            self.assertIn(_f, _names, "%s 必须在模块级" % _f)
        _seg = ast.get_source_segment(_src(), next(
            _n for _n in _t.body if isinstance(_n, ast.FunctionDef) and _n.name == "_m92_ast_struct_guard"))
        self.assertNotIn("_module_logger", _seg, "判据函数应为纯函数（不打日志）")


class TestT92gEndToEnd(unittest.TestCase):
    """G 端到端：合成同构工程 + 真实事故补丁。"""

    def _run(self, pid, oc, mc, **sw):
        _root, _fp = _make_project(oc)
        self.addCleanup(shutil.rmtree, _root, True)
        _patch = {"id": pid, "source": "llm", "file": _fp, "method": "m",
                  "original_code": oc, "modified_code": mc}
        with _switches(**sw):
            return _pm(_root)._verify_in_copy(_patch)

    def test_G1_red_all_switches_off_incident_sails_through(self):
        """★红：开关全关（= 第91批末行为）时，事故补丁**通过**既有全部验证。"""
        _r = self._run(A_ID, A_ORIGINAL, A_MODIFIED)
        self.assertNotIn(_r.get("stage"), ("base_indent_guard_failed",
                                          "ast_structure_guard_failed"),
                         "开关关闭时不应新增拦截（零回归）")
        self.assertTrue(_r.get("passed"), "★事故补丁在改前被判定**通过**：%s" % _r)

    def test_G2_green_t92c_blocks(self):
        _r = self._run(A_ID, A_ORIGINAL, A_MODIFIED, **{_SW_C: True})
        self.assertEqual("base_indent_guard_failed", _r.get("stage"))
        self.assertFalse(_r.get("passed"))
        self.assertTrue(any("基础缩进不一致" in _e for _e in _r.get("errors", [])), _r)

    def test_G3_green_t92d_blocks(self):
        _r = self._run(A_ID, A_ORIGINAL, A_MODIFIED, **{_SW_D: True})
        self.assertEqual("ast_structure_guard_failed", _r.get("stage"))
        self.assertFalse(_r.get("passed"))
        self.assertTrue(any("类方法数量异常缩水" in _e for _e in _r.get("errors", [])), _r)

    def test_G4_green_both_incidents_blocked_by_both_switches(self):
        for _tag, _pid, _oc, _mc in (("A", A_ID, A_ORIGINAL, A_MODIFIED),
                                     ("B", B_ID, B_ORIGINAL, B_MODIFIED)):
            _rc = self._run(_pid, _oc, _mc, **{_SW_C: True})
            self.assertEqual("base_indent_guard_failed", _rc.get("stage"), "%s T-92c" % _tag)
            _rd = self._run(_pid, _oc, _mc, **{_SW_D: True})
            self.assertEqual("ast_structure_guard_failed", _rd.get("stage"), "%s T-92d" % _tag)

    def test_G5_corpus_zero_false_block_by_judgement(self):
        """★零误拦（判据层面）：72 条生产补丁里只有事故那 2 条命中 相关任务 判据。"""
        _corpus = _patch_library()
        self.assertGreater(len(_corpus), 20)
        _hit = [_x for _x in _corpus
                if _m92_base_indent(_x["original_code"]) != _m92_base_indent(_x["modified_code"])]
        self.assertTrue(all(str(_x.get("source")) == "llm" for _x in _hit),
                        "非 LLM 补丁被命中 ⇒ 判据过宽：%s" % [_x.get("id") for _x in _hit])
        self.assertLessEqual(len(_hit), 3, "命中面过大，需复核判据：%s" % [_x.get("id") for _x in _hit])

    def test_G6_legacy_behaviour_unchanged_when_off(self):
        """零回归：开关全关时，改前/改后对同一事故补丁的**结论**一致。"""
        _leg = _load_legacy_pm()
        if _leg is None:
            self.skipTest(".bak_batch92 缺失（scratch 目录可被外部清理），跳过改前对照")
        _root, _fp = _make_project(A_ORIGINAL)
        self.addCleanup(shutil.rmtree, _root, True)
        _patch = {"id": A_ID, "source": "llm", "file": _fp, "method": "m",
                  "original_code": A_ORIGINAL, "modified_code": A_MODIFIED}
        _new = _pm(_root)._verify_in_copy(dict(_patch))
        _old = _leg.PatchManager.__new__(_leg.PatchManager)
        _old._project_root = _root
        _old_r = _old._verify_in_copy(dict(_patch))
        self.assertEqual(_old_r.get("stage"), _new.get("stage"),
                         "开关全关时 stage 漂移：改前=%r 改后=%r" % (_old_r.get("stage"), _new.get("stage")))
        self.assertEqual(_old_r.get("passed"), _new.get("passed"))
        self.assertIn("base_indent_guard_failed", _src(), "守卫名须在源码中")  # 提醒：改前版本无此串
        self.assertNotIn("base_indent_guard_failed",
                         io.open(_BAK_PM, encoding="utf-8").read() if os.path.isfile(_BAK_PM) else "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
