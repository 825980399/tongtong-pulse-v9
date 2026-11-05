# -*- coding: utf-8 -*-
"""454 刀2：本地算子 compare（证据不足不瞎编）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.reasoning import local_ops as LO

EV = [
    {"id": "E1", "text": "方案A 性能为 90，复杂度为 3"},
    {"id": "E2", "text": "方案B 性能为 90，复杂度为 7"},
]


def test_两组数据逐维对照正确():
    r = LO.compare(["方案A", "方案B"], ["性能", "复杂度"], EV)
    assert r["status"] == "ok", r
    assert any(x["dimension"] == "性能" for x in r["similarities"]), r
    assert any(x["dimension"] == "复杂度" for x in r["differences"]), r
    assert r["evidence_ids"] == ["E1", "E2"]
    assert 0.0 < r["confidence"] <= 1.0


def test_空证据显式标记不编造():
    r = LO.compare(["A", "B"], ["性能"], [])
    assert r["status"] == "insufficient_evidence"
    assert r["similarities"] == [] and r["differences"] == []
    assert r["confidence"] == 0.0


def test_对象数不足或维度为空都是坏输入():
    assert LO.compare(["A"], ["性能"], EV)["status"] == "insufficient_evidence"
    assert LO.compare(["A", "B"], [], EV)["status"] == "insufficient_evidence"


def test_某对象没有证据也不编():
    r = LO.compare(["方案A", "方案Z"], ["性能"], EV)
    assert r["status"] == "insufficient_evidence"
    assert r["reason"] == "operand_without_evidence"


def test_缺维度素材时列unknown而不猜():
    ev = [{"id": "E1", "text": "方案A 涉及性能与成本"},
          {"id": "E2", "text": "方案B 涉及性能与成本"}]
    r = LO.compare(["方案A", "方案B"], ["性能", "可维护性"], ev)
    assert r["status"] == "ok"
    assert "可维护性" in r["unknown_dimensions"]


def test_文本型相同与不同也能判():
    ev = [{"id": "E1", "text": "甲 采用同步写入"},
          {"id": "E2", "text": "乙 采用异步写入"}]
    r = LO.compare(["甲", "乙"], ["写入"], ev)
    assert r["status"] == "ok"
    assert r["differences"] or r["similarities"]


def test_返回值字段契约齐全():
    r = LO.compare(["方案A", "方案B"], ["性能"], EV)
    for k in ("status", "similarities", "differences", "evidence_ids", "confidence"):
        assert k in r, k
