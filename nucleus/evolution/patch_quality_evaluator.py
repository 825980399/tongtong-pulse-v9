"""补丁质量评估器 —— 内在模型 **L4** 第一个落地（主线第42批 T2 / P0-250）。

定位
----
自主进化循环占全部 LLM 调用的 **98.8%**（第41批实测 2077/2103），而补丁是否
真正修好了问题，此前只能看 ``effectiveness`` 字段 —— 该字段由
"修复前错误数 / 修复后错误数"推导，而第41批 T1 已定性：**判据结构性恒假**
（要求错误行同时含文件名与方法名，而框架日志从不输出方法名）→
``baseline`` 恒 0 → ``effectiveness`` **恒 1.0（0/0 的产物，虚假满分）**。

本模块用**原始 baseline / post_apply 计数**重算效果，把三类情况分开：

===================  ==========================================================
情况                  判定
===================  ==========================================================
``baseline > 0``     ``real_effectiveness = (baseline - post) / baseline``（可为负）
``baseline == 0``    **``None`` = 不可判定** —— 0/0 无意义，此前被算成 1.0
``baseline == 0`` 且 ``post > 0``   ``-1.0``（凭空引入错误）
===================  ==========================================================

L1 仅观测（**不改变任何进化决策**）
-----------------------------------
本模块**只读** `patch_history.json`、只写评估报告 JSON，不拒绝补丁、不改状态、
不发网络请求。评估器启用与否都不影响进化的既有行为。

评分（0-100）= 效果(40) + 验证(25) + 回归(20) + 稳定性(15)
标签：``good`` / ``mediocre`` / ``bad`` / ``fake_pass`` / ``unverifiable`` / ``not_applied``

★ ``fake_pass`` —— **声称验证通过、但真实效果可判定且 <= 0**（本批实测：
  ``PulseLiver._build_knowledge_association_graph``，baseline=4/post=4 → eff=0.0
  却被记为 ``runtime_verified=True``）。
★ ``unverifiable`` —— 验证通过但 **baseline=0，无参照物可比**，不能证明有效
  （本批 62/67 条属此类，是"判据缺陷"的下游后果，而非补丁本身的问题）。

★N-9「假通过=2」复核结论（第158批 第9刀）
------------------------------------------
上两类标签依赖 ``baseline_errors`` / ``post_apply_errors`` 的**文件级**计数，
会把「补丁已逐字落地」的方法也误判。本批逐条复核了被误判的 **2 条**（均在
``organs/body/PulseLiver.py``，见 ``data/patches/patch_history.json``）：

===============================  ==========  ==========  ==================  =============
补丁 id / 方法                    文件级计数    原标签      方法级复现探针       复核结论
===============================  ==========  ==========  ==================  =============
``patch_1788590332_d156``        baseline=4  fake_pass   verdict=true_pass   **真通过**
``_build_knowledge_association_graph``  post=4  eff=0.0   1 处 → 0 处        （摘除）
``patch_1788765051_7ff3``        baseline=0  fake_pass   verdict=true_pass   **真通过**
``_save_fuse_cooldown``          post=4      eff=**-1.0** 1 处 → 0 处        （摘除）
                                             （claimed 0.5）
===============================  ==========  ==========  ==================  =============

两条的原标签**都是 ``fake_pass``**（这正是「假通过=2」的所指）：前者文件级 4→4
算出 eff=0.0；后者 baseline=0 而 post=4，按 :func:`real_effectiveness` 的
「baseline==0 且 post>0 → -1.0」规则算出 eff=-1.0（其补丁侧自记的 claimed
effectiveness 为 0.5）。两者的 ``reprobe_verdict`` 均为 ``true_pass``（复现探针实测：修复前 1 处裸 pass
无留痕 → 修复后 0 处），与第42批 T4 逐字比对结论（补丁 100% 落地，4→4 属文件级
口径、与目标方法无关）一致。补丁历史清单一并已由门控测试
``tests/test_liver_silent_fix_m42.py`` 固化（源码回退即失败）。

因此本模块新增 :func:`reprobe_effectiveness`（方法级复现探针效果）与
:data:`N9_REPROBE_EXEMPT`（N-9 复核清单）：**清单内条目**在复现证据成立时用复现
探针作为效果判据，上述 2 条据此从 ``fake_pass`` / ``unverifiable`` 摘除，落入正常
分数分级；**清单外条目**维持原文件级判据，行为完全不变。

★为什么用显式清单、而不是对所有记录通用启用复现判据：实测共 **70** 条记录带
``reprobe_verdict=true_pass``，通用启用会连带改写其中 68 条 ``unverifiable`` 的定性
——那已超出本批授权（路灯口径「假通过=**2**」），且这些条目属「文件级 baseline=0
无参照物」这一**已知判据缺陷**的下游后果，应由专门批次统一处理。本刀保持改动面
精确可审计。
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from typing import Any

from nucleus._silent_except import silent_exc

__all__ = [
    "DEFAULT_HISTORY_PATH",
    "DEFAULT_REPORT_PATH",
    "LABELS",
    "WEIGHTS",
    "REPROBE_TRUE_PASS",
    "N9_REPROBE_EXEMPT",
    "evaluator_enabled",
    "real_effectiveness",
    "reprobe_effectiveness",
    "evaluate_patch",
    "evaluate_history",
    "summarize",
    "format_summary_line",
    "save_report",
    "load_history",
    "evaluate_and_report",
]

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 补丁历史（PatchManager 的持久化文件）。
DEFAULT_HISTORY_PATH = os.path.join(_PROJECT_ROOT, "data", "patches", "patch_history.json")
#: 评估报告输出路径。
DEFAULT_REPORT_PATH = os.path.join(_PROJECT_ROOT, "data", "evolution",
                                   "patch_quality_report.json")

#: 四个评分维度的满分。
WEIGHTS = {"effectiveness": 40.0, "verification": 25.0, "regression": 20.0, "stability": 15.0}

#: 全部标签（顺序即判定优先级）。
LABELS = ("fake_pass", "unverifiable", "not_applied", "good", "mediocre", "bad")

#: ``baseline == 0`` 时的效果中性分（不给满分：无参照物 ≠ 有效）。
_EFF_NO_BASELINE = 20.0

#: ★第158批第9刀（N-9）：复现探针判定为「真通过」的取值（``reprobe_verdict``）。
REPROBE_TRUE_PASS = "true_pass"

#: ★第158批第9刀（N-9）复核清单：经逐条核实「文件级计数判据误判、方法级复现探针
#: 证真通过」的补丁，元素为 ``(file, method)``。
#:
#: ★为什么是**显式清单**而不是对所有记录通用启用：实测有 70 条记录带
#: ``reprobe_verdict=true_pass``，若通用启用会把其中 68 条 ``unverifiable`` 一并改写
#: ——那已超出本批授权范围（路灯口径为「假通过=**2**」），且这些 ``unverifiable``
#: 是「文件级 baseline=0 无参照物」这一**已知判据缺陷**的下游后果（见上文档，
#: 本批 62/67 条属此类），其定性应由专门批次处理，不在本刀擅自扩大。
#: 因此这里只收 N-9 指定的 2 条，保证改动面精确可审计。
N9_REPROBE_EXEMPT = frozenset({
    ("organs/body/PulseLiver.py", "_build_knowledge_association_graph"),
    ("organs/body/PulseLiver.py", "_save_fuse_cooldown"),
})


# ------------------------------------------------------------------ 小工具
def _num(v: Any) -> float | None:
    """取数值（排除 bool）；非数值 → None。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _in_test_env() -> bool:
    """测试环境判定（pytest 下不得写生产 data/）。"""
    try:
        return ("pytest" in sys.modules) or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    except Exception as e:
        silent_exc(e, where="nucleus.evolution.patch_quality_evaluator::_in_test_env L91")
        return False


