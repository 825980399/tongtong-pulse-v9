#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SERP 经验库污染分析器（**只读**，绝不写生产数据）

主线第46批 T2（P0-3）交付物之一。

用法::

    python tools/serp_pollution_analyzer.py                       # 分析并打印
    python tools/serp_pollution_analyzer.py --report <out.json>   # 另存结构化报告
    python tools/serp_pollution_analyzer.py --top 30              # 重复模板 TopN

设计约束（★硬性）：
  * **只读**：全程不打开任何写模式的句柄指向生产数据目录。
  * 与第44批 ``nucleus/data/write_guard.py`` 协同：即便误传 ``--apply`` 也**不提供写能力**。
  * 框架在线时可安全运行（只读不产生写竞争）。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ★第48批 T2（P2-320）：跨盘安全的相对路径
from nucleus._silent_except import silent_exc
from nucleus.data.path_utils import safe_relpath as _safe_relpath  # noqa: E402

DEFAULT_POOL = os.path.join(ROOT, "data", "experience", "experience_pool.json")

# 「模板化」检测：把摘要中的可变片段替换为占位符后比较
_TPL_PATTERNS = [
    (re.compile(r"\d+(?:\.\d+)?%?"), "#"),
    (re.compile(r"\d+"), "#"),
]


def _norm_tpl(text: str) -> str:
    """归一化：数字 → #，空白折叠。用于识别「模板化生成」。"""
    _s = (text or "").strip()
    for _p, _r in _TPL_PATTERNS:
        _s = _p.sub(_r, _s)
    return re.sub(r"\s+", " ", _s)


def load_pool(path: str) -> list:
    """只读载入经验记录列表。"""
    if not os.path.isfile(path):
        raise SystemExit("[SERP] 经验库不存在: {}".format(path))
    _doc = json.load(io.open(path, encoding="utf-8"))
    if isinstance(_doc, list):
        return _doc
    if isinstance(_doc, dict):
        for _k in ("experiences", "records", "items", "data", "pool"):
            if isinstance(_doc.get(_k), list):
                return _doc[_k]
    raise SystemExit("[SERP] 无法识别经验库结构: {}".format(path))


def basic_stats(recs: list) -> dict:
    """基础统计：总数 / 污染数 / 干净数 / 原因分布。"""
    _n = len(recs)
    _pol = [r for r in recs if isinstance(r, dict) and r.get("polluted")]
    _clean = [r for r in recs if isinstance(r, dict) and not r.get("polluted")]
    return {
        "total": _n,
        "polluted": len(_pol),
        "clean": len(_clean),
        "pollution_rate": round(len(_pol) / _n, 4) if _n else 0.0,
        "reason_dist": dict(Counter(
            str(r.get("pollution_reason") or "<无>") for r in _pol).most_common()),
        "quality_flag_dist": dict(Counter(
            str(r.get("quality_flag") or "<无>") for r in recs).most_common()),
        "field_coverage": {
            _f: sum(1 for r in recs if isinstance(r, dict) and _f in r)
            for _f in ("polluted", "pollution_reason", "quality_flag",
                       "quality_weight", "is_summarized", "summary",
                       "motivation", "timestamp")
        },
    }


