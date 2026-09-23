# -*- coding: utf-8 -*-
"""★主线第93批 T-93a：`apply_all_pending` **写盘点**结构关 门控单测。

被测对象 = **真实源码**（`nucleus/reasoning/PatchManager.py`），不复刻任何逻辑；
`.bak_batch93` 仅用于「改前行为对照」，缺失时 skipTest（scratch 目录可被外部清理）。

任务书原文（第93批 T-93a）：
    「第92批在 `_verify_in_copy` 加了结构关（基础缩进 + AST 不变量），但
      `apply_all_pending` 写盘点（`:2102` 附近）只有 `ast.parse` 复验。
      风险：补丁绕过 `_verify_in_copy` 直落活文件时，结构关不生效。
      改法：在写盘点复用第92批纯函数补一道；开关复用，不新增；默认关闭。
      验收：写盘点与验证侧判据一致；既有补丁应用流程不受影响（开关关闭时零行为变化）」

★本批 T0 的**前提修正**（与任务书表述不同，已实证）：
    写盘点**不是**「绕过验证」的独立路径 —— `apply_all_pending` 在 L2250 先调
    `_verify_in_copy(patch)`，不通过即 `rejected_verify` 早退，**根本到不了写盘点**。
    又因两处共用同一对开关与同一对判据，开关开启时**验证侧必然先拦**
    ⇒ 写盘点关在真实链路上是**不可达的第二道闸（纵深防御）**，
    而**非**任务书设想的「唯一防线」。B 组即为该结论的可执行证据。
    ⇒ 本关的价值：① 覆盖未来可能新增的、不经 `_verify_in_copy` 的写盘路径；
       ② 让「同一判据落在两处」成为可断言的一致性锚点。

覆盖六组：
  A. 静态接线（AST）—— 两关真在 `apply_all_pending` 内、在 `.py` 分支内、顺序正确、
     与验证侧调用**同一对纯函数**、守卫体含拒绝四件套；
  B. ★可达性实测 —— 真实链路下事故补丁被 `rejected_verify` 拦（**不是**写盘点的
     `rejected_*_guard`）⇒ 证明写盘点关不可达；且写盘点唯一；
  C. 写盘点关本身（mock 前置闸门直达）—— 先红后绿 + 拒绝时**不写坏文件**；
  D. 判据一致性 —— 写盘侧与验证侧同源、同数值；
  E. 零行为变化 —— 开关默认关闭；改前/改后开关全关时结论一致；
  F. 先红后绿 —— 改前（`.bak_batch93`）不含写盘点结构关。
"""
import ast
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

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
)

_PM_SRC_PATH = os.path.join(ROOT, "nucleus", "reasoning", "PatchManager.py")
_BAK_PM = os.path.join(ROOT, ".bak_batch93", "nucleus", "reasoning", "PatchManager.py.bak")

_SW_C = "ENABLE_M92_PATCH_BASE_INDENT_GUARD"
_SW_D = "ENABLE_M92_PATCH_AST_STRUCT_GUARD"

_MISSING = object()

# ---------------------------------------------------------------------------
# 合成「事故形态」样本：modified 首行落在 col 0（base 8 → 0）
#   ⇒ 原位替换后 `class C` 体提前终止，后续方法被嵌套进模块级函数，
#     整份源码**仍是合法 Python**（`ast.parse` / `compile` 全放行）。
#   ★modified 必须与 original **内容不同**，否则先被「修改内容为空」类判定拦。
# ---------------------------------------------------------------------------
_OC = (
    '        if not question:\n'
    '            return None\n'
    '        _cn_map = {"乘以": "*"}\n'
    '        _expr = question\n'
    '        for _cn, _op in _cn_map.items():\n'
    '            _expr = _expr.replace(_cn, _op)\n'
    '        return _expr\n'
)
_MC = (
    'def _calc_math_question(question):\n'
    '    if not question:\n'
    '        return None\n'
    '    _cn_map = {"乘以": "*"}\n'
    '    _expr = question\n'
    '    for _cn, _op in _cn_map.items():\n'
    '        _expr = _expr.replace(_cn, _op)\n'
    '    return _expr\n'
)


