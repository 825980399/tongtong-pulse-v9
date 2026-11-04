# -*- coding: utf-8 -*-
"""452 刀1：检索内容 dry-run（只记录不注入）。"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus import evidence_trace as ET
from nucleus import shadow_v2_probe as SVP


@pytest.fixture()
def trace_path(tmp_path, monkeypatch):
    p = str(tmp_path / "evidence_trace.jsonl")
    monkeypatch.setattr(config, "M452_EVIDENCE_TRACE_PATH", p, raising=False)
    monkeypatch.setattr(config, "ENABLE_M452_EVIDENCE_TRACE", True, raising=False)
    return p


def _hits():
    return [{"text": "A 正文", "source": "organs/a.py", "score": 0.91},
            {"text": "B 正文", "source": "nucleus/b.py", "_score": 0.75},
            {"text": "C 低分", "source": "junk", "score": 0.2}]


def test_build_evidence_block_返回正文来源分数(trace_path):
    b = ET.build_evidence_block(lambda q, k: _hits(), "审批桥", top_k=3, min_score=0.6)
    assert len(b) == 2, b
    assert b[0]["text"] == "A 正文" and b[0]["source"] == "organs/a.py", b
    assert b[1]["score"] == 0.75, b      # `_score` 兼容真实检索器


def test_build_evidence_block_低分被过滤(trace_path):
    b = ET.build_evidence_block(lambda q, k: _hits(), "x", top_k=3, min_score=0.9)
    assert len(b) == 1 and b[0]["score"] == 0.91, b


def test_retriever为None返回空列表(trace_path):
    assert ET.build_evidence_block(None, "x") == []


def test_retriever异常不抛(trace_path):
    def _boom(q, k):
        raise RuntimeError("down")
    assert ET.build_evidence_block(_boom, "x") == []


def test_note_hits每行一条且带kept标记(trace_path):
    n = ET.note_hits("审批桥为什么批不出来", _hits(), top_k=3, min_score=0.6)
    assert n == 3, n
    with open(trace_path, encoding="utf-8") as f:
        rows = [json.loads(x) for x in f if x.strip()]
    assert len(rows) == 3, rows
    assert all(r["kind"] == "evidence_dry_run" for r in rows)
    assert [r["kept"] for r in rows] == [True, True, False], rows
    assert rows[0]["query"] == "审批桥为什么批不出来"


def test_开关关掉不写(trace_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M452_EVIDENCE_TRACE", False, raising=False)
    assert ET.build_evidence_block(lambda q, k: _hits(), "x") == []
    assert ET.note_hits("x", _hits()) == 0


def test_测试进程不许写默认生产路径(monkeypatch):
    monkeypatch.setattr(config, "M452_EVIDENCE_TRACE_PATH",
                        ET.DEFAULT_TRACE_REL, raising=False)
    assert ET.note_hits("x", _hits()) == 0


def test_影子探针回调只调用一次且不改返回(trace_path):
    seen = []
    frag = SVP.build_shadow_v2_fragment(lambda q, k: _hits(), "审批桥", top_k=3,
                                        evidence_sink=lambda q, h: seen.append((q, len(h))))
    assert seen == [("审批桥", 3)], seen
    assert "retriever=available" in frag, frag
    assert "条数=3" in frag, frag


def test_影子探针无回调时逐字回到旧行为(trace_path):
    frag = SVP.build_shadow_v2_fragment(None, "x")
    assert "retriever=unavailable" in frag, frag
