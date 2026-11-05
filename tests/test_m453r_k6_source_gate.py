# -*- coding: utf-8 -*-
"""453R 刀6：证据源准入（经验池无 source 字段，类别由现有字段推导）。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus import evidence_injection as EI
from nucleus import evidence_source as ESRC
from nucleus import evidence_trace as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL = os.path.join(ROOT, "data", "experience", "experience_pool.json")


def _pool():
    with open(POOL, encoding="utf-8") as f:
        d = json.load(f)
    ex = d.get("experiences") if isinstance(d, dict) else d
    if isinstance(ex, dict):
        ex = list(ex.values())
    return ex


def test_经验池确实没有source字段():
    # ★T0 偏差硬证据：任务书写「source in [qa,human_seed,kal]」，盘面 0 条有该字段
    rows = _pool()
    assert len(rows) > 300
    assert sum(1 for e in rows if e.get("source")) == 0


def test_白名单与排除表():
    assert ESRC.ADMITTED_SOURCES == ("qa", "human_seed", "kal")
    for c in ("template", "semantic_summary", "self_inspection", "delegated"):
        assert c in ESRC.EXCLUDED_SOURCES


def test_八类分类各有判据():
    cases = [
        ({"is_template": True}, "template"),
        ({"summary": '{"来源": "semantic", "优化建议": "x"}'}, "semantic_summary"),
        ({"summary": "回答用户关于「曈曈状态」的问题"}, "self_inspection"),
        ({"summary": "meta_not_search 委托给肺"}, "delegated"),
        ({"summary": "用户访客提问：审批桥为何卡住？，回答方法：rule"}, "qa"),
        ({"source": "human_seed"}, "human_seed"),
        ({"source": "kal"}, "kal"),
        ({"summary": "动机循环内部评估"}, "event_distill"),
    ]
    for h, want in cases:
        assert ESRC.classify_source(h) == want, (h, ESRC.classify_source(h))


def test_准入只放qa_human_seed_kal():
    assert ESRC.source_admitted({"source": "qa"}) is True
    assert ESRC.source_admitted({"source": "human_seed"}) is True
    assert ESRC.source_admitted({"source": "kal"}) is True
    for c in ("template", "semantic_summary", "self_inspection", "delegated"):
        assert ESRC.source_admitted({"source": c}) is False, c


def test_全池分布与准入条数可审计():
    d = ESRC.describe(path=POOL)
    assert d["total"] >= 380
    assert d["admitted"] < d["total"], d
    assert "template" in d["distribution"]
    assert sum(d["distribution"].values()) == d["total"]


def test_注入层按source过滤且dropped可审计(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M453R_SOURCE_GATE", True, raising=False)
    items = [
        {"text": "可用问答经验", "score": 0.8, "source_class": "qa"},
        {"text": "模板", "score": 0.8, "source_class": "template"},
        {"text": "语义摘要", "score": 0.8, "source_class": "semantic_summary"},
        {"text": "自检问答", "score": 0.8, "source_class": "self_inspection"},
    ]
    r = EI.build_injection(items, now=1000.0)
    assert len(r["items"]) == 1 and r["items"][0]["text"] == "可用问答经验", r
    assert any(k.startswith("source_not_admitted:") for k in r["dropped"]), r["dropped"]


def test_reward低于05被排除():
    items = [{"text": "低分经验", "score": 0.8, "source_class": "qa",
              "result_reward": 0.3},
             {"text": "高分经验", "score": 0.8, "source_class": "qa",
              "result_reward": {"intensity": 0.9}}]
    r = EI.build_injection(items, now=1000.0)
    assert r["dropped"].get("low_reward") == 1, r["dropped"]
    assert len(r["items"]) == 1


def test_dry_run保留全量命中并打标():
    h = {"motivation": "维持系统平衡", "summary": "动机循环内部评估",
         "score": 0.8, "is_template": True}
    b = ET.build_evidence_block(lambda q, k: [h], "x", min_score=0.6)
    assert len(b) == 1                     # ★不删命中（观测完整性）
    assert b[0]["source_class"] == "template"
    assert b[0]["admitted"] is False


def test_关闸后回到453行为(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M453R_SOURCE_GATE", False, raising=False)
    r = EI.build_injection([{"text": "模板也放行", "score": 0.8,
                             "source_class": "template"}], now=1000.0)
    assert r["reason"] == "ok" and len(r["items"]) == 1
