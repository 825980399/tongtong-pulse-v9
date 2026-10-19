# -*- coding: utf-8 -*-
"""318批刀3：压测首跑可复用件——基线快照 + 七段留痕对表 + 人推计数。

用法：
    python tools/stress/collect_stress_baseline.py                 # 出一份基线快照
    python tools/stress/collect_stress_baseline.py --loop 3        # 第 3 圈（带圈次前缀）
    python tools/stress/collect_stress_baseline.py --json out.json # 另存 JSON

**开三环、闭四环**（星轨+小林裁定）：本脚本只读统计，不触发执行环；
执行环（第四环）在压测期间显式标注"不真执行"（`requires_confirmation=True` 不消费）。

八项基线：台账 / pending / 待复核+覆盖率 / 图 / hub / ledger / 产出率 / M92。
七段留痕：发现`[307刀1]` → 入池`review_intake` → 分诊`[311刀1]` → 派单`[304刀5]`
          → 执行`SOP硬闸截断` → 验证`[309刀1]` → 沉淀`[316刀2]`。
人推计数（五类）：人工重启 / 改config / 手动拍 / 数值仅人工变化 / requires_confirmation手动放行。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

#: 七段留痕锚点（闭环顺序）
SEGMENTS = [
    ("1发现", "[307刀1]"),
    ("2入池", "review_intake"),
    ("3分诊", "[311刀1]"),
    ("4派单", "[304刀5]"),
    ("5执行", "SOP硬闸截断"),
    ("6验证", "[309刀1]"),
    ("7沉淀", "[316刀2]"),
]

#: 人推计数锚点（五类）
HUMAN_PUSH = [
    ("人工重启", r"框架启动完成|framework.*(start|boot)|曈曈启动"),
    ("改config", r"config.*(已修改|reload|热加载)"),
    ("手动拍", r"手动拍|manual_beat|人工触发"),
    ("数值仅人工变化", r"(人工|手动).*(调整|修改|设置)"),
    ("requires_confirmation手动放行", r"requires_confirmation.*(放行|确认|approved)"),
]


def _read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as _f:
            return json.load(_f)
    except Exception as _e:
        print(f"[WARN] 读 JSON 失败 {path}: {_e}")
        return default


def _count_jsonl(path: str) -> int:
    _n = 0
    try:
        with open(path, encoding="utf-8") as _f:
            for _line in _f:
                if _line.strip():
                    _n += 1
    except Exception as _e:
        print(f"[WARN] 读 JSONL 失败 {path}: {_e}")
    return _n


def _latest(pattern: str) -> str:
    _fs = sorted(glob.glob(os.path.join(_ROOT, pattern)))
    return _fs[-1] if _fs else ""


def _ledger() -> dict:
    _p = os.path.join(_ROOT, "data", "world_model", "expectation_ledger.jsonl")
    _rows = []
    try:
        with open(_p, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _rows.append(json.loads(_line))
                except ValueError:
                    continue
    except Exception as _e:
        print(f"[WARN] ledger 不可读: {_e}")
    _v: dict[str, int] = {}
    _g: dict[str, int] = {}
    for _r in _rows:
        _v[_r.get("verdict", "?")] = _v.get(_r.get("verdict", "?"), 0) + 1
        if _r.get("gap_reason"):
            _g[_r["gap_reason"]] = _g.get(_r["gap_reason"], 0) + 1
    _mt = 0.0
    try:
        _mt = float(os.path.getmtime(_p))
    except OSError as _e:
        print(f"[WARN] ledger mtime 不可读: {_e}")
    return {"rows": len(_rows), "verdicts": _v, "gap_reasons": _g, "mtime": _mt}


def baseline() -> dict:
    """八项基线快照。"""
    _out: dict[str, Any] = {"as_of": time.time(),
                            "as_of_str": time.strftime("%Y-%m-%d %H:%M:%S")}
    # 1) 台账（approved / runtime_verified）
    _led = _read_json(os.path.join(_ROOT, "data", "patches", "patch_history.json"), {})
    if isinstance(_led, dict):
        _led = _led.get("patches") or _led.get("history") or []
    if not isinstance(_led, list):
        _led = []
    _st: dict[str, int] = {}
    for _p in _led:
        if isinstance(_p, dict):
            _k = str(_p.get("status", "?"))
            _st[_k] = _st.get(_k, 0) + 1
    _out["ledger_patches"] = {"total": len(_led), "by_status": _st}
    # 2) pending
    _pend = _read_json(os.path.join(_ROOT, "data", "patches", "pending_patches.json"), [])
    if isinstance(_pend, dict):
        _pend = _pend.get("patches") or _pend.get("pending") or []
    _out["pending"] = len(_pend) if isinstance(_pend, list) else 0
    # 3) 待复核 + 覆盖率（走 self_inspector 统计出口）
    try:
        from nucleus.self_inspector import get_dead_code_review_stats
        _s = get_dead_code_review_stats() or {}
        _out["dead_code_review"] = {
            "count": _s.get("count", 0), "mode": _s.get("mode", ""),
            "coverage": _s.get("coverage", 0.0),
            "resolve_rate": _s.get("resolve_rate"),
            "criteria_version": _s.get("criteria_version", ""),
            "graph_file": _s.get("graph_file", "")}
    except Exception as _e:
        print(f"[WARN] 待复核统计不可读: {_e}")
        _out["dead_code_review"] = {"error": type(_e).__name__}
    # 4) 图
    _g = _latest("data/call_graph/graph_*.json")
    _gd = _read_json(_g, {}) or {}
    _out["graph"] = {"file": os.path.basename(_g) if _g else "",
                     "nodes": len(_gd.get("nodes") or []),
                     "edges": len(_gd.get("edges") or []),
                     "stats": _gd.get("stats") or {}}
    # 5) hub
    _hub_p = os.path.join(_ROOT, "data", "verification_learning", "hub_entries.json")
    _hub = _read_json(_hub_p, None)
    if _hub is None:
        _hub_p2 = _latest("data/**/verification_learning*.json")
        _hub = _read_json(_hub_p2, None) if _hub_p2 else None
    if isinstance(_hub, dict):
        _hub = _hub.get("entries") or []
    if not isinstance(_hub, list):
        _hub = []
    _org: dict[str, int] = {}
    _origin: dict[str, int] = {}
    for _e in _hub:
        if not isinstance(_e, dict):
            continue
        _org[str(_e.get("organ", "?"))] = _org.get(str(_e.get("organ", "?")), 0) + 1
        _origin[str(_e.get("origin", "?"))] = _origin.get(str(_e.get("origin", "?")), 0) + 1
    _out["hub"] = {"entries": len(_hub), "by_organ": _org, "by_origin": _origin}
    # 6) ledger
    _out["expectation_ledger"] = _ledger()
    # 7) 产出率
    try:
        from nucleus.reasoning.SafeEvolutionExecutor import get_output_rate_stats
        _out["output_rate"] = get_output_rate_stats()
    except Exception as _e:
        print(f"[WARN] 产出率不可读: {_e}")
        _out["output_rate"] = {"error": type(_e).__name__}
    # 8) M92
    _m92 = _out["dead_code_review"].get("mode", "")
    _out["m92"] = {"dead_code_mode": _m92,
                   "resolve_gate_judge": 0.50,
                   "high_confidence_gate": 0.70,
                   "resolve_rate": _out["dead_code_review"].get("resolve_rate")}
    return _out


def segment_counts(since_ts: float | None = None) -> dict[str, int]:
    """七段留痕计数（扫描 logs/pulse*.log*）。"""
    _out = {_name: 0 for _name, _ in SEGMENTS}
    _files = sorted(glob.glob(os.path.join(_ROOT, "logs", "pulse*.log*")))
    for _f in _files:
        try:
            with open(_f, "rb") as _fh:
                _s = _fh.read().decode("utf-8", "ignore")
        except Exception as _e:
            print(f"[WARN] 日志不可读 {_f}: {_e}")
            continue
        for _name, _anchor in SEGMENTS:
            _out[_name] += _s.count(_anchor)
    return _out


def human_push_counts() -> dict[str, int]:
    """人推计数（五类规则写死，可审计）。"""
    _out = {_name: 0 for _name, _ in HUMAN_PUSH}
    _files = sorted(glob.glob(os.path.join(_ROOT, "logs", "pulse*.log*")))
    for _f in _files:
        try:
            with open(_f, "rb") as _fh:
                _s = _fh.read().decode("utf-8", "ignore")
        except Exception as _e:
            print(f"[WARN] 日志不可读 {_f}: {_e}")
            continue
        for _name, _pat in HUMAN_PUSH:
            _out[_name] += len(re.findall(_pat, _s))
    return _out


def task_face() -> dict:
    """刀4：任务面前置判定——`reviewed>0` 才算"这段有料"。"""
    try:
        from nucleus.reasoning.SafeEvolutionExecutor import get_output_rate_stats
        _s = get_output_rate_stats()
    except Exception as _e:
        print(f"[WARN] 产出率不可读: {_e}")
        return {"task_face_empty": True, "reason": "产出率不可读"}
    _runs = int(_s.get("runs", 0) or 0)
    _prod = int(_s.get("productive", 0) or 0)
    return {"runs": _runs, "productive": _prod, "rate": _s.get("rate", 0.0),
            "task_face_empty": _prod == 0,
            "reason": ("零产出归因=判据看不见（子进程不可见父进程运行期指标）"
                       if _prod == 0 and _runs else "本窗尚无子进程跑批")}


def main() -> int:
    _ap = argparse.ArgumentParser(description="压测首跑：基线快照 + 七段留痕 + 人推计数")
    _ap.add_argument("--loop", type=int, default=None, help="压测圈次（写进输出）")
    _ap.add_argument("--json", default="", help="另存 JSON 路径")
    _a = _ap.parse_args()

    _res = {"loop": _a.loop, "baseline": baseline(),
            "segments": segment_counts(), "human_push": human_push_counts(),
            "task_face": task_face(),
            "note": "开三环闭四环：执行环(第四环)不真执行；本脚本只读统计"}

    print("=" * 62)
    print("压测首跑快照  圈次=%s  时间=%s" % (_a.loop or "-", _res["baseline"]["as_of_str"]))
    print("=" * 62)
    _b = _res["baseline"]
    print("1) 台账: %s" % _b["ledger_patches"])
    print("2) pending: %s" % _b["pending"])
    print("3) 待复核/覆盖率: %s" % _b["dead_code_review"])
    print("4) 图: %s nodes=%s edges=%s" % (_b["graph"]["file"], _b["graph"]["nodes"],
                                           _b["graph"]["edges"]))
    print("5) hub: %s" % _b["hub"])
    print("6) ledger: %s" % _b["expectation_ledger"])
    print("7) 产出率: %s" % _b["output_rate"])
    print("8) M92: %s" % _b["m92"])
    print("-" * 62)
    print("七段留痕: %s" % _res["segments"])
    print("人推计数: %s" % _res["human_push"])
    print("任务面: %s" % _res["task_face"])
    print("-" * 62)
    print("注: %s" % _res["note"])

    if _a.json:
        with open(_a.json, "w", encoding="utf-8") as _f:
            json.dump(_res, _f, ensure_ascii=False, indent=2)
        print("已写出: %s" % _a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
