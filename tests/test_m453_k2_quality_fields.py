# -*- coding: utf-8 -*-
"""453 刀2：trace 质量字段 + 数据闸 distinct 统计。"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus import evidence_trace as ET


@pytest.fixture()
def trace(tmp_path, monkeypatch):
    p = str(tmp_path / "evidence_trace.jsonl")
    monkeypatch.setattr(config, "M452_EVIDENCE_TRACE_PATH", p, raising=False)
    monkeypatch.setattr(config, "ENABLE_M452_EVIDENCE_TRACE", True, raising=False)
    return p


def _hits():
    return [
        {"text": "审批桥三闸需上游供数", "source": "organs/a.py", "score": 0.91,
         "is_template": False, "result_reward": {"intensity": 0.9},
         "quality_weight": 0.9, "pollution_risk": "low", "activation_count": 7},
        {"motivation": "维持系统平衡", "summary": "动机循环内部评估", "_score": 0.72,
         "is_template": True, "result_reward": {"intensity": 0.36},
         "pollution_risk": "medium", "activation_count": 28676},
    ]


def _rows(p):
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def test_每行都有六个质量字段(trace):
    n = ET.note_hits("审批桥为什么批不出来", _hits(), top_k=3, min_score=0.6)
    assert n == 2
    for r in _rows(trace):
        for k in ("is_template", "result_reward", "quality_weight",
                  "pollution_risk", "activation_count", "exp_timestamp"):
            assert k in r, k


def test_模板类被标记True(trace):
    ET.note_hits("x", _hits())
    rows = _rows(trace)
    assert rows[0]["is_template"] is False
    assert rows[1]["is_template"] is True
    assert rows[1]["text"] == "维持系统平衡。动机循环内部评估"


def test_reward从dict取数值(trace):
    ET.note_hits("x", _hits())
    rows = _rows(trace)
    assert rows[0]["result_reward"] == pytest.approx(0.9)
    assert rows[1]["result_reward"] == pytest.approx(0.36)


def test_质量权重模板被打折(trace):
    ET.note_hits("x", _hits())
    rows = _rows(trace)
    assert rows[0]["quality_weight"] > rows[1]["quality_weight"]


def test_既有kept与query字段不丢(trace):
    ET.note_hits("审批桥为什么批不出来", _hits())
    rows = _rows(trace)
    assert [r["kept"] for r in rows] == [True, True]
    assert all(r["kind"] == "evidence_dry_run" for r in rows)
    assert rows[0]["query"] == "审批桥为什么批不出来"


def test_trace_stats按distinct与eligible口径(trace):
    # 3 个不同请求，每个 2 条 score>=0.65；再加 1 个只有 1 条的请求
    for rid in ("r1", "r2", "r3"):
        ET.note_hits("q-" + rid, _hits(), request_id=rid)
    ET.note_hits("q-r4", _hits()[:1], request_id="r4")
    s = ET.trace_stats(trace, min_score=0.65, min_evidence=2)
    assert s["available"] is True
    assert s["rows"] == 7, s            # 3×2 命中 + r4 的 1 条 = 7 行
    assert s["distinct_requests"] == 4, s
    assert s["eligible_requests"] == 3, s


def test_文件不存在时不抛且available为False(tmp_path):
    s = ET.trace_stats(str(tmp_path / "nope.jsonl"))
    assert s["available"] is False
    assert s["rows"] == 0 and s["distinct_requests"] == 0


def test_坏行跳过且不影响其余计数(tmp_path):
    p = tmp_path / "bad.jsonl"
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write("{not json}\n")
        f.write(json.dumps({"request_id": "r1", "query": "q", "score": 0.9}) + "\n")
        f.write(json.dumps({"request_id": "r1", "query": "q", "score": 0.8}) + "\n")
    s = ET.trace_stats(str(p))
    assert s["rows"] == 2, s
    assert s["distinct_requests"] == 1
    assert s["eligible_requests"] == 1


def test_生产留痕按distinct口径统计不虚高():
    # ★不锁死活体易变值（框架持续写盘）：只守「distinct 不可能超过行数」这一不变量。
    # T0 时文件不存在；框架跑起来后已出现。两种状态都允许，读数由交付报告按时刻给。
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "eval", "evidence_trace.jsonl")
    s = ET.trace_stats(p)
    assert 0 <= s["distinct_requests"] <= s["rows"], s
    assert 0 <= s["eligible_requests"] <= s["distinct_requests"], s
