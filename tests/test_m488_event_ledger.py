import time

import pytest

import config
from nucleus.field.InfoField import InfoField


def _field(monkeypatch, ledger=True, fanout=False):
    monkeypatch.setattr(config, "ENABLE_M488_EVENT_LEDGER", ledger)
    monkeypatch.setattr(config, "ENABLE_M488_FANOUT_STAT", fanout)
    return InfoField(max_history=50)


def test_publish_records_emitted(monkeypatch):
    f = _field(monkeypatch, ledger=True)
    f.publish({"pulse_id": "p1", "event_type": "heart.beat", "layer": "L1", "priority": 5})
    # emitted 复用既有 _event_publish_count（按 event_type）
    assert f._event_publish_count.get("heart.beat") == 1
    led = f._m488_compute_ledger()
    assert led["heart.beat"]["emitted"] == 1


def test_ttl_publish_recorded(monkeypatch):
    f = _field(monkeypatch, ledger=True)
    old = time.time_ns() - 10_000_000_000  # 10s 前 ⇒ TTL 过期
    f.publish({"pulse_id": "p2", "event_type": "x.y", "layer": "L1", "priority": 5,
               "ttl_ns": 5_000_000_000, "timestamp_ns": old})
    assert f._event_dropped.get("x.y", {}).get("ttl_publish") == 1


def test_matched_and_handled(monkeypatch):
    f = _field(monkeypatch, ledger=True)
    flag = {}
    f.register_condition("t_org", event_types=["heart.beat"],
                         handler=lambda p: flag.setdefault("ran", True))
    f.publish({"pulse_id": "p3", "event_type": "heart.beat", "layer": "L1", "priority": 5})
    # matched 按器官匹配数累加（同步、确定性）
    assert f._event_matched_count.get("heart.beat") == 1
    # handled 随 handler 执行累加（可能经线程池，短暂等待）
    time.sleep(0.3)
    assert f._event_handled_count.get("heart.beat") == 1
    assert flag.get("ran") is True


def test_ghost_loss_detected(monkeypatch):
    f = _field(monkeypatch, ledger=True)
    f._event_matched_count["ghost.et"] = 10
    f._event_handled_count["ghost.et"] = 5
    # 应投递10 - 实处理5 - 丢弃0 = 5 幽灵丢失
    led = f._m488_compute_ledger()
    assert led["ghost.et"]["ghost_loss"] == 5


def test_switch_off_no_counting(monkeypatch):
    f = _field(monkeypatch, ledger=False)
    f.publish({"pulse_id": "p4", "event_type": "heart.beat", "layer": "L1", "priority": 5})
    # 488 新增的三类计数器在开关关时必须为空（rollback-safe，零回归）
    assert f._event_matched_count == {}
    assert f._event_handled_count == {}
    assert f._event_dropped == {}
