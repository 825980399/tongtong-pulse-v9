"""★487-3 门控测试：对话链埋点（dialog_latency_*.jsonl）+ pulse_pairs 加 correlation_id。

门控 ENABLE_M486_DIALOG_TRACE：关 ⇒ 不打埋点；开 ⇒ 写 data/monitor/dialog_latency_*.jsonl。
"""
import os
import json

import config
import nucleus.dialog_latency_trace as _dlt
import nucleus.data.write_guard as _wg
from utils import pulse_tracer as _pt


def test_record_off_noop(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M486_DIALOG_TRACE", False)
    assert _dlt.record("cid1", "chat.message", "L1", "chat_service", "emit") is None


def test_record_on_writes_row_and_synthetic_flag(monkeypatch):
    # pytest 环境下写盘守卫默认拒写生产 data/（设计如此）；此处绕过守卫验证写逻辑。
    monkeypatch.setattr(_wg, "guard_write", lambda *a, **k: True)
    monkeypatch.setattr(config, "ENABLE_M486_DIALOG_TRACE", True)
    _r = _dlt.record("cidX", "chat.message", "L1", "chat_service", "emit",
                     is_synthetic=True)
    assert _r is not None
    _p = _dlt.out_path(_r["ts"])
    try:
        assert os.path.isfile(_p)
        with open(_p, encoding="utf-8") as _f:
            _lines = [l for l in _f.read().splitlines() if l.strip()]
        _row = json.loads(_lines[-1])
        assert _row["cid"] == "cidX"
        assert _row["event"] == "chat.message"
        assert _row["node"] == "chat_service"
        assert _row["is_synthetic"] is True
    finally:
        if os.path.isfile(_p):
            os.remove(_p)


def test_cortex_helper_writes(monkeypatch):
    from organs.brain.PulseCortex import PulseCortex
    monkeypatch.setattr(_wg, "guard_write", lambda *a, **k: True)
    monkeypatch.setattr(config, "ENABLE_M486_DIALOG_TRACE", True)
    PulseCortex._m487_dialog_trace(None, "semantic.classify", "L1", "emit", "cidY")
    _p = _dlt.out_path()
    try:
        assert os.path.isfile(_p)
        with open(_p, encoding="utf-8") as _f:
            _lines = [l for l in _f.read().splitlines() if l.strip()]
        _row = json.loads(_lines[-1])
        assert _row["event"] == "semantic.classify"
        assert _row["node"] == "PulseCortex"
    finally:
        if os.path.isfile(_p):
            os.remove(_p)


def test_pulse_tracer_correlation_id():
    # ★487-3：log_emit 必须透传 correlation_id（pulse_pairs.jsonl 落盘行随之带该字段）
    _pt.log_emit("organ", "event", "L1", "pid", "amp", "target",
                 ttl_ns=None, correlation_id="cidZ")
    _ev = _pt._events[-1]
    assert _ev.get("correlation_id") == "cidZ"
