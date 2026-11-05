# -*- coding: utf-8 -*-
"""★454刀7：从 KAL 冷索引 parquet 提炼高信任证据卡片（20 条）。

数据源口径（★与任务书不一致，见交付报告 §0）
------------------------------------------------
任务书说「知识图谱 568 节点、trust≥60 仅 50 条」，那是**活体 L1 node_pool
内存快照**（烛微 454 前置 §529 / 455 前置 §448 分别记为 568/50 与 579/61）。
离线脚本访问不到活体内存，实际可读的是 ``data/knowledge/cold/*.parquet``
**冷化索引**：实测 5536 行、trust≥60 有 2503 条，且几乎全是 L1 代码/自我理解
笔记（烛微 §C4 说的偏科）。

所以本脚本做三件事来避免「拿垃圾凑 20 条」：
1. 先按 trust≥60 + quality_flag + 排除 /自我/状态 与 /情绪 过滤；
2. 再按「可支撑推理动作」打分：`clean` 标记、abstraction、importance、
   是否含明显代码模板（降权）；
3. 给每条标 `domain`（self_code / design_doc / general）与 `dim`，
   明确提醒通用推理不要拿 KAL 卡当万能证据。

用法：
    python tools/extract_kal_evidence.py                 # 落盘 20 条
    python tools/extract_kal_evidence.py --dry-run       # 只打印统计
    python tools/extract_kal_evidence.py --limit 30      # 改条数
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

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLD_GLOB = os.path.join(ROOT, "data", "knowledge", "cold", "*.parquet")
OUT = os.path.join(ROOT, "data", "eval", "kal_seed_evidence.json")

_CODE_MARKS = ("```", "关键步骤", "依赖的外部数据", "参数:", "代码片段",
               "def ", "self.", "功能:")
_STATE_PATHS = ("/自我/状态", "/情绪")
_SPACE_DOMAIN = (("设计文档", "design_doc"), ("架构", "self_code"),
                 ("代码", "self_code"), ("自我理解", "self_code"))
_DIM_HINTS = (
    ("证据", "D3_evidence"), ("验证", "D3_evidence"), ("阈值", "D6_critical"),
    ("告警", "D6_critical"), ("降级", "D8_creative"), ("计划", "D1_multi_step"),
    ("步骤", "D1_multi_step"), ("推理", "D7_abstract"), ("抽象", "D7_abstract"),
    ("监控", "D4_long_task"), ("心跳", "D4_long_task"), ("反思", "D5_metacognition"),
)


def _clean_value(v: Any, limit: int = 80) -> str:
    """value 摘要到 80 字：去围栏、去换行、单行化、不臆造结论。"""
    t = str(v or "")
    t = re.sub(r"```[a-zA-Z]*", "", t)
    t = re.sub(r"[\r\n\t]+", " ", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = t.strip().strip('"').strip()
    return t[:limit]


def _quality(row: dict) -> float:
    """可支撑推理动作的启发式打分（纯排序用，不写回节点）。"""
    v = str(row.get("value") or "")
    q = float(row.get("trust_score") or 0) / 100.0
    if str(row.get("quality_flag") or "") == "clean":
        q += 0.35
    if isinstance(row.get("abstraction"), (int, float)):
        q += float(row["abstraction"]) * 0.25
    if str(row.get("importance") or "") in ("S", "A"):
        q += 0.15
    if any(m in v for m in _CODE_MARKS):
        q -= 0.45          # 纯代码片段笔记不适合直接当推理证据
    return round(q, 4)


def _domain(row: dict) -> str:
    p = str(row.get("space_path") or "")
    for k, d in _SPACE_DOMAIN:
        if k in p:
            return d
    return "general"


def _dim(row: dict) -> str:
    blob = " ".join([str(row.get("value") or "")[:200],
                     " ".join(str(x) for x in (row.get("keywords") or [])[:8]),
                     str(row.get("space_path") or "")])
    for k, d in _DIM_HINTS:
        if k in blob:
            return d
    return "D2_cot_trace"


def load_nodes() -> tuple[list[dict], dict]:
    """读全部冷化 parquet；pyarrow 不可用 ⇒ 明确报错，绝不编造节点。"""
    import pyarrow.parquet as pq

    rows: list[dict] = []
    files = sorted(glob.glob(COLD_GLOB))
    for p in files:
        rows.extend(pq.read_table(p).to_pylist())
    meta = {"files": [os.path.basename(p) for p in files], "total": len(rows)}
    return rows, meta


def select(rows: list[dict], limit: int = 20) -> tuple[list[dict], dict]:
    hi = [r for r in rows
          if isinstance(r.get("trust_score"), (int, float))
          and float(r["trust_score"]) >= 60.0]
    ok_flag = [r for r in hi
               if str(r.get("quality_flag") or "clean").lower() in ("clean", "ok", "normal", "none")]
    clean = [r for r in ok_flag
             if not any(x in str(r.get("space_path") or "") for x in _STATE_PATHS)]
    clean.sort(key=_quality, reverse=True)
    picked = []
    seen = set()
    for r in clean:
        text = _clean_value(r.get("value"), 80)
        if len(text) < 20:
            continue                      # 太短撑不起一个推理动作
        key = text[:24]
        if key in seen:
            continue
        seen.add(key)
        picked.append(r)
        if len(picked) >= limit:
            break
    return picked, {"trust_ge_60": len(hi), "quality_ok": len(ok_flag),
                    "after_path_filter": len(clean), "picked": len(picked)}


def cards(rows: list[dict]) -> list[dict]:
    now = time.time()
    out = []
    for i, r in enumerate(rows, 1):
        text = _clean_value(r.get("value"), 80)
        kws = [str(k) for k in (r.get("keywords") or [])[:3] if k]
        q = "关于" + "与".join(kws) + "的判断依据" if kws else "高信任知识节点能支撑什么推理"
        dom = _domain(r)
        out.append({
            "id": f"kal{i:02d}",
            "question_pattern": q,
            "method": "先核对节点来源与信任度，再把它当作可引用的背景依据",
            "outcome": text,
            "caveat": ("该节点来自框架自我理解/代码域，仅适用于代码与架构推理，"
                       "不要当通用领域事实" if dom == "self_code"
                       else "冷化索引快照，需核对时效后再引用"),
            "confidence": round(min(0.9, float(r.get("trust_score") or 60) / 100.0), 3),
            "source": "kal",
            "node_id": str(r.get("node_id") or ""),
            "domain": dom,
            "dim": _dim(r),
            "space_path": str(r.get("space_path") or ""),
            "reward": 0.8,
            "timestamp": now,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="454 刀7 KAL 证据提炼")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    try:
        rows, meta = load_nodes()
    except Exception as e:
        print("FAIL 读冷索引失败（不编造节点）:", type(e).__name__, e, file=sys.stderr)
        return 2
    picked, stats = select(rows, args.limit)
    print(json.dumps({"source": meta, "filters": stats}, ensure_ascii=False))
    if args.dry_run:
        return 0
    items = cards(picked)
    d = {"version": "454.v1", "created": time.strftime("%Y-%m-%d %H:%M:%S"),
         "purpose": "KAL 高信任节点证据卡片（冷化索引，标注 domain 偏科）",
         "count": len(items), "source_meta": meta, "filters": stats,
         "items": items}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print("OK", args.out, "count", len(items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
