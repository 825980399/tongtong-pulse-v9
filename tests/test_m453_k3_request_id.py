# -*- coding: utf-8 -*-
"""453 刀3：request_id 去重（一次请求 6 行不能算 6 个问题）。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus import evidence_injection as EI
from nucleus import evidence_trace as ET

_LUNG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "organs", "body", "PulseLung.py")


def _hits():
    return [{"text": "A", "score": 0.91}, {"text": "B", "_score": 0.72},
            {"text": "C", "score": 0.66}]


def test_new_request_id是12位hex且两次不同():
    a, b = EI.new_request_id(), EI.new_request_id()
    assert len(a) == 12 and all(c in "0123456789abcdef" for c in a)
    assert a != b


def test_稳定派生同一问题必然同id():
    assert ET.stable_request_id("审批桥为什么批不出来") == ET.stable_request_id("审批桥为什么批不出来")
    assert EI.request_id_for("q") == ET.stable_request_id("q")


def test_不同问题id不同():
    assert ET.stable_request_id("问题甲") != ET.stable_request_id("问题乙")


def test_同请求多行共享同一个request_id(tmp_path, monkeypatch):
    p = str(tmp_path / "t.jsonl")
    monkeypatch.setattr(config, "M452_EVIDENCE_TRACE_PATH", p, raising=False)
    monkeypatch.setattr(config, "ENABLE_M452_EVIDENCE_TRACE", True, raising=False)
    rid = EI.new_request_id()
    # 模拟 3 渠道 × 2 轮 = 6 次 note_hits
    for _ in range(6):
        ET.note_hits("同一个问题", _hits(), request_id=rid)
    with open(p, encoding="utf-8") as f:
        rows = [json.loads(x) for x in f if x.strip()]
    assert len(rows) == 18                       # 6 次 × 3 条命中
    assert {r["request_id"] for r in rows} == {rid}
    s = ET.trace_stats(p)
    assert s["distinct_requests"] == 1, s          # ★不是 18
    assert s["eligible_requests"] == 1, s


def test_未透传时按query稳定派生也能去重(tmp_path, monkeypatch):
    p = str(tmp_path / "t.jsonl")
    monkeypatch.setattr(config, "M452_EVIDENCE_TRACE_PATH", p, raising=False)
    monkeypatch.setattr(config, "ENABLE_M452_EVIDENCE_TRACE", True, raising=False)
    for _ in range(3):
        ET.note_hits("同一个问题", _hits()[:1])
    assert ET.trace_stats(p)["distinct_requests"] == 1


def test_不同问题不会被误合并(tmp_path, monkeypatch):
    p = str(tmp_path / "t.jsonl")
    monkeypatch.setattr(config, "M452_EVIDENCE_TRACE_PATH", p, raising=False)
    monkeypatch.setattr(config, "ENABLE_M452_EVIDENCE_TRACE", True, raising=False)
    for q in ("甲", "乙", "丙"):
        ET.note_hits(q, _hits()[:1])
    assert ET.trace_stats(p)["distinct_requests"] == 3


def test_生产影子sink已把request_id透传给note_hits():
    with open(_LUNG, encoding="utf-8") as _f:
        src = _f.read()
    assert "request_id=getattr(self, \"_m453_request_id\", None)" in src


def test_生产入口确实生成request_id():
    with open(_LUNG, encoding="utf-8") as _f:
        src = _f.read()
    assert "self._m453_request_id = _ei453.new_request_id()" in src


def test_影子探针复用入口命中不重复检索():
    with open(_LUNG, encoding="utf-8") as _f:
        src = _f.read()
    assert "_m453_cached = getattr(self, \"_m453_hits\", None)" in src
    assert "lambda _q, _k: _m453_cached" in src