def time_dist(recs: list, bucket_s: int = 86400) -> dict:
    """污染记录的时间分布（按 bucket_s 分桶，默认 1 天）。"""
    _b = defaultdict(lambda: {"total": 0, "polluted": 0})
    for _r in recs:
        if not isinstance(_r, dict):
            continue
        _ts = float(_r.get("timestamp") or 0)
        if not _ts:
            continue
        _k = int(_ts // bucket_s) * bucket_s
        _b[_k]["total"] += 1
        if _r.get("polluted"):
            _b[_k]["polluted"] += 1
    _rows = []
    for _k in sorted(_b):
        _v = _b[_k]
        _rows.append({
            "bucket_start": _k,
            "date": time.strftime("%Y-%m-%d", time.localtime(_k)),
            "total": _v["total"],
            "polluted": _v["polluted"],
            "clean": _v["total"] - _v["polluted"],
            "rate": round(_v["polluted"] / _v["total"], 4) if _v["total"] else 0.0,
        })
    return {"bucket_seconds": bucket_s, "rows": _rows}


def duplicate_shape(recs: list, topn: int = 20) -> dict:
    """分析「内容重复(历史灌水)」的形态。

    分三层判定：
      L1 完全重复   —— summary 原文完全相同
      L2 模板化生成 —— 数字归一化后相同（说明是同一句话灌水）
      L3 高度相似   —— 归一化后仍不同，但 token 集合高度重合（Jaccard ≥ 0.8）
    """
    _sums = [(r.get("summary") or "") for r in recs if isinstance(r, dict)]

    _exact = Counter(_sums)
    _exact_groups = {k: v for k, v in _exact.items() if k and v > 1}

    _tpl = Counter(_norm_tpl(s) for s in _sums if s)
    _tpl_groups = {k: v for k, v in _tpl.items() if k and v > 1}

    # L3：在同一 motivation 组内做 Jaccard 近似（限制规模，避免 O(n^2) 爆炸）
    _by_mot = defaultdict(list)
    for _r in recs:
        if isinstance(_r, dict):
            _by_mot[str(_r.get("motivation") or "")].append(_r.get("summary") or "")
    _near = 0
    _near_samples = []
    for _mot, _ss in _by_mot.items():
        _ss = [s for s in _ss if s]
        if len(_ss) < 2 or len(_ss) > 400:
            continue
        _seen = set()
        for _i in range(len(_ss)):
            _a = set(_norm_tpl(_ss[_i]))
            if not _a:
                continue
            for _j in range(_i + 1, len(_ss)):
                _b = set(_norm_tpl(_ss[_j]))
                if not _b:
                    continue
                _jac = len(_a & _b) / len(_a | _b)
                if _jac >= 0.8 and _norm_tpl(_ss[_i]) != _norm_tpl(_ss[_j]):
                    _key = tuple(sorted((_ss[_i], _ss[_j])))
                    if _key not in _seen:
                        _seen.add(_key)
                        _near += 1
                        if len(_near_samples) < 10:
                            _near_samples.append({
                                "motivation": _mot[:40],
                                "a": _ss[_i][:70], "b": _ss[_j][:70],
                                "jaccard": round(_jac, 3)})

    return {
        "n_summaries": len([s for s in _sums if s]),
        "L1_exact": {
            "distinct_texts": len(_exact),
            "duplicate_groups": len(_exact_groups),
            "records_in_dup_groups": sum(_exact_groups.values()),
            "wasted_records": sum(v - 1 for v in _exact_groups.values()),
            "top": [{"text": k[:90], "count": v}
                    for k, v in _exact.most_common(topn) if v > 1],
        },
        "L2_template": {
            "distinct_templates": len(_tpl),
            "template_groups": len(_tpl_groups),
            "records_in_tpl_groups": sum(_tpl_groups.values()),
            "top": [{"template": k[:90], "count": v}
                    for k, v in _tpl.most_common(topn) if v > 1],
        },
        "L3_near": {
            "pairs": _near,
            "samples": _near_samples,
        },
    }


def irreversible_analysis(recs: list) -> dict:
    """识别「不可逆污染」的特征。

    不可逆的判定维度（可解释、可复核）：
      A. 已被摘要合并（``is_summarized=true``）——原文已并入上层摘要，删原文会丢信息
      B. 已被激活引用（``activation_count > 0``）——有消费方依赖
      C. 带非零质量权重（``quality_weight > 0``）——已进入质量体系
      D. 带字段快照（``field_frequency_snapshot`` 非空）——已参与场频统计
    """
    _pol = [r for r in recs if isinstance(r, dict) and r.get("polluted")]
    _flags = {"summarized": 0, "activated": 0, "weighted": 0, "snapshot": 0}
    _combos = Counter()
    _irreversible = []
    for _r in _pol:
        _a = bool(_r.get("is_summarized"))
        _b = int(_r.get("activation_count") or 0) > 0
        _c = float(_r.get("quality_weight") or 0) > 0
        _d = bool(_r.get("field_frequency_snapshot"))
        _flags["summarized"] += _a
        _flags["activated"] += _b
        _flags["weighted"] += _c
        _flags["snapshot"] += _d
        _key = ("S" if _a else "-") + ("A" if _b else "-") + \
               ("W" if _c else "-") + ("F" if _d else "-")
        _combos[_key] += 1
        if _a or _b or _c or _d:
            _irreversible.append(_r.get("id"))
    return {
        "polluted_total": len(_pol),
        "flag_counts": _flags,
        "combination_dist": dict(_combos.most_common()),
        "irreversible_count": len(_irreversible),
        "irreversible_rate": round(len(_irreversible) / len(_pol), 4) if _pol else 0.0,
        "safely_deletable": len(_pol) - len(_irreversible),
        "sample_ids": _irreversible[:10],
    }


def clean_quality(recs: list) -> dict:
    """干净记录的质量分布。"""
    _clean = [r for r in recs if isinstance(r, dict) and not r.get("polluted")]
    if not _clean:
        return {"count": 0}
    _w = [float(r.get("quality_weight") or 0) for r in _clean]
    _w.sort()
    _n = len(_w)

    def _pct(p):
        return _w[min(_n - 1, int(_n * p))] if _n else 0.0

    return {
        "count": _n,
        "weight_min": _w[0], "weight_p25": _pct(0.25),
        "weight_median": _pct(0.50), "weight_p75": _pct(0.75),
        "weight_max": _w[-1],
        "weight_mean": round(sum(_w) / _n, 4),
        "zero_weight": sum(1 for x in _w if x == 0),
        "motivation_dist": dict(Counter(
            str(r.get("motivation") or "<无>")[:40] for r in _clean).most_common(12)),
        "summarized": sum(1 for r in _clean if r.get("is_summarized")),
        "activated": sum(1 for r in _clean if int(r.get("activation_count") or 0) > 0),
        # 干净记录内部是否也存在重复（★清洗前必查）
        "internal_exact_dup_groups": sum(
            1 for _k, _v in Counter((r.get("summary") or "") for r in _clean).items()
            if _k and _v > 1),
    }


#: ★第47批 T2 摘要止血代码实施时刻（默认基准线）——
#: 2026-09-14 07:40，早于此时刻的记录都是「止血前」的。
DEFAULT_HEMOSTASIS_SINCE = 1789342800.0     # 2026-09-14 07:40:00 GMT+8


def parse_since(value) -> float:
    """解析 `--since`：支持 epoch 秒或 ``YYYY-MM-DD HH:MM:SS``。"""
    if value in (None, ""):
        return DEFAULT_HEMOSTASIS_SINCE
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        silent_exc(e, where="tools.serp_pollution_analyzer::parse_since L269")
    for _fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(str(value), _fmt))
        except ValueError:
            continue
    return DEFAULT_HEMOSTASIS_SINCE


