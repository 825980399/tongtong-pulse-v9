# -*- coding: utf-8 -*-
"""补丁验证语义拆分（主线第47批 T1，P0-2 / P2-309）

背景（第46批根因分析 R4）
--------------------------
当前系统把两种**不同语义**合成一个布尔值 ``verified``：

* ``verification.passed=true`` 的语义是 **No-Regression**
  （"改完没弄坏" —— syntax / import / regression 通过）
* ``baseline_errors → post_apply_errors`` 想表达的是 **Problem-Fixed**
  （"问题真的消失了"）

结果："没弄坏"被当成"修好了"上报，60 条补丁中 ``baseline_errors=0`` 占绝大多数，
而 ``claimed_effectiveness`` 仍高达 0.97 —— 这是**自欺欺人**，不是验证。

``nucleus/reasoning/PatchManager.py`` 里更直接：::

    problem_gone=bool(_verify_result.get("passed")),   # ← 复用 No-Regression
    function_ok=bool(_verify_result.get("passed")),

同一个 ``passed`` 同时喂给"问题消失"与"功能正常"，语义合流的**确切位置**在此。

本模块做什么
------------
提供**纯函数**语义拆分，供三处复用：
  1. ``PatchManager`` 应用补丁后写入字段
  2. 历史补丁回填工具
  3. 展示层 / 健康诊断读取真实修复率

设计原则
--------
* **不可判定就是不可判定**：``problem_fixed`` 在无法验证时为 ``None``，
  **绝不默认 True**，也**绝不退化成 0/1**。
* **向后兼容**：原 ``verified`` 保留（= ``no_regression``），标注 deprecated。
* **纯函数、无副作用、无 IO**：便于单测与回填。
"""

from __future__ import annotations

from typing import Any
from nucleus._silent_except import silent_exc

#: 语义拆分后的字段名（统一在此定义，避免各处硬编码）
F_NO_REGRESSION = "no_regression"
F_PROBLEM_FIXED = "problem_fixed"
F_GRANULARITY = "verification_granularity"
F_EFFECTIVENESS = "effectiveness"
F_VERIFIED = "verified"              # deprecated，保留向后兼容
F_SPLIT_VERSION = "verification_split_version"
# ★第51批 T1（P0-2）：主动复现字段（见 nucleus/evolution/patch_active_reprobe.py）
F_REPROBE_VERDICT = "reprobe_verdict"

#: 验证粒度取值
GRAN_STATIC_ONLY = "static_only"      # 只有静态检查（语法/导入/回归）
GRAN_REPRODUCTION = "reproduction"    # 有基线复现，但无法判定效果
GRAN_EFFECTIVENESS = "effectiveness"  # 可计算真实修复效果
GRAN_ACTIVE_REPROBE = "active_reproduction"  # ★第51批 T1：代码级主动复现（不依赖日志）

SPLIT_VERSION = 1


