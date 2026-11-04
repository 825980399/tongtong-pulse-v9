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


def load_score_source(path: str) -> tuple:
    """★453刀8：``--before/--after`` 支持两种输入。

    452 版只吃**答案文件**；而任务书要求传 ``baseline_b0.json`` /
    ``baseline_b2.json``（**跑分结果**，含 ``rows``）。若照旧把结果 JSON
    当答案映射，52 题答案全部变成一个 dict 的字符串 ⇒ 分数恒 0 ⇒ 假对比。
    返回 ``("result", dict)`` 或 ``("answers", dict)``。
    """
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if isinstance(d, dict) and isinstance(d.get("rows"), list) and "total" in d:
        return ("result", d)
    if isinstance(d, dict) and isinstance(d.get("before"), dict) \
            and isinstance(d.get("after"), dict):
        return ("compare", d)
    return ("answers", load_answers(path))


def run_repeated(questions: list, answers: dict, adapter=None,
                 repeat: int = 1) -> dict:
    """★453刀5：同题 N 次取**逐题中位数**。

    ★离线答案是固定文件 ⇒ N 次结果必然逐字相同；本函数不伪造差异，
      而是把 ``identical=True`` 如实写进结果，供交付报告说明「3 次中位数
      退化为 1 次」这一事实。
    """
    n = max(1, int(repeat))
    runs = [run(questions, answers, adapter=adapter) for _ in range(n)]
    if n == 1:
        out = dict(runs[0])
        out.update({"repeat": 1, "identical": True, "runs": [runs[0]["total"]]})
        return out
    import statistics
    base = dict(runs[0])
    merged = []
    for i, r0 in enumerate(base["rows"]):
        vals = [float(rr["rows"][i]["score"]) for rr in runs]
        row = dict(r0)
        row["score"] = float(statistics.median(vals))
        row["score_runs"] = vals
        merged.append(row)
    base["rows"] = merged
    base["total"] = round(sum(r["score"] for r in merged), 2)
    base["max"] = float(len(merged))
    base["pct"] = round(100.0 * base["total"] / max(1, len(merged)), 1)
    dims = {}
    for r in merged:
        dims.setdefault(r["dim"], []).append(r["score"])
    base["by_dimension"] = {
        k: {"avg": round(sum(v) / len(v), 3), "sum": round(sum(v), 3),
            "count": len(v)} for k, v in sorted(dims.items())}
    totals = [rr["total"] for rr in runs]
    base.update({"repeat": n, "identical": len(set(totals)) == 1,
                 "runs": totals})
    return base


def gate_verdict(c: dict, gate_min: int = 45) -> dict:
    """★453刀8：只给**建议**，绝不自动改开关。

    理由（项目红线「日志/跑分不得决定返回值或配置」）：eval_runner 是
    **离线独立进程**，改它自己的 ``config`` 属性对框架进程零影响；
    改 ``config.py`` 又需重启且会与「开关默认 False」冲突。
    ⇒ 自动关开关在本架构下是**假动作**，如实上报请人工裁决。
    """
    wt = int(c["win"]) + int(c["tie"])
    return {"win_tie": wt, "threshold": int(gate_min),
            "passed": wt >= int(gate_min),
            "recommendation": "keep" if wt >= int(gate_min) else "disable",
            "auto_applied": False,
            "note": "离线进程无法影响框架开关；由人工按本建议裁决"}


