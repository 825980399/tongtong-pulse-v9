"""188·刀3 隔离单测：外部基准首考 harness 骨架。

验证点：
- 任务集加载与 schema 校验（合法集可加载；非法 category/method 抛错）；
- 自动评分：exact_match / contains / rule（覆盖度与阈值）三种口径正确；
- run_benchmark 端到端：成绩落盘 JSONL 含依赖度/L2 命中率快照字段；
- snapshot_dependency_and_l2 返回只读快照字典（不抛错）。

隔离：candidate_provider 注入假作答；不依赖生产 PulseLung 运行态。
不改动任何既有测试集合。
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, ".")

import tools.benchmark.external_bench_harness as bh

SAMPLE = [
    {"id": "zh_code_01", "category": "code", "prompt": "x",
     "grading": {"method": "contains", "expected": "def add", "max_score": 1.0}},
    {"id": "zh_reason_01", "category": "reasoning", "prompt": "y",
     "grading": {"method": "rule", "keywords": ["A", "B", "C"], "threshold": 0.5, "max_score": 1.0}},
    {"id": "zh_know_01", "category": "knowledge", "prompt": "z",
     "grading": {"method": "exact_match", "expected": "北京", "max_score": 1.0}},
]


@pytest.fixture
def task_file():
    p = os.path.join(tempfile.gettempdir(), "m188_blade3_tasks.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(SAMPLE, f, ensure_ascii=False)
    return p


def test_load_task_set_valid(task_file):
    tasks = bh.load_task_set(task_file)
    assert len(tasks) == 3
    assert tasks[0].id == "zh_code_01"
    assert tasks[0].category == "code"


def test_load_task_set_invalid_category():
    bad = [{"id": "x", "category": "nope", "prompt": "p"}]
    p = os.path.join(tempfile.gettempdir(), "m188_blade3_bad.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(bad, f)
    with pytest.raises(ValueError):
        bh.load_task_set(p)


def test_load_task_set_invalid_method():
    bad = [{"id": "x", "category": "code", "prompt": "p", "grading": {"method": "magic"}}]
    p = os.path.join(tempfile.gettempdir(), "m188_blade3_bad2.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(bad, f)
    with pytest.raises(ValueError):
        bh.load_task_set(p)


def test_grade_exact_match():
    t = bh.BenchTask(id="1", category="knowledge", prompt="p",
                     grading={"method": "exact_match", "expected": "北京"})
    assert bh.grade_answer(t, "北京")["correct"] is True
    assert bh.grade_answer(t, "上海")["correct"] is False
    assert bh.grade_answer(t, " 北京 ")["correct"] is True  # 归一化


def test_grade_contains():
    t = bh.BenchTask(id="2", category="code", prompt="p",
                     grading={"method": "contains", "expected": "def add"})
    assert bh.grade_answer(t, "here is def add(a,b): pass")["correct"] is True
    assert bh.grade_answer(t, "no function here")["correct"] is False


def test_grade_rule_coverage():
    t = bh.BenchTask(id="3", category="reasoning", prompt="p",
                     grading={"method": "rule", "keywords": ["A", "B", "C"], "threshold": 0.5})
    # 命中 2/3 = 0.667 >= 0.5 → 满分覆盖度
    g = bh.grade_answer(t, "A and B mentioned")
    assert abs(g["coverage"] - 2 / 3) < 1e-3
    assert g["correct"] is True
    # 命中 1/3 = 0.333 < 0.5 → 不达标（但仍有部分分 = 覆盖度）
    g2 = bh.grade_answer(t, "only A")
    assert g2["correct"] is False
    assert abs(g2["coverage"] - 1 / 3) < 1e-3


def test_run_benchmark_end_to_end(task_file):
    out = os.path.join(tempfile.gettempdir(), "m188_blade3_scores.jsonl")

    def provider(t):
        # 假作答：code 给全、reason 给 1 个关键词、know 给正确
        if t.id == "zh_code_01":
            return "def add(a,b): return a+b"
        if t.id == "zh_reason_01":
            return "A only"
        if t.id == "zh_know_01":
            return "北京"
        return ""

    res = bh.run_benchmark(task_file, out, candidate_provider=provider)
    assert res["n_tasks"] == 3
    # code 命中(1) + reason 覆盖度 1/3≈0.3333 + know 命中(1) = 2.3333
    assert res["score_total"] == pytest.approx(2.3333, abs=1e-3)
    # 成绩件含快照字段
    with open(out, encoding="utf-8") as f:
        lines = [json.loads(l) for l in f if l.strip()]
    assert len(lines) == 3
    for rec in lines:
        assert "dependency_ratio" in rec
        assert "l2_hit_rate" in rec
        assert "snapshot_ts" in rec


def test_snapshot_readonly():
    snap = bh.snapshot_dependency_and_l2()
    assert isinstance(snap, dict)
    assert "dependency_ratio" in snap
    assert "l2_hit_rate" in snap
    # 依赖度快照应取到权威常量 0.9694（或回退值），不为 None 即代表成功只读
    assert snap["dependency_ratio"] is not None