# --------------------------------------------------------------- 基础设施
def _src():
    return io.open(_PM_SRC_PATH, encoding="utf-8").read()


def _tree():
    return ast.parse(_src())


def _method_node(name):
    """→ PatchManager 类方法 `name` 的 FunctionDef 节点。"""
    for _nd in _tree().body:
        if isinstance(_nd, ast.ClassDef) and _nd.name == "PatchManager":
            for _y in _nd.body:
                if isinstance(_y, (ast.FunctionDef, ast.AsyncFunctionDef)) and _y.name == name:
                    return _y
    raise AssertionError("未找到 PatchManager.%s" % name)


def _calls_in(node):
    """→ 该函数体内所有被调用的函数名集合（含属性调用取末段）。"""
    _out = set()
    for _n in ast.walk(node):
        if isinstance(_n, ast.Call):
            _f = _n.func
            if isinstance(_f, ast.Name):
                _out.add(_f.id)
            elif isinstance(_f, ast.Attribute):
                _out.add(_f.attr)
    return _out


def _switches(**kw):
    """上下文管理器：临时改写 config 属性（getter 每次重新 import ⇒ 即时生效）。"""
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


def _make_project(oc, tail=6):
    """合成**同构**工程：`class C` + `def target`（oc 为其体）+ 若干 after 方法。"""
    _root = tempfile.mkdtemp(prefix="m93_proj_")
    _lines = ["# synthetic", "class C:", "    def before(self):", "        return 1", "",
              "    def target(self):", '        """doc."""'] + oc.split("\n") + [""]
    for _k in range(1, tail + 1):
        _lines += ["    def after%d(self):" % _k, "        return %d" % _k, ""]
    os.makedirs(os.path.join(_root, "organs", "brain"), exist_ok=True)
    _fp = os.path.join(_root, "organs", "brain", "PulseInnerWorld.py")
    io.open(_fp, "w", encoding="utf-8").write("\n".join(_lines))
    return _root, _fp


def _load_legacy_pm():
    """载入 `.bak_batch93` 里的**改前** PatchManager（缺失时返回 None）。"""
    if not os.path.isfile(_BAK_PM):
        return None
    import importlib.machinery
    import importlib.util
    _ld = importlib.machinery.SourceFileLoader("m93_legacy_patchmanager", _BAK_PM)
    _sp = importlib.util.spec_from_loader(_ld.name, _ld)
    _mod = importlib.util.module_from_spec(_sp)
    _ld.exec_module(_mod)
    return _mod


def _run_apply(oc=_OC, mc=_MC, mock_gates=False, **sw):
    """端到端：合成工程 + 真实 `apply_all_pending`。

    mock_gates=True 时把「人格基线 / 安全检查 / 副本验证」三道**前置**闸门 mock 为
    放行，使流程直达**写盘点**（用于验证写盘点关本身）。
    """
    _root, _fp = _make_project(oc)
    _pmg = PatchManager(_root)
    _p = {"id": "p93", "source": "llm", "file": _fp, "method": "target",
          "original_code": oc, "modified_code": mc,
          "status": "approved", "line": 10}
    io.open(_pmg.get_pending_file(), "w", encoding="utf-8").write(
        json.dumps([_p], ensure_ascii=False))
    _before = io.open(_fp, encoding="utf-8").read()

    _ctx = mock.patch.object(PatchManager, "_check_personality_baseline",
                             return_value={"safe": True, "reason": ""}), \
        mock.patch.object(PatchManager, "_check_patch_safety",
                          return_value={"safe": True, "reason": ""}), \
        mock.patch.object(PatchManager, "_verify_in_copy",
                          return_value={"passed": True, "stage": "mocked", "errors": []})

    try:
        if mock_gates:
            for _c in _ctx:
                _c.start()
        try:
            with _switches(**sw):
                _r = _pmg.apply_all_pending(only_approved=False)
        finally:
            if mock_gates:
                for _c in _ctx:
                    _c.stop()
        _after = io.open(_fp, encoding="utf-8").read()
    finally:
        shutil.rmtree(_root, True)

    _det = _r.get("details", [])
    return {
        "applied": _r.get("applied"),
        "failed": _r.get("failed"),
        "status": [_d.get("status") for _d in _det],
        "reason": " ".join(str(_d.get("reason", "")) for _d in _det),
        "file_changed": _before != _after,
    }


