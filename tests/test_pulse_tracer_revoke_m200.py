# -*- coding: utf-8 -*-
"""200批 刀1 隔离单测：pulse_tracer 判定撤销机制（平反机制断言）。"""
import time

from utils import pulse_tracer as pt


def _emit(event_type, matched=None):
    return {
        "timestamp": time.time(),
        "type": "emit",
        "organ": "OrganA",
        "event_type": event_type,
        "layer": "L0",
        "pulse_id": "p1",
        "matched_organs": matched if matched is not None else [],
    }


def test_late_receive_exonerates_emit():
    # 无 receive → 孤儿；迟到 receive 回填 matched_organs → 平反
    ev = [_emit("evt.x")]
    orphans = pt._detect_orphans(ev, min_age=0.0, subscription_provider=lambda e: False)
    assert any(o["event_type"] == "evt.x" for o in orphans)
    ev[0]["matched_organs"].append("OrganB")
    orphans2 = pt._detect_orphans(ev, min_age=0.0, subscription_provider=lambda e: False)
    assert not any(o["event_type"] == "evt.x" for o in orphans2)


def test_subscription_provider_true_exonerates():
    ev = [_emit("evt.y")]
    # 提供器 True → 平反（压掉 B 路异构发射假阳性）
    assert not pt._detect_orphans(ev, min_age=0.0, subscription_provider=lambda e: True)
    # 提供器 False → 仍孤儿
    assert pt._detect_orphans(ev, min_age=0.0, subscription_provider=lambda e: False)
    # 提供器 None → 维持原判定（保守，仍孤儿）
    assert pt._detect_orphans(ev, min_age=0.0, subscription_provider=lambda e: None)


def test_set_subscription_provider_passthrough(monkeypatch):
    monkeypatch.setattr(pt, "_subscription_provider", lambda e: True)
    ev = [_emit("evt.z")]
    assert not pt._detect_orphans(
        ev, min_age=0.0, subscription_provider=pt._subscription_provider)
    monkeypatch.setattr(pt, "_subscription_provider", None)
    assert pt._detect_orphans(
        ev, min_age=0.0, subscription_provider=pt._subscription_provider)


def test_log_receive_fills_matched_organs(monkeypatch):
    # 真实路径：log_receive 在匹配 emit 时回填 matched_organs（即「平反」动作）
    monkeypatch.setattr(pt, "_events", [])
    monkeypatch.setattr(pt, "_ensure_flusher", lambda: None)
    pt.log_emit("OrganA", "evt.r", "L0", "p2")
    pt.log_receive("OrganB", "evt.r", "OrganA")
    emit = next(e for e in pt._events if e.get("type") == "emit")
    assert "OrganB" in emit["matched_organs"]
