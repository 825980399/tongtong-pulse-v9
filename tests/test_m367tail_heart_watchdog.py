#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""破零前置阻碍：心跳重入守卫卡死时看门狗永久 skip 的修复（367 后续）。

实测证据链（只读取证）：
  · `data/monitor/health_snapshot.json` mtime 停在 10-25 04:57:55，`beat_count` 冻结 1800；
  · 但 04:58 之后 `time.tick` 仍有 21 次 ⇒ TimeCore 在跑、PulseHeart 心跳链单方面死掉；
  · 04:55:28/04:56:28/04:57:29 连续三次 `内存=99%→100%` + 负载 critical；
  · ⇒ 某拍心跳体内 I/O 卡住 ⇒ `_heartbeat_in_progress` 永远 True（finally 不执行）
  · ⇒ 看门狗 `if self._heartbeat_in_progress: return skip_in_progress` **永久跳过**
  · ⇒ 心跳死 ⇒ W1 年龄爆炸 ⇒ w1=stale ⇒ 抽验 provisional(非 PASS)
  · ⇒ `_m340_judge_action_open` streak 恒 0/3 ⇒ O3 开闸 NOT_RUN
  · ⇒ pending 的 O3 永远 6/7 步 ⇒ requires_confirmation 永不放行 ⇒ **破零不可能**。
