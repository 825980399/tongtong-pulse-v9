# -*- coding: utf-8 -*-
"""454 刀4：本地算子 quantify（缺数值不返回 0 冒充）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.reasoning import local_ops as LO


def test_求和与均值():
    r = LO.quantify("sum", [10, "20个", 30], unit="条")
    assert r["status"] == "ok" and r["value"] == 60.0
    assert r["unit"] == "条" and "sum(" in r["formula"]
    assert LO.quantify("mean", [10, 20, 30])["value"] == 20.0


def test_中文metric别名():
    assert LO.quantify("总和", [1, 2])["value"] == 3.0
    assert LO.quantify("均值", [2, 4])["value"] == 3.0
    assert LO.quantify("比例", [1, 4])["value"] == 0.25
    assert LO.quantify("计数", [1, 2, 3])["value"] == 3.0


def test_从证据文本抽数字():
    r = LO.quantify("sum", [], evidence=[{"id": "E1", "text": "作废 17 条，入队 1 条"}])
    assert r["status"] == "ok" and r["value"] == 18.0
    assert r["evidence_ids"] == ["E1"]


def test_缺数值显式标记():
    r = LO.quantify("sum", ["没有数字", "缺数据"], unit="条")
    assert r["status"] == "insufficient_numbers"
    assert r["value"] is None and r["formula"] is None
    assert r["reason"] == "no_numbers"


def test_零分母不编造():
    r = LO.quantify("ratio", [0, 0])
    assert r["status"] == "insufficient_numbers"
    assert r["reason"] == "zero_denominator"


def test_布尔不当数值():
    r = LO.quantify("sum", [True, False, 5])
    assert r["value"] == 5.0, r["numbers"]


def test_返回值字段契约齐全():
    for r in (LO.quantify("sum", [1, 2]), LO.quantify("sum", [])):
        for k in ("status", "value", "unit", "formula", "evidence_ids", "numbers"):
            assert k in r, k
