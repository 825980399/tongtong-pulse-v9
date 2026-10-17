# -*- coding: utf-8 -*-
"""第200批 刀2：真孤儿补注册 —— growth.need_detected 接收方登记。

依据：烛微§1.3 方案②（接收方补注册），仅对「真孤儿」用。
growth.need_detected 由 PulseLiver（:3361/:3385/:3411）+ PulseInnerWorld 多路发射，
而订阅侧 grep 零（无任何 organ 的 on_pulse / InfoField 订阅处理它）→ 真·发了没人听。

本模块在 EventBus（旁路遥测面，ENABLE_EVENT_BUS=False 时无生产接线）上登记一个
no-op 接收方，使「订阅表」出现该事件的接收方。配合 200刀1 的判定撤销机制
（pulse_tracer 默认提供器扫描 EventBus 订阅表），该事件在孤儿判定中被平反。

不做：不翻生产开关（EventBus 默认不启用 → 接收方永不真正触发，零行为副作用）；
不补注册快照/报告类事件；不补 digest.knowledge（留刀1现场对表）。
"""
from __future__ import annotations

from nucleus._silent_except import silent_exc
from nucleus.const import GrowthEvent
from nucleus.events.EventBus import get_event_bus

_EVENT_TYPE = GrowthEvent.NEED_DETECTED
_registered = False


def _noop_handler(event) -> None:
    """no-op 接收方：仅用于把 event_type 登记进订阅表，不改变任何生产行为。"""
    return


def register_growth_need_detected_receiver(bus=None) -> str | None:
    """在 EventBus 上登记 growth.need_detected 的 no-op 接收方（幂等）。

    返回订阅 ID；总线不可用或注册失败返回 None。
    """
    global _registered
    try:
        _bus = bus or get_event_bus()
        # 幂等：已登记且订阅表仍含该事件 → 跳过
        if _registered and _bus.subscription_count(_EVENT_TYPE) > 0:
            return None
        _sid = _bus.subscribe(_EVENT_TYPE, _noop_handler, observer=True)
        _registered = True
        return _sid
    except Exception as e:  # 总线不可用等 → 静默降级，绝不扰动运行路径
        silent_exc(e, where="nucleus.events.orphan_receivers.register_growth_need_detected_receiver")
        return None


def is_growth_need_detected_registered(bus=None) -> bool:
    """查询 growth.need_detected 是否已在订阅表中（供测试与诊断断言）。"""
    try:
        _bus = bus or get_event_bus()
        return _bus.subscription_count(_EVENT_TYPE) > 0
    except Exception as e:  # 总线不可用 → 视为未登记
        silent_exc(e, where="nucleus.events.orphan_receivers.is_growth_need_detected_registered")
        return False
