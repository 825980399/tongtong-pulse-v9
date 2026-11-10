"""★487-5 门控测试：出队 TTL —— tick 类脉冲入队 >5s 直接丢弃（受 ENABLE_M487_DEQUEUE_TTL）。"""
import time
import config
from nucleus.field.InfoField import InfoField


def _mk(monkeypatch):
    f = InfoField(max_history=50)

    class _Lk:
        def __enter__(self_):
            return None

        def __exit__(self_, *_a):
            return False

    f._lock = _Lk()
    f._m483_deadline_check = lambda *a, **k: (False, 0.0)
    f._total_expired_at_dequeue = 0
    return f


def test_expired_tick_dropped(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M487_DEQUEUE_TTL", True)
    f = _mk(monkeypatch)
    _old = time.time_ns() - 10_000_000_000
    assert f._m487_dequeue_ttl_drop(
        {"event_type": "heart.beat", "timestamp_ns": _old}) is True
    assert f._total_expired_at_dequeue == 1


def test_non_tick_not_dropped(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M487_DEQUEUE_TTL", True)
    f = _mk(monkeypatch)
    _old = time.time_ns() - 10_000_000_000
    assert f._m487_dequeue_ttl_drop(
        {"event_type": "chat.message", "timestamp_ns": _old}) is False


def test_fresh_tick_not_dropped(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M487_DEQUEUE_TTL", True)
    f = _mk(monkeypatch)
    assert f._m487_dequeue_ttl_drop(
        {"event_type": "heart.beat", "timestamp_ns": time.time_ns()}) is False


def test_gate_off_not_dropped(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M487_DEQUEUE_TTL", False)
    f = _mk(monkeypatch)
    _old = time.time_ns() - 10_000_000_000
    assert f._m487_dequeue_ttl_drop(
        {"event_type": "heart.beat", "timestamp_ns": _old}) is False
    assert f._total_expired_at_dequeue == 0