# ==========================================================================
# A. 静态接线
# ==========================================================================
class TestT93aStaticWiring(unittest.TestCase):

    def test_A1_both_getters_called_inside_apply_all_pending(self):
        _names = _calls_in(_method_node("apply_all_pending"))
        self.assertIn("_m92_base_indent_guard_on", _names,
                      "写盘点必须调用基础缩进关开关")
        self.assertIn("_m92_ast_struct_guard_on", _names,
                      "写盘点必须调用结构不变量关开关")

    def test_A2_new_reject_statuses_present(self):
        _s = _src()
        self.assertIn("rejected_base_indent_guard", _s)
        self.assertIn("rejected_ast_structure_guard", _s)

    def test_A3_order_base_indent_then_syntax_then_struct(self):
        """顺序：缩进关 → 既有语法复验 → 结构关（结构关需 replace 后的全文）。"""
        _s = _src()
        _seg = ast.get_source_segment(_s, _method_node("apply_all_pending"))
        self.assertIsNotNone(_seg)
        _i_base = _seg.find("_m92_base_indent_guard_on()")
        _i_syn = _seg.find('ast.parse(modified_full, filename="<llm-patch>")')
        _i_st = _seg.find("_m92_ast_struct_guard_on()")
        self.assertGreater(_i_base, -1)
        self.assertGreater(_i_syn, -1)
        self.assertGreater(_i_st, -1)
        self.assertLess(_i_base, _i_syn, "缩进关应在语法复验之前")
        self.assertLess(_i_syn, _i_st, "结构关应在语法复验之后（此时已有 modified_full）")

    def test_A4_same_pure_functions_as_verify_side(self):
        """★判据一致性：两处必须调用**同一对纯函数**。"""
        _apply = _calls_in(_method_node("apply_all_pending"))
        _verify = _calls_in(_method_node("_verify_in_copy"))
        for _f in ("_m92_base_indent", "_m92_ast_struct_guard"):
            self.assertIn(_f, _apply, "写盘点未调用 %s" % _f)
            self.assertIn(_f, _verify, "验证侧未调用 %s" % _f)

    def test_A5_guards_live_inside_py_only_branch(self):
        """两关必须在 `if patch["file"].endswith(".py"):` 分支内（非 .py 文件无意义）。"""
        _node = _method_node("apply_all_pending")
        _found = []
        for _n in ast.walk(_node):
            if not isinstance(_n, ast.If):
                continue
            _t = ast.get_source_segment(_src(), _n.test) or ""
            if "endswith" in _t and ".py" in _t:
                _body = ast.get_source_segment(_src(), _n) or ""
                for _f in ("_m92_base_indent_guard_on", "_m92_ast_struct_guard_on"):
                    if _f in _body:
                        _found.append(_f)
        self.assertIn("_m92_base_indent_guard_on", _found,
                      "缩进关未落在 `.py` 分支内")
        self.assertIn("_m92_ast_struct_guard_on", _found,
                      "结构关未落在 `.py` 分支内")

    def test_A6_reject_body_has_four_pieces(self):
        """守卫体必须是完整拒绝四件套：applied=False / failed+=1 / details / continue。"""
        _seg = ast.get_source_segment(_src(), _method_node("apply_all_pending")) or ""
        for _k in ('patch["applied"] = False',
                   'results["failed"] += 1',
                   'results["details"].append({',
                   'continue'):
            self.assertIn(_k, _seg, "写盘点拒绝体缺少：%s" % _k)

    def test_A7_guard_functions_are_module_level_pure(self):
        _names = {_n.name for _n in _tree().body if isinstance(_n, ast.FunctionDef)}
        for _f in ("_m92_base_indent", "_m92_ast_struct_guard",
                   "_m92_base_indent_guard_on", "_m92_ast_struct_guard_on"):
            self.assertIn(_f, _names, "%s 必须在模块级" % _f)


