# -*- coding: utf-8 -*-
"""454 刀6：人工种子 30 条 + 真正接入注入候选（不数文件条数）。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus import seed_evidence as SE
from nucleus.evidence_source import ADMITTED_SOURCES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(ROOT, "data", "eval", "human_seed_evidence.json")
LUNG = os.path.join(ROOT, "organs", "body", "PulseLung.py")


def _items():
    with open(SEED, encoding="utf-8") as f:
        return json.load(f)


def test_30条落盘():
    d = _items()
    assert d["count"] == 30 and len(d["items"]) == 30


def test_覆盖8维度且D1_D3_D6优先():
    dims = {}
    for x in _items()["items"]:
        dims[x["dim"]] = dims.get(x["dim"], 0) + 1
    assert len(dims) == 8, dims
    for k in ("D1_multi_step", "D3_evidence", "D6_critical"):
        assert dims[k] >= 5, dims


def test_每条格式齐全且不是万能答案():
    for x in _items()["items"]:
        for k in ("question_pattern", "method", "outcome", "caveat",
                  "confidence", "source", "reward", "timestamp", "dim"):
            assert k in x and x[k] not in (None, ""), (x["id"], k)
        assert x["source"] == "human_seed"
        assert x["reward"] == 1.0
        assert len(x["caveat"]) >= 10, x["id"]      # 写清「哪里容易错」


def test_问题模式互不相同不会被去重误杀():
    qs = [x["question_pattern"] for x in _items()["items"]]
    assert len(set(qs)) == 30


def test_白名单已含human_seed():
    # 453R 已把 human_seed/kal 登记，454 不必重复登记，但必须确认
    assert "human_seed" in ADMITTED_SOURCES and "kal" in ADMITTED_SOURCES


def test_候选同一入口统计达标_不是数文件条数():
    s = SE.candidate_stats(score_mode="pool")
    assert s["available"] is True
    assert s["total_rows"] == 50, s
    assert s["raw_admitted"] >= 15, s           # ★任务书：从 2 涨到 ≥15
    assert s["by_source"].get("human_seed") == 30, s
    assert s["injectable"] >= 15, s             # 真的能进 build_injection


def test_query模式是线上单次请求口径():
    s = SE.candidate_stats("对比 A 和 B 的性能差异", score_mode="query")
    assert s["available"] is True and s["raw_admitted"] <= 50
    assert s["score_mode"] == "query"


def test_候选与经验池同形状():
    rows = SE.candidates("对比 A 和 B", limit=5)
    assert rows
    for r in rows[:3]:
        for k in ("source", "source_class", "text", "inject_text",
                  "timestamp", "result_reward", "quality_weight", "score"):
            assert k in r, k
        # ★不检查字面「用户原文」：种子正文里这个词可能作为说明出现，
        #   真正要守的是「不为空、是结论而不是问题复述」。
        assert len(r["inject_text"]) >= 10, r["id"]
        assert r["inject_text"] != r["question_pattern"]


def test_开关关闭时候选为空(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M454_SEED_EVIDENCE", False, raising=False)
    assert SE.candidates("x") == []
    monkeypatch.setattr(config, "ENABLE_M454_SEED_EVIDENCE", True, raising=False)


def test_注入入口确实合并种子():
    with open(LUNG, encoding="utf-8") as f:
        src = f.read()
    assert "from nucleus import seed_evidence as _se454" in src
    assert "_block453 = list(_block453) + _seed454" in src
    assert "_inj453 = _ei453.build_injection(_block453)" in src
