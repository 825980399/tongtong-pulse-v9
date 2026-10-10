# -*- coding: utf-8 -*-
"""主线第55批 T7：SERP 清洗后经验库质量评估（★只评估，不修改任何数据）

背景
----
第50批建立了经验库清洗闸门 ``nucleus/data/experience_cleanup.py``：
**只标记不删除**（写入 ``is_cleaned=False`` / ``pollution_risk="high"`` /
``cleanup_reason`` / ``cleanup_batch``），由 L3 检索闸门 ``is_retrievable`` 拦截。

本工具回答「**清洗之后，经验库还剩多少可用的、质量如何**」。

评估维度
--------
1. **清洗覆盖度**：已标记 / 总数，按 ``cleanup_reason`` 分类分布
2. **可用池规模**：``is_retrievable`` 为真的条数
3. **★漏网检测**（最有价值）：对「可用池」重跑 ``classify``，
   若命中 ``template_summary_legacy`` / ``write_side_boilerplate`` 的**明确特征**
   → 该条**符合污染特征却未被标记**，说明清洗规则未覆盖
4. **内容质量**：可用池的 summary 长度分布、空摘要率

★设计约束
---------
* **只读** —— 本脚本绝不写回经验库（无 cleanup_file / mark_polluted 调用）
* 结论只用于人工裁决，**不自动删除/修改任何记录**
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from typing import Any, Dict, List

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from nucleus.data import experience_cleanup as EC  # noqa: E402

# ★第55批 T3：跨盘安全的 relpath（测试沙箱可能落在与项目不同的盘，
#   裸 os.path.relpath 会抛 ValueError: path is on mount 'C:', start on 'D:'）
from nucleus.data.path_utils import normalize_relpath  # noqa: E402

DEFAULT_POOL = os.path.join(_PROJ, "data", "experience", "experience_pool.json")

#: summary 的常见字段名（不同批次可能不同）
_SUMMARY_KEYS = ("summary", "content", "text", "experience", "description")


def _summary_of(exp: dict) -> str:
    for _k in _SUMMARY_KEYS:
        _v = exp.get(_k)
        if isinstance(_v, str) and _v.strip():
            return _v
    return ""


def _ts_of(exp: dict) -> Any:
    for _k in ("created_at", "timestamp", "saved_at", "time", "date"):
        if exp.get(_k) is not None:
            return exp.get(_k)
    return None


def load_pool(path: str) -> List[dict]:
    """加载经验池（只读）。"""
    with io.open(path, encoding="utf-8") as _f:
        _d = json.load(_f)
    if isinstance(_d, dict):
        for _k in ("experiences", "records", "items", "data"):
            if isinstance(_d.get(_k), list):
                return [x for x in _d[_k] if isinstance(x, dict)]
    return [x for x in _d if isinstance(x, dict)] if isinstance(_d, list) else []


def assess(pool: str = DEFAULT_POOL) -> Dict[str, Any]:
    """执行评估（★只读）。"""
    _recs = load_pool(pool)
    _total = len(_recs)

    # 1) 清洗覆盖度（★铁律 59：区分「字段不存在」与「值为 False」）
    _marked = [r for r in _recs if r.get("polluted") is True
               or r.get(EC.F_IS_CLEANED) is False]
    _by_reason: Dict[str, int] = {}
    for _r in _marked:
        _k = str(_r.get(EC.F_CLEANUP_REASON) or "(未填 reason)")
        _by_reason[_k] = _by_reason.get(_k, 0) + 1

    _risk: Dict[str, int] = {}
    for _r in _recs:
        _k = str(_r.get(EC.F_POLLUTION_RISK) or "(无)")
        _risk[_k] = _risk.get(_k, 0) + 1

    # 2) 可用池
    _usable = [r for r in _recs if EC.is_retrievable(r)]

    # 3) ★漏网检测：可用池里命中**明确污染特征**的
    _missed: List[dict] = []
    for _r in _usable:
        _cls = EC.classify(_r)
        # 只有「模板」与「写入侧样板」是**明确特征**；
        # CLASS_OTHER 是兜底（无特征），不能据此判污染
        if _cls in (EC.CLASS_TEMPLATE, EC.CLASS_WRITE_SIDE):
            _missed.append({
                "id": _r.get("id"),
                "reason_class": _cls,
                "summary": _summary_of(_r)[:80],
            })

    # 4) 内容质量（可用池）
    _lens = [len(_summary_of(r)) for r in _usable]
    _empty = sum(1 for _n in _lens if _n == 0)
    _short = sum(1 for _n in _lens if 0 < _n < 20)
    _lens_sorted = sorted(_lens)

    def _pct(p: float) -> int:
        if not _lens_sorted:
            return 0
        _i = min(len(_lens_sorted) - 1, int(len(_lens_sorted) * p))
        return _lens_sorted[_i]

    _avg = (sum(_lens) / float(len(_lens))) if _lens else 0.0

    return {
        "pool": normalize_relpath(pool, _PROJ),
        "total": _total,
        "marked": len(_marked),
        "marked_rate": (len(_marked) / float(_total)) if _total else 0.0,
        "by_reason": _by_reason,
        "by_risk": _risk,
        "usable": len(_usable),
        "usable_rate": (len(_usable) / float(_total)) if _total else 0.0,
        "missed": _missed,
        "missed_count": len(_missed),
        "missed_rate": (len(_missed) / float(len(_usable))) if _usable else 0.0,
        "quality": {
            "empty_summary": _empty,
            "short_lt20": _short,
            "avg_len": round(_avg, 1),
            "p50": _pct(0.50), "p90": _pct(0.90), "max": _pct(1.0),
        },
        "gate_enabled": EC.filter_enabled(),
    }


def render_md(a: Dict[str, Any]) -> str:
    _q = a["quality"]
    _L = [
        "# SERP 清洗后经验库质量评估报告（主线第55批 T7）",
        "",
        "> **★只评估，不修改。** 本报告由 `tools/serp_quality_report.py` 生成（只读）。",
        "> 任何删除/改写都必须单独提补丁并过门禁。",
        "",
        "## 一、总体",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        "| 经验池 | `{}` |".format(a["pool"]),
        "| 记录总数 | %d |" % a["total"],
        "| 已标记污染 | %d（%.2f%%） |" % (a["marked"], 100 * a["marked_rate"]),
        "| **可用（通过检索闸门）** | **%d（%.2f%%）** |" % (
            a["usable"], 100 * a["usable_rate"]),
        "| 检索闸门开关 | `%s` |" % ("开启" if a["gate_enabled"] else "关闭"),
        "",
        "## 二、污染标记分布",
        "",
        "| `cleanup_reason` | 条数 |",
        "|---|---|",
    ]
    for _k, _v in sorted(a["by_reason"].items(), key=lambda x: -x[1]):
        _L.append("| `%s` | %d |" % (_k, _v))
    _L += ["", "| `pollution_risk` | 条数 |", "|---|---|"]
    for _k, _v in sorted(a["by_risk"].items(), key=lambda x: -x[1]):
        _L.append("| `%s` | %d |" % (_k, _v))

    _L += [
        "",
        "## 三、★漏网检测（可用池中的疑似污染）",
        "",
        "对**通过了检索闸门**的 %d 条重跑 `classify()`，命中**明确污染特征**"
        % a["usable"],
        "（模板摘要 / 写入侧样板）的有 **%d 条（%.2f%%）**。"
        % (a["missed_count"], 100 * a["missed_rate"]),
        "",
        "> ★判据说明：`classify()` 的 `other_polluted` 是**兜底类**（无明确特征），",
        "> 不能据此判污染 —— 本节只统计两类**明确特征**命中。",
        "",
    ]
    if a["missed"]:
        _L += ["| id | 特征类 | 摘要预览 |", "|---|---|---|"]
        for _m in a["missed"][:30]:
            _L.append("| `{}` | `{}` | {} |".format(
                _m["id"], _m["reason_class"],
                (_m["summary"] or "—").replace("|", "\\|")))
        if a["missed_count"] > 30:
            _L.append("| … | 其余 %d 条略 | |" % (a["missed_count"] - 30))
    else:
        _L.append("**未发现漏网** —— 可用池中无记录命中明确污染特征。")

    _L += [
        "",
        "## 四、可用池内容质量",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        "| 空摘要 | %d |" % _q["empty_summary"],
        "| 摘要 < 20 字 | %d |" % _q["short_lt20"],
        "| 平均长度 | {:.1f} 字 |".format(_q["avg_len"]),
        "| 中位数 (p50) | %d |" % _q["p50"],
        "| p90 | %d |" % _q["p90"],
        "| 最长 | %d |" % _q["max"],
        "",
        "---",
        "",
        "★**纪律**：本报告不构成删除授权。清洗闸门为「只标记不删除」，",
        "本节结论仅用于判断「清洗规则是否需要补充」。",
    ]
    return "\n".join(_L) + "\n"


def main() -> int:
    _ap = argparse.ArgumentParser(description="SERP 清洗后经验库质量评估（只读）")
    _ap.add_argument("--pool", default=DEFAULT_POOL)
    _ap.add_argument("--out", default=os.path.join(
        _PROJ, "docs", "经验库质量评估报告_第55批.md"))
    _ap.add_argument("--json", dest="json_out", default="")
    args = _ap.parse_args()

    _a = assess(args.pool)
    print("总数 %d / 已标记 %d (%.2f%%) / 可用 %d (%.2f%%) / 漏网 %d" % (
        _a["total"], _a["marked"], 100 * _a["marked_rate"],
        _a["usable"], 100 * _a["usable_rate"], _a["missed_count"]))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    io.open(args.out, "w", encoding="utf-8", newline="").write(render_md(_a))
    print("报告已写入：{}".format(args.out))

    if args.json_out:
        io.open(args.json_out, "w", encoding="utf-8", newline="").write(
            json.dumps(_a, ensure_ascii=False, indent=2))
        print("JSON 已写入：{}".format(args.json_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
