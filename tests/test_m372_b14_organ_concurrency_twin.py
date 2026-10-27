# -*- coding: utf-8 -*-
"""373 刀7 · 372 刀14 孪生补建：热路径 `_handle_pulse_safe` 并发契约（P3）

票：`T-372-b14测试孪生-1`（372 刀14 评估结论「建议补建」，本刀落地）

被测：`base/BasePulseOrgan.py::_handle_pulse_safe`（v9.5 并发安全版）。
契约三条：
  a) 多线程并发调用**不抛异常**（异常在器官边界内被吞成 None + 计数）；
  b) 连续异常达阈值 ⇒ 熔断（`OrganStatus.FUSED`），告警**只发一次**；
  c) 冷却到期 ⇒ 半开（`OrganStatus.RECOVERING`），一次成功即复位回
     `OrganStatus.RUNNING` 且计数/翻倍系数清零。

口径：一律用 `OrganStatus` 枚举比较，**不用字符串** `"fused"`
（372 刀14 证据件 §四 点名的坑：字符串比较在枚举改名后不会报错，只会静默失效）。
"""

import threading
import time

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import OrganStatus


class _TwinOrgan(BasePulseOrgan):
    """最小可测器官：on_pulse 由 pulse["mode"] 决定成功/抛异常/慢返回。"""

    def __init__(self):
        super().__init__("刀7并发孪生")
        self.alarms = []
        self.start()  # is_running=True + status=RUNNING（基类 start 的标准动作）

    def on_pulse(self, pulse):
        mode = pulse.get("mode", "ok")
        if mode == "sleep":
            time.sleep(float(pulse.get("sleep", 0.05)))
            return {"ok": True}
        if mode == "boom":
            raise RuntimeError("刀7 故意失败")
        return {"ok": True}

    def on_time_tick(self, _pulse):
        return {"tick": True}

    def get_resonance_conditions(self):
        return []

    def _emit(self, event_type, payload=None, **_kw):
        # 只记录不真发：本孪生测的是 `_handle_pulse_safe` 的并发契约与
        # 「熔断告警只发一次」，告警传输本身不属被测面。
        self.alarms.append((event_type, payload or {}))


def _run_concurrent(organ, pulses, timeout=5.0):
    """Barrier 同起跑：所有线程在 barrier 上对齐后再进入被测方法。"""
    barrier = threading.Barrier(len(pulses))
    escaped = []
    results = [None] * len(pulses)

    def _worker(_i, _p):
        try:
            barrier.wait(timeout=timeout)
            results[_i] = organ._handle_pulse_safe(_p)
        except BaseException as _e:  # 逃逸出边界即违约，必须被抓到
            escaped.append(_e)

    _ts = [threading.Thread(target=_worker, args=(_i, _p), daemon=True)
           for _i, _p in enumerate(pulses)]
    for _t in _ts:
        _t.start()
    for _t in _ts:
        _t.join(timeout=timeout)
    return escaped, results


def test_01_ten_threads_concurrent_no_exception():
    """① 10 线程同起跑并发 ⇒ 零逃逸异常、全部拿到结果、状态不被打乱。"""
    organ = _TwinOrgan()
    escaped, results = _run_concurrent(organ, [{"event_type": "t"}] * 10)
    assert escaped == [], f"并发调用逃逸异常 {len(escaped)} 个: {escaped}"
    assert results.count({"ok": True}) == 10, f"结果不完整: {results}"
    assert organ.status is OrganStatus.RUNNING, f"实得 {organ.status}"
    assert organ._error_count == 0


def test_02_three_errors_then_two_normals_trip_fuse_and_reject():
    """② 5 线程（3 error + 2 normal）⇒ 熔断，且熔断期后来的调用被拒收。

    分两波跑（各波内部仍 Barrier 同起跑）：如果 5 个线程混在一波，后到的成功
    调用会把 `_error_count` 清零，熔断与否取决于调度顺序 —— 那就成了测调度器
    而不是测契约（372 刀9 G5 教训）。分波后每条断言都是确定性的。
    """
    organ = _TwinOrgan()
    escaped, _results = _run_concurrent(organ, [{"event_type": "t", "mode": "boom"}] * 3)
    assert escaped == [], f"并发调用逃逸异常: {escaped}"
    assert organ.status is OrganStatus.FUSED, f"3 次错误未熔断，实得 {organ.status}"
    assert organ._error_count == 3, f"错误计数丢失更新（实得 {organ._error_count}，期望 3）"
    _alarms = [x for x in organ.alarms if x[1].get("type") == "organ_fused"]
    assert len(_alarms) == 1, f"熔断告警应只发 1 次，实得 {len(_alarms)}"
    assert organ._fuse_multiplier == 2, "熔断后翻倍系数未升档"
    assert organ._fuse_until > time.time(), "冷却窗口未建立"

    # 2 个 normal 线程：熔断期内必须被拒收，且不得改动计数/状态
    escaped2, results2 = _run_concurrent(organ, [{"event_type": "t"}] * 2)
    assert escaped2 == [], f"熔断期调用逃逸异常: {escaped2}"
    assert results2 == [None, None], f"熔断期未拒收: {results2}"
    assert organ.status is OrganStatus.FUSED and organ._error_count == 3


def test_03_half_open_recovers_on_next_success():
    """③ 半开：冷却未到 ⇒ 拒收且状态不变；冷却到期 ⇒ RECOVERING，成功即复位。"""
    organ = _TwinOrgan()
    organ.status = OrganStatus.FUSED
    organ._fuse_until = time.time() + 60.0
    assert organ._handle_pulse_safe({"event_type": "t"}) is None
    assert organ.status is OrganStatus.FUSED, "冷却期内不该被放行"

    organ._fuse_until = time.time() - 1.0  # 冷却到期
    assert organ._handle_pulse_safe({"event_type": "t"}) == {"ok": True}
    assert organ.status is OrganStatus.RUNNING, f"半开后未恢复，实得 {organ.status}"
    assert organ._error_count == 0 and organ._fuse_multiplier == 1, "恢复后未复位"
