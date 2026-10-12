"""188·刀3 外部基准首考 harness（独立离线 / 影子通道）。

定位：中文本地任务子集的最小闭环骨架。仅为"骨架"——不跑真实跑分、不接网页浏览、
不翻生产开关、不导入生产 PulseLung。真实首考由小林约窗执行。

组成：
  1. 任务集 JSON schema（见 TASK_SCHEMA / BenchTask）；
  2. 自动评分接口 grade_answer：答案匹配（exact_match / contains）+ 规则分（rule，关键词覆盖）；
  3. 成绩落盘件 run_benchmark：逐条结果落盘 JSONL，并内嵌**当日依赖度 + L2 命中率快照**
     （防"跑分即失真"）——快照仅**只读**生产指标，绝不改动依赖度计数或调用 PulseLung；
  4. 独立离线入口：run_benchmark() 函数 + __main__ CLI。

硬约束（烛微§2）：影子通道——依赖度/L2 命中率只读取后嵌入成绩件，不混生产运行态。
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# 轻量静默上报（与全库一致）；缺失时退化为 no-op，保证模块可独立加载。
try:
    from nucleus._silent_except import silent_exc
except Exception:  # pragma: no cover
    def silent_exc(e, where="", level="debug"):  # type: ignore
        return None


# ---------------------------------------------------------------------------
# 任务集 JSON schema（最小闭环骨架；真实首考可在其上扩展字段）
# ---------------------------------------------------------------------------
TASK_SCHEMA = {
    "type": "object",
    "required": ["id", "category", "prompt"],
    "properties": {
        "id": {"type": "string"},
        "category": {
            "type": "string",
            "enum": ["code", "chat", "reasoning", "knowledge"],
        },
        "prompt": {"type": "string"},
        "grading": {
            "type": "object",
            "required": ["method"],
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["exact_match", "contains", "rule"],
                },
                "expected": {"type": "string"},          # exact_match / contains 用
                "keywords": {"type": "array", "items": {"type": "string"}},  # rule 用
                "threshold": {"type": "number"},         # rule 覆盖阈值 0~1
                "max_score": {"type": "number"},         # 默认 1.0
            },
        },
    },
}

_VALID_CATEGORIES = set(TASK_SCHEMA["properties"]["category"]["enum"])
_VALID_METHODS = set(
    TASK_SCHEMA["properties"]["grading"]["properties"]["method"]["enum"]
)


@dataclass
class BenchTask:
    """单条基准任务（对应任务集 JSON 中的一条）。"""

    id: str
    category: str
    prompt: str
    grading: dict[str, Any] = field(default_factory=dict)


def _norm(text: str) -> str:
    """归一化：去首尾空白 + 转小写（中文不受影响，英文不敏感比对）。"""
    return (text or "").strip().lower()


def load_task_set(path: str) -> list[BenchTask]:
    """加载并校验任务集 JSON（list[BenchTask]）。骨架级校验，不引入第三方 schema 依赖。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("任务集 JSON 顶层必须是数组")
    tasks: list[BenchTask] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"第 {i} 条不是对象")
        _id = item.get("id")
        _cat = item.get("category")
        _prompt = item.get("prompt")
        if not _id or not isinstance(_prompt, str):
            raise ValueError(f"第 {i} 条缺少 id 或 prompt")
        if _cat not in _VALID_CATEGORIES:
            raise ValueError(
                f"第 {i} 条 category={_cat!r} 非法，应在 {sorted(_VALID_CATEGORIES)}"
            )
        _g = item.get("grading") or {}
        _m = _g.get("method", "exact_match")
        if _m not in _VALID_METHODS:
            raise ValueError(
                f"第 {i} 条 grading.method={_m!r} 非法，应在 {sorted(_VALID_METHODS)}"
            )
        tasks.append(BenchTask(id=str(_id), category=_cat, prompt=_prompt, grading=_g))
    return tasks


# ---------------------------------------------------------------------------
# 自动评分接口
# ---------------------------------------------------------------------------
def grade_answer(task: BenchTask, candidate: str) -> dict[str, Any]:
    """对单条任务评分（答案匹配 / 规则分）。纯函数，零副作用。

    method:
      - exact_match：归一化后完全相等 → 满分，否则 0；
      - contains：expected 归一化子串命中 candidate → 满分，否则 0；
      - rule：keywords 覆盖率 >= threshold → 满分*覆盖度，correct=覆盖度达阈。
    """
    g = task.grading or {}
    method = g.get("method", "exact_match")
    max_score = float(g.get("max_score", 1.0))
    cand = _norm(candidate)

    if method == "exact_match":
        exp = _norm(g.get("expected", ""))
        ok = cand == exp
        return {
            "score": max_score if ok else 0.0,
            "max_score": max_score,
            "method": method,
            "correct": ok,
        }
    if method == "contains":
        exp = _norm(g.get("expected", ""))
        ok = bool(exp) and (exp in cand)
        return {
            "score": max_score if ok else 0.0,
            "max_score": max_score,
            "method": method,
            "correct": ok,
        }
    if method == "rule":
        kws = g.get("keywords") or []
        if not kws:
            return {
                "score": 0.0,
                "max_score": max_score,
                "method": method,
                "correct": False,
                "detail": "no_keywords",
            }
        hit = sum(1 for k in kws if _norm(k) in cand)
        cov = hit / len(kws)
        thr = float(g.get("threshold", 0.5))
        ok = cov >= thr
        return {
            "score": round(max_score * cov, 4),
            "max_score": max_score,
            "method": method,
            "correct": ok,
            "coverage": round(cov, 4),
        }
    return {
        "score": 0.0,
        "max_score": max_score,
        "method": method,
        "correct": False,
        "detail": "unknown_method",
    }