# ==========================================================================
# B. ★可达性（本批核心发现）
# ==========================================================================
class TestT93bReachability(unittest.TestCase):
    """写盘点关在真实链路上的可达性 —— 实测而非推断。"""

    def test_B1_verify_in_copy_runs_before_write_point_statically(self):
        _seg = ast.get_source_segment(_src(), _method_node("apply_all_pending")) or ""
        _i_v = _seg.find("_verify_in_copy(patch)")
        _i_write = _seg.find('os.replace(_tmp_path, patch["file"])')
        self.assertGreater(_i_v, -1, "apply_all_pending 必须先跑副本验证")
        self.assertGreater(_i_write, -1)
        self.assertLess(_i_v, _i_write, "副本验证必须早于写活文件")

    def test_B2_real_chain_blocked_by_verify_side_not_write_side(self):
        """★核心：T-92c 开启时，真实链路被 `rejected_verify` 拦 ——
        证明写盘点的 `rejected_base_indent_guard` **不可达**。"""
        _r = _run_apply(**{_SW_C: True})
        self.assertEqual(0, _r["applied"])
        self.assertEqual(["rejected_verify"], _r["status"])
        self.assertNotIn("rejected_base_indent_guard", _r["status"],
                         "★若出现写盘点 stage，说明本批可达性结论已变，须重评 T-93a 价值")
        self.assertFalse(_r["file_changed"])

    def test_B3_same_for_struct_guard(self):
        _r = _run_apply(**{_SW_D: True})
        self.assertEqual(0, _r["applied"])
        self.assertEqual(["rejected_verify"], _r["status"])
        self.assertNotIn("rejected_ast_structure_guard", _r["status"])
        self.assertFalse(_r["file_changed"])

    def test_B4_public_verify_in_copy_is_pure_delegation(self):
        """公开封装 `verify_in_copy` 只转发，不是新的写盘入口。"""
        _n = _method_node("verify_in_copy")
        _seg = ast.get_source_segment(_src(), _n) or ""
        self.assertIn("return self._verify_in_copy(patch)", _seg)
        self.assertNotIn("os.replace", _seg)
        self.assertNotIn("modified_full", _seg)

    def test_B5_single_code_write_point(self):
        """★全类「写活代码」的路径唯一（`os.replace(_tmp_path, patch["file"])`）。

        全类共有 **2** 处 `tempfile.mkstemp` + `os.fdopen` 写盘对，勿混淆：
          * `apply_all_pending`（写**代码**活文件，`f.write(modified_full)`）← 本关覆盖点
          * `_save_json`（写**JSON** 元数据，`json.dump(data, f, ...)`）← 与代码结构无关
        `f.write(modified_full)` 亦出现 2 次：一次是 `_verify_in_copy` 写**沙箱副本**
        （`tmp_target`），一次是 `apply_all_pending` 写**活文件**。
        真正决定「补丁落到活代码」的只有 `os.replace(_tmp_path, patch["file"])` 那一条。
        """
        _s = _src()
        self.assertEqual(1, _s.count('os.replace(_tmp_path, patch["file"])'),
                         "代码写盘点不唯一 ⇒ 写盘点关覆盖面须复核")
        self.assertEqual(2, _s.count("f.write(modified_full)"),
                         "应为「写副本」+「写活文件」各一次")
        # 第二处 mkstemp/fdopen 属 `_save_json`（JSON 元数据），不是代码写盘
        self.assertEqual(1, _s.count('os.replace(tmp_path, path)'))
        self.assertEqual(1, _s.count("json.dump(data, f, ensure_ascii=False, indent=2)"))


