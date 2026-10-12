# -*- coding: utf-8 -*-
"""200批 刀3 隔离单测：lfi_self_awareness_compare 挂 DailyScheduler 调度点（存在断言）。"""
from nucleus.self_awareness.DailyScheduler import SelfAwarenessDailyScheduler
from tools import lfi_self_awareness_compare as cmp


def test_register_with_daily_schedule_mounts_hook():
    sched = SelfAwarenessDailyScheduler()
    result = cmp.register_with_daily_schedule(scheduler=sched)
    assert result is sched
    assert sched.has_daily_hook(cmp.DAILY_SCHEDULE_HOOK) is True
    assert cmp.is_scheduled(scheduler=sched) is True


def test_hook_recorded_in_scheduler():
    sched = SelfAwarenessDailyScheduler()
    cmp.register_with_daily_schedule(scheduler=sched)
    names = [h[0] for h in sched._daily_hooks]
    assert cmp.DAILY_SCHEDULE_HOOK in names


def test_schedule_hook_constant():
    # 调度点常量存在且非空（供运维/约窗引用）
    assert isinstance(cmp.DAILY_SCHEDULE_HOOK, str) and cmp.DAILY_SCHEDULE_HOOK
