# -*- coding: utf-8 -*-
"""396 刀1（P0）验收：心跳 Timer 链 livelock 收口。

事故锚点（洞鉴391 实锤）：
  · 看门狗节拍 `max(5.0, iv/2.0)` ＝ 5.0s，心跳周期实测 9.5s；
  · `_schedule_next_beat()` **无条件 cancel 在途 Timer** 再新建；
  · ⇒ gap 一旦越过 2×iv，看门狗每 5s 就"救援"一次，每次都在 9.5s 到期前把 Timer
    取消重建 ⇒ 定时器永远活不到 fire ⇒ 心跳永久停摆；而每次救援都返回
    `rescheduled` ＝ 成功（安慰剂）。
  · 现场：beat 冻在 35，gap 从 19s 单调涨到 3000s+，看门狗 353/484 次重建 Timer
    全部"成功"。

本文件按任务书四条判据组织，另加两条行为级反演（真 Timer 跑，不复述 if）：
  · livelock 反演：救援比心跳快时，旧实现永不 fire、新实现必须 fire；
  · 预排反演：本拍就是当前 Timer 时，`_schedule_next_beat` 必须真的新建（否则链断）。
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import organs.body.PulseHeart as PH
from organs.body.PulseHeart import PulseHeart


def _mk_bare(**kw):
    """不带任何打桩的轻量实例：只给 `_schedule_next_beat` 真正需要的属性。

    ★与 test_m374/test_m365 的 `_mk_heart` 不同，这里**不替换** `_schedule_next_beat`
    与 `_trigger_heartbeat`——本刀测的就是这两个方法本身，打桩等于把被测对象换掉。
    """
    h = PulseHeart.__new__(PulseHeart)
    h._logs = []
    h._log = lambda level, msg, **k: h._logs.append((level, msg, k))
    h.is_running = True
    h.organ_name = "心脏"
    h.current_interval = 30.0
    h.base_interval = 30.0
    h.min_interval = 3.0
    h.max_interval = 60.0
    h._timer_lock = threading.Lock()
    h._next_timer = None
    h._beat_count = 0
    h._last_beat_time = time.time()
    h._heartbeat_in_progress = False
    h._heartbeat_started_at = 0.0
    h._m374_takeover = 0
    h._m374_takeover_streak = 0
    h._m374_beat_seq = 0
    h._m374_suspend = 0
    h._m374_last_dump = None
    h._m365_wd_fail = 0
    h._survival_orchestrator = None
    h.pulse_core = None
    h.info_field = None
    h._scheduled_tasks = {}
    h._status_sweep_counter = 0
    h._emit = lambda *a, **k: None
    h._calculate_heart_rate = lambda: h.current_interval
    # ★396 新增字段显式给齐（不靠 getattr 兜底：__init__ 漏声明要被测试抓住，
    #   而不是被测试自己的兜底掩盖）
    h._m396_sched_created = 0
    h._m396_sched_skipped = 0
    h._m396_rescue_pending = None
    h._m396_rescue_ok = 0
    h._m396_rescue_failed = 0
    h._m396_enter_fp = None
    h._m396_enter_beat = -1
    for _k, _v in kw.items():
        setattr(h, _k, _v)
    return h


def _mk_heart(**kw):
    """带 `_log` 收集、但不打桩调度方法的实例（看门狗用例用）。"""
    return _mk_bare(**kw)


def _live_timer(interval: float = 60.0) -> threading.Timer:
    t = threading.Timer(interval, lambda: None)
    t.daemon = True
    t.start()
    return t


def _codes(h, code):
    return [m for _l, m, k in h._logs if k.get("error_code") == code]


# ============================================================ 测1：存活 ⇒ 不 cancel
def test_01_live_timer_is_not_cancelled():
    """在途 Timer 存活时，`_schedule_next_beat` 不得 cancel、不得替换它。

    这是本刀的**核心判据**：旧实现无条件 `cancel()`，只要调用方比 Timer 到期更频繁，
    定时器永远活不到 fire。现在必须原样留着。
    """
    h = _mk_bare(current_interval=60.0)
    old = _live_timer(60.0)
    h._next_timer = old
    try:
        created = h._schedule_next_beat()
        assert created is False, "存活时不得新建（新建前必然 cancel，就是它掐死链）"
        assert h._next_timer is old, "在途 Timer 被替换了"
        assert old.finished.is_set() is False, "在途 Timer 被 cancel 了（livelock 根因）"
        assert h._m396_sched_skipped == 1
        assert h._m396_sched_created == 0
    finally:
        old.cancel()


# ============================================================ 测2：死 Timer ⇒ 新建
def test_02_dead_timer_triggers_recreate():
    """在途 Timer 已死（None / 已结束 / 已被取消）⇒ 必须新建，链才接得上。"""
    h = _mk_bare(current_interval=60.0)

    # ① None
    assert h._schedule_next_beat() is True
    t1 = h._next_timer
    assert isinstance(t1, threading.Timer) and t1.is_alive()

    # ② 已结束（interval 极短，跑完即退出）
    t1.cancel()
    t1.join(2.0)
    assert t1.is_alive() is False, "夹具前提：这条 Timer 必须已经结束"
    h.current_interval = 60.0
    assert h._schedule_next_beat() is True
    t2 = h._next_timer
    assert t2 is not t1 and t2.is_alive(), "死 Timer 之后必须建出新的活 Timer"
    t2.cancel()


def test_03_cancelled_timer_counts_as_dead():
    """已 cancel 但线程尚未退出的 Timer 也必须判死（否则链断了没人接）。"""
    h = _mk_bare(current_interval=60.0)
    t = _live_timer(60.0)
    h._next_timer = t
    t.cancel()
    t.join(2.0)  # cancel 会 set finished，线程随即退出
    assert t.is_alive() is False
    assert h._schedule_next_beat() is True
    h._next_timer.cancel()


def test_04_own_timer_still_creates():
    """★预排反演：本拍**就是**在途那个 Timer ⇒ 必须放行新建。

    如果这里也判"存活就不建"，`_m374_schedule_ahead()` 在第一次到期后就再也排不出
    下一拍，链当场断——比原缺陷更糟。判据靠 `_cur is current_thread()`，不是只看存活。
    """
    h = _mk_bare(current_interval=0.05)
    made: list[bool] = []

    def _cb():
        made.append(h._schedule_next_beat())
        if len(made) >= 3:
            h._cancel_timer()

    h._trigger_heartbeat = _cb
    assert h._schedule_next_beat() is True

    _deadline = time.time() + 5.0
    while len(made) < 3 and time.time() < _deadline:
        time.sleep(0.02)
    assert len(made) >= 3, f"Timer 链未能连续自续（只跑了 {len(made)} 拍）"
    assert all(made), f"预排路径必须全部新建成功，实得 {made}"


# ============================================================ 测3：看门狗节拍 ≥ 心跳周期
class _Clock:
    """只实现 `_m365_watchdog_loop` 用到的 time 面；记录每轮实际睡多久。"""

    def __init__(self):
        self.t = 1_000_000.0
        self.naps: list[float] = []
        self.stop_after = 2

    def time(self):
        return self.t

    def sleep(self, sec):
        self.naps.append(sec)
        self.t += sec
        if len(self.naps) >= self.stop_after:
            raise _StopLoop

    def strftime(self, *_a, **_k):
        return "fake"


class _StopLoop(Exception):
    """从假时钟里打断看门狗循环（等价于线程被要求退出）。"""


@pytest.mark.parametrize("iv", [9.5, 10.0, 30.0, 60.0])
def test_05_watchdog_nap_is_slower_than_heartbeat(monkeypatch, iv):
    """看门狗节拍必须**慢于**心跳周期（任务书判据：≥ 心跳周期）。

    为什么是"慢于"而不是"快于"：救援者比被救援者快，就永远观察不到"下一拍来没来"，
    只能看到"gap 还大"，于是不停重置 ⇒ livelock。旧公式 `max(5.0, iv/2.0)` 在
    iv=9.5 时给出 5.0，正是本事故的数值。
    """
    h = _mk_heart(current_interval=iv)
    h._m365_wd_running = True
    h.m365_beat_watchdog = lambda: {"ok": True, "action": "none"}
    h._m374_wd_trace = lambda: None
    clock = _Clock()
    monkeypatch.setattr(PH, "time", clock)

    with pytest.raises(_StopLoop):
        h._m365_watchdog_loop()

    assert clock.naps, "看门狗没睡过（循环体没进）"
    assert clock.naps[0] > iv, f"节拍 {clock.naps[0]}s 不慢于心跳 {iv}s ⇒ 仍会 livelock"
    assert clock.naps[0] >= PH._M396_WD_NAP_MIN - 1e-9, "下限 15s 未生效"


def test_06_old_formula_would_fail_new_judgement():
    """反证：旧公式在实测周期 9.5s 上必然不满足「慢于心跳」——防止有人改回去。"""
    iv = 9.5
    assert max(5.0, iv / 2.0) < iv, "旧公式在 9.5s 上本就比心跳快（本事故数值）"


# ============================================================ 测4：beat 不增 ⇒ 不报成功
def test_07_alive_chain_refuses_rescue_and_reports_not_ok():
    """★核心：链还活着（Timer 存活）+ beat 不前进 ⇒ 拒绝重建、`ok=False`。

    旧实现在这里会 cancel 重建并返回 `rescheduled`/`ok=True`——既掐死链又谎报成功。
    """
    h = _mk_heart(current_interval=9.5, _beat_count=35)
    h._last_beat_time = time.time() - 100.0  # gap 远超 2×9.5
    live = _live_timer(60.0)
    h._next_timer = live
    try:
        r = h.m365_beat_watchdog()
        assert r["action"] == "no_rescue_needed", r
        assert r["ok"] is False, "beat 没前进就不能报成功"
        assert r["beat"] == 35
        assert r["created"] is False
        assert h._next_timer is live and live.finished.is_set() is False, "链被重建/掐断了"
        assert _codes(h, "heartbeat_chain_alive_no_rescue"), h._logs
        assert not _codes(h, "heartbeat_rescue_pending"), "不该发出救援"
    finally:
        live.cancel()


def test_08_rescue_pending_is_not_success():
    """真的需要救援（没有活 Timer）⇒ 只记 pending，`ok=False`，结果留待核对。"""
    h = _mk_heart(current_interval=9.5, _beat_count=35)
    h._last_beat_time = time.time() - 100.0
    h._next_timer = None
    try:
        r = h.m365_beat_watchdog()
        assert r["action"] == "rescue_pending", r
        assert r["ok"] is False, "动作发出≠成功"
        assert r["created"] is True
        assert h._m396_rescue_pending and h._m396_rescue_pending["beat_before"] == 35
    finally:
        h._cancel_timer()


# ============================================================ 延迟确认（下游观测量）
def test_09_rescue_confirmed_only_when_beat_advances():
    """下一次看门狗醒来，beat 前进了 ⇒ 记 rescue_ok 并清 pending。"""
    now = time.time()
    h = _mk_heart(current_interval=9.5, _beat_count=36)
    h._last_beat_time = now - 1.0  # 心跳已恢复，gap 正常
    h._m396_rescue_pending = {"beat_before": 35, "at": now - 20.0,
                             "gap_before": 100.0, "deadline": now + 30.0}
    r = h.m365_beat_watchdog()
    assert r["action"] == "none"
    assert h._m396_rescue_ok == 1
    assert h._m396_rescue_pending is None
    assert h._m396_rescue_failed == 0
    assert any("[396链] 续链已确认生效" in m for _l, m, _k in h._logs), h._logs


def test_10_rescue_failed_when_beat_frozen_past_deadline():
    """过了确认窗口 beat 仍不动 ⇒ 记 rescue_failed + ERROR（不谎报成功）。"""
    now = time.time()
    h = _mk_heart(current_interval=9.5, _beat_count=35)
    h._last_beat_time = now - 100.0
    h._next_timer = _live_timer(60.0)  # 让后续分支走 no_rescue_needed，别混入新救援
    h._m396_rescue_pending = {"beat_before": 35, "at": now - 60.0,
                             "gap_before": 100.0, "deadline": now - 1.0}
    try:
        h.m365_beat_watchdog()
        assert h._m396_rescue_failed == 1
        assert h._m396_rescue_ok == 0
        assert h._m396_rescue_pending is None
        _msgs = " ".join(m for _l, m, _k in h._logs)
        assert "续链**失败**" in _msgs, _msgs
        assert "问题在早退" in _msgs, "必须点明问题不在链（排障方向）"
        assert _codes(h, "heartbeat_rescue_failed")
    finally:
        h._next_timer.cancel()


# ============================================================ 进入留痕
def test_11_enter_trace_throttled_but_state_change_breaks_through():
    """进入留痕：状态不变按 60 拍节流，状态指纹变化立即打一条。"""
    h = _mk_heart(current_interval=9.5)
    h._next_timer = _live_timer(60.0)
    try:
        h._m396_trace_enter()
        h._m396_trace_enter()
        h._m396_trace_enter()
        _ent = [m for _l, m, _k in h._logs if "[396刀1] 心跳进入" in m]
        assert len(_ent) == 1, f"状态不变时只应 1 条，实得 {len(_ent)}"

        h._beat_count = 61  # 跨过 60 拍节流窗
        h._m396_trace_enter()
        assert len([m for _l, m, _k in h._logs if "[396刀1] 心跳进入" in m]) == 2

        h.is_running = False  # 指纹变化必须立即留痕
        h._m396_trace_enter()
        _ent = [m for _l, m, _k in h._logs if "[396刀1] 心跳进入" in m]
        assert len(_ent) == 3
        assert "is_running=False" in _ent[-1]
    finally:
        h._next_timer.cancel()


def test_12_trace_is_first_line_of_trigger():
    """任务书要求「首行」：留痕必须在 `is_running` 早退判断**之前**。

    否则 `is_running=False` 那种停摆（正是现场怀疑的成因之一）一行日志都没有。
    """
    import inspect

    _src = inspect.getsource(PH.PulseHeart._trigger_heartbeat)
    assert "self._m396_trace_enter()" in _src
    assert _src.index("self._m396_trace_enter()") < _src.index("if not self.is_running:"), \
        "留痕必须在 is_running 早退之前（否则该路径再次静默）"


# ============================================================ livelock 行为级反演
def test_13_livelock_reversed_when_rescue_faster_than_beat():
    """★真 Timer 反演：救援频率高于心跳时，新实现必须仍能 fire。

    数值按现场比例缩小 100 倍：心跳 0.05s，救援每 0.02s 一次（对应 9.5s / 5s）。
    旧实现每轮 cancel+新建 ⇒ 计时器永远差 0.05s ⇒ 0.5s 内一次都不 fire。
    """
    h = _mk_bare(current_interval=0.05)
    fired = threading.Event()
    hits: list[int] = []

    def _cb():
        hits.append(1)
        fired.set()

    h._trigger_heartbeat = _cb
    assert h._schedule_next_beat() is True

    _deadline = time.time() + 0.6
    while time.time() < _deadline and not fired.is_set():
        time.sleep(0.02)
        h._schedule_next_beat()      # 等价于看门狗"救援"

    assert fired.is_set(), (
        "Timer 在救援节拍（0.02s）下没能 fire —— livelock 未修好；"
        f"created={h._m396_sched_created} skipped={h._m396_sched_skipped}")
    assert h._m396_sched_skipped > 0, "救援应当被幂等拒绝过（否则说明判据没生效）"
    h._cancel_timer()


# ============================================================ 静态守卫
def test_14_fields_declared_in_init():
    """396 新字段必须在 `__init__` 显式声明（靠 getattr 兜底会掩盖初始化遗漏）。"""
    import inspect

    _src = inspect.getsource(PH.PulseHeart.__init__)
    for _f in ("_m396_sched_created", "_m396_sched_skipped", "_m396_rescue_pending",
               "_m396_rescue_ok", "_m396_rescue_failed", "_m396_enter_fp",
               "_m396_enter_beat"):
        assert f"self.{_f}" in _src, f"__init__ 未声明 {_f}"


def test_15_schedule_returns_bool_and_has_no_unconditional_cancel():
    """静态守卫：`_schedule_next_beat` 里不允许出现「无条件 cancel」。

    它必须被「存活判定」包住——否则任何一次重构把 `if` 挪掉，livelock 就回来了。
    """
    import inspect

    _src = inspect.getsource(PH.PulseHeart._schedule_next_beat)
    _i_alive = _src.index("_m396_timer_alive")
    _i_cancel = _src.index("_cur.cancel()")
    assert _i_alive < _i_cancel, "cancel 必须在存活判定之后"
    assert "return False" in _src and "return True" in _src, "必须返回是否新建"
