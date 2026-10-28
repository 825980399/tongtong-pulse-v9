# -*- coding: utf-8 -*-
"""★379 刀2 · 孤儿/死信判据统一（DJ4-04，P1）

票面：pulse_tracer 自报孤儿=0 vs 运行态 716 条死广播，判据冲突。

统一口径（本刀定义，写进分析报告）：
  · **死信** = 发布后 60s 内无任何订阅方收到 delivery；
  · **孤儿** = 发布点本身在 AST 调用图中无上游。
两者**分开计数、不混报** —— 混报正是这次"0 vs 716"的病根。

本件验三件：
  1. 无订阅方发布会登记死信（有订阅方则不登记）；
  2. 死信按事件名分桶、带 sources，可与 EventBus 零匹配计数对账；
  3. `orphan_dead_report()` 两侧读数并列，且**不提供**合并总数（防再次混报）。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.events.EventBus import EventBus  # noqa: E402
from utils import pulse_tracer as pt  # noqa: E402


def _bus():
    return EventBus(async_enabled=False)


@pytest.fixture(autouse=True)
def _clean():
    pt.reset_dead_letters()
    yield
    pt.reset_dead_letters()


def test_m379_d2_no_subscriber_marks_dead_letter():
    """★核心：无订阅方发布 ⇒ 死信 +1，且记下来源。"""
    _b = _bus()
    _b.publish("demo.dead.event", payload={"a": 1}, source="probe")
    _s = pt.dead_letter_stats()
    assert _s["total"] == 1, _s
    assert _s["distinct_events"] == 1
    assert _s["items"][0]["event"] == "demo.dead.event"
    assert _s["items"][0]["sources"] == ["probe"]
    assert _s["window_sec"] == 60.0


def test_m379_d2_delivered_event_is_not_dead_letter():
    """反控：有订阅方真收到 ⇒ **不算**死信（否则死信数就没意义）。"""
    _b = _bus()
    _got = []
    _b.subscribe("demo.live.event", lambda e: _got.append(e))
    _b.publish("demo.live.event", payload={}, source="probe")
    assert len(_got) == 1
    assert pt.dead_letter_stats()["total"] == 0


def test_m379_d2_buckets_by_event_name():
    _b = _bus()
    _b.publish("demo.a", source="s1")
    _b.publish("demo.a", source="s2")
    _b.publish("demo.b", source="s1")
    _s = pt.dead_letter_stats()
    assert _s["total"] == 3 and _s["distinct_events"] == 2
    _top = _s["items"][0]
    assert _top["event"] == "demo.a" and _top["count"] == 2
    assert _top["sources"] == ["s1", "s2"]


def test_m379_d2_report_keeps_two_facts_apart():
    """★核心：两侧并列，**没有**合并总数这个字段（防再次混报）。"""
    _b = _bus()
    _b.publish("demo.dead.event", source="probe")
    _rep = pt.orphan_dead_report(events=[], ast_upstream_provider=lambda _e: False)
    assert "orphans" in _rep and "dead_letters" in _rep and "ast_orphans" in _rep
    assert "total" not in _rep, "不许给合并总数：死信与孤儿是两件事"
    assert _rep["orphans"]["kind"] == "no_receiver_emit"
    assert _rep["dead_letters"]["total"] == 1


def test_m379_d2_orphan_entries_are_labelled_not_ast():
    """`_detect_orphans` 的条目要标 kind=no_receiver_emit，不许再叫"AST 孤儿"。"""
    _ev = [{"type": "emit", "timestamp": 1.0, "organ": "胃",
            "event_type": "demo.orphan.evt", "matched_organs": []}]
    _o = pt._detect_orphans(_ev, min_age=0.0, subscription_provider=None)
    assert _o and _o[0]["kind"] == "no_receiver_emit"


def test_m379_d2_ast_provider_optional():
    _rep_off = pt.orphan_dead_report(events=[], ast_upstream_provider=None)
    assert _rep_off["ast_orphans"]["unavailable"] is True
    _rep_on = pt.orphan_dead_report(events=[], ast_upstream_provider=lambda _e: True)
    assert _rep_on["ast_orphans"]["unavailable"] is False