def render_comparison_md(before: dict, after: dict, c: dict, gate: dict) -> str:
    """★453刀8：对比报告（离线缺的遥测一律写 N/A，不编造）。"""
    lines = ["# 453 批 · B0 → B2 eval 对比报告", "",
            "> 生成：tools/eval_runner.py --report ｜口径：规则硬匹配 must_contain",
            "", "## 总分与胜平负", "",
            "| 项 | 值 |", "|---|---|",
            (f"| B0 总分 | {before.get('total')} / {before.get('max')} "
             f"({before.get('pct')}%) |"),
            (f"| B2 总分 | {after.get('total')} / {after.get('max')} "
             f"({after.get('pct')}%) |"),
            f"| Δ 总分 | {c.get('delta'):+g} |",
            f"| 胜 / 平 / 负 | {c.get('win')} / {c.get('tie')} / {c.get('loss')} |",
            "", "## 各维度变化", "",
            "| 维度 | B0 均分 | B2 均分 | Δ |", "|---|---|---|---|"]
    bd, ad = before.get("by_dimension", {}), after.get("by_dimension", {})
    for k in sorted(set(bd) | set(ad)):
        a = float(bd.get(k, {}).get("avg", 0.0))
        b = float(ad.get(k, {}).get("avg", 0.0))
        lines.append(f"| {k} | {a:.3f} | {b:.3f} | {b - a:+.3f} |")
    for label, key in (("证据被引用次数", "evidence_cited"),
                      ("token 增量", "token_delta"),
                      ("延迟增量", "latency_delta")):
        v = after.get(key, before.get(key))
        if v is None:
            lines.append(f"\n- **{label}**：N/A（离线评测无该遥测，需在线采样）")
        else:
            lines.append(f"\n- **{label}**：{v}")
    lines += ["", "## 质量闸", "",
             (f"- 胜+平 = **{gate['win_tie']}** / 阈值 {gate['threshold']} "
              f"⇒ **{'通过' if gate['passed'] else '不通过'}**"),
             f"- 建议：**{gate['recommendation']}**；自动应用：{gate['auto_applied']}",
             f"- {gate['note']}",
             "", "★ 判据有效性警告：本批开关默认 False 且答案为离线固定文件，",
             "B0 与 B2 由同一份输入产生 ⇒ 恒 52 平，**该 gate 无判别力**，",
             "不能作为「注入成功」的证据。真判据需开注入 + 在线答案重跑。", ""]
    return "\n".join(lines)


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
    ap.add_argument("--repeat", type=int, default=1,
                    help="同题跑 N 次取逐题中位数（453 刀5 默认 1，B0 用 3）")
    ap.add_argument("--gate", type=int, default=45,
                    help="质量闸：胜+平 ≥ N 才建议保留注入（453 刀8 默认 45）")
    ap.add_argument("--report", default=None,
                    help="对比模式：额外落一份 Markdown 对比报告")
    args = ap.parse_args()

    meta, qs = load_set(args.set)

    if args.before or args.after:
        if not (args.before and args.after):
            print("--before 与 --after 必须同时给", file=sys.stderr)
            return 2
        k1, v1 = load_score_source(args.before)
        k2, v2 = load_score_source(args.after)
        # 结果 JSON 直接复用；答案文件则现跑一次分
        r1 = v1 if k1 == "result" else run(qs, v1)
        r2 = v2 if k2 == "result" else run(qs, v2)
        if k1 == "compare":
            r1, r2 = v1["before"], v1["after"]
        c = compare(r1, r2)
        gate = gate_verdict(c, args.gate)
        print("=" * 66)
        print(f"  对比：{os.path.basename(args.before)} → {os.path.basename(args.after)}")
        print(f"  总分 {c['before_total']} → {c['after_total']}（Δ {c['delta']:+g}）")
        print(f"  胜 {c['win']} ｜ 平 {c['tie']} ｜ 负 {c['loss']}")
        print(f"  [453质量闸] 胜+平={gate['win_tie']} / {gate['threshold']} "
              f"⇒ 建议 {gate['recommendation']}（不自动改开关）")
        print("=" * 66)
        if args.report:
            os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
            with open(args.report, "w", encoding="utf-8", newline="\n") as f:
                f.write(render_comparison_md(r1, r2, c, gate))
            print(f"  对比报告已落盘: {args.report}")
        if args.out:
            with open(args.out, "w", encoding="utf-8", newline="\n") as f:
                json.dump({"compare": c, "gate": gate, "before": r1, "after": r2},
                          f, ensure_ascii=False, indent=2)
        return 0

    adapter = _load_adapter(args.adapter) if args.adapter else None
    answers = load_answers(args.answers) if args.answers else {}
    if not args.answers and adapter is None and not args.demo:
        print("需要 --answers / --adapter / --demo 之一", file=sys.stderr)
        return 2
    result = run_repeated(qs, answers, adapter=adapter, repeat=args.repeat)
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