def _seg_stats(recs: list) -> dict:
    """对一段记录做覆盖率统计。"""
    _n = len(recs)
    if not _n:
        return {"count": 0}
    _pol = sum(1 for r in recs if isinstance(r, dict) and r.get("polluted"))
    _raw = sum(1 for r in recs
               if isinstance(r, dict) and (r.get("raw_summary") or "").strip())
    _summ = sum(1 for r in recs if isinstance(r, dict) and r.get("is_summarized"))
    # motivation 完整性：摘要过的记录里，motivation 是否仍 > 50 字（未被截断）
    _moti_ok = 0
    for r in recs:
        if not isinstance(r, dict):
            continue
        if not r.get("is_summarized"):
            _moti_ok += 1                      # 未摘要 → 必然完整
        elif len(str(r.get("motivation") or "")) > 50:
            _moti_ok += 1
    _snap = sum(1 for r in recs
                if isinstance(r, dict) and r.get("field_frequency_snapshot"))
    _ver2 = sum(1 for r in recs
                if isinstance(r, dict) and r.get("summary_version") == 2)
    return {
        "count": _n,
        "polluted": _pol,
        "clean": _n - _pol,
        "pollution_rate": round(_pol / _n, 4),
        "raw_summary_coverage": round(_raw / _n, 4),
        "raw_summary_count": _raw,
        "summarized": _summ,
        "motivation_intact_rate": round(_moti_ok / _n, 4),
        "snapshot_coverage": round(_snap / _n, 4),
        "summary_version_2": _ver2,
    }


