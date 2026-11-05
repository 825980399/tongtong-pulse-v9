# -*- coding: utf-8 -*-
"""454 刀7：KAL 冷索引提炼 20 条 + 准入不因 node_id 被挡。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus import seed_evidence as SE
from nucleus.evidence_source import classify_source, source_admitted

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KAL = os.path.join(ROOT, "data", "eval", "kal_seed_evidence.json")
TOOL = os.path.join(ROOT, "tools", "extract_kal_evidence.py")


def _items():
    with open(KAL, encoding="utf-8") as f:
        return json.load(f)


def test_20条落盘():
    d = _items()
    assert d["count"] == 20 and len(d["items"]) == 20
    assert d["source_meta"]["total"] >= 5000        # 冷索引真实规模


def test_每条带node_id与domain且value不超80字():
    for x in _items()["items"]:
        assert x["source"] == "kal"
        assert x["node_id"].startswith("node:"), x["id"]
        assert x["domain"] in ("self_code", "design_doc", "general")
        assert len(x["outcome"]) <= 80, x["id"]
        assert "不要当通用领域事实" in x["caveat"] or "核对时效" in x["caveat"]


def test_标注了偏科领域():
    doms = {}
    for x in _items()["items"]:
        doms[x["domain"]] = doms.get(x["domain"], 0) + 1
    assert doms.get("self_code", 0) > 0, doms      # ★烛微 §C4：如实标偏科


def test_筛选口径写在脚本里():
    with open(TOOL, encoding="utf-8") as f:
        src = f.read()
    assert "trust_score" in src and ">= 60" in src
    assert "/自我/状态" in src and "/情绪" in src
    assert "pyarrow" in src


def test_node_id作source仍被判为kal():
    # ★任务书要求「保留 node_id 作为 source」，453R 的分类函数原本会把
    #   "node:xxx" 当显式类别 ⇒ 20 条全被挡。454 改为 source_class 优先。
    row = {"source": "node:abc123", "source_class": "kal",
           "text": "高信任节点结论", "is_template": False}
    assert classify_source(row) == "kal"
    assert source_admitted(row) is True


def test_准入后by_source含kal():
    s = SE.candidate_stats(score_mode="pool")
    assert s["by_source"].get("kal") == 20, s
    # ★456 刀5：human_seed 30→40 ⇒ 准入总数 50→60（kal 20 条不变）。
    #   回滚点：删掉 hs31~hs40 并把本条改回 50。
    assert s["raw_admitted"] == 60, s


def test_KAL候选能进注入段():
    s = SE.candidate_stats(score_mode="pool")
    assert s["injectable"] >= 15
    assert all(k != "source_not_admitted:kal" for k in s["dropped"]), s["dropped"]


def test_冷索引与活体node_pool口径不同_报告如实():
    # 任务书说 568 节点 / 50 条 trust>=60，那是活体 L1 快照；
    # 离线脚本读的是 parquet 冷索引（5536 行 / trust>=60 有 2503 条）。
    d = _items()
    assert d["filters"]["trust_ge_60"] > 100, d["filters"]
    assert d["filters"]["picked"] == 20
