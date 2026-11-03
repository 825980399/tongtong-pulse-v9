# -*- coding: utf-8 -*-
"""443 刀5：O-A4 影子进快照 + InfluxDB 写入健壮性。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import nucleus.field.active_memory_dual as amd
from nucleus.timeseries_store.influxdb_store import InfluxDBStore
from organs.core.PulseMetricsCollector import PulseMetricsCollector

_STUB_ATTRS = (
    "_cached_organ_stats", "_cached_heartbeat", "_cached_current_user",
    "_dream_count", "_dream_last_topic", "_reflection_count", "_reflection_last_domain",
    "_liver_compress_count", "_liver_fuse_count", "_instinct_upgrade_count",
    "_headless_stats", "_cached_social_emotions", "_cached_top_interests",
    "_cached_life_state", "_cached_emotion_state",
)


def _bare_collector():
    _c = object.__new__(PulseMetricsCollector)
    _c._beat_count = 0
    _c.info_field = None
    for _a in _STUB_ATTRS:
        setattr(_c, _a, {})
    _c._log = lambda *a, **k: None
    return _c


def test_snapshot_carries_oa4_shadow(monkeypatch):
    """快照必须带 `oa4_shadow`，且读数随影子打分变化（不是常量）。"""
    monkeypatch.setattr(amd, "_M383_SHADOW",
                        {"scored": 0, "hit": 0, "no_rule": 0, "agree": 0, "disagree": 0},
                        raising=False)
    _s0 = _bare_collector()._build_observability_snapshot()
    assert "oa4_shadow" in _s0, "快照缺 oa4_shadow（383 的读数仍然零消费）"
    assert _s0["oa4_shadow"]["scored"] == 0

    amd.observe_llm_answer("测试请求", "回答含 事实凭证A")
    amd.observe_llm_answer("测试请求", "回答含 事实凭证A")
    _s1 = _bare_collector()._build_observability_snapshot()
    assert _s1["oa4_shadow"]["scored"] == 2, _s1["oa4_shadow"]


def test_sample_rss_falls_back_to_psutil(monkeypatch):
    """InfoField 未就绪时 `_sample_rss()` 不许返回 None（否则该点零字段、整批被拒）。"""
    import nucleus.field.InfoField as IF
    monkeypatch.setattr(IF, "get_info_field", lambda *a, **k: None, raising=True)
    _st = InfluxDBStore()
    _r = _st._sample_rss()
    assert isinstance(_r, dict), "兜底失效：返回 None 会让 process_rss 变成零字段点"
    assert _r["process_rss_mb"] > 0
    assert 0.0 <= _r["system_percent"] <= 100.0


def test_flush_skips_zero_field_points(monkeypatch):
    """零字段点必须被跳过：一个空点会让 Influx **整批拒收**（同批的 node_* 一起丢）。"""
    _st = InfluxDBStore()
    _st._available = True

    class _FakeWrite:
        def __init__(self):
            self.calls = 0

        def write(self, **k):
            self.calls += 1

    _fw = _FakeWrite()
    _st._write_api = _fw
    _st._buffer = [{"measurement": "process_rss", "tags": {}, "fields": {},
                    "time": 1}]
    monkeypatch.setattr(_st, "_sample_rss", lambda: None, raising=False)

    assert _st._flush_locked() is False, "全是零字段点时应返回 False（没写 ≠ 写成功）"
    assert _fw.calls == 0, "零字段点不该被发给 Influx"
    assert _st._buffer == [], "缓冲应被清空（否则会无限重试）"
