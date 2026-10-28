# -*- coding: utf-8 -*-
"""384 刀1：知识快照质量问题分类扫描（只读）。

## 为什么需要它

任务书要「先出 586 处分类清单（重复/占位符/区长噪声/低信任）」，但现成的
`tools/cleanup_alias_placeholder_nodes.py` 在 720MB 快照上报「占位符污染 0 个」——
**假读数**：`mark_duplicate_nodes._locate_nodes_array` 命中了调用图里的字符串值
`"args": "nodes"`（偏移 ~1.04MB）而不是真字段 `"nodes":`（~1.25MB），于是扫了
一个不是节点数组的位置，`json.loads` 全部失败又被静默 continue ⇒ 0 个节点。
（该定位器已在本刀修复，见 `tests/test_m384_d1_snapshot_locate.py`。）

本脚本在**修好的解析器**上重跑，输出可复算的分类计数。

## 四类口径（全部可复算，不猜）

| 类 | 判据 | 备注 |
|---|---|---|
| 占位符 | `is_placeholder_alias_node`（159 批判据，与清理工具同源） | 票面点名 |
| 低信任 | `trust_score < 30` | ★该字段是 **0-100** 制（样例 90.0），不是 0-1 |
| 重复 | 同 `value` 全文出现 >1 次，多余份数 = Σ(count−1) | 按全文，不按前缀 |
| 路径噪声 | `space_path` 段数 ≥ `--path-depth`（默认 5） | 票面「区长噪声」未给判据，这是**候选口径** |

★「区长噪声」票面没有给判据。本脚本不假装知道它是什么：给出「路径段数≥N」这个
  可复算的候选口径并单列，同时输出 `space_path` 分布供人判断。

## 只读纪律

**不写快照、不改任何节点**（票面：只标记不删，且本刀只出清单不落地处置）。
`--json` 只写你显式指定的报告文件。

用法：
    python tools/m384_kb_quality_scan.py                    # 打印摘要
    python tools/m384_kb_quality_scan.py --json tmp/q.json   # 另存完整清单
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.dirname(os.path.abspath(__file__))
for _p in (_ROOT, _TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from nucleus._silent_except import silent_exc  # noqa: E402
from mark_duplicate_nodes import iter_snapshot_nodes  # noqa: E402
from cleanup_alias_placeholder_nodes import is_placeholder_alias_node  # noqa: E402

DEFAULT_SNAPSHOT = os.path.join(_ROOT, "data", "knowledge",
                                "pulse_knowledge_snapshot.json")

#: 低信任阈值：`trust_score` 是 0-100 制（实测样例 90.0）。
LOW_TRUST_MAX = 30.0
#: 极短内容阈值（候选口径，只统计不判定）
SHORT_VALUE_MAX = 12


def _segments(space_path: str) -> int:
    return len([_x for _x in str(space_path or "").split("/") if _x])


def scan(snapshot: str, path_depth: int = 5) -> dict:
    """流式扫描快照，返回分类统计与明细。**只读**。"""
    total = 0
    bad_slices = 0
    placeholder = []
    low_trust = []
    deep_path = []
    empty_path = []
    short_value = []
    value_counter: dict = {}
    path_counter = collections.Counter()
    quality_counter = collections.Counter()
    placeholder_by_path = collections.Counter()

    for _d in iter_snapshot_nodes(snapshot):
        total += 1
        _v = str(_d.get("value") or "")
        _sp = str(_d.get("space_path") or "")
        _nid = _d.get("node_id")
        value_counter[_v] = value_counter.get(_v, 0) + 1
        path_counter[_sp] += 1
        quality_counter[str(_d.get("quality_flag") or "<无>")] += 1

        if is_placeholder_alias_node(_d):
            placeholder.append({"node_id": _nid, "space_path": _sp,
                                "value": _v[:100]})
            placeholder_by_path[_sp] += 1

        try:
            _ts = float(_d.get("trust_score"))
        except (TypeError, ValueError) as _te:
            silent_exc(_te, where="m384_kb_quality_scan.scan trust_score",
                       level="debug")
            _ts = -1.0
        if 0 <= _ts < LOW_TRUST_MAX:
            low_trust.append({"node_id": _nid, "space_path": _sp,
                              "trust_score": _ts, "value": _v[:80]})

        if not _sp.strip():
            empty_path.append(_nid)
        elif _segments(_sp) >= path_depth:
            deep_path.append({"node_id": _nid, "space_path": _sp,
                              "depth": _segments(_sp)})

        if 0 < len(_v.strip()) < SHORT_VALUE_MAX:
            short_value.append({"node_id": _nid, "value": _v})

    dup_groups = {v: c for v, c in value_counter.items() if c > 1}
    dup_extra = sum(c - 1 for c in dup_groups.values())
    dup_detail = sorted(({"value": v[:100], "count": c}
                         for v, c in dup_groups.items()),
                        key=lambda x: -x["count"])[:30]

    return {
        "snapshot": snapshot,
        "total_nodes": total,
        "bad_slices": bad_slices,
        "placeholder": len(placeholder),
        "low_trust": len(low_trust),
        "duplicate_extra": dup_extra,
        "duplicate_groups": len(dup_groups),
        "deep_path": len(deep_path),
        "empty_path": len(empty_path),
        "short_value": len(short_value),
        "path_depth_threshold": path_depth,
        "quality_flag_now": dict(quality_counter),
        "placeholder_by_path_top": placeholder_by_path.most_common(10),
        "space_path_top": path_counter.most_common(15),
        "uniq_values": len(value_counter),
        "samples": {
            "placeholder": placeholder[:10],
            "low_trust": low_trust[:10],
            "deep_path": deep_path[:10],
            "duplicate": dup_detail[:10],
        },
        "_detail": {
            "placeholder": placeholder,
            "low_trust": low_trust,
            "deep_path": deep_path,
        },
    }


def analyse_path(space_path: str) -> dict:
    """★385刀3：单条长路径的**分档判据**（纯函数，可复算）。

    为什么用「段重复」作最强信号：正常层级不会出现同名层级两次
    （`/自我/架构/设计文档/...` 里每段都不同）；而
    `/…/编程助手/框架/异步/协议/框架/进化/框架/编译/框架/…` 里「框架」出现 4 次，
    是把**关键词当成路径段**堆出来的 —— 这才是真噪声，不是"路径长"。

    `noise=True` 的判据：**段重复 ≥2 次** 或 **段数 ≥8**。
    """
    _s = [x for x in str(space_path or "").split("/") if x]
    _c = collections.Counter(_s)
    _dup = [k for k, v in _c.items() if v >= 2]
    return {
        "path": space_path,
        "depth": len(_s),
        "chars": len(str(space_path or "")),
        "dup_segs": _dup,
        "max_dup": (max(_c.values()) if _c else 0),
        "long_segs": [x for x in _s if len(x) > 24],
        "noise": bool(_dup) or len(_s) >= 8,
    }


def classify_long_paths(rows: list) -> dict:
    """★385刀3：把 `deep_path` 明细分成两档（`noise_likely` / `long_ok`）。

    返回 `{"noise_likely": [...], "long_ok": [...], "depth_hist": {...}}`，
    每项都是 `analyse_path` 的输出，按「噪声分高者先」排序。
    """
    _a = [analyse_path((r or {}).get("space_path")) for r in (rows or [])
          if isinstance(r, dict)]
    _noise = [x for x in _a if x["noise"]]
    _ok = [x for x in _a if not x["noise"]]
    _noise.sort(key=lambda y: (-y["max_dup"], -y["depth"]))
    _ok.sort(key=lambda y: (-y["depth"], -y["chars"]))
    return {
        "noise_likely": _noise,
        "long_ok": _ok,
        "depth_hist": dict(collections.Counter(x["depth"] for x in _a)),
    }


def _print(r: dict) -> None:
    print("=" * 76)
    print("384刀1 知识快照质量问题分类（只读）")
    print("=" * 76)
    print("节点总数 = %d｜唯一 value = %d" % (r["total_nodes"], r["uniq_values"]))
    print()
    print("占位符（159批判据）      = %d" % r["placeholder"])
    print("低信任（trust<%.0f，0-100 制）= %d" % (LOW_TRUST_MAX, r["low_trust"]))
    print("重复：%d 组，多余 %d 份" % (r["duplicate_groups"], r["duplicate_extra"]))
    print("路径噪声（段数>=%d）      = %d   ← 票面「区长噪声」的候选口径"
          % (r["path_depth_threshold"], r["deep_path"]))
    print("路径为空                   = %d" % r["empty_path"])
    print("极短内容（<%d 字）         = %d" % (SHORT_VALUE_MAX, r["short_value"]))
    print()
    print("quality_flag 现值：%s" % r["quality_flag_now"])
    print()
    print("占位符按 space_path top 5：")
    for _p, _c in r["placeholder_by_path_top"][:5]:
        print("  %5d  %s" % (_c, _p or "(空)"))
    print()
    print("space_path top 5（全量）：")
    for _p, _c in r["space_path_top"][:5]:
        print("  %5d  %s" % (_c, _p or "(空)"))


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="384刀1 知识快照质量分类（只读）")
    _ap.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)
    _ap.add_argument("--path-depth", type=int, default=5)
    _ap.add_argument("--json", default="", help="完整清单写到该路径")
    _args = _ap.parse_args(argv)

    if not os.path.isfile(_args.snapshot):
        print("[错误] 快照不存在: %s" % _args.snapshot)
        return 2

    try:
        _r = scan(_args.snapshot, _args.path_depth)
    except ValueError as _e:
        silent_exc(_e, where="m384_kb_quality_scan.scan", level="warning")
        print("[失败] 快照结构无法定位节点数组：%s" % _e)
        print("       若报「未找到 nodes:」说明结构又变了，先修 _locate_nodes_array。")
        return 3

    if _r["total_nodes"] == 0:
        # ★这条守卫的意义：0 个节点与「快照真空」读数一样，必须显式拒绝出结论
        print("[失败] 扫出 0 个节点 —— 这不是「干净」，是解析失败。")
        print("       请先核对 _locate_nodes_array 的定位偏移。")
        return 4

    _print(_r)

    if _args.json:
        _out = _args.json if os.path.isabs(_args.json) else os.path.join(_ROOT, _args.json)
        os.makedirs(os.path.dirname(_out) or ".", exist_ok=True)
        with open(_out, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(_r, _f, ensure_ascii=False, indent=1)
        print("\n完整清单已写入 %s" % _out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
