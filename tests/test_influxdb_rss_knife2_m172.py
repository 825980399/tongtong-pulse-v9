# -*- coding: utf-8 -*-
"""172刀2 门控单测：InfluxDB 写侧含 RSS 采样（复用 InfoField 双口径读数）。

验收判据：
  1) 开关关闭（ENABLE_INFLUXDB_TIMESERIES=False）时 write_point 零 IO、buffer 空、主链无感；
  2) 开关开启后，冲刷出的测量点含 process_rss_mb 字段；
  3) 失败路径（InfoField 未就绪 / 采集失败）返回 None，flush 仍成功、不阻塞主链；
    4) 补 RSS 采样点 record_process_rss 写入 process_rss 测量点并附 process_rss_mb。
"""


def _make_store_available():
    """构造一个「可用」的 InfluxDBStore（不连真实服务，仅注入假 write_api）。"""
    from nucleus.timeseries_store import influxdb_store as m
    _store = m.InfluxDBStore()
    _store._available = True
    _store._last_flush = __import__("time").time()  # 抑制 write_point 内部按间隔自动冲刷

    class _FakeWriteApi:
        def __init__(self):
            self.records = []

        def write(self, bucket=None, org=None, record=None):
            if isinstance(record, list):
                self.records.extend(record)
            else:
                self.records.append(record)

    _store._write_api = _FakeWriteApi()
    return _store, m


class _FakeField:
    def __init__(self, ok=True, rss=123.4, sys_pct=55.5):
        self._ok = ok
        self._rss = rss
        self._sys = sys_pct

    def _m169_memory_pressure(self):
        return {"ok": self._ok, "process_rss_mb": self._rss,
                "system_percent": self._sys}


def test_disabled_zero_io_and_no_buffer(monkeypatch):
    """验收①：开关关闭时零 IO、主链无感。"""
    import config
    monkeypatch.setattr(config, "ENABLE_INFLUXDB_TIMESERIES", False)
    from nucleus.timeseries_store import influxdb_store as m
    _store = m.InfluxDBStore()
    _rc = _store.write_point("node_activated", {"node_id": "n1"}, {"count": 1})
    assert _rc is False
    assert _store._buffer == []


def test_enabled_flush_contains_process_rss_mb(monkeypatch):
    """验收②：开关开启后测量点含 process_rss_mb 字段。"""
    import config
    monkeypatch.setattr(config, "ENABLE_INFLUXDB_TIMESERIES", True)
    monkeypatch.setattr(config, "ENABLE_INFLUXDB_RSS_SAMPLING", True)
    from nucleus.field import InfoField
    monkeypatch.setattr(InfoField, "get_info_field", lambda: _FakeField())
    _store, _ = _make_store_available()
    assert _store.write_point("node_activated", {"node_id": "n1"}, {"count": 1}) is True
    assert _store.flush() is True
    _lines = [_r.to_line_protocol() for _r in _store._write_api.records]
    assert _lines, "应有冲刷记录"
    assert any("process_rss_mb=" in _l for _l in _lines), _lines


def test_rss_failure_path_not_blocking(monkeypatch):
    """验收③：失败路径（InfoField 未就绪）不阻塞主链。"""
    import config
    monkeypatch.setattr(config, "ENABLE_INFLUXDB_TIMESERIES", True)
    from nucleus.field import InfoField
    monkeypatch.setattr(InfoField, "get_info_field", lambda: None)
    _store, _ = _make_store_available()
    assert _store._sample_rss() is None
    assert _store.write_point("node_activated", {"node_id": "n1"}, {"count": 1}) is True
    assert _store.flush() is True
    _lines = [_r.to_line_protocol() for _r in _store._write_api.records]
    assert _lines
    assert all("process_rss_mb=" not in _l for _l in _lines), _lines


def test_record_process_rss_sampling_point(monkeypatch):
    """补 RSS 采样点：record_process_rss 写入 process_rss 测量点。"""
    import config
    monkeypatch.setattr(config, "ENABLE_INFLUXDB_TIMESERIES", True)
    monkeypatch.setattr(config, "ENABLE_INFLUXDB_RSS_SAMPLING", True)
    from nucleus.field import InfoField
    monkeypatch.setattr(InfoField, "get_info_field", lambda: _FakeField())
    _store, _ = _make_store_available()
    assert _store.record_process_rss() is True
    assert _store.flush() is True
    _lines = [_r.to_line_protocol() for _r in _store._write_api.records]
    assert any(_l.startswith("process_rss") for _l in _lines), _lines
    assert any("process_rss_mb=" in _l for _l in _lines), _lines
