# -*- coding: utf-8 -*-
"""172刀3 门控单测：PulseMetricsCollector 内存压力双口径采样落盘面。

验收判据：
  1) info_field 未就绪 → ok=False、数值 None、不抛；
  2) info_field 就绪且采集成功 → ok=True、含 process_rss_mb / system_percent；
  3) info_field 就绪但采集失败(ok=False) → ok=False、不抛。
"""


def _make_collector(info_field):
    from organs.core.PulseMetricsCollector import PulseMetricsCollector
    _c = PulseMetricsCollector.__new__(PulseMetricsCollector)
    _c.info_field = info_field
    return _c


class _FakeFieldOK:
    def _m169_memory_pressure(self):
        return {"ok": True, "process_rss_mb": 234.5, "system_percent": 61.2, "reason": "正常"}


class _FakeFieldFail:
    def _m169_memory_pressure(self):
        return {"ok": False, "process_rss_mb": None, "system_percent": None, "reason": "采集失败"}


def test_info_field_none():
    _c = _make_collector(None)
    _r = _c._collect_memory_pressure()
    assert _r["ok"] is False
    assert _r["process_rss_mb"] is None
    assert _r["system_percent"] is None
    assert _r["reason"]


def test_info_field_ok():
    _c = _make_collector(_FakeFieldOK())
    _r = _c._collect_memory_pressure()
    assert _r["ok"] is True
    assert _r["process_rss_mb"] == 234.5
    assert _r["system_percent"] == 61.2


def test_info_field_collect_fail():
    _c = _make_collector(_FakeFieldFail())
    _r = _c._collect_memory_pressure()
    assert _r["ok"] is False
    assert _r["process_rss_mb"] is None