def _as_int(value: Any) -> int | None:
    """安全转 int；None / 空 / 非法一律返回 None（★不退化成 0）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        silent_exc(e, where="nucleus.evolution.patch_verification_split::_as_int L69")
        return None


def split_verification(patch: dict[str, Any]) -> dict[str, Any]:
    """把一条补丁的验证结果拆成「没弄坏」与「修好了」两件事。

    Args:
        patch: 补丁字典（可含 ``verification`` / ``baseline_errors``
               / ``post_apply_errors`` / ``runtime_verify_result``）。

    Returns:
        ``{no_regression, problem_fixed, verification_granularity,
           effectiveness, verified, verification_split_version, reason}``

        其中：
        * ``no_regression: bool | None`` —— 语法/导入/回归是否通过（"没弄坏"）
        * ``problem_fixed: bool | None`` —— 目标问题是否真消失（"修好了"）；
          **无法验证时为 None**
        * ``verification_granularity: str`` —— 验证粒度
        * ``effectiveness: float | None`` —— 真实修复效果；
          **不可计算时为 None，不再默认 0.97**
    """
    _v = patch.get("verification")
    _v = _v if isinstance(_v, dict) else {}

    # ---------- ① No-Regression（"没弄坏"） ----------
    _passed = _v.get("passed")
    if _passed is None:
        # 没有 verification 结构时，退回旧的 verified 字段
        _passed = patch.get(F_VERIFIED)
    if _passed is None:
        _no_regression = None
        _nr_note = "无静态/回归验证结果"
    else:
        _no_regression = bool(_passed)
        _nr_note = ("静态与回归验证通过" if _no_regression
                    else "静态或回归验证未通过")

    # ---------- ② Problem-Fixed（"修好了"） ----------
    # 基线优先取顶层，回退 runtime_verify_result.baseline
    _baseline = _as_int(patch.get("baseline_errors"))
    _after = _as_int(patch.get("post_apply_errors"))
    _rvr = patch.get("runtime_verify_result")
    if isinstance(_rvr, dict):
        if _baseline is None:
            _baseline = _as_int(_rvr.get("baseline"))
        if _after is None:
            _after = _as_int(_rvr.get("after_fix"))

    # ---------- ⓪ ★第51批 T1：主动复现优先（代码级，不受日志轮转影响） ----------
    _rp = patch.get(F_REPROBE_VERDICT)
    if _rp == "true_pass":
        _problem_fixed = True
        _gran = GRAN_ACTIVE_REPROBE
        _pf_note = "主动复现：目标问题**完全消失**"
    elif _rp == "partial_fix":
        _problem_fixed = True
        _gran = GRAN_REPRODUCTION
        _pf_note = "主动复现：问题**减少但未清零**（部分修复）"
    elif _rp in ("false_pass", "ineffective"):
        _problem_fixed = False
        _gran = GRAN_ACTIVE_REPROBE
        _pf_note = ("主动复现：**无法复现**目标问题（假通过）"
                    if _rp == "false_pass" else "主动复现：问题**未减少**（修复无效）")
    elif _rp == "verification_failed":
        _problem_fixed = None
        _gran = GRAN_STATIC_ONLY
        _pf_note = "主动复现：代码片段无法解析，不可判定"
    elif _rp is not None:
        _problem_fixed = None
        _gran = GRAN_STATIC_ONLY
        _pf_note = "主动复现：不适用（无检测器），不判通过"
    elif _baseline is None:
        _problem_fixed = None
        _gran = GRAN_STATIC_ONLY
        _pf_note = "无修复前基线，无法判定问题是否消失"
    elif not _baseline:
        # ★基线为 0 —— 这是本批的核心修复点：
        #   旧逻辑会把它当成"修复前 0 个错 → 效果 100%"，
        #   实际是"基线根本没采到"（第46批 R1：日志已轮转截断）。
        _problem_fixed = None
        _gran = GRAN_STATIC_ONLY
        _pf_note = "修复前基线为 0（未采到真实基线），不可判定"
    elif _after is None:
        _problem_fixed = None
        _gran = GRAN_REPRODUCTION
        _pf_note = "有基线但缺修复后计数，不可判定"
    else:
        _problem_fixed = bool(_after < _baseline)
        _gran = GRAN_EFFECTIVENESS
        _pf_note = ("问题已消失" if _problem_fixed
                    else f"问题仍在（修复后仍有 {_after} 处）")

    # ---------- ③ effectiveness ----------
    if _baseline and _after is not None:
        _eff = (_baseline - _after) / float(_baseline)
        # 允许负值（越修越坏）；上限 1.0
        _eff = max(-1.0, min(1.0, _eff))
    else:
        _eff = None

    return {
        F_NO_REGRESSION: _no_regression,
        F_PROBLEM_FIXED: _problem_fixed,
        F_GRANULARITY: _gran,
        F_EFFECTIVENESS: _eff,
        F_VERIFIED: _no_regression,      # deprecated：等于 no_regression
        F_SPLIT_VERSION: SPLIT_VERSION,
        "reason": "%s；%s" % (_nr_note, _pf_note),
    }


def apply_split(patch: dict[str, Any]) -> dict[str, Any]:
    """把拆分结果**原地写入**补丁字典（并返回拆分结果）。

    ★不删除任何既有字段 —— 向后兼容优先。
    """
    _r = split_verification(patch)
    for _k in (F_NO_REGRESSION, F_PROBLEM_FIXED, F_GRANULARITY,
               F_EFFECTIVENESS, F_VERIFIED, F_SPLIT_VERSION):
        patch[_k] = _r[_k]
    patch["verification_split_reason"] = _r["reason"]
    return _r


def is_deprecated_verified(patch: dict[str, Any]) -> bool:
    """该补丁的 ``verified`` 是否为「deprecated 语义」（即应改用 no_regression）。"""
    return F_SPLIT_VERSION in patch


def backfill(patches: list[dict[str, Any]],
             apply: bool = True) -> dict[str, Any]:
    """对一批补丁执行语义拆分回填，返回统计报告。

    Args:
        patches: 补丁列表（就地修改）。
        apply:   True 写入字段；False 只统计（dry-run）。

    Returns:
        ``{total, no_regression_true/false/none,
            problem_fixed_true/false/none,
            granularity_dist, real_fix_rate, fixable_rate,
            old_claimed_rate, effectiveness_values}``

        ★ ``real_fix_rate`` = ``problem_fixed=True`` / **可判定补丁数**
          （第85批 相关任务 改口径：不可判定的 None **移出分母**，
           否则指标会被永久压低到接近 0）
        ★ ``old_claimed_rate`` = 旧口径（verified=true）/ 总数 —— 用于对比虚高幅度
    """
    _n = len(patches)
    _nr = {True: 0, False: 0, None: 0}
    _pf = {True: 0, False: 0, None: 0}
    _gran: dict[str, int] = {}
    _effs: list[float] = []

    for _p in patches:
        if not isinstance(_p, dict):
            continue
        # 旧口径（回填前先记）
        _old = bool(((_p.get("verification") or {}) if isinstance(
            _p.get("verification"), dict) else {}).get("passed")) \
            or bool(_p.get(F_VERIFIED))
        _r = split_verification(_p)
        if apply:
            for _k in (F_NO_REGRESSION, F_PROBLEM_FIXED, F_GRANULARITY,
                       F_EFFECTIVENESS, F_VERIFIED, F_SPLIT_VERSION):
                _p[_k] = _r[_k]
            _p["verification_split_reason"] = _r["reason"]
        _nr[_r[F_NO_REGRESSION]] = _nr.get(_r[F_NO_REGRESSION], 0) + 1
        _pf[_r[F_PROBLEM_FIXED]] = _pf.get(_r[F_PROBLEM_FIXED], 0) + 1
        _g = _r[F_GRANULARITY]
        _gran[_g] = _gran.get(_g, 0) + 1
        if _r[F_EFFECTIVENESS] is not None:
            _effs.append(_r[F_EFFECTIVENESS])
        if _old:
            pass  # 统计在下方 old_claimed 累加
    _old_claimed = sum(1 for _p in patches
                       if isinstance(_p, dict)
                       and (bool((_p.get("verification") or {}).get("passed"))
                            if isinstance(_p.get("verification"), dict)
                            else bool(_p.get(F_VERIFIED))))

    # ★M85-3（第85批 相关任务 / D84-2）：不可判定（None）的补丁**移出分母**。
    #   它们既不证明"修好了"也不证明"没修好"，留在分母只会把指标永久压低
    #   （实测 64 条里 62 条 problem_fixed=None → 旧口径恒 ≈0%）。
    #   同时用 verifiable_rate / verifiable_count 单独展示"有多少是可判定的"。
    _verifiable = _pf.get(True, 0) + _pf.get(False, 0)
    return {
        "total": _n,
        "no_regression": {"true": _nr.get(True, 0), "false": _nr.get(False, 0),
                          "none": _nr.get(None, 0)},
        "problem_fixed": {"true": _pf.get(True, 0), "false": _pf.get(False, 0),
                          "none": _pf.get(None, 0)},
        "granularity_dist": _gran,
        "real_fix_rate": round(_pf.get(True, 0) / _verifiable, 4) if _verifiable else 0.0,
        "verifiable_rate": round(_verifiable / _n, 4) if _n else 0.0,
        "verifiable_count": _verifiable,
        "old_claimed_rate": round(_old_claimed / _n, 4) if _n else 0.0,
        "effectiveness_mean": (round(sum(_effs) / len(_effs), 4)
                               if _effs else None),
        "effectiveness_count": len(_effs),
    }


def real_fix_rate(patches: list[dict[str, Any]]) -> float:
    """**真实**修复率 = ``problem_fixed is True`` / **可判定补丁数**。

    ★取代旧的「基于 verified 的修复率」。
    ★第85批 相关任务（D84-2）：不可判定（``None``）的补丁**分子分母都不计**——
    留在分母只会让指标永久接近 0，无助于判断本地修复能力的真实水平。
    """
    if not patches:
        return 0.0
    _n = sum(1 for _p in patches if isinstance(_p, dict))
    if not _n:
        return 0.0
    _ok = 0
    _known = 0
    for _p in patches:
        if not isinstance(_p, dict):
            continue
        _pf = _p.get(F_PROBLEM_FIXED)
        if _pf is None and F_SPLIT_VERSION not in _p:
            _pf = split_verification(_p)[F_PROBLEM_FIXED]
        if _pf is True:
            _ok += 1
            _known += 1
        elif _pf is False:
            _known += 1
    # ★M85-3（第85批 相关任务 / D84-2）：分母 = 可判定补丁数（True + False）；
    #   无可判定样本时返回 0.0（不返回 0/0 的 NaN，也不虚报满分）。
    return round(_ok / _known, 4) if _known else 0.0


def display_label(patch: dict[str, Any]) -> str:
    """展示层标签：区分「无回归」与「修复效果未验证」。

    返回形如：
      * ``"✅ 无回归 · ✅ 问题已修复"``
      * ``"✅ 无回归 · ❓ 修复效果未验证"``
      * ``"❌ 存在回归"``
    """
    _nr = patch.get(F_NO_REGRESSION)
    _pf = patch.get(F_PROBLEM_FIXED)
    if _nr is None and _pf is None and F_SPLIT_VERSION not in patch:
        _r = split_verification(patch)
        _nr, _pf = _r[F_NO_REGRESSION], _r[F_PROBLEM_FIXED]

    _head = ("✅ 无回归" if _nr is True
             else "❌ 存在回归" if _nr is False
             else "⚪ 未做回归验证")
    if _nr is False:
        return _head
    _tail = ("· ✅ 问题已修复" if _pf is True
             else "· ❌ 问题仍在" if _pf is False
             else "· ❓ 修复效果未验证")
    return _head + " " + _tail

# _m51_t1_wire