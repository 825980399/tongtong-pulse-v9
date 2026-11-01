# -*- coding: utf-8 -*-
"""★375批刀4：effect 验证零调权 —— 一维不许冒充三维。

两个缺陷叠在一起，才让「零调权」形同虚设：

1. **调用侧**：post-apply 恒传 `problem_gone=None` / `perf_delta_ms=None`，
   三维只剩 `function_ok` 一维（而它只是「改完没弄坏」，不是「问题消失了」）。
2. **判据侧**：`verify()` 用 `all(_known)` 判通过，而 `all([True])` 也为真
   ⇒ **只凭一维 True 就判通过、信任分 +10**。零调权在这里等于没有判据。

本刀：判据侧改成「覆盖不足 = 不可判定（不奖不罚）」；调用侧用同一次应用里
`apply_reprobe()` 刚回填的 `reprobe_verdict` 回填 `problem_gone`
（`false_pass` ⇒ None：修复前复现不出 = 没有基线，不是「问题仍在」）。
`perf_delta_ms` 无实测耗时源，如实保持 None，不编造。
"""
import ast
import os
import threading

from nucleus.evolution.EvolutionEffectVerifier import EvolutionEffectVerifier as V

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PATCH_MANAGER = os.path.join(ROOT, "nucleus", "reasoning", "PatchManager.py")
EFFECT_VERIFIER = os.path.join(ROOT, "nucleus", "evolution", "EvolutionEffectVerifier.py")


def _bare():
    """裸实例：不读盘、不写盘（`save` 在 fixture 里被打桩），只跑判定树。"""
    _v = object.__new__(V)
    _v._verdicts = {}
    _v._lock = threading.Lock()
    return _v


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _verify(problem_gone, function_ok, perf_delta_ms):
    import pytest

    with pytest.MonkeyPatch.context() as _mp:
        _mp.setattr(V, "save", lambda self: None, raising=False)
        return _bare().verify(
            {"id": "t375", "file": "x.py", "issue_type": "silent_except"},
            {"type": "silent_except"},
            problem_gone=problem_gone,
            function_ok=function_ok,
            perf_delta_ms=perf_delta_ms)


# ============================ ① 核心：一维覆盖不再算通过


def test_01_single_dimension_is_not_a_pass():
    """★post-apply 的原始形态（只有 function_ok）不许再判通过。"""
    _r = _verify(None, True, None)
    assert _r["passed"] is None, "一维 True 被判通过 = 三维判据被冒充"
    assert _r["trust_delta"] == 0, "不许给信任分"
    assert "不可判定" in _r["reason"]
    assert _r["dims"]["problem_gone"] is None and _r["dims"]["perf_ok"] is None


def test_02_two_dims_known_still_insufficient():
    _r = _verify(True, True, None)
    assert _r["passed"] is None
    assert _r["trust_delta"] == 0
    assert "缺 perf_ok" in _r["reason"]


def test_03_three_dims_all_true_is_a_pass():
    _r = _verify(True, True, 10.0)
    assert _r["passed"] is True
    assert _r["trust_delta"] == 10


def test_04_nothing_measured():
    _r = _verify(None, None, None)
    assert _r["passed"] is None
    assert _r["trust_delta"] == 0


# ============================ ② 既有语义不许被改动（第47批）


def test_05_problem_still_present_is_failure():
    _r = _verify(False, True, None)
    assert _r["passed"] is False
    assert _r["trust_delta"] == -15, "问题仍在是最重的一档"


def test_06_undecidable_problem_gone_outranks_other_failure():
    """`problem_gone` 不可判定时一律「不奖不罚」——即使别的维度失败（第47批语义）。"""
    _r = _verify(None, False, None)
    assert _r["passed"] is None
    assert _r["trust_delta"] == 0


def test_07_function_broken_is_failure():
    _r = _verify(True, False, 10.0)
    assert _r["passed"] is False
    assert _r["trust_delta"] == -10


def test_08_perf_regression_is_failure():
    _r = _verify(True, True, 999999.0)
    assert _r["passed"] is False
    assert _r["trust_delta"] == -10


# ============================ ③ 复现结论 → problem_gone 映射


def test_09_reprobe_mapping():
    assert V.problem_gone_from_reprobe({"reprobe_verdict": "true_pass"})[0] is True
    assert V.problem_gone_from_reprobe({"reprobe_verdict": "partial_fix"})[0] is False
    # false_pass = 修复前根本复现不出 ⇒ 没有基线，不是「问题仍在」
    assert V.problem_gone_from_reprobe({"reprobe_verdict": "false_pass"})[0] is None


def test_10_reprobe_missing_or_unknown_is_undecidable():
    for _p in ({}, None, {"reprobe_verdict": ""}, {"reprobe_verdict": "whatever"}):
        _v, _note = V.problem_gone_from_reprobe(_p)
        assert _v is None, "缺值/未知结论不许猜成 True 或 False"
        assert "不可判定" in _note


# ============================ ④ 调用侧真的回填了


def test_11_post_apply_passes_real_problem_gone():
    """按 AST 看**实参**，不看源码字符串 —— 注释里还留着旧形态 `problem_gone=None`。

    这是本批第二次踩「注释当代码」（前一次在刀2 test_07）：只要改动点附近
    有解释性注释写着旧值，源码子串断言就会永远为真。
    """
    _tree = ast.parse(_read("nucleus/reasoning/PatchManager.py"))
    _calls = [n for n in ast.walk(_tree)
              if isinstance(n, ast.Call)
              and getattr(n.func, "attr", "") == "verify"
              and getattr(getattr(n.func, "value", None), "id", "") == "_verifier"]
    assert len(_calls) == 1, f"期望 1 处 _verifier.verify 调用，实际 {len(_calls)}"
    _kw = {k.arg: k.value for k in _calls[0].keywords}
    assert "problem_gone" in _kw, "没传 problem_gone"
    _pg = _kw["problem_gone"]
    assert not (isinstance(_pg, ast.Constant) and _pg.value is None), (
        "调用侧仍恒传 problem_gone=None（零调权未修）")
    assert getattr(_pg, "id", "") == "_m375_pg", "problem_gone 应来自复现结论回填"
    _perf = _kw.get("perf_delta_ms")
    # ★418 同步（第412批契约变更）：412 已接入**实测**耗时源
    #   `_m412_perf_delta_ms(patch, project_root)`，不再是恒 `None`。
    #   原断言「必须是 Constant None」写于 375，随即过期 —— 它真正想守的不变量是
    #   **「不许编造数字」**，那应当要求「来自实测调用」，而不是「恒为 None」。
    assert isinstance(_perf, ast.Call), (
        "perf_delta_ms 应来自实测耗时源 _m412_perf_delta_ms(...)，不许写死")
    assert getattr(_perf.func, "id", "") == "_m412_perf_delta_ms", ast.unparse(_perf)
    for _a in _perf.args:
        assert not (isinstance(_a, ast.Constant) and isinstance(_a.value, (int, float))), (
            "实测耗时源的实参不许是数字字面量：%s" % ast.unparse(_a))
    _has_map = any(isinstance(n, ast.Call)
                   and getattr(n.func, "attr", "") == "problem_gone_from_reprobe"
                   for n in ast.walk(_tree))
    assert _has_map, "没有调用 problem_gone_from_reprobe"


def test_12_old_shortcut_removed():
    """旧的 `all(_known)` 捷径已消失（它让 `[True]` 也算全通过）。"""
    _t = _read("nucleus/evolution/EvolutionEffectVerifier.py")
    assert "elif all(_known):" not in _t
    assert "elif len(_known) == 3:" in _t