# ==========================================================================
# C. 写盘点关本身（mock 前置闸门直达）
# ==========================================================================
class TestT93cWritePointGuard(unittest.TestCase):

    def test_C1_red_all_off_incident_is_applied(self):
        """红：开关全关（= 第92批末行为）时，事故补丁**照常写入**（零回归基线）。"""
        _r = _run_apply(mock_gates=True)
        self.assertEqual(1, _r["applied"], _r)
        self.assertEqual(["applied"], _r["status"], _r)
        self.assertTrue(_r["file_changed"])

    def test_C2_green_t93c_blocks_at_write_point(self):
        _r = _run_apply(mock_gates=True, **{_SW_C: True})
        self.assertEqual(["rejected_base_indent_guard"], _r["status"], _r)
        self.assertEqual(0, _r["applied"])
        self.assertEqual(1, _r["failed"])
        self.assertIn("基础缩进不一致（未写入）", _r["reason"])
        self.assertFalse(_r["file_changed"], "★拒绝时绝不写坏文件")

    def test_C3_green_t93d_blocks_at_write_point(self):
        _r = _run_apply(mock_gates=True, **{_SW_D: True})
        self.assertEqual(["rejected_ast_structure_guard"], _r["status"], _r)
        self.assertEqual(0, _r["applied"])
        self.assertIn("类方法数量异常缩水", _r["reason"])
        self.assertFalse(_r["file_changed"], "★拒绝时绝不写坏文件")

    def test_C4_benign_patch_passes_both_guards(self):
        """良性补丁（等缩进、不缩水）在开关全开时正常写入 ⇒ 关不误拦。"""
        _oc = ("        if not question:\n"
               "            return None\n"
               "        return question\n")
        _mc = ("        if not question:\n"
               "            return None\n"
               "        return str(question)\n")
        _r = _run_apply(oc=_oc, mc=_mc, mock_gates=True, **{_SW_C: True, _SW_D: True})
        self.assertEqual(1, _r["applied"], _r)
        self.assertEqual(["applied"], _r["status"], _r)

    def test_C5_guard_is_off_by_default(self):
        self.assertFalse(_m92_base_indent_guard_on(), "写盘侧复用开关，默认必须关闭")
        self.assertFalse(_m92_ast_struct_guard_on())


# ==========================================================================
# D. 判据一致性（写盘侧 vs 验证侧）
# ==========================================================================
class TestT93dParityWithVerifySide(unittest.TestCase):

    def test_D1_threshold_shared(self):
        self.assertEqual(0.10, _M92_STRUCT_SHRINK_RATIO)

    def test_D2_indent_judgement_identical_value(self):
        """写盘侧与验证侧对同一补丁的基础缩进读数必须一致（同一函数）。"""
        self.assertEqual(8, _m92_base_indent(_OC))
        self.assertEqual(0, _m92_base_indent(_MC))
        self.assertNotEqual(_m92_base_indent(_OC), _m92_base_indent(_MC))

    def test_D3_struct_judgement_identical_value(self):
        _root, _fp = _make_project(_OC)
        self.addCleanup(shutil.rmtree, _root, True)
        _full = io.open(_fp, encoding="utf-8").read()
        _mod = _full.replace(_OC, _MC)
        _v = _m92_ast_struct_guard(_full, _mod)
        self.assertIsNotNone(_v)
        self.assertFalse(_v["ok"])
        self.assertEqual(8, _v["before"])
        self.assertEqual(2, _v["after"])
        self.assertAlmostEqual(0.75, _v["ratio"], places=6)

    def test_D4_write_point_passes_full_content_not_snippet(self):
        """结构关的实参必须是 `(full_content, modified_full)`（整份源码），
        传片段会导致类方法计数恒为 0 ⇒ 关形同虚设。"""
        _seg = ast.get_source_segment(_src(), _method_node("apply_all_pending")) or ""
        self.assertIn("_m92_ast_struct_guard(full_content, modified_full)", _seg)


