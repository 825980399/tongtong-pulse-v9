# -*- coding: utf-8 -*-
"""175刀1 门控单测：PulseMetricsCollector 内存压力采集链的 GC 观测面。

★365刀B1 夹具收口（口径变更，已在 365 交付报告 §偏差 声明）：
  GC 回收动作已**收口到 `nucleus.runtime_metrics.check_memory_alarm_detailed`**
  （旧 175 刀「RSS 单闸 + 独立 gc.collect()」块退役），collector 侧不再自己调
  `gc.collect()`。因此本文件的判据从「collector 有没有调 gc.collect」改为：
    1) 采集链是否把 `auto_gc=True` 传给告警链（否则三闸接好也不回收）；
    2) `gc_triggered` 是否**单轨**取自 `growth_alarm`（不再有第二套 `_mem_gc_triggered`）；
    3) `gc_last_rss_mb` 只在真触发了自动 GC 时才记录（info_field 口径 RSS）。
  三闸（percent/rss/growth）判定本身由 `tests/test_m364_b_batch.py::TestB1MemoryAutoGC`
  覆盖，本文件不再重复断言（避免同一事实两处口径）。

注：配置读取走 sys.modules["config"]（与产品代码一致），测试以 stub 注入。
"""


def _make_collector(info_field):
    from organs.core.PulseMetricsCollector import PulseMetricsCollector
    import logging
    _c = PulseMetricsCollector.__new__(PulseMetricsCollector)
    _c.info_field = info_field
    # ★364刀A6（夹具漂移）：339批起 `_collect_memory_pressure` 用 `self._logger` 留痕，
    #   本夹具走 `__new__` 只塞 info_field ⇒ AttributeError 把整段采集打成「采集异常」，
    #   gc 断言自然全红。补 `_logger` 即恢复（不改生产代码）。
    _c._logger = logging.getLogger("test.m175.collector")
    return _c


class _FakeFieldHigh:
    def _m169_memory_pressure(self):
        return {"ok": True, "process_rss_mb": 9000.0, "system_percent": 50.0,
                "breached": True, "reason": ""}


class _FakeFieldLow:
    def _m169_memory_pressure(self):
        return {"ok": True, "process_rss_mb": 1000.0, "system_percent": 30.0,
                "breached": False, "reason": ""}


class _FakeAlarm:
    """假装 `RuntimeMetrics.check_memory_alarm_detailed`：记录入参、返回给定读数。"""

    def __init__(self, payload):
        self.payload = dict(payload)
        self.kwargs = []

    def __call__(self, *args, **kwargs):
        self.kwargs.append(dict(kwargs))
        return dict(self.payload)


class _FakeMetrics:
    def __init__(self, alarm):
        self.check_memory_alarm_detailed = alarm


import sys
import types
from unittest.mock import patch

# patch 目标必须先真实导入（否则 `nucleus.runtime_metrics` 属性不存在，patch 会报 AttributeError）
sys.path.insert(0, __file__.rsplit("tests", 1)[0].rstrip("\\/"))
import nucleus.runtime_metrics  # noqa: E402,F401


def _run(info_field, payload, stub):
    _alarm = _FakeAlarm(payload)
    with patch.dict(sys.modules, {"config": stub}), \
         patch("nucleus.runtime_metrics.get_runtime_metrics",
               lambda: _FakeMetrics(_alarm)):
        _c = _make_collector(info_field)
        _r = _c._collect_memory_pressure()
    return _r, _alarm


def test_gc_triggered_when_enabled_and_over():
    _stub = types.SimpleNamespace(ENABLE_MEMORY_AUTO_GC=True, MEMORY_AUTO_GC_RSS_MB=1000.0)
    _r, _alarm = _run(_FakeFieldHigh(),
                      {"triggered": True, "gc_triggered": True,
                       "auto_gc_last": {"collected": 12, "pause_ms": 1.5,
                                        "rss_mb": 9000.0, "triggers": ["rss"]}},
                      _stub)
    assert _r["ok"] is True
    assert _r["gc_triggered"] is True, _r
    assert _r["gc_last_rss_mb"] == 9000.0
    # 回收动作已收口到告警链：collector 必须传 auto_gc=True（否则永不回收）
    assert _alarm.kwargs and _alarm.kwargs[0].get("auto_gc") is True, _alarm.kwargs


def test_no_gc_when_disabled():
    _stub = types.SimpleNamespace(ENABLE_MEMORY_AUTO_GC=False, MEMORY_AUTO_GC_RSS_MB=1000.0)
    _r, _alarm = _run(_FakeFieldHigh(),
                      {"triggered": False, "gc_triggered": False},
                      _stub)
    assert _r["gc_triggered"] is False
    assert _r["gc_last_rss_mb"] is None
    assert _alarm.kwargs[0].get("auto_gc") is True


def test_no_gc_when_under_threshold():
    _stub = types.SimpleNamespace(ENABLE_MEMORY_AUTO_GC=True, MEMORY_AUTO_GC_RSS_MB=16384.0)
    _r, _ = _run(_FakeFieldHigh(),
                 {"triggered": False, "gc_triggered": False},
                 _stub)
    assert _r["gc_triggered"] is False
    assert _r["gc_last_rss_mb"] is None


def test_peak_fields_present():
    _stub = types.SimpleNamespace(ENABLE_MEMORY_AUTO_GC=False, MEMORY_AUTO_GC_RSS_MB=8192.0)
    _r, _ = _run(_FakeFieldHigh(),
                 {"triggered": False, "gc_triggered": False}, _stub)
    assert "rss_peak_mb" in _r
    assert "rss_peak_ts" in _r
    assert "instant_drop_mb" in _r
    assert _r["rss_peak_mb"] == 9000.0


def test_field_none_still_safe():
    _stub = types.SimpleNamespace(ENABLE_MEMORY_AUTO_GC=True, MEMORY_AUTO_GC_RSS_MB=1.0)
    _r, _ = _run(None, {"triggered": True, "gc_triggered": True}, _stub)
    assert _r["ok"] is False
    assert _r["gc_triggered"] is False
