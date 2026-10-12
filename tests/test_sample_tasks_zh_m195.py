# -*- coding: utf-8 -*-
"""195刀1 隔离单测：sample_tasks_zh.json 题集扩充验收。

验收：题量 20-50 覆盖三 category 均衡；字段 schema 完整；grading 可机检；id 唯一。
"""
import json
import os

_PATH = os.path.join(os.path.dirname(__file__), "..", "tools", "benchmark", "sample_tasks_zh.json")


def _load():
    with open(_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def test_load_and_count():
    data = _load()
    assert 20 <= len(data) <= 50, "题量 %d 不在 20-50" % len(data)


def test_three_categories_balanced():
    data = _load()
    cats = {}
    for d in data:
        cats[d["category"]] = cats.get(d["category"], 0) + 1
    assert set(cats) == {"code", "reasoning", "knowledge"}, cats
    assert min(cats.values()) >= 10, "category 分布不均衡: %s" % cats


def test_schema_complete():
    data = _load()
    for d in data:
        for k in ("id", "category", "prompt", "grading"):
            assert k in d, "缺字段 %s: %s" % (k, d.get("id"))
        assert d["grading"].get("method") in ("contains", "exact_match", "rule"), \
            "grading.method 非法: %s" % d["id"]


def test_ids_unique():
    data = _load()
    ids = [d["id"] for d in data]
    assert len(ids) == len(set(ids)), "id 重复"
