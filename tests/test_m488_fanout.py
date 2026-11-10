import time

import pytest

import config
from nucleus.field.InfoField import InfoField


def _field(monkeypatch, fanout=True, ledger=False):
    monkeypatch.setattr(config, "ENABLE_M488_FANOUT_STAT", fanout)
    monkeypatch.setattr(config, "ENABLE_M488_EVENT_LEDGER", ledger)
    return InfoField(max_history=50)


def test_fanout_off_returns_empty(monkeypatch):
    f = _field(monkeypatch, fanout=False)
    assert f.get_fanout_stats() == {}


def test_fanout_coefficient(monkeypatch):
    f = _field(monkeypatch, fanout=True)
    f._event_publish_count["ev.a"] = 10
    f._event_matched_count["ev.a"] = 30
    f._m488_compute_fanout()
    # 平均扇出系数 = matched / emitted = 30/10 = 3.0
    assert f._fanout_current["ev.a"] == 3.0
    assert f._fanout_baseline.get("ev.a") is not None


def test_fanout_real_publish(monkeypatch):
    f = _field(monkeypatch, fanout=True)
    f.register_condition("f_org", event_types=["heart.beat"], handler=lambda p: None)
    f.publish({"pulse_id": "p5", "event_type": "heart.beat", "layer": "L1", "priority": 5})
    time.sleep(0.3)
    f._m488_compute_fanout()
    # heart.beat 匹配 1 个器官、发布 1 次 ⇒ 系数应为 1.0
    assert f._fanout_current.get("heart.beat") == 1.0


def test_fanout_2x_alert(monkeypatch, caplog):
    import logging
    f = _field(monkeypatch, fanout=True)
    f._event_publish_count["ev.b"] = 10
    f._event_matched_count["ev.b"] = 30          # 当前系数 3.0
    f._fanout_baseline["ev.b"] = 1.0             # 7 天基线 1.0
    f._fanout_high_since["ev.b"] = time.time() - 700  # 已超 10 分钟
    f._m488_compute_fanout()
    assert "翻倍告警" in caplog.text
