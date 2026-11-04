# -*- coding: utf-8 -*-
"""★452刀7（P0-7）：推理能力 eval 跑分器。

为什么要有它
------------
改推理代码之前没有"尺子"：只能凭一两句对话感觉"好像变好了"。本脚本把
`data/eval/eval_set_v1.json`（星轨出题）按 `must_contain` 硬匹配规则打分，
改前/改后各跑一遍就知道是进步还是退步。

评分口径（与 eval_set_v1.json 的 `scoring.rules` 逐字一致）
----------------------------------------------------------
    命中率 = 命中关键词数 / must_contain 总数
    ≥ 60% → 1 分 ｜ 30%~60% → 0.5 分 ｜ < 30% → 0 分

两种喂法
--------
1. **离线（默认）**：`--answers answers.json`，文件为 `[{"id","answer"}]` 或
   `{"<id>": "<answer>"}`。适合把对话接口产出的答案先落盘、再离线复算。
2. **在线**：`--adapter path/to/module.py`，模块需提供 `answer(question: str) -> str`
   （或 `generate` / `ask`）。脚本用 importlib 动态加载，**不在仓库里写死任何
   框架调用** —— 本批是纯安全刀，不擅自把评测器接进主循环。

对比模式
--------
`--before A.json --after B.json`：逐题比分数，输出 胜/平/负 + 净胜分。

用法
----
    python tools/eval_runner.py --demo
    python tools/eval_runner.py --answers A.json --out data/eval/report_before.json
    python tools/eval_runner.py --before A.json --after B.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_SET = os.path.join(_ROOT, "data", "eval", "eval_set_v1.json")

FULL = 1.0
HALF = 0.5
ZERO = 0.0


def load_set(path: str) -> tuple[dict, list]:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    qs = d.get("questions") if isinstance(d, dict) else d
    if not isinstance(qs, list):
        raise TypeError("评测集里没有 questions 列表")
    return (d if isinstance(d, dict) else {}), qs


def load_answers(path: str) -> dict:
    """接受 `[{id,answer}]` 或 `{id: answer}` 两种形状。"""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if isinstance(d, dict):
        return {str(k): str(v or "") for k, v in d.items()}
    out = {}
    for r in d or []:
        if isinstance(r, dict) and r.get("id"):
            out[str(r["id"])] = str(r.get("answer") or r.get("response") or "")
    return out


def _load_adapter(path: str):
    spec = importlib.util.spec_from_file_location("m452_eval_adapter", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"无法加载 adapter: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("answer", "generate", "ask"):
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    raise ValueError("adapter 必须提供 answer(question) / generate / ask 之一")


def score_one(q: dict, answer: str) -> dict:
    """单题评分。返回 {id, dim, score, hits, total, hit_ratio}。"""
    kws = list(q.get("must_contain") or [])
    text = str(answer or "")
    hits = [k for k in kws if str(k) in text]
    total = len(kws)
    ratio = (len(hits) / total) if total else 0.0
    if ratio >= 0.6:
        s = FULL
    elif ratio >= 0.3:
        s = HALF
    else:
        s = ZERO
    return {"id": str(q.get("id") or ""), "dim": str(q.get("dim") or ""),
            "score": s, "hits": len(hits), "total": total,
            "hit_ratio": round(ratio, 3), "matched": hits[:8]}


def run(questions: list, answers: dict, adapter=None) -> dict:
    rows = []
    for q in questions:
        qid = str(q.get("id") or "")
        if adapter is not None:
            try:
                ans = adapter(str(q.get("question") or ""))
            except Exception as e:
                ans = ""
                print(f"  ! {qid} adapter 异常: {type(e).__name__}: {e}", file=sys.stderr)
        else:
            ans = answers.get(qid, "")
        rows.append(score_one(q, ans))
    dims: dict = {}
    for r in rows:
        d = dims.setdefault(r["dim"], {"sum": 0.0, "n": 0})
        d["sum"] += r["score"]
        d["n"] += 1
    by_dim = {k: {"avg": round(v["sum"] / v["n"], 3), "sum": v["sum"],
                  "count": v["n"]} for k, v in sorted(dims.items())}
    return {"rows": rows, "by_dimension": by_dim,
            "total": round(sum(r["score"] for r in rows), 2),
            "max": float(len(rows)),
            "pct": round(100.0 * sum(r["score"] for r in rows) / max(1, len(rows)), 1)}


def compare(before: dict, after: dict) -> dict:
    a = {r["id"]: r["score"] for r in before["rows"]}
    b = {r["id"]: r["score"] for r in after["rows"]}
    win = tie = loss = 0
    for k in sorted(a):
        d = b.get(k, 0.0) - a[k]
        if d > 0:
            win += 1
        elif d < 0:
            loss += 1
        else:
            tie += 1
    return {"win": win, "tie": tie, "loss": loss,
            "before_total": before["total"], "after_total": after["total"],
            "delta": round(after["total"] - before["total"], 2)}


def _table(result: dict, meta: dict) -> str:
    lines = []
    lines.append("=" * 66)
    lines.append(f"  eval_set {meta.get('version', '?')} ｜ 共 {len(result['rows'])} 题"
                 f" ｜ 总分 {result['total']}/{result['max']} ({result['pct']}%)")
    lines.append("=" * 66)
    lines.append(f"  {'ID':<8} {'维度':<20} {'得分':>5} {'命中':>7} {'命中率':>7}")
    lines.append("-" * 66)
    for r in result["rows"]:
        lines.append(f"  {r['id']:<8} {r['dim']:<20} {r['score']:>5.1f} "
                     f"{str(r['hits']) + '/' + str(r['total']):>7} {r['hit_ratio']:>7.2f}")
    lines.append("-" * 66)
    lines.append("  各维度平均分：")
    for k, v in result["by_dimension"].items():
        label = str(meta.get("dimensions", {}).get(k, ""))
        lines.append(f"    {k:<20} {v['avg']:>6.3f}  ({v['count']} 题) {label}")
    lines.append("=" * 66)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="452 eval 跑分器")
    ap.add_argument("--set", default=_DEFAULT_SET, help="评测集 JSON")
    ap.add_argument("--answers", default=None, help="离线答案文件")
    ap.add_argument("--adapter", default=None, help="在线答案模块（提供 answer(question)）")
    ap.add_argument("--before", default=None, help="对比模式：改前答案文件")
    ap.add_argument("--after", default=None, help="对比模式：改后答案文件")
    ap.add_argument("--demo", action="store_true", help="空答案跑通全部题目（证明脚本可用）")
    ap.add_argument("--out", default=None, help="结果 JSON 落盘路径")
    ap.add_argument("--json", action="store_true", help="只输出 JSON（不打表格）")
    args = ap.parse_args()

    meta, qs = load_set(args.set)

    if args.before or args.after:
        if not (args.before and args.after):
            print("--before 与 --after 必须同时给", file=sys.stderr)
            return 2
        r1 = run(qs, load_answers(args.before))
        r2 = run(qs, load_answers(args.after))
        c = compare(r1, r2)
        print("=" * 66)
        print(f"  对比：{os.path.basename(args.before)} → {os.path.basename(args.after)}")
        print(f"  总分 {c['before_total']} → {c['after_total']}（Δ {c['delta']:+g}）")
        print(f"  胜 {c['win']} ｜ 平 {c['tie']} ｜ 负 {c['loss']}")
        print("=" * 66)
        if args.out:
            with open(args.out, "w", encoding="utf-8", newline="\n") as f:
                json.dump({"compare": c, "before": r1, "after": r2},
                          f, ensure_ascii=False, indent=2)
        return 0

    adapter = _load_adapter(args.adapter) if args.adapter else None
    answers = load_answers(args.answers) if args.answers else {}
    if not args.answers and adapter is None and not args.demo:
        print("需要 --answers / --adapter / --demo 之一", file=sys.stderr)
        return 2
    result = run(qs, answers, adapter=adapter)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_table(result, meta))
        if args.demo:
            print("  ★ --demo：全部空答案，分数应恒为 0；只用于证明 52 题跑得通。")
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
