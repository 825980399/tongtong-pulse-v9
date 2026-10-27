# -*- coding: utf-8 -*-
"""374 刀1 验收：心跳链存活加固（先排下一拍／租约守卫接管／停放探针／抓栈）。

事故锚点（2026-10-26）：beat 冻结在 1374，全进程零输出 209s（RSS −2.05GB、打开文件
17→0），恢复后所有周期件自愈，唯独心跳与 TimeCore 两条自续链 Timer 死掉——因为续链
写在 `finally` 里，而那一拍卡在 `field.publish` 上没返回。

本文件只验**链路存活机制**，不改 W1 判据（test_09 反证边界）。
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import organs.body.PulseHeart as PH
from nucleus.chronos.TimeCore import TimeCore
from organs.body.PulseHeart import PulseHeart


class _Field:
    """最小 info_field：publish 成功返回（不阻塞）。"""

    def __init__(self):
        self.pulses = []

    def publish(self, pulse):
        self.pulses.append(pulse)


def _mk_heart(**kw):
    h = PulseHeart.__new__(PulseHeart)
    h._logs = []
    h._sched = []

    def _log(level, msg, **kw2):
        h._logs.append((level, msg, kw2))

    def _schedule():
        h._sched.append(time.time())

    h._log = _log
    h._schedule_next_beat = _schedule
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
    # ★374刀1 新增字段：显式给齐（不靠 getattr 兜底，否则"__init__ 漏声明"这类
    #   缺陷会被测试自己掩盖）
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


def _errs(h, code):
    return [m for _l, m, k in h._logs if k.get("error_code") == code]


# ---------------------------------------------------------------- ① 预排
def test_01_schedule_ahead_when_body_runs():
    """正常一拍：进入即预排一次，finally 再排一次（口径不变，仍是单链）。"""
    h = _mk_heart()
    h._trigger_heartbeat()
    assert len(h._sched) == 2, f"应为「预排+收尾」各一次，实得 {len(h._sched)}"
    assert h._beat_count == 1
    assert getattr(h, "_m374_pre_sched_fail", 0) == 0


def test_02_skip_path_still_keeps_chain_alive():
    """★核心：守卫在租约内 ⇒ 这一拍跳过，但**下一拍必须已经排好**。

    这就是 17:24 事故的正面反演：原实现跳过时什么都不排，链在第一次跳过就断了。
    """
    h = _mk_heart(_heartbeat_in_progress=True, _heartbeat_started_at=time.time() - 5.0)
    h._trigger_heartbeat()
    assert len(h._sched) == 1, "跳过路径必须仍然排下一拍（否则链死）"
    assert h._beat_count == 0, "租约内不得叠加执行"
    assert h._heartbeat_in_progress is True, "租约内不得抢守卫"


def test_03_expired_lease_takes_over():
    """租约过期（4×间隔）⇒ 接管续跳：计数+1、beat 前进、ERROR 留痕。"""
    h = _mk_heart(_heartbeat_in_progress=True, _heartbeat_started_at=time.time() - 40.0)
    h._m374_dump_stall = lambda held: "fake_dump.txt"
    h._trigger_heartbeat()
    assert h._beat_count == 1, "接管后必须真的跳一拍（否则链仍然是死的）"
    assert getattr(h, "_m374_takeover", 0) == 1
    assert _errs(h, "heartbeat_guard_takeover"), h._logs
    assert "fake_dump.txt" in " ".join(m for _l, m, _k in h._logs)
    assert h._heartbeat_in_progress is False, "本拍收尾后必须释放守卫"


def test_04_takeover_capped_keeps_chain_only():
    """连续接管超上限 ⇒ 只保链不自伤（不再起新本体，避免线程堆积）。"""
    h = _mk_heart(_heartbeat_in_progress=True, _heartbeat_started_at=time.time() - 40.0,
                  _m374_takeover_streak=PH._M374_MAX_TAKEOVER_STREAK)
    h._trigger_heartbeat()
    assert h._beat_count == 0, "已封顶时不得再起本体"
    assert _errs(h, "heartbeat_takeover_capped"), h._logs
    assert len(h._sched) == 1, "封顶分支仍必须保住链"


def test_05_guard_without_started_at_not_miskilled():
    """守卫为真但没有进入时刻 ⇒ 按最后一拍进入时刻反推，不当作"永久卡死"误杀。"""
    h = _mk_heart(_heartbeat_in_progress=True, _heartbeat_started_at=0.0)
    h._last_beat_time = time.time() - 4.0
    h._trigger_heartbeat()
    assert h._beat_count == 0, "刚起拍 4s 不该被判卡死"
    assert len(h._sched) == 1


def test_06_lease_scales_with_interval():
    h = _mk_heart(current_interval=10.0)
    assert h._m374_guard_lease() == pytest.approx(30.0)
    h.current_interval = 0
    assert h._m374_guard_lease() == pytest.approx(3.0 * PH._M374_FALLBACK_INTERVAL)


def test_07_dump_stall_writes_thread_stacks(tmp_path, monkeypatch):
    """卡死抓栈真的落盘且含全线程栈（不是只写一行头）。"""
    monkeypatch.setattr(PH, "_M374_STALL_DIR", str(tmp_path / "stall"))
    h = _mk_heart(_beat_count=9)
    p = h._m374_dump_stall(41.5)
    assert p and os.path.exists(p)
    with open(p, encoding="utf-8") as _f:
        body = _f.read()
    assert "374刀1" in body and "held=41.5s" in body and "beat=9" in body
    # faulthandler 的表头是「Current thread 0x…」（当前线程）与「Thread 0x…」（其余线程），
    # 只起一条线程时只有前者 —— 所以按「有地址 + 有栈帧」判，别钉表头字面量。
    assert "0x" in body and 'File "' in body, "必须含真栈帧（faulthandler 输出）"


# ---------------------------------------------------------------- ② 观测
def test_08_watchdog_trace_is_throttled():
    h = _mk_heart()
    h._m374_wd_trace()
    h._m374_wd_trace()
    _traces = [m for _l, m, _k in h._logs if "[374刀1][365链] 看门狗存活" in m]
    assert len(_traces) == 1, "10 分钟窗口内只留一条（否则日志被自己刷满）"
    h._m374_wd_trace_at = time.time() - PH._M374_WD_TRACE_EVERY_SEC - 1
    h._m374_wd_trace()
    _traces = [m for _l, m, _k in h._logs if "[374刀1][365链] 看门狗存活" in m]
    assert len(_traces) == 2


class _FakeClock:
    """只实现 PulseHeart 用到的时间面；第一次 sleep 模拟「睡了 5s 实际过了 200s」。"""

    def __init__(self, jump=200.0):
        self.t = 1_000_000.0
        self.jump = jump
        self.naps = []

    def time(self):
        return self.t

    def sleep(self, sec):
        self.naps.append(sec)
        self.t += self.jump if len(self.naps) == 1 else sec

    def strftime(self, *_a, **_k):
        return "fake-ts"


def test_09_process_suspend_detected_by_watchdog(monkeypatch, tmp_path):
    """看门狗「睡过头」⇒ 判进程级停放 + 抓栈（本次事故唯一缺失的痕迹）。

    ★这里必须真跑 `_m365_watchdog_loop`：原稿用 `if False else None` 空转，
    断言全绿但判据分支一次都没进（等于没测）。假时钟只替换 `PH.time`，
    不动全局 time 模块，避免污染 pytest 自身计时。
    """
    monkeypatch.setattr(PH, "_M374_STALL_DIR", str(tmp_path / "stall"))
    h = _mk_heart()
    clock = _FakeClock(jump=200.0)
    monkeypatch.setattr(PH, "time", clock)

    _beats = []

    def _beat():
        _beats.append(1)
        if len(_beats) >= 2:
            h._m365_wd_running = False

    h._m365_wd_running = True
    h.m365_beat_watchdog = _beat
    h._m374_wd_trace = lambda: None  # 存活留痕另有 test_08
    h._m365_watchdog_loop()

    assert h._m374_suspend == 1, f"停放未被识别（第 1 次 sleep 跳了 200s）：{clock.naps}"
    assert _errs(h, "process_suspend_detected"), h._logs
    assert h._m374_last_dump and os.path.exists(h._m374_last_dump), "停放时必须抓栈"
    _msgs = " ".join(m for _l, m, _k in h._logs)
    assert "全进程被挂起" in _msgs, "告警必须点明是进程级停放（不是单链故障）"
    assert len(_beats) == 2, "停放那拍仍要执行自检，不能因抓栈跳过"


# ---------------------------------------------------------------- ③ TimeCore
def _mk_timecore(**kw):
    tc = TimeCore.__new__(TimeCore)
    tc._logs = []
    tc._sched = []
    tc._casts = []
    tc._log = lambda msg: tc._logs.append(msg)
    tc._schedule_next = lambda: tc._sched.append(time.time())
    tc._broadcast = lambda: tc._casts.append(1)
    tc._running = True
    tc._interval = 60.0
    tc._last_tick_time = time.time()
    tc._active_schedule_enabled = False
    tc._m374_tick_in_progress = False
    tc._m374_tick_started = 0.0
    tc._m374_tick_seq = 0
    for k, v in kw.items():
        setattr(tc, k, v)
    return tc


def test_10_timecore_schedule_ahead_on_skip():
    """TimeCore 同型：上一拍还在跑（租约内）⇒ 本拍不叠加，但链已排好。"""
    tc = _mk_timecore(_m374_tick_in_progress=True, _m374_tick_started=time.time() - 10.0)
    tc._tick()
    assert tc._casts == [], "租约内不得叠加广播"
    assert len(tc._sched) == 1, "必须已经排下一拍（否则 tick 链死）"


def test_11_timecore_expired_lease_takes_over():
    tc = _mk_timecore(_m374_tick_in_progress=True, _m374_tick_started=time.time() - 200.0)
    tc._tick()
    assert tc._casts == [1], "超租约必须接管并广播"
    assert getattr(tc, "_m374_tick_takeover", 0) == 1
    assert any("374刀1" in m for m in tc._logs), tc._logs
    assert tc._m374_tick_in_progress is False, "收尾必须释放"


# ---------------------------------------------------------------- ④ 边界
def test_12_w1_criteria_untouched():
    """边界：本刀不改 W1 判据本体（90s 窗／三项 and／连续 3 tick）。"""
    from nucleus.world_model import external_heartbeat as EH

    assert EH.DEFAULT_MAX_AGE_SEC == 90.0
    with open("nucleus/self_inspector.py", encoding="utf-8") as _f:
        _src = _f.read()
    assert "_M340_JUDGE_ACTION_MIN_TICKS = 3" in _src


def test_13_timecore_no_timer_proliferation(monkeypatch):
    """★自核查发现的坑：TimeCore「进入即预排 + finally 再排」必须强 cancel 旧的。

    它原来没有 `_timer_lock`/cancel（PulseHeart 有），不补的话每拍翻倍（1→2→4…），
    这比断链更快致命。本用例直接盯「排两次 ⇒ 只留一个活定时器」。
    """
    import importlib

    _mod = importlib.import_module("nucleus.chronos.TimeCore")
    made = []

    class _FakeTimer:
        def __init__(self, sec, fn):
            self.sec = sec
            self.fn = fn
            self.daemon = False
            self.cancelled = False
            made.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(_mod.threading, "Timer", _FakeTimer)
    tc = _mk_timecore()
    tc._timer = None
    tc._schedule_next = _mod.TimeCore._schedule_next.__get__(tc)
    tc._tick()

    assert len(made) == 2, f"预排 + finally 应各建一个，实得 {len(made)}"
    assert made[0].cancelled is True, "★第二个必须取消第一个，否则每拍翻倍"
    assert made[1].cancelled is False, "当前这一拍不能自己取消自己"


class _BlockingCore:
    """每次 emit 都挂住，直到测试放行；用于制造「两拍同时在跑」。"""

    def __init__(self):
        self.gates = []

    def emit(self, **_kw):
        ev = threading.Event()
        self.gates.append(ev)
        ev.wait(5.0)
        return {"ok": True}


def test_14_stale_beat_cannot_release_takeover_guard():
    """卡死旧拍的迟到返回不得释放接管拍持有中的守卫（否则第三拍叠进来）。

    真并发驱动（不是把 `if` 复述一遍）：A 卡在 emit 里 → 租约过期 → B 接管并卡住
    → 放行 A → 此时守卫必须仍在 B 手里。
    """
    h = _mk_heart()
    core = _BlockingCore()
    h.pulse_core = core
    h._m374_dump_stall = lambda _held: "fake"

    ta = threading.Thread(target=h._trigger_heartbeat, daemon=True)
    ta.start()
    _deadline = time.time() + 5.0
    while len(core.gates) < 1 and time.time() < _deadline:
        time.sleep(0.01)
    assert len(core.gates) == 1, "A 拍没进到 emit"
    assert h._heartbeat_in_progress is True

    # 手工把 A 的租约推到过期（等价于 A 已卡死 100s）
    h._heartbeat_started_at -= 100.0

    tb = threading.Thread(target=h._trigger_heartbeat, daemon=True)
    tb.start()
    _deadline = time.time() + 5.0
    while len(core.gates) < 2 and time.time() < _deadline:
        time.sleep(0.01)
    assert len(core.gates) == 2, "B 拍没有接管（应已进到 emit）"

    core.gates[0].set()          # 放行「卡死后又返回」的旧拍 A
    ta.join(5.0)
    assert not ta.is_alive()
    assert h._heartbeat_in_progress is True, "★旧拍 A 的 finally 替 B 开了门"

    core.gates[1].set()          # 放行接管拍 B
    tb.join(5.0)
    assert not tb.is_alive()
    assert h._heartbeat_in_progress is False, "接管拍 B 收尾后必须释放"


def test_15_started_at_declared_in_init():
    """★字段必须在 __init__ 里声明：靠 getattr 兜底会掩盖初始化遗漏。"""
    import inspect

    _src = inspect.getsource(PH.PulseHeart.__init__)
    for _f in ("_heartbeat_started_at", "_m374_takeover", "_m374_takeover_streak",
               "_m374_beat_seq"):
        assert f"self.{_f}" in _src, f"__init__ 未声明 {_f}"


def test_16_guard_lock_is_module_level():
    """锁必须是**模块级**：单测常用 `__new__` 造实例（绕开 __init__），
    实例锁会让所有这类脚手架直接 AttributeError（本刀已踩过一次）。"""
    import importlib

    _tcmod = importlib.import_module("nucleus.chronos.TimeCore")
    for _mod, _name in ((PH, "_M374_GUARD_LOCK"),
                        (_tcmod, "_TIMER_LOCK"),
                        (_tcmod, "_M374_TICK_LOCK")):
        _lock = getattr(_mod, _name, None)
        assert _lock is not None, f"{_name} 缺失"
        assert hasattr(_lock, "acquire") and hasattr(_lock, "release"), f"{_name} 不是锁"

