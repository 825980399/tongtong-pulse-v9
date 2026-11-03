# -*- coding: utf-8 -*-
"""第434批刀4 单测：队列水位出口 + 假告警修复。

守两件事（都跟"看得见"有关）：
  ① `m363_diag_bridge.collect()` 的 `queue_alarm` 不再 `bool(dict)` 恒 True——
     `alarm` 字段必须解引用 dict 里的 `"alarm"`，且保留 depth/threshold/suppressed；
  ② health_snapshot（`_build_observability_snapshot`）能看到四层队列深度
     （`backpressure` 块来自 `InfoField.get_backpressure_stats`，取不到给 None 不给假 0）。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import nucleus.m363_diag_bridge as _bridge
from organs.core.PulseMetricsCollector import PulseMetricsCollector


# ================================================================ 假告警
class _FakeBusAlarmFalse:
    def m360_event_metrics(self):
        return {"queue_depth": 5, "queue_alarm_threshold": 100}

    def m360_check_queue_alarm(self):
        return {"alarm": False, "depth": 5, "threshold": 100}


class _FakeBusAlarmTrue:
    def m360_event_metrics(self):
        return {"queue_depth": 250, "queue_alarm_threshold": 100}

    def m360_check_queue_alarm(self):
        return {"alarm": True, "depth": 250, "threshold": 100, "suppressed": False}


def test_queue_alarm_false_when_dict_says_false(monkeypatch):
    """bool(dict) 会把 {'alarm': False} 判成 True；修复后必须解引用 alarm 字段。"""
    monkeypatch.setattr(_bridge, "_get",
                        lambda kind: _FakeBusAlarmFalse() if kind == "event_bus" else None)
    out = _bridge.collect()
    qa = out["queue_alarm"]
    assert qa["alarm"] is False, "alarm=False 被 bool(dict) 翻成 True（假告警未修）"
    assert qa["depth"] == 5
    assert qa["threshold"] == 100
    assert qa["suppressed"] is False


def test_queue_alarm_true_preserves_fields(monkeypatch):
    monkeypatch.setattr(_bridge, "_get",
                        lambda kind: _FakeBusAlarmTrue() if kind == "event_bus" else None)
    out = _bridge.collect()
    qa = out["queue_alarm"]
    assert qa["alarm"] is True
    assert qa["depth"] == 250
    assert qa["threshold"] == 100
    assert qa["suppressed"] is False


# ================================================================ 背压进快照
_STUB_ATTRS = (
    "_cached_organ_stats", "_cached_heartbeat", "_cached_current_user",
    "_dream_count", "_dream_last_topic", "_reflection_count",
    "_reflection_last_domain", "_liver_compress_count", "_liver_fuse_count",
    "_instinct_upgrade_count", "_headless_stats", "_cached_social_emotions",
    "_cached_top_interests", "_cached_life_state", "_cached_emotion_state",
)


def _bare_collector():
    """不经 __init__ 的裸实例（只测读数装配，不起器官总线）。"""
    _c = object.__new__(PulseMetricsCollector)
    _c._beat_count = 0
    _c.info_field = None
    # 裸实例无 _logger：_m403_collect_warm/cold 里 info_field 缺方法会走 except 并
    # self._log(...)，打桩掉避免 AttributeError 盖过本刀真正要断言的字段。
    _c._log = lambda level, message, error_code=None: None
    for _a in _STUB_ATTRS:
        setattr(_c, _a, {})
    return _c


class _FakeInfoField:
    def get_stats(self):
        return {"total_matched": 0, "total_published": 0, "active_conditions": 0}

    def get_backpressure_stats(self):
        return {
            "L0": {"queue_depth": 0, "limit": 0, "rejected": 0},
            "L1": {"queue_depth": 3, "limit": 500, "rejected": 1},
            "L2": {"queue_depth": 0, "limit": 200, "rejected": 0},
            "L3": {"queue_depth": 7, "limit": 100, "rejected": 2},
        }


def test_backpressure_in_snapshot(monkeypatch):
    _c = _bare_collector()
    _c.info_field = _FakeInfoField()
    monkeypatch.setattr(_c, "_collect_memory_pressure",
                        lambda: {"process_rss_mb": 1.0, "system_percent": 50.0})
    monkeypatch.setattr(_c, "_m401_extra_fields_on", lambda: False)
    _s = _c._build_observability_snapshot()
    assert "backpressure" in _s, "快照缺 backpressure 块"
    _bp = _s["backpressure"]
    assert _bp["L1"]["queue_depth"] == 3
    assert _bp["L3"]["rejected"] == 2
    assert _bp["L2"]["limit"] == 200


def test_backpressure_none_when_no_info_field(monkeypatch):
    _c = _bare_collector()
    _c.info_field = None
    monkeypatch.setattr(_c, "_collect_memory_pressure",
                        lambda: {"process_rss_mb": 1.0, "system_percent": 50.0})
    monkeypatch.setattr(_c, "_m401_extra_fields_on", lambda: False)
    _s = _c._build_observability_snapshot()
    assert _s["backpressure"] is None, "取不到 info_field 时应为 None 而非假 0"
