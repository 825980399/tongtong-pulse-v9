# -*- coding: utf-8 -*-
"""426 刀5：对话中间过程落盘（append-only jsonl / 有界 / 可关）。

basetemp：`--basetemp=tmp/m426_bt_d5`（本批每文件独立 basetemp，铁律 58）。
★落盘根一律用 `M426_TRACE_ROOT` 指向 tmp（铁律 38：第一件事就是改路径）。
"""

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
from nucleus import dialogue_process_trace as tr


@pytest.fixture()
def trace_root(tmp_path, monkeypatch):
    _d = tmp_path / "trace_root"
    _d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("M426_TRACE_ROOT", str(_d))
    monkeypatch.setattr(config, "ENABLE_M426_DIALOGUE_TRACE", True, raising=False)
    tr._pending.clear()
    yield _d
    tr._pending.clear()


def _lines(root):
    p = tr.out_path()
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def test_10_one_record_per_dialogue(trace_root):
    """一次对话 ⇒ 一行 JSON，关键中间结果齐全（不是"打了大概"）。"""
    tr.begin("c1", "曈曈 经验池里有没有关于语义缓存的记录？", "路灯")
    tr.stamp("c1", intent="知识", intent_type="知识", intent_confidence=0.83,
             keywords=["语义缓存", "经验池"], knowledge_paths=["/知识/系统"],
             suggested_method="knowledge_retrieve", emotion="好奇",
             interest_changes=["语义缓存(Δ0.12)"],
             experience_hits=["exp_1|用户访客提问…"],
             channel_candidates=["zhipu-47-flash", "deepseek"],
             channel_reason="活跃渠道 priority 升序")
    tr.finish("c1", method="llm_channel", answer_preview="命中率 62.5%")

    _rows = _lines(trace_root)
    assert len(_rows) == 1, _rows
    _r = _rows[0]
    assert _r["correlation_id"] == "c1"
    assert _r["stage"] == "finished"
    assert _r["intent"] == "知识"
    assert _r["intent_confidence"] == pytest.approx(0.83)
    assert _r["knowledge_paths"] == ["/知识/系统"]
    assert _r["emotion"] == "好奇"
    assert _r["interest_changes"] == ["语义缓存(Δ0.12)"]
    assert _r["experience_hits"] == ["exp_1|用户访客提问…"]
    assert _r["channel_candidates"] == ["zhipu-47-flash", "deepseek"]
    assert _r["method"] == "llm_channel"
    assert _r["answer_preview"] == "命中率 62.5%"
    assert isinstance(_r["latency_ms"], float) and _r["latency_ms"] >= 0


def test_20_begin_is_idempotent_and_does_not_wipe_fields(trace_root):
    """同一 correlation_id 二次 begin 不许抹掉先前 stamp（真实链路里会被调两次）。"""
    tr.begin("c2", "问题原文", "路灯")
    tr.stamp("c2", intent_type="知识", intent_confidence=0.9)
    tr.begin("c2", "问题原文", "路灯")          # 路由阶段会再 begin 一次
    tr.stamp("c2", emotion="平静")
    tr.finish("c2", method="rule_reason")

    _rows = _lines(trace_root)
    assert len(_rows) == 1
    assert _rows[0]["intent_type"] == "知识", "第二次 begin 把先前字段抹了"
    assert _rows[0]["intent_confidence"] == pytest.approx(0.9)
    assert _rows[0]["emotion"] == "平静"


def test_30_pending_is_bounded(trace_root):
    """pending 有界：灌 260 条也不许无限涨，溢出的以 dropped_overflow 收口。"""
    for _i in range(260):
        tr.begin(f"c{_i:03d}", f"问题 {_i}")
    assert len(tr._pending) <= tr._MAX_PENDING, len(tr._pending)
    _rows = _lines(trace_root)
    assert _rows, "溢出收口一条都没写"
    assert all(r["stage"] == "dropped_overflow" for r in _rows), {r["stage"] for r in _rows}


def test_40_switch_off_writes_nothing(trace_root, monkeypatch):
    """开关关闭 ⇒ 完全静默（不建文件、不占内存）。"""
    monkeypatch.setattr(config, "ENABLE_M426_DIALOGUE_TRACE", False, raising=False)
    tr.begin("c3", "问题")
    tr.stamp("c3", intent="知识")
    assert tr.finish("c3", method="x") is None
    assert tr._pending == {}
    assert _lines(trace_root) == []


def test_50_field_bounds_hold(trace_root):
    """存判据不存素材：超长文本/超长列表必须被截断。"""
    tr.begin("c4", "问" * 500, "路灯")
    tr.stamp("c4", keywords=[f"k{i}" for i in range(50)],
             knowledge_paths=[f"/p{i}" for i in range(30)])
    tr.finish("c4", answer_preview="答" * 500)

    _r = _lines(trace_root)[0]
    assert len(_r["user_question"]) <= tr._MAX_TEXT
    assert len(_r["answer_preview"]) <= 120
    assert len(_r["keywords"]) <= tr._MAX_LIST
    assert len(_r["knowledge_paths"]) <= tr._MAX_LIST


def test_60_atomic_line_format(trace_root):
    """append-only：两次对话两行，行内是完整 JSON（可被 jq/grep 直接消费）。"""
    for _i in (1, 2):
        tr.begin(f"z{_i}", f"问题{_i}")
        tr.finish(f"z{_i}", method="m")
    _p = tr.out_path()
    _raw = Path(_p).read_text(encoding="utf-8")
    assert _raw.count("\n") == 2, repr(_raw[-80:])
    assert _raw.endswith("\n")
    for _line in _raw.strip().split("\n"):
        assert json.loads(_line)["correlation_id"].startswith("z")
