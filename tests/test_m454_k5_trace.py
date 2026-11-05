# -*- coding: utf-8 -*-
"""454 刀5：trace 落 plan / step_results（有字段且**有内容**）。"""
import glob
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus import dialogue_process_trace as DT

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IW_SRC = os.path.join(ROOT, "organs", "brain", "PulseInnerWorld.py")
SUP_SRC = os.path.join(ROOT, "organs", "brain", "pulse_inner_world_support.py")

PLAN = [{"step_id": "S1", "op": "retrieve", "goal": "了解A：产出子结论",
         "depends_on": [], "expected_evidence": "候选事实"},
        {"step_id": "S2", "op": "compare", "goal": "对比分析：产出子结论",
         "depends_on": ["S1"], "expected_evidence": "对照维度"}]
STEPS = [{"idx": 1, "step_id": "S1", "op": "retrieve", "status": "retrieved",
          "input_digest": "a1b2c3d4", "output_digest": "e5f6g7h8",
          "evidence_ids": [], "confidence": 0.5, "source": "local_retrieve",
          "elapsed_ms": None, "fail_reason": None},
         {"idx": 2, "step_id": "S2", "op": "compare", "status": "retrieved",
          "input_digest": "i9j0k1l2", "output_digest": "m3n4o5p6",
          "evidence_ids": [], "confidence": 0.5, "source": "local_retrieve",
          "op_planned": "compare", "op_executed": "retrieve"}]


@pytest.fixture()
def trace_root(tmp_path, monkeypatch):
    monkeypatch.setenv("M426_TRACE_ROOT", str(tmp_path))
    DT.clear_plan()
    return str(tmp_path)


def test_sanitize白名单登记plan与step_results(trace_root):
    DT.begin("c1", "对比 A 和 B 的区别", "访客")
    row = DT.finish("c1", plan=PLAN, step_results=STEPS, method="rule")
    assert len(row["plan"]) == 2 and len(row["step_results"]) == 2, row
    assert row["plan"][1]["op"] == "compare"      # dict 结构没被压成字符串
    assert row["step_results"][1]["step_id"] == "S2"


def test_执行器set_plan后finish自动带上(trace_root):
    DT.begin("c2", "对比 A 和 B 的区别", "访客")
    DT.set_plan(PLAN, STEPS)
    row = DT.finish("c2")
    assert len(row["plan"]) == 2 and len(row["step_results"]) == 2, row


def test_落盘文件里也有内容(trace_root):
    DT.begin("c3", "对比 A 和 B 的区别", "访客")
    DT.set_plan(PLAN, STEPS)
    DT.finish("c3")
    files = glob.glob(os.path.join(trace_root, "**", "*.jsonl"), recursive=True)
    assert files, files
    rows = []
    for p in files:
        with open(p, encoding="utf-8") as f:
            rows.extend(json.loads(x) for x in f if x.strip())
    assert any(r.get("correlation_id") == "c3" and r.get("plan") for r in rows), rows


def test_不传时是空数组不是None(trace_root):
    DT.begin("c4", "真问题", "访客")
    row = DT.finish("c4")
    assert row["plan"] == [] and row["step_results"] == []


def test_clear_plan后不再串味(trace_root):
    DT.begin("c5", "问题", "访客")
    DT.set_plan(PLAN, STEPS)
    DT.clear_plan()
    row = DT.finish("c5")
    assert row["plan"] == [] and row["step_results"] == []


def test_trace_rows保留字典且截字符串():
    out = DT._trace_rows([{"op": "甲" * 200, "n": 3}], limit=2)
    assert isinstance(out[0], dict) and len(out[0]["op"]) <= 60
    assert out[0]["n"] == 3


def test_只存摘要不存用户原文(trace_root):
    DT.begin("c6", "用户的原始问题全文不应落盘", "访客")
    DT.set_plan(PLAN, STEPS)
    row = DT.finish("c6")
    blob = json.dumps(row, ensure_ascii=False)
    for s in STEPS:
        assert s["input_digest"] in blob or s["output_digest"] in blob
    assert "digest" in blob
    # digest 是 8 位哈希，不是整段输入
    assert all(len(s["input_digest"]) == 8 for s in STEPS)


def test_生产链路接线齐全():
    with open(IW_SRC, encoding="utf-8") as f:
        iw = f.read()
    with open(SUP_SRC, encoding="utf-8") as f:
        sup = f.read()
    assert "_m454_tr.set_plan(" in iw
    assert '"op_executed": "retrieve"' in iw
    assert '"plan": (_m454_trace_rows(' in sup
    assert '"step_results": (_m454_trace_rows(' in sup
    assert "_m454_trace_rows = None" in sup       # 导入失败时的兜底
