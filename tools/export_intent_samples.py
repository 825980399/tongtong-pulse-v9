# -*- coding: utf-8 -*-
"""★452刀8-3：从真实对话 trace 导出意图标注样本集。

用途：451 刀1 交付时明确写了「整体意图准确率无标注集」—— 本脚本把
`data/experience/dialogue_process_*.jsonl` 里最近的真实对话抽出来，
导出成 `data/eval/intent_samples.json`，供后续人工标注（本批**不标注**）。

★只导出、不猜标签：`expected_intent` 字段留空，由人填。把 QICA 自己判的
  `intent_type` 当成"正确答案"就是循环论证（铁律 30）—— 所以单独放
  `observed_intent_type` 字段，标明它是**待校验值**而不是基准。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc

_DEFAULT_GLOB = os.path.join(_ROOT, "data", "experience", "dialogue_process_*.jsonl")
_DEFAULT_OUT = os.path.join(_ROOT, "data", "eval", "intent_samples.json")


def collect(pattern: str, limit: int) -> list:
    rows: list = []
    for p in sorted(glob.glob(pattern)):
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError as _je:
                    silent_exc(_je, where="export_intent_samples::collect 坏行",
                               level="debug")
                    continue
                q = str(r.get("user_question") or "").strip()
                if not q or r.get("stage") != "finished":
                    continue
                rows.append({
                    "id": f"dlg_{len(rows) + 1:04d}",
                    "ts": r.get("ts_text"),
                    "question": q[:300],
                    "observed_intent_type": r.get("intent_type"),
                    "observed_intent": r.get("intent"),
                    "observed_confidence": r.get("intent_confidence"),
                    "keywords": list(r.get("keywords") or [])[:10],
                    "answer_preview": str(r.get("answer_preview") or "")[:120],
                    "expected_intent": None,   # ★留空，等人工标注
                    "labeled": False,
                })
    rows.sort(key=lambda x: str(x.get("ts") or ""))
    return rows[-max(1, int(limit)):]


def main() -> int:
    ap = argparse.ArgumentParser(description="452 意图标注样本导出")
    ap.add_argument("--glob", default=_DEFAULT_GLOB)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--out", default=_DEFAULT_OUT)
    ap.add_argument("--target", type=int, default=50, help="目标样本数（仅写进元数据）")
    a = ap.parse_args()
    rows = collect(a.glob, a.limit)
    payload = {
        "version": "intent_samples/452.v1",
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "data/experience/dialogue_process_*.jsonl",
        "target": int(a.target),
        "count": len(rows),
        "note": ("只导出、不标注：expected_intent 留空由人工填；"
                 "observed_* 是 QICA 的待校验输出，不是正确答案（铁律 30）"),
        "samples": rows,
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"导出 {len(rows)} 条 → {a.out}")
    if len(rows) < a.target:
        print(f"  ★注意：目标 {a.target} 条，实际只有 {len(rows)} 条真实对话"
              f"（盘面不足，未编造样本）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