# ---------------------------------------------------------------------------
# 成绩落盘件 + 只读快照（影子通道）
# ---------------------------------------------------------------------------
def snapshot_dependency_and_l2() -> dict[str, Any]:
    """只读快照当日依赖度与 L2 命中率（防"跑分即失真"）。

    ★硬约束：仅**读取**生产指标，绝不调用生产 PulseLung、绝不改动依赖度计数。
    依赖度优先取运行期权威常量 LLM_DEPENDENCY_BASELINE（0.9694），
    回退到运行期指标文件 data/metrics/llm_dependency.json；
    L2 命中率取自 L2 单例 stats（内存口径；骨架阶段为 0 亦可）。
    """
    dep: float | None = None
    try:
        from nucleus.LLMDependencyMetrics import LLM_DEPENDENCY_BASELINE

        dep = float(LLM_DEPENDENCY_BASELINE)
    except Exception:
        try:
            p = os.path.join("data", "metrics", "llm_dependency.json")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    d = json.load(f)
                dep = d.get("llm_dependency_ratio", d.get("overall_llm_share"))
                dep = float(dep) if dep is not None else None
        except Exception as e:
            silent_exc(e, where="tools.benchmark.external_bench_harness::snapshot_dependency_and_l2[dep]")
            dep = None

    l2: float | None = None
    try:
        from nucleus.llm.semantic_cache import get_semantic_cache_l2

        l2 = get_semantic_cache_l2().stats().get("hit_rate")
    except Exception as e:
        silent_exc(e, where="tools.benchmark.external_bench_harness::snapshot_dependency_and_l2[l2]")
        l2 = None

    return {
        "dependency_ratio": dep,
        "l2_hit_rate": l2,
        "snapshot_ts": time.time(),
    }


def _write_jsonl(path: str, records: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(rec, ensure_ascii=False) + "\n" for rec in records)


def run_benchmark(
    task_set_path: str,
    output_path: str,
    candidate_provider: Callable[[BenchTask], str] | None = None,
) -> dict[str, Any]:
    """执行基准首考闭环（骨架）：加载任务集 → 逐条评分（调用方提供候选答案）→ 落盘。

    candidate_provider: 接收 BenchTask 返回候选答案字符串。骨架默认返回空串
    （不跑真实 LLM）；真实首考时由小林约窗注入离线中文子集的作答逻辑。
    """
    tasks = load_task_set(task_set_path)
    snap = snapshot_dependency_and_l2()
    records: list[dict[str, Any]] = []
    for t in tasks:
        cand = (candidate_provider(t) if candidate_provider else "") or ""
        g = grade_answer(t, cand)
        records.append(
            {
                "task_id": t.id,
                "category": t.category,
                "prompt": t.prompt,
                "candidate": cand,
                "grading": g,
                # ★防失真字段：成绩件内嵌快照，跑分即失真可见
                "dependency_ratio": snap["dependency_ratio"],
                "l2_hit_rate": snap["l2_hit_rate"],
                "snapshot_ts": snap["snapshot_ts"],
            }
        )
    _write_jsonl(output_path, records)
    total = sum(r["grading"]["score"] for r in records)
    max_total = sum(r["grading"]["max_score"] for r in records)
    return {
        "n_tasks": len(tasks),
        "output": output_path,
        "score_total": round(total, 4),
        "score_max": round(max_total, 4),
        "snapshot": snap,
        "records": records,
    }


def _sample_task_set() -> list[dict[str, Any]]:
    """演示用中文本地任务子集（离线、无网页依赖）。"""
    return [
        {
            "id": "zh_code_01",
            "category": "code",
            "prompt": "用 Python 写一个返回两数之和的函数。",
            "grading": {"method": "contains", "expected": "def add", "max_score": 1.0},
        },
        {
            "id": "zh_reason_01",
            "category": "reasoning",
            "prompt": "为什么天空是蓝色的？请解释原理。",
            "grading": {
                "method": "rule",
                "keywords": ["瑞利散射", "波长", "蓝光"],
                "threshold": 0.5,
                "max_score": 1.0,
            },
        },
        {
            "id": "zh_know_01",
            "category": "knowledge",
            "prompt": "中国的首都是哪里？",
            "grading": {"method": "exact_match", "expected": "北京", "max_score": 1.0},
        },
    ]


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    ap = argparse.ArgumentParser(description="188·刀3 外部基准首考 harness（离线骨架）")
    ap.add_argument("--task-set", help="任务集 JSON 路径（缺省写入内置示例）")
    ap.add_argument("--out", required=True, help="成绩落盘 JSONL 路径")
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="用内置示例任务集跑一次骨架自测（不依赖外部作答逻辑）",
    )
    args = ap.parse_args(argv)
    if args.self_test or not args.task_set:
        import tempfile

        tmp = os.path.join(tempfile.gettempdir(), "bench_sample_tasks_zh.json")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_sample_task_set(), f, ensure_ascii=False, indent=2)
        args.task_set = tmp
    res = run_benchmark(args.task_set, args.out)
    print(
        f"[bench] n_tasks={res['n_tasks']} score={res['score_total']}/{res['score_max']} "
        f"dep={res['snapshot']['dependency_ratio']} l2={res['snapshot']['l2_hit_rate']} "
        f"-> {args.out}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