def hemostasis_section(recs: list, since: float | None = None) -> dict:
    """★摘要止血效果验证：按「止血前 / 止血后」分段统计。

    基准线默认取第47批 T2 代码实施时刻（``DEFAULT_HEMOSTASIS_SINCE``）。

    ★注意：**代码实施 ≠ 生效**。`_summarize_experience` 属框架运行时逻辑，
       必须**重启框架**后才会用新实现处理新增/衰减记录。
       若框架未重启，`after` 段的新增记录仍带旧行为特征 → 判定为「止血未生效」。
    """
    _since = parse_since(since) if since is not None else DEFAULT_HEMOSTASIS_SINCE
    _before, _after = [], []
    for _r in recs:
        if not isinstance(_r, dict):
            continue
        _ts = float(_r.get("timestamp") or 0)
        (_after if _ts >= _since else _before).append(_r)

    _b = _seg_stats(_before)
    _a = _seg_stats(_after)

    # ---------- 判定 ----------
    _verdict, _reason = "unknown", ""
    if not _after:
        _verdict = "not_yet_effective"
        _reason = ("基准线（{}）之后**零新增记录**。代码已实施但测试期无新数据，"
                   "无法确认止血效果 —— 需 **重启框架** 后继续观察。".format(time.strftime("%Y-%m-%d %H:%M", time.localtime(_since))))
    else:
        _new_pol = _a.get("polluted", 0)
        _cov = _a.get("raw_summary_coverage", 0.0)
        if _new_pol == 0 and _cov >= 0.9:
            _verdict = "effective"
            _reason = ("止血后新增 %d 条：**0 条污染**，raw_summary 覆盖率 %.1f%% "
                       "→ 止血生效。" % (_a["count"], _cov * 100))
        elif _new_pol == 0 and _cov < 0.9:
            _verdict = "partial"
            _reason = ("止血后新增 %d 条：0 条污染，但 raw_summary 覆盖率仅 %.1f%% "
                       "（<90%%）→ 可能未重启或摘要路径未触发。"
                       % (_a["count"], _cov * 100))
        else:
            _verdict = "not_yet_effective"
            _reason = ("止血后新增 %d 条中仍有 **%d 条污染** → 止血未生效"
                       "（框架可能未重启，或存在其他污染源）。"
                       % (_a["count"], _new_pol))

    return {
        "since": _since,
        "since_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_since)),
        "before": _b,
        "after": _a,
        "new_records_after": _a.get("count", 0),
        "new_pollution_after": _a.get("polluted", 0),
        "verdict": _verdict,
        "reason": _reason,
        "can_proceed_cleanup": _verdict in ("effective", "partial"),
    }


def build_report(path: str, topn: int = 20,
                 since: float | None = None) -> dict:
    _recs = load_pool(path)
    _since_ts = parse_since(since) if since is not None \
        else DEFAULT_HEMOSTASIS_SINCE
    return {
        "meta": {
            "generated_at": time.time(),
            "generated_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            # ★第48批 T2（P2-320）：跨盘安全（--pool 可传任意盘路径）
            "pool_path": _safe_relpath(path, ROOT),
            "pool_size_bytes": os.path.getsize(path),
            "pool_mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(os.path.getmtime(path))),
            "read_only": True,
        },
        "basic": basic_stats(_recs),
        "time_dist": time_dist(_recs),
        "duplicate_shape": duplicate_shape(_recs, topn=topn),
        "irreversible": irreversible_analysis(_recs),
        "clean_quality": clean_quality(_recs),
        # ★第48批 T4：摘要止血效果验证（按时间分段 + 三项覆盖率）
        "hemostasis": hemostasis_section(_recs, _since_ts),
    }