# ==========================================================================
# E. 零行为变化
# ==========================================================================
class TestT93eZeroRegression(unittest.TestCase):

    def test_E1_write_point_guard_absent_before_batch(self):
        if not os.path.isfile(_BAK_PM):
            self.skipTest(".bak_batch93 缺失（scratch 目录可被外部清理）")
        _old = io.open(_BAK_PM, encoding="utf-8").read()
        self.assertNotIn("rejected_base_indent_guard", _old)
        self.assertNotIn("rejected_ast_structure_guard", _old)
        self.assertIn("rejected_base_indent_guard", _src())

    def test_E2_legacy_conclusion_unchanged_when_switches_off(self):
        """★零回归：开关全关时，改前/改后 `apply_all_pending` 对同一补丁结论一致。"""
        _leg = _load_legacy_pm()
        if _leg is None:
            self.skipTest(".bak_batch93 缺失，跳过改前对照")
        _root, _fp = _make_project(_OC)
        self.addCleanup(shutil.rmtree, _root, True)
        _p = {"id": "p93", "source": "llm", "file": _fp, "method": "target",
              "original_code": _OC, "modified_code": _MC,
              "status": "approved", "line": 10}

        def _run(_cls):
            _m = _cls(_root)
            io.open(_m.get_pending_file(), "w", encoding="utf-8").write(
                json.dumps([dict(_p)], ensure_ascii=False))
            _b = io.open(_fp, encoding="utf-8").read()
            _r = _m.apply_all_pending(only_approved=False)
            _a = io.open(_fp, encoding="utf-8").read()
            return (_r.get("applied"), _r.get("failed"),
                    [_d.get("status") for _d in _r.get("details", [])], _b != _a)

        _new = _run(PatchManager)
        # 用**独立的合成工程**跑改前版本，保证两侧起点完全相同（避免文件被改后互相污染）
        _root2, _fp2 = _make_project(_OC)
        self.addCleanup(shutil.rmtree, _root2, True)
        _p2 = dict(_p)
        _p2["file"] = _fp2
        _m2 = _leg.PatchManager(_root2)
        io.open(_m2.get_pending_file(), "w", encoding="utf-8").write(
            json.dumps([_p2], ensure_ascii=False))
        _b2 = io.open(_fp2, encoding="utf-8").read()
        _r2 = _m2.apply_all_pending(only_approved=False)
        _a2 = io.open(_fp2, encoding="utf-8").read()
        _old = (_r2.get("applied"), _r2.get("failed"),
                [_d.get("status") for _d in _r2.get("details", [])], _b2 != _a2)

        self.assertEqual(_old, _new, "开关全关时结论漂移：改前=%r 改后=%r" % (_old, _new))


# ==========================================================================
# F. 先红后绿（改前不含 ⇒ 改后含）
# ==========================================================================
class TestT93fRedGreen(unittest.TestCase):

    def test_F1_legacy_apply_all_pending_lacks_struct_guard(self):
        if not os.path.isfile(_BAK_PM):
            self.skipTest(".bak_batch93 缺失")
        _old = io.open(_BAK_PM, encoding="utf-8").read()
        _t = ast.parse(_old)
        _node = None
        for _nd in _t.body:
            if isinstance(_nd, ast.ClassDef) and _nd.name == "PatchManager":
                for _y in _nd.body:
                    if isinstance(_y, ast.FunctionDef) and _y.name == "apply_all_pending":
                        _node = _y
        self.assertIsNotNone(_node)
        _seg = ast.get_source_segment(_old, _node) or ""
        self.assertNotIn("_m92_ast_struct_guard_on", _seg,
                         "改前不应有写盘点结构关")
        self.assertNotIn("_m92_base_indent_guard_on", _seg)
        # 改后必须有
        _new = ast.get_source_segment(_src(), _method_node("apply_all_pending")) or ""
        self.assertIn("_m92_ast_struct_guard_on", _new)
        self.assertIn("_m92_base_indent_guard_on", _new)

    def test_F2_write_point_globally_single_occurrence_each(self):
        _s = _src()
        self.assertEqual(1, _s.count("rejected_base_indent_guard"))
        self.assertEqual(1, _s.count("rejected_ast_structure_guard"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