def _is_production_data_path(path: str) -> bool:
    """路径是否位于项目的 ``data/`` 下（生产数据区）。"""
    try:
        _p = os.path.abspath(path).replace("\\", "/").lower()
        _r = os.path.abspath(_PROJECT_ROOT).replace("\\", "/").lower()
        return _p.startswith(_r + "/data/")
    except Exception as e:
        silent_exc(e, where="nucleus.evolution.patch_quality_evaluator::_is_production_data_path L101")
        return True


def evaluator_enabled() -> bool:
    """灰度开关（默认 True）。关闭时 :func:`evaluate_and_report` 直接返回 None。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_PATCH_QUALITY_EVALUATOR", True))
    except Exception as e:
        silent_exc(e, where="nucleus.evolution.patch_quality_evaluator::evaluator_enabled L110")
        return True


# ------------------------------------------------------------------ 核心判据
def real_effectiveness(baseline: Any, post_apply: Any) -> float | None:
    """用原始错误计数重算效果；**不可判定时返回 None**。

    * ``baseline > 0``          → ``(baseline - post) / baseline``（post>baseline 时为负）
    * ``baseline == 0, post == 0`` → ``None`` —— 0/0 无意义
      （**这是本批最关键的修正**：补丁侧把该情况记为 ``1.0``，形成虚假满分）
    * ``baseline == 0, post > 0``  → ``-1.0``（无基线却出现错误，必然是新引入的）

    Args:
        baseline: 修复前错误数。
        post_apply: 修复后错误数。

    Returns:
        效果值（可为负）或 ``None``。
    """
    _b = _num(baseline)
    _p = _num(post_apply)
    if _b is None:
        return None
    if _b == 0:
        if _p is None or _p == 0:
            return None                     # ★不可判定，绝不返回 1.0
        return -1.0
    if _p is None:
        return None
    return (_b - _p) / _b


def reprobe_effectiveness(p: dict) -> float | None:
    """★第158批第9刀（N-9）：用**方法级复现探针**重算效果；证据不足时返回 None。

    为什么需要它
    ------------
    ``baseline_errors`` / ``post_apply_errors`` 是**文件级**错误计数——同一文件里
    其它方法的错误也会计入。于是出现两类误判（即 N-9「假通过=2」）：

    * ``PulseLiver._build_knowledge_association_graph``：baseline=4 / post=4
      → ``real_effectiveness`` = 0.0 → 被打成 ``fake_pass``；
    * ``PulseLiver._save_fuse_cooldown``：baseline=0 → 效果不可判定
      → 被打成 ``unverifiable``。

    而两者的 **``reprobe_verdict`` 均为 ``true_pass``**：方法级复现探针实测
    「修复前 1 处裸 pass（无留痕）→ 修复后 0 处」，与第42批 T4 的逐字比对结论
    （补丁已 100% 落地）一致。``reprobe_*`` 的粒度与补丁目标（单个方法）一致，
    因此**当复现探针成功时，其结论优先于文件级计数**。

    Args:
        p: 单条补丁历史记录（只读）。

    Returns:
        ``(reprobe_baseline_hits - reprobe_after_hits) / reprobe_baseline_hits``；
        无复现证据、或证据不足以判定（基线为 0 / 字段缺失 / 非数值）时为 ``None``。
    """
    if not isinstance(p, dict):
        return None
    if str(p.get("reprobe_verdict") or "") != REPROBE_TRUE_PASS:
        return None
    _b = _num(p.get("reprobe_baseline_hits"))
    _a = _num(p.get("reprobe_after_hits"))
    if _b is None or _a is None or _b <= 0:
        return None
    return (_b - _a) / _b


def _n9_exempt(p: Any) -> bool:
    """该补丁是否在 N-9 复核清单（:data:`N9_REPROBE_EXEMPT`）内。"""
    if not isinstance(p, dict):
        return False
    return (str(p.get("file") or ""), str(p.get("method") or "")) in N9_REPROBE_EXEMPT


def _score_effectiveness(real_eff: float | None) -> tuple[float, str]:
    if real_eff is None:
        return _EFF_NO_BASELINE, "无基线可比（baseline=0）→ 效果不可判定"
    if real_eff <= 0:
        return 0.0, "真实效果 <= 0（修复无效或引入错误）"
    return round(20.0 + 20.0 * min(1.0, real_eff), 2), "真实效果 %.1f%%" % (real_eff * 100.0)


def _score_verification(vr: Any) -> tuple[float, str]:
    if not isinstance(vr, dict):
        return 0.0, "无运行期验证记录"
    if vr.get("verified") is False:
        return 5.0, "验证未通过"
    if vr.get("detail"):
        return WEIGHTS["verification"], "有验证记录与明细"
    return 15.0, "有验证记录但缺明细"


def _score_regression(baseline: Any, post: Any, vr: Any) -> tuple[float, str]:
    _b, _p = _num(baseline), _num(post)
    if _b is not None and _p is not None and _p > _b:
        return 0.0, "修复后错误数上升（引入回归）"
    if isinstance(vr, dict) and vr.get("new_issues"):
        return 8.0, "验证期发现新问题"
    return WEIGHTS["regression"], "无回归迹象"


def _score_stability(repeats: int) -> tuple[float, str]:
    if repeats <= 1:
        return WEIGHTS["stability"], "该位置仅修复一次"
    if repeats == 2:
        return 8.0, "同一位置被重复修复 2 次"
    return 0.0, "同一位置被重复修复 %d 次（前次未修好）" % repeats


# ------------------------------------------------------------------ 单条评估
def evaluate_patch(p: dict, repeats: int = 1) -> dict[str, Any]:
    """评估单条补丁，返回评分、标签与理由（只读，不修改入参）。"""
    if not isinstance(p, dict):
        return {"id": None, "label": "bad", "score": 0.0,
                "reasons": ["记录格式非法（非 dict）"]}
    _vr = p.get("runtime_verify_result")
    _vr = _vr if isinstance(_vr, dict) else {}
    _baseline = p.get("baseline_errors")
    if _baseline is None:
        _baseline = _vr.get("baseline")
    _post = p.get("post_apply_errors")
    if _post is None:
        _post = _vr.get("after_fix")

    _claimed = _num(_vr.get("effectiveness"))
    _real = real_effectiveness(_baseline, _post)
    _verified = bool(p.get("runtime_verified")) or (_vr.get("verified") is True)

    # ★第158批第9刀（N-9）：方法级复现探针优先于文件级计数。
    #   仅对 N-9 复核清单内的条目（见 ``N9_REPROBE_EXEMPT``）采纳复现探针结论；
    #   其余记录维持原文件级判据，避免把影响面扩大到本批未授权的范围。
    _reprobe = reprobe_effectiveness(p) if _n9_exempt(p) else None
    _reprobe_ok = _reprobe is not None and _reprobe > 0
    _eff_for_score = _reprobe if _reprobe_ok else _real

    _s_eff, _r_eff = _score_effectiveness(_eff_for_score)
    if _reprobe_ok:
        _r_eff = ("方法级复现探针优先：真实效果 %.1f%%（文件级计数：%s）"
                  % (_reprobe * 100.0,
                     "不可判定" if _real is None else "%.1f%%" % (_real * 100.0)))
    _s_ver, _r_ver = _score_verification(_vr)
    _s_reg, _r_reg = _score_regression(_baseline, _post, _vr)
    _s_stb, _r_stb = _score_stability(repeats)
    _score = round(_s_eff + _s_ver + _s_reg + _s_stb, 2)

    # ---- 标签（优先级：fake_pass > unverifiable > not_applied > 分数分级）
    #   ★N-9：reprobe 证据成立时**不**判 fake_pass / unverifiable——二者都是文件级
    #   计数的口径产物（4→4 得 0.0、baseline=0 得 None），已被方法级复现证伪。
    if _verified and not _reprobe_ok and _real is not None and _real <= 0:
        _label = "fake_pass"
    elif _verified and not _reprobe_ok and _real is None:
        _label = "unverifiable"
    elif not p.get("applied") and not _verified:
        _label = "not_applied"
    elif _score >= 75.0:
        _label = "good"
    elif _score >= 50.0:
        _label = "mediocre"
    else:
        _label = "bad"

    return {
        "id": p.get("id"),
        "file": p.get("file"),
        "method": p.get("method"),
        "issue_type": p.get("issue_type"),
        "risk_level": p.get("risk_level"),
        "applied": bool(p.get("applied")),
        "verified": _verified,
        "claimed_effectiveness": _claimed,
        "real_effectiveness": _real,
        # ★第158批第9刀（N-9）：方法级复现探针效果 + 本次是否采纳它作为效果判据。
        "reprobe_effectiveness": _reprobe,
        "reprobe_used": _reprobe_ok,
        "baseline_errors": _baseline,
        "post_apply_errors": _post,
        "repeats": repeats,
        "scores": {"effectiveness": _s_eff, "verification": _s_ver,
                   "regression": _s_reg, "stability": _s_stb},
        "score": _score,
        "label": _label,
        "reasons": [_r_eff, _r_ver, _r_reg, _r_stb],
    }


# ------------------------------------------------------------------ 全量评估
def _repeats_map(hist: list) -> dict:
    _c: dict = {}
    for _p in hist:
        if isinstance(_p, dict):
            _k = (str(_p.get("file", "")), str(_p.get("method", "")))
            _c[_k] = _c.get(_k, 0) + 1
    return _c


def evaluate_history(hist: Any) -> dict[str, Any]:
    """评估整份补丁历史，返回 ``{"summary", "patches", "findings"}``。"""
    if not isinstance(hist, list):
        hist = []
    _rep = _repeats_map(hist)
    _items = []
    for _p in hist:
        if not isinstance(_p, dict):
            continue
        _k = (str(_p.get("file", "")), str(_p.get("method", "")))
        _items.append(evaluate_patch(_p, repeats=_rep.get(_k, 1)))
    _items.sort(key=lambda x: x["score"])

    _sum = summarize(_items)
    _sum["total"] = len(hist)
    _sum["history_records"] = len(_items)
    return {"summary": _sum, "patches": _items, "findings": _build_findings(_items, _sum)}


def summarize(items: list) -> dict[str, Any]:
    """按标签汇总（含平均分与两套效果口径）。"""
    from collections import Counter
    _c = Counter(x.get("label") for x in items if isinstance(x, dict))
    _scores = [x["score"] for x in items if isinstance(x, dict) and isinstance(x.get("score"), (int, float))]
    _claimed = [x["claimed_effectiveness"] for x in items
                if isinstance(x, dict) and isinstance(x.get("claimed_effectiveness"), (int, float))]
    _computable = [x for x in items
                   if isinstance(x, dict) and x.get("real_effectiveness") is not None]
    _out: dict[str, Any] = {k: _c.get(k, 0) for k in LABELS}
    _out["avg_score"] = round(sum(_scores) / len(_scores), 2) if _scores else 0.0
    _out["applied"] = sum(1 for x in items if isinstance(x, dict) and x.get("applied"))
    _out["verified"] = sum(1 for x in items if isinstance(x, dict) and x.get("verified"))
    _out["real_effectiveness_computable"] = len(_computable)
    _out["claimed_effectiveness_avg"] = round(sum(_claimed) / len(_claimed), 4) if _claimed else 0.0

    # ★主线第47批 T1（P0-2）：补上「真实修复率」口径。
    #   旧口径 ``verified`` 只证明 No-Regression（"改完没弄坏"），却被当成
    #   Problem-Fixed（"问题真消失了"）上报 → 修复率虚高到 100%。
    #   此处新增基于 ``problem_fixed`` 的真实口径，供展示层与健康诊断使用。
    try:
        from nucleus.evolution.patch_verification_split import (
            real_fix_rate as _real_fix_rate,
        )
        from nucleus.evolution.patch_verification_split import (
            split_verification as _split,
        )
        _n = sum(1 for x in items if isinstance(x, dict))
        _out["no_regression_rate"] = round(
            sum(1 for x in items
                if isinstance(x, dict) and _split(x).get("no_regression") is True)
            / _n, 4) if _n else 0.0
        _out["real_fix_rate"] = _real_fix_rate(
            [x for x in items if isinstance(x, dict)])
        _out["verifiable_rate"] = round(
            sum(1 for x in items
                if isinstance(x, dict) and _split(x).get("problem_fixed") is not None)
            / _n, 4) if _n else 0.0
        _out["fix_rate_gap"] = round(
            _out["no_regression_rate"] - (_out["real_fix_rate"] or 0.0), 4)
    except Exception as e:      # 拆分模块异常不影响既有汇总
        silent_exc(e, where="nucleus.evolution.patch_quality_evaluator::summarize L305")
    return _out


def _build_findings(items: list, summary: dict) -> list:
    """自动生成结论条目（供报告与日志使用）。"""
    _f = []
    _n = summary.get("history_records") or len(items)
    _unv = summary.get("unverifiable", 0)
    if _n and _unv:
        _f.append(
            "%d/%d（%.1f%%）的补丁**无法验证有效性**：baseline=0 使效果不可判定，"
            "其 claimed effectiveness 是 0/0 除法的产物（虚假满分）。"
            "根因见第41批 T1（判据要求错误行同时含文件名与方法名，而日志从不输出方法名）。"
            % (_unv, _n, 100.0 * _unv / _n))
    if summary.get("fake_pass"):
        _names = ", ".join("%s.%s" % (os.path.basename(str(x.get("file"))), x.get("method"))
                           for x in items if x.get("label") == "fake_pass")[:200]
        _f.append("发现 %d 条**假通过**（声称验证通过但真实效果<=0）：%s"
                  % (summary["fake_pass"], _names))
    _rep = [x for x in items if x.get("repeats", 1) > 1]
    if _rep:
        _f.append("%d 条补丁位于**被重复修复**的位置（说明前次未真正修好）。" % len(_rep))
    if summary.get("real_effectiveness_computable", 0) == 0 and _n:
        _f.append("★**全部补丁**的真实效果都不可判定 —— 在 baseline 判据修好之前，"
                  "本评估器无法给出有效的有效性结论（只做观测，不据此决策）。")
    return _f


def format_summary_line(summary: dict) -> str:
    """生成任务书要求的日志行。"""
    return ("[补丁评估] 总补丁=%s 优质=%s 中等=%s 劣质=%s 假通过=%s"
            "（不可验证=%s 未应用=%s 平均分=%s）" % (
                summary.get("history_records", 0), summary.get("good", 0),
                summary.get("mediocre", 0), summary.get("bad", 0),
                summary.get("fake_pass", 0), summary.get("unverifiable", 0),
                summary.get("not_applied", 0), summary.get("avg_score", 0.0)))


# ------------------------------------------------------------------ IO
def load_history(path: str | None = None) -> list:
    """读取补丁历史；失败返回空列表（绝不抛出）。"""
    _p = path or DEFAULT_HISTORY_PATH
    try:
        with io.open(_p, encoding="utf-8", errors="replace") as _f:
            _d = json.loads(_f.read())
        return _d if isinstance(_d, list) else []
    except (OSError, ValueError):
        return []


def save_report(report: dict, path: str | None = None) -> str | None:
    """写评估报告；测试环境 + 生产路径 → 拒写（返回 None）。"""
    _p = path or DEFAULT_REPORT_PATH
    if path is None and _in_test_env() and _is_production_data_path(_p):
        return None
    try:
        _d = os.path.dirname(_p)
        if _d:
            os.makedirs(_d, exist_ok=True)
        with io.open(_p, "w", encoding="utf-8") as _f:
            _f.write(json.dumps(report, ensure_ascii=False, indent=2))
        return _p
    except OSError as e:
        silent_exc(e, "patch_quality_evaluator.py:368:save_report", level="warning")
        return None


def evaluate_and_report(history_path: str | None = None, report_path: str | None = None,
                        logger: Any = None) -> dict[str, Any] | None:
    """读取历史 → 评估 → 写报告 → 打日志。**只读**，零副作用于进化决策。

    Returns:
        评估报告 dict；开关关闭时返回 ``None``。
    """
    if not evaluator_enabled():
        return None
    _hist = load_history(history_path)
    _ev = evaluate_history(_hist)
    _ev["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _ev["history_path"] = history_path or DEFAULT_HISTORY_PATH
    _ev["report_path"] = save_report(_ev, report_path)
    _line = format_summary_line(_ev["summary"])
    if logger is not None:
        try:
            logger.info(_line)
            for _f in _ev["findings"]:
                logger.info("[补丁评估] %s", _f)
        except Exception as _e:                      # 日志失败不得影响评估结果
            print("[补丁评估] 日志输出失败: %s: %s" % (type(_e).__name__, _e),
                  file=sys.stderr)
    return _ev
