# -*- coding: utf-8 -*-
"""490-5 / 490-6 证据注入双闸归因单测（共开关 on/off）。

用 monkeypatch 拦截 append_jsonl，避免测试进程写生产 data/。
"""
import pytest

import config
import nucleus.m490_diag as diag
from nucleus.evidence_injection import build_injection
from nucleus.evidence_trace import build_evidence_block

_SWITCHES = (
    "ENABLE_M490_EVIDENCE_TRACE_STATS",
    "ENABLE_M452_EVIDENCE_TRACE",
    "ENABLE_EVIDENCE_INJECTION",
    "ENABLE_LUNG_EVIDENCE_INJECT",
)


@pytest.fixture
def cfg(monkeypatch):
    _saved = {k: getattr(config, k, None) for k in _SWITCHES}
    yield config
    for k, v in _saved.items():
        if v is None:
            if hasattr(config, k):
                delattr(config, k)
        else:
            setattr(config, k, v)


@pytest.fixture
def captured(monkeypatch, cfg):
    rows = []

    def _cap(prefix, row):
        rows.append((prefix, dict(row)))
        return row

    monkeypatch.setattr(diag, "append_jsonl", _cap)
    diag._pending.set(None)
    yield rows
    diag._pending.set(None)


_HITS = [
    {"text": "a", "score": 0.42},
    {"text": "b", "score": 0.60},
    {"text": "c", "score": 0.70},
    {"text": "d", "score": 0.90},
]


def _request(query="q1", min_score=0.65):
    blk = build_evidence_block(lambda q, k: _HITS, query,
                               top_k=3, min_score=min_score)
    res = build_injection(blk, min_score=min_score)
    return blk, res


def test_4905_4906_on_single_row_with_all_three_fields(cfg, captured):
    """真实调用链：一次请求恰好 1 行，三字段都有数。"""
    cfg.ENABLE_M490_EVIDENCE_TRACE_STATS = True
    cfg.ENABLE_M452_EVIDENCE_TRACE = True
    _blk, _res = _request()
    assert len(captured) == 1
    _prefix, row = captured[0]
    assert _prefix == "evidence_injection"
    assert row["hits_raw"] == len(_HITS)
    assert row["hits_kept_first"] == len(_blk)
    assert isinstance(row["hits_kept_second"], int)
    # 第一道挡掉的分数被记下来（归因盲点修复）
    assert row["blocked_first"] == row["hits_raw"] - row["hits_kept_first"]
    assert row["blocked_scores_first"] == [0.42, 0.6]
    # 5 桶直方图齐全
    assert set(row["score_hist"]) == {"<0.45", "0.45-0.55", "0.55-0.65",
                                     "0.65-0.8", ">=0.8"}
    assert sum(row["score_hist"].values()) == len(_HITS)


def test_4905_4906_off_no_row_and_no_behavior_change(cfg, captured):
    """关开关：不落任何行，且 build_injection 返回值与开启时逐字一致。"""
    cfg.ENABLE_M452_EVIDENCE_TRACE = True
    cfg.ENABLE_M490_EVIDENCE_TRACE_STATS = True
    _blk_on, _res_on = _request()
    _on_rows = [r for _, r in captured]
    assert len(_on_rows) == 1
    # 清空后关开关再跑同一请求
    captured.clear()
    diag._pending.set(None)
    cfg.ENABLE_M490_EVIDENCE_TRACE_STATS = False
    _blk_off, _res_off = _request()
    assert captured == []
    assert _res_off["reason"] == _res_on["reason"]
    assert _res_off["dropped"] == _res_on["dropped"]
    assert _res_off["fragment"] == _res_on["fragment"]
    assert _blk_off == _blk_on


def test_4906_bucket_boundaries(cfg):
    """5 桶边界：<0.45 / 0.45-0.55 / 0.55-0.65 / 0.65-0.8 / >=0.8。"""
    h = diag.score_histogram([0.1, 0.45, 0.55, 0.65, 0.8, 1.2])
    assert h == {"<0.45": 1, "0.45-0.55": 1, "0.55-0.65": 1,
                 "0.65-0.8": 1, ">=0.8": 2}


def test_4906_first_gate_only_request_is_flushed_on_next_request(cfg, captured):
    """某请求只走到第一道闸时，下一次请求会补落一行，不丢数据。"""
    cfg.ENABLE_M490_EVIDENCE_TRACE_STATS = True
    cfg.ENABLE_M452_EVIDENCE_TRACE = True
    build_evidence_block(lambda q, k: _HITS, "only-first", top_k=3, min_score=0.65)
    assert captured == []                      # 暂存在 pending
    _request("second")                          # 新请求会先补落上一行
    assert len(captured) == 2
    assert captured[0][1]["hits_kept_second"] == 0
    assert captured[1][1]["hits_raw"] == len(_HITS)