def main() -> int:
    _ap = argparse.ArgumentParser(description="SERP 经验库污染分析器（只读）")
    _ap.add_argument("--pool", default=DEFAULT_POOL, help="经验库 JSON 路径")
    _ap.add_argument("--report", default="", help="结构化报告输出路径（JSON）")
    _ap.add_argument("--top", type=int, default=20, help="重复模板 TopN")
    _ap.add_argument("--since", default="",
                     help="止血生效时刻（epoch 秒或 'YYYY-MM-DD HH:MM:SS'）")
    _ns = _ap.parse_args()

    _rep = build_report(_ns.pool, topn=_ns.top, since=_ns.since or None)
    _b = _rep["basic"]
    _t = _rep["time_dist"]
    _d = _rep["duplicate_shape"]
    _i = _rep["irreversible"]
    _c = _rep["clean_quality"]

    print("=" * 70)
    print("SERP 经验库污染分析（只读）")
    print("=" * 70)
    print("数据源: {}  ({:.2f} MB, mtime {})".format(_rep["meta"]["pool_path"],
             _rep["meta"]["pool_size_bytes"] / 1024.0 / 1024,
             _rep["meta"]["pool_mtime"]))
    print()
    print("[1] 基础统计")
    print("    总记录 %d  污染 %d (%.1f%%)  干净 %d"
          % (_b["total"], _b["polluted"], _b["pollution_rate"] * 100, _b["clean"]))
    print("    污染原因分布:", _b["reason_dist"])
    print()
    print("[2] 时间分布（按天，污染最多的前 10 天）")
    for _r in sorted(_t["rows"], key=lambda x: -x["polluted"])[:10]:
        print("    %s  总 %4d  污染 %4d  (%.1f%%)"
              % (_r["date"], _r["total"], _r["polluted"], _r["rate"] * 100))
    print()
    print("[3] 重复形态")
    print("    L1 完全重复  : %d 组，涉及 %d 条，冗余 %d 条"
          % (_d["L1_exact"]["duplicate_groups"],
             _d["L1_exact"]["records_in_dup_groups"],
             _d["L1_exact"]["wasted_records"]))
    print("    L2 模板化    : %d 组，涉及 %d 条"
          % (_d["L2_template"]["template_groups"],
             _d["L2_template"]["records_in_tpl_groups"]))
    print("    L3 高度相似  : %d 对" % _d["L3_near"]["pairs"])
    print()
    print("[4] 不可逆污染")
    print("    污染 %d 条中，带依赖标记的 %d 条 (%.1f%%) → 可安全删除 %d 条"
          % (_i["polluted_total"], _i["irreversible_count"],
             _i["irreversible_rate"] * 100, _i["safely_deletable"]))
    print("    依赖标记:", _i["flag_counts"])
    print()
    _h = _rep["hemostasis"]
    print()
    print("[5] ★摘要止血效果验证（基准线 {}）".format(_h["since_str"]))
    _b, _a = _h["before"], _h["after"]
    print("    止血前: %5d 条  污染 %4d (%.1f%%)  raw_summary 覆盖 %.1f%%"
          % (_b.get("count", 0), _b.get("polluted", 0),
             _b.get("pollution_rate", 0) * 100,
             _b.get("raw_summary_coverage", 0) * 100))
    if _a.get("count"):
        print("    止血后: %5d 条  污染 %4d (%.1f%%)  raw_summary 覆盖 %.1f%%"
              % (_a["count"], _a.get("polluted", 0),
                 _a.get("pollution_rate", 0) * 100,
                 _a.get("raw_summary_coverage", 0) * 100))
    else:
        print("    止血后:     0 条（无新增数据）")
    print("    判定: **{}**".format(_h["verdict"]))
    print("    说明: {}".format(_h["reason"]))
    print("    可否执行清洗: %s" % ("是" if _h["can_proceed_cleanup"] else "否"))
    print()
    print("[6] 干净记录质量")
    if _c.get("count"):
        print("    %d 条  weight: min=%.3f p25=%.3f med=%.3f p75=%.3f max=%.3f mean=%.3f"
              % (_c["count"], _c["weight_min"], _c["weight_p25"], _c["weight_median"],
                 _c["weight_p75"], _c["weight_max"], _c["weight_mean"]))
        print("    零权重 %d 条；已摘要 %d 条；被激活 %d 条"
              % (_c["zero_weight"], _c["summarized"], _c["activated"]))
        print("    ★干净记录内部完全重复组: %d" % _c["internal_exact_dup_groups"])

    if _ns.report:
        _os_dir = os.path.dirname(os.path.abspath(_ns.report))
        if _os_dir and not os.path.isdir(_os_dir):
            os.makedirs(_os_dir, exist_ok=True)
        with io.open(_ns.report, "w", encoding="utf-8") as _f:
            _f.write(json.dumps(_rep, ensure_ascii=False, indent=2))
        print()
        print("报告已写出 →", _ns.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
