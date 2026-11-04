# -*- coding: utf-8 -*-
"""452 刀2：trace 扩字段 + 寒暄不占环形缓冲。"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import organs.brain.pulse_inner_world_support as SUP
from nucleus import dialogue_process_trace as DT


@pytest.fixture()
def trace_root(tmp_path, monkeypatch):
    monkeypatch.setenv("M426_TRACE_ROOT", str(tmp_path))
    return str(tmp_path)


def test_新增字段能通过_sanitize白名单(trace_root):
    """★最容易漏的一处：_sanitize 是显式白名单，不登记就会被静默丢掉。"""
    DT.begin("c1", "那你在读取运行日志的时候会关注什么", "访客")
    row = DT.finish("c1", method="rule", answer_preview="会关注",
                    step_count=4, evidence_count=2, is_chitchat=False)
    assert row["step_count"] == 4 and row["evidence_count"] == 2, row
    assert row["is_chitchat"] is False, row


def test_不传新字段时是None_False不是假0(trace_root):
    DT.begin("c2", "真问题", "访客")
    row = DT.finish("c2")
    assert row["step_count"] is None and row["evidence_count"] is None, row
    assert row["is_chitchat"] is False, row


def test_JSON序列化反序列化通过(trace_root):
    DT.begin("c3", "真问题", "访客")
    row = DT.finish("c3", step_count=3, evidence_count=1, is_chitchat=True)
    s = json.dumps(row, ensure_ascii=False)
    back = json.loads(s)
    assert back["step_count"] == 3 and back["is_chitchat"] is True, back


def _organ():
    o = object.__new__(SUP.PulseInnerWorldSupportMixin)
    o._inference_trace = []
    o._max_trace = 50
    o._log = lambda *a, **k: None
    o._attribute_meaning = lambda *a, **k: None
    return o


def test_寒暄不占环形缓冲():
    o = _organ()
    for _ in range(3):
        o._trace_inference("你好呀", "你好", "rule", 0.9, "访客")
    assert o._inference_trace == [], o._inference_trace
    assert o._m452_chitchat_skipped == 3, o._m452_chitchat_skipped


def test_真问题入trace且带新字段():
    o = _organ()
    o._trace_inference("那你在读取运行日志时会关注什么级别？", "会关注 WARNING",
                       "rule", 0.8, "访客")
    assert len(o._inference_trace) == 1
    e = o._inference_trace[0]
    assert e["steps"] == [], e
    assert e["step_count"] == 0 and e["evidence_count"] == 0, e
    assert e["is_chitchat"] is False, e


def test_环形缓冲仍按50上限():
    o = _organ()
    for i in range(60):
        o._trace_inference(f"第{i}个真实推理问题？", "答", "rule", 0.8, "访客")
    assert len(o._inference_trace) == 50, len(o._inference_trace)


def test_chitchat短语表在模块级可用():
    assert "你好" in SUP._M452_CHITCHAT_PHRASES
