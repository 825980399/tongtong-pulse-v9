# -*- coding: utf-8 -*-
"""200批 刀2 隔离单测：growth.need_detected 真孤儿补注册（订阅侧grep零→有接收方）。"""
from nucleus.events import orphan_receivers as orp
from nucleus.events.EventBus import get_event_bus, reset_event_bus


def test_register_growth_need_detected_receiver():
    reset_event_bus()
    try:
        sid = orp.register_growth_need_detected_receiver()
        assert sid is not None
        # 订阅表出现接收方（订阅侧 grep 零 → 现已有接收方）
        assert orp.is_growth_need_detected_registered() is True
        assert get_event_bus().subscription_count(orp._EVENT_TYPE) == 1
        # 幂等：再次注册返回 None（已登记）
        assert orp.register_growth_need_detected_receiver() is None
    finally:
        reset_event_bus()


def test_event_type_is_growth_need_detected():
    # 常量一致性：补注册的事件正是任务书实锤组 growth.need_detected
    assert orp._EVENT_TYPE == "growth.need_detected"
