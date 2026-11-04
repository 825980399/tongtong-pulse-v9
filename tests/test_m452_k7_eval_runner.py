# -*- coding: utf-8 -*-
"""452 刀7：eval 跑分器。"""
import importlib.util
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def runner():
    p = os.path.join(_ROOT, "tools", "eval_runner.py")
    spec = importlib.util.spec_from_file_location("m452_eval_runner", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def qset(runner):
    return runner.load_set(os.path.join(_ROOT, "data", "eval", "eval_set_v1.json"))


def test_评测集能读(runner, qset):
    meta, qs = qset
    assert meta.get("version"), meta
    assert len(qs) >= 50, len(qs)     # ★票面说 50，实测 52
    assert all("must_contain" in q and q["must_contain"] for q in qs)


def test_单题三档评分(runner):
    q = {"id": "t1", "dim": "D1_multi_step", "must_contain": ["甲", "乙", "丙", "丁"]}
    assert runner.score_one(q, "甲乙丙丁")["score"] == 1.0
    assert runner.score_one(q, "甲乙")["score"] == 0.5
    assert runner.score_one(q, "甲")["score"] == 0.0
    assert runner.score_one(q, "")["score"] == 0.0


def test_空答案跑通全部题目(runner, qset):
    _, qs = qset
    r = runner.run(qs, {})
    assert len(r["rows"]) == len(qs), len(r["rows"])
    assert r["total"] == 0.0, r["total"]
    assert r["max"] == float(len(qs)), r["max"]


def test_满分答案与维度均分(runner, qset):
    _, qs = qset
    ans = {str(q["id"]): " ".join(list(q.get("must_contain") or [])) for q in qs}
    r = runner.run(qs, ans)
    assert r["total"] == float(len(qs)), r["total"]
    assert r["pct"] == 100.0, r["pct"]
    assert all(v["avg"] == 1.0 for v in r["by_dimension"].values()), r["by_dimension"]


def test_对比模式胜平负(runner, qset):
    _, qs = qset
    before = runner.run(qs, {})
    ans = {str(q["id"]): " ".join(list(q.get("must_contain") or [])) for q in qs}
    after = runner.run(qs, ans)
    c = runner.compare(before, after)
    assert c["win"] == len(qs) and c["loss"] == 0 and c["tie"] == 0, c
    assert c["delta"] == float(len(qs)), c


def test_答案文件两种形状(runner, tmp_path):
    p = str(tmp_path / "a.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump([{"id": "q1", "answer": "甲"}, {"id": "q2", "response": "乙"}], f)
    assert runner.load_answers(p) == {"q1": "甲", "q2": "乙"}


def test_表格输出可读(runner, qset, capsys):
    _, qs = qset
    r = runner.run(qs, {})
    txt = runner._table(r, {"version": "v1.0", "dimensions": {}})
    assert "总分" in txt and "各维度平均分" in txt and "D1-01" in txt, txt[:200]
