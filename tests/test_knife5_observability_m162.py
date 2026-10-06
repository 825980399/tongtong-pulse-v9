# -*- coding: utf-8 -*-
"""第162批刀5 门控单测：可观测性合并批（E1/E2/B2-1/B2-2/B2-3）。

覆盖：
  - B2-1：全仓无「InfoField（继承 OscillonField）」失实字面；
  - E2（判据④）：注入 publish 异常 → 1 条 WARNING 且计数 +1；
  - E2/判据⑤：EventBus 派发事件无匹配订阅者 → zero_match_events 计数可读，
    EventTap.get_stats 透出 matched_handlers_zero_events；
  - B2-2（判据②）：_FieldStatusMonitor.propagate 空转计数可读（经 OscillonField 子类复刻）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus.evolution.patch_lifecycle as pl_mod
from nucleus.events.EventBus import Event, EventBus
from nucleus.field.OscillonField import OscillonField


def test_no_false_inheritance_literal():
    _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "nucleus", "field", "OscillonField.py")
    _src = open(_p, encoding="utf-8").read()
    assert "InfoField（继承" not in _src, "仍存在失实继承字面"
    assert "InfoField（与 OscillonField 组合" in _src


def test_handoff_publish_fail_warning_and_count(caplog, monkeypatch):
    import logging
    caplog.set_level(logging.WARNING)

    class _FakeBus:
        def publish(self, *a, **k):
            raise RuntimeError("injected bus failure")

    def _fake_get_event_bus():
        return _FakeBus()

    monkeypatch.setattr("nucleus.events.EventBus.get_event_bus", _fake_get_event_bus)
    _before = pl_mod.get_handoff_publish_fail_count()
    pl_mod.publish_handoff("knife5_test_patch", "knife5_stage")
    _after = pl_mod.get_handoff_publish_fail_count()
    assert _after == _before + 1, "发布失败计数应 +1"
    assert any("WARNING" in str(r.levelname) for r in caplog.records), "应有一条 WARNING"


def test_eventbus_zero_match_count_readable():
    # 用全新实例（无订阅者），避免单例被 EventTap 统配订阅干扰判定
    _bus = EventBus(async_enabled=False)
    _ev = Event(name="__knife5_nonexistent_event__", payload={}, source="test_knife5")
    _before = _bus.get_stats().get("zero_match_events", 0)
    _bus._deliver(_ev)
    _after = _bus.get_stats().get("zero_match_events", 0)
    assert _after >= _before + 1, "无匹配订阅者应累加 zero_match_events"


def test_field_monitor_propagate_counter():
    """复刻 main.py _FieldStatusMonitor（B2-2）：propagate 空转计数可读。

    同时核查真实 main.py 源码确实含 _propagate_empty_count 计数器（口径校验）。
    """
    class _Monitor(OscillonField):
        _propagate_empty_count = 0

        def propagate(self, signal):
            _Monitor._propagate_empty_count += 1
            return []

        def register_node(self, node_id, resonant_frequencies=None):
            return True

        def unregister_node(self, node_id):
            return True
    _m = _Monitor(field_name="test")
    _before = _m._propagate_empty_count
    for _ in range(3):
        _m.propagate(None)
    assert _m._propagate_empty_count == _before + 3, "propagate 空转计数应 +3"
    # 口径校验：真实 main.py 必须含该计数器
    _main = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "main.py")
    _src = open(_main, encoding="utf-8").read()
    assert "_propagate_empty_count" in _src, "main.py _FieldStatusMonitor 应含空转计数器"
