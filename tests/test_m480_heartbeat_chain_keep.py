# -*- coding: utf-8 -*-
"""480 刀6：`is_running=False` 时心跳链必须继续（台账 T1/F4）。

## 判据（来自活体）

`_trigger_heartbeat` 首行是 `if not self.is_running: return`，而
「租约接管续跳」(374刀1) 与「预排下一拍」(396刀1) 都写在它**之后**。
于是 `is_running` 一变 False，这一拍静默早退、链再也没有人续：
  · 看门狗只能看着 age 涨（20:41→21:21 连续 5 次 274→2704s）；
  · `takeover=0`（接管逻辑一次都没进来过）；
  · `is_running` 后来恢复 True 也没有下一拍叫醒它 —— 不可自发恢复。

## 为什么这样测（而不是只读源码）

`PulseHeart.__new__` + 手工注入（与 test_m374_a1 同款）：绕开 `__init__` 的
重依赖，只观察「这一拍到底有没有排下一拍 / 有没有发心跳」两个可观测事实。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: F401  —— 确保开关可读
from organs.body.PulseHeart import PulseHeart


class _Field:
    def __init__(self):
        self.pulses = []

    def publish(self, pulse):
        self.pulses.append(pulse)


def _mk_heart(**kw):
    h = PulseHeart.__new__(PulseHeart)
    h._logs = []
    h._sched = []
    h._log = lambda level, msg, **k: h._logs.append((level, msg, k))
    h._schedule_next_beat = lambda: h._sched.append(1)
    h._m396_trace_enter = lambda: None
    h.is_running = True
    h.organ_name = "心脏"
    h.current_interval = 10.0
    h.base_interval = 10.0
    h.min_interval = 3.0
    h.max_interval = 60.0
    h._last_beat_time = 0.0
    h._beat_count = 0
    h._heartbeat_in_progress = False
    h._heartbeat_started_at = 0.0
    h._m374_takeover = 0
    h._m374_takeover_streak = 0
    h._m374_suspend = 0
    h._m374_last_dump = None
    h._m365_wd_fail = 0
    h._m374_beat_seq = 0
    h._survival_orchestrator = None
    h.pulse_core = None
    h.info_field = _Field()
    h._scheduled_tasks = {}
    h._status_sweep_counter = 0
    h._emit = lambda *a, **k: None
    h._calculate_heart_rate = lambda: 10.0
    for k, v in kw.items():
        setattr(h, k, v)
    return h


def test_m480_stopped_heart_still_reschedules():
    """核心判据：is_running=False ⇒ 仍排下一拍（链不断）。"""
    h = _mk_heart(is_running=False)
    h._trigger_heartbeat()
    assert len(h._sched) == 1, "停机态必须续链，否则链断死后无法自发恢复"


def test_m480_stopped_heart_does_not_beat():
    """续链 ≠ 发心跳：停机态不得向信息场发脉冲。"""
    h = _mk_heart(is_running=False)
    h._trigger_heartbeat()
    assert h.info_field.pulses == [], "停机态不该发心跳"
    assert h._beat_count == 0


def test_m480_stopped_heart_logs_error_code():
    """这个组合（Timer 活着 + is_running=False）本身异常，必须留痕。"""
    h = _mk_heart(is_running=False)
    h._trigger_heartbeat()
    _codes = [k.get("error_code") for _l, _m, k in h._logs]
    assert "heartbeat_chain_kept_while_stopped" in _codes


def test_m480_running_heart_unchanged():
    """对照组：正常态行为逐字不变（预排 + 收尾各一次）。"""
    h = _mk_heart(is_running=True)
    h._trigger_heartbeat()
    assert len(h._sched) == 2, f"应为「预排+收尾」各一次，实得 {len(h._sched)}"
    assert h._beat_count == 1
    assert h.info_field.pulses, "正常态必须发心跳"


def test_m480_switch_off_restores_early_return(monkeypatch):
    """开关关 ⇒ 逐字回到「首行 return」（A/B 对照，证明刀6 是唯一变量）。"""
    monkeypatch.setattr(PulseHeart, "_m480_keep_chain_on", staticmethod(lambda: False))
    h = _mk_heart(is_running=False)
    h._trigger_heartbeat()
    assert h._sched == [], "关掉开关应回到不续链的旧行为"


def test_m480_switch_default_on():
    assert PulseHeart._m480_keep_chain_on() is True