"""
from __future__ import annotations

import io
import os
import re
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from organs.body.PulseHeart import PulseHeart  # noqa: E402

_SRC = os.path.join(_ROOT, "organs", "body", "PulseHeart.py")


def _make_heart(*, in_progress: bool, started_ago: float | None,
                interval: float = 30.0, last_beat_ago: float = 120.0):
    """构造一个**不走 __init__** 的轻量心跳对象（不启线程池、不连 InfoField）。"""
    h = PulseHeart.__new__(PulseHeart)
    now = time.time()
    h.is_running = True
    h.current_interval = interval
    h._last_beat_time = now - last_beat_ago
    h._beat_count = 1800
    h._heartbeat_in_progress = in_progress
    if started_ago is not None:
        h._heartbeat_started_at = now - started_ago
    h._rescheduled = []
    h._logs = []

    def _fake_schedule():
        h._rescheduled.append(time.time())

    def _fake_log(level, msg, **kw):
        h._logs.append((level, msg, kw))

    h._schedule_next_beat = _fake_schedule
    h._log = _fake_log
    return h


class TestStuckGuard:
    def test_01_stuck_guard_is_force_reset(self):
        """卡死（守卫持有 10×interval）⇒ 强制复位 + 续链 + ERROR 留痕。"""
        h = _make_heart(in_progress=True, started_ago=300.0, interval=30.0)
        r = h.m365_beat_watchdog()
        assert r["action"] != "skip_in_progress", r
        assert h._heartbeat_in_progress is False, "必须强制复位守卫"
        assert h._heartbeat_started_at == 0.0
        assert len(h._rescheduled) == 1, "必须续链一次（且只一次）"
        assert any("367链" in m for _, m, _ in h._logs if isinstance(m, str))
        assert any(kw.get("error_code") == "heartbeat_guard_stuck"
                   for _, _, kw in h._logs)

    def test_02_normal_slow_beat_not_preempted(self):
        """正常慢拍（守卫持有 1×interval，未过 3×）⇒ 仍然跳过，不插队。"""
        h = _make_heart(in_progress=True, started_ago=30.0, interval=30.0)
        r = h.m365_beat_watchdog()
        assert r["action"] == "skip_in_progress", r
        assert h._heartbeat_in_progress is True, "不该被复位"
        assert h._rescheduled == []

    def test_03_boundary_just_under_threshold(self):
        """边界内侧（2.9×interval）⇒ 仍跳过；不因临界抖动误杀。"""
        h = _make_heart(in_progress=True, started_ago=86.0, interval=30.0)
        r = h.m365_beat_watchdog()
        assert r["action"] == "skip_in_progress", r
        assert h._rescheduled == []

    def test_04_boundary_just_over_threshold(self):
        """边界外侧（3.1×interval）⇒ 强制复位。"""
        h = _make_heart(in_progress=True, started_ago=93.0, interval=30.0)
        r = h.m365_beat_watchdog()
        assert r["action"] != "skip_in_progress", r
        assert h._heartbeat_in_progress is False

    def test_05_no_started_at_is_conservative(self):
        """守卫在跑但没有进入时刻（旧对象/未设）⇒ 保守跳过，**不误杀**。"""
        h = _make_heart(in_progress=True, started_ago=None, interval=30.0)
        r = h.m365_beat_watchdog()
        assert r["action"] == "skip_in_progress", r
        assert h._heartbeat_in_progress is True
        assert h._rescheduled == []

    def test_06_not_in_progress_reschedules_on_gap(self):
        """常规判据未变：没在跑且 gap > 2×interval ⇒ 续链。"""
        h = _make_heart(in_progress=False, started_ago=None,
                        interval=30.0, last_beat_ago=120.0)
        r = h.m365_beat_watchdog()
        assert r.get("action") != "skip_in_progress"
        assert len(h._rescheduled) == 1

    def test_07_counter_accumulates(self):
        """强制复位次数计数（供观测；两次卡死 ⇒ 2）。"""
        h = _make_heart(in_progress=True, started_ago=300.0, interval=30.0)
        h.m365_beat_watchdog()
        h._heartbeat_in_progress = True
        h._heartbeat_started_at = time.time() - 300.0
        h.m365_beat_watchdog()
        assert getattr(h, "_m367_stuck_forced", 0) == 2


class TestSkipVisibility:
    def test_11_slow_guard_warns(self):
        """守卫已持有 2×interval（慢但未到 3×）⇒ 跳过**但必须告警**（可见性）。

        这是 17 小时断链无人察觉的直接原因：跳过分支过去完全静默。
        """
        h = _make_heart(in_progress=True, started_ago=60.0, interval=30.0)
        r = h.m365_beat_watchdog()
        assert r["action"] == "skip_in_progress"
        assert any(kw.get("error_code") == "heartbeat_guard_held" for _, _, kw in h._logs),             "慢持有必须留痕：%r" % (h._logs,)
        assert getattr(h, "_m367_skip_warn", 0) == 1

    def test_12_fast_guard_stays_quiet(self):
        """正常快拍（< 1×interval）⇒ 不告警（不刷屏）。"""
        h = _make_heart(in_progress=True, started_ago=5.0, interval=30.0)
        r = h.m365_beat_watchdog()
        assert r["action"] == "skip_in_progress"
        assert not any(kw.get("error_code") == "heartbeat_guard_held" for _, _, kw in h._logs)

    def test_13_warn_throttled_to_60s(self):
        """60s 内重复调用只告警一次（不刷屏）。"""
        h = _make_heart(in_progress=True, started_ago=60.0, interval=30.0)
        h.m365_beat_watchdog()
        h.m365_beat_watchdog()
        h.m365_beat_watchdog()
        assert getattr(h, "_m367_skip_warn", 0) == 1, "60s 内应只告警 1 次"


class TestStampsWired:
    def test_08_enter_sets_started_at(self):
        """进入心跳时必须写进入时刻（否则看门狗无法判卡死）。

        ★374刀1：进入块包进 `with _M374_GUARD_LOCK:`，时刻改取**入口处的一次
        读数** `_m374_now`（同一拍只读一次钟，保证"守卫判据用的钟"与"记下的钟"
        是同一个数）。判据按语义守：进入段内必须把 `_heartbeat_started_at` 写成
        真实时间值，来源允许 `_m374_now` / `time()`；写成常量即失败。
        """
        src = io.open(_SRC, encoding="utf-8").read()
        i = src.index("with _M374_GUARD_LOCK:")
        seg = src[i:i + 500]
        assert "self._heartbeat_in_progress = True" in seg, "进入时未置位"
        assert re.search(
            r"self\._heartbeat_started_at = (_m374_now|time\(\)|time\.time\(\))", seg
        ), "进入时未记真实时刻（不得写常量/0）"

    def test_09_finally_clears_started_at(self):
        """finally 里必须清零（否则下一拍会被误判成卡死）。

        ★374刀1：释放改成「按令牌序号认人」——只有仍持有租约的那一拍能释放，
        卡死旧拍的迟到返回不得替接管拍开门。判据比原先的"两行相邻"更严：
        要求在**令牌守卫内**同时释放标志与清零时刻。
        """
        src = io.open(_SRC, encoding="utf-8").read()
        # 令牌判据在两处出现（info_field 早退 + finally），要盯**最后**那处＝finally
        i = src.rindex("if _m374_tok is not None and self._m374_beat_seq == _m374_tok:")
        seg = src[i:i + 600]
        assert "self._heartbeat_in_progress = False" in seg, "finally 未释放守卫"
        assert "self._heartbeat_started_at = 0.0" in seg, "finally 未清零时刻"

    def test_10_no_dual_timer_risk(self):
        """强制复位只续链**一次**（双定时器是原注释明确要防的）。"""
        h = _make_heart(in_progress=True, started_ago=300.0, interval=30.0)
        h.m365_beat_watchdog()
        assert len(h._rescheduled) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
