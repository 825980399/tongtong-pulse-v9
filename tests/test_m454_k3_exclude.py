# -*- coding: utf-8 -*-
"""454 刀3：本地算子 exclude（全排除不静默清空）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.reasoning import local_ops as LO

CANDIDATES = [
    {"text": "审批桥闸门读数齐全", "score": 0.9},
    {"text": "模板占位无数据", "score": 0.2},
    {"text": "审批桥上游供数正常", "score": 0.8},
]


def test_按must与阈值正确筛掉():
    r = LO.exclude(CANDIDATES, {"must": ["审批桥"], "min_score": 0.5})
    assert r["status"] == "ok"
    assert len(r["kept"]) == 2, r
    assert len(r["dropped"]) == 1
    assert "reason" in r["dropped"][0] and "index" in r["dropped"][0]
    assert r["counts"] == {"total": 3, "kept": 2, "dropped": 1}


def test_否定词命中被排除():
    r = LO.exclude([{"text": "这是模板"}, {"text": "这是经验"}],
                   {"negations": ["模板"]})
    assert r["status"] == "ok" and len(r["kept"]) == 1
    assert "negation_hit" in r["dropped"][0]["reason"]


def test_全被排除返回原集合加告警_不静默清空():
    items = [{"text": "甲"}, {"text": "乙"}]
    r = LO.exclude(items, {"negations": ["甲", "乙"]})
    assert r["status"] == "all_excluded", r
    assert r["kept"] == items, "不能返回空集合"
    assert r["warning"] and "复核" in r["warning"]
    assert len(r["dropped"]) == 2


def test_空候选不误报all_excluded():
    r = LO.exclude([], {"must": ["x"]})
    assert r["status"] == "ok" and r["kept"] == [] and r["warning"] is None


def test_criteria可以是纯关键词列表():
    r = LO.exclude(CANDIDATES, ["审批桥"])
    assert len(r["kept"]) == 2


def test_evidence_ids来自传入证据():
    r = LO.exclude(CANDIDATES, {"must": ["审批桥"]},
                   evidence=[{"id": "E1", "text": "审批桥"}])
    assert r["evidence_ids"] == ["E1"]


def test_返回值字段契约齐全():
    r = LO.exclude(CANDIDATES, {"must": ["审批桥"]})
    for k in ("status", "kept", "dropped", "evidence_ids", "warning", "counts"):
        assert k in r, k
