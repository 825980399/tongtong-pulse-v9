# -*- coding: utf-8 -*-
"""176批段2·commit2（调用面）门控单测：内存自动重启开关接线。

验收判据：
  1) ENABLE_MEMORY_AUTO_RESTART=True 且 RSS 连续 2 次 > MEMORY_AUTO_RESTART_RSS_MB
     → restart_armed=True、restart_count=2、_last_restart_armed=True；
  2) 默认 ENABLE_MEMORY_AUTO_RESTART=False → restart_armed=False（零回归）；
  3) RSS 未越阈 → restart_armed=False、restart_count=0；
  4) PatchManager 共用 3 次上限计数桶：bump 递增 restart_count.txt，
     越上限后 memory_restart_bucket_state().locked=True 且写入冷却起点；
  5) _maybe_memory_restart（main）：默认关→False；开+桶未锁→触发(_spawn_self_restart 调用一次)；
     补丁重启在途(mutex)→False 不触发；桶锁→False 不触发。

配置读取走 sys.modules["config"]（与产品代码一致），测试以 stub 注入，
不依赖真实 config 热路径 import。
"""

import sys
import types
from unittest.mock import patch


# --------------------------------------------------------------------------
# 1) PulseMetricsCollector 决策三态
# --------------------------------------------------------------------------
def _make_collector(info_field):
    from organs.core.PulseMetricsCollector import PulseMetricsCollector
    _c = PulseMetricsCollector.__new__(PulseMetricsCollector)
    _c.info_field = info_field
    _c._mem_restart_streak = 0
    return _c


class _FakeFieldHigh:
    def _m169_memory_pressure(self):
        return {"ok": True, "process_rss_mb": 10000.0, "system_percent": 60.0,
                "reason": ""}


class _FakeFieldLow:
    def _m169_memory_pressure(self):
        return {"ok": True, "process_rss_mb": 1000.0, "system_percent": 20.0,
                "reason": ""}


def test_restart_armed_after_two_consecutive_over():
    _stub = types.SimpleNamespace(
        ENABLE_MEMORY_AUTO_GC=False,
        MEMORY_AUTO_GC_RSS_MB=8192.0,
        ENABLE_MEMORY_AUTO_RESTART=True,
        MEMORY_AUTO_RESTART_RSS_MB=9216.0,
        MEMORY_RESTART_COOLDOWN_HOURS=24.0,
    )
    with patch.dict(sys.modules, {"config": _stub}):
        _c = _make_collector(_FakeFieldHigh())
        _r1 = _c._collect_memory_pressure()
        assert _r1["restart_armed"] is False, _r1  # 第 1 次越阈，未达连续 2 次
        assert _r1["restart_count"] == 1, _r1
        _r2 = _c._collect_memory_pressure()
        assert _r2["restart_armed"] is True, _r2  # 连续 2 次越阈
        assert _r2["restart_count"] == 2, _r2
        assert _c._last_restart_armed is True


def test_restart_not_armed_when_disabled():
    _stub = types.SimpleNamespace(
        ENABLE_MEMORY_AUTO_GC=False,
        MEMORY_AUTO_GC_RSS_MB=8192.0,
        ENABLE_MEMORY_AUTO_RESTART=False,  # 默认关
        MEMORY_AUTO_RESTART_RSS_MB=9216.0,
        MEMORY_RESTART_COOLDOWN_HOURS=24.0,
    )
    with patch.dict(sys.modules, {"config": _stub}):
        _c = _make_collector(_FakeFieldHigh())
        for _ in range(3):
            _r = _c._collect_memory_pressure()
        assert _r["restart_armed"] is False, _r  # 零回归
        assert _r["restart_count"] == 0
        assert getattr(_c, "_last_restart_armed", False) is False


def test_restart_not_armed_when_under_threshold():
    _stub = types.SimpleNamespace(
        ENABLE_MEMORY_AUTO_GC=False,
        MEMORY_AUTO_GC_RSS_MB=8192.0,
        ENABLE_MEMORY_AUTO_RESTART=True,
        MEMORY_AUTO_RESTART_RSS_MB=9216.0,
        MEMORY_RESTART_COOLDOWN_HOURS=24.0,
    )
    with patch.dict(sys.modules, {"config": _stub}):
        _c = _make_collector(_FakeFieldLow())
        _r = _c._collect_memory_pressure()
        assert _r["restart_armed"] is False, _r
        assert _r["restart_count"] == 0


# --------------------------------------------------------------------------
# 2) PatchManager 共用 3 次上限计数桶
# --------------------------------------------------------------------------
def _make_patch_mgr(tmp_path):
    """用 __new__ 仅初始化桶方法所需属性，避开 PatchManager 构造器的后台线程
    （否则非 daemon 线程会使 pytest 进程无法退出）。与 175 采集环测试同隔离范式。"""
    import nucleus.reasoning.PatchManager as _pm_mod
    _stub = types.SimpleNamespace(
        MEMORY_RESTART_COOLDOWN_HOURS=24.0,
        PATCH_RESTART_COOLDOWN_HOURS=24.0,
    )
    with patch.dict(sys.modules, {"config": _stub}):
        _pm = _pm_mod.PatchManager.__new__(_pm_mod.PatchManager)
        _pm._patch_dir = str(tmp_path)
        _pm._max_restart_count = 3
        return _pm


def test_bucket_state_initial(tmp_path):
    _pm = _make_patch_mgr(tmp_path)
    _st = _pm.memory_restart_bucket_state()
    assert _st["counter"] == 0
    assert _st["max"] == 3
    assert _st["locked"] is False
    assert "remain_sec" in _st


def test_bucket_bump_and_lock(tmp_path):
    _pm = _make_patch_mgr(tmp_path)
    assert _pm.bump_restart_counter_for_memory_restart() == 1
    assert _pm.bump_restart_counter_for_memory_restart() == 2
    assert _pm.bump_restart_counter_for_memory_restart() == 3
    _st = _pm.memory_restart_bucket_state()
    assert _st["counter"] == 3
    assert _st["locked"] is False  # 恰好达 3 次阈值，尚未超限（与 apply_all_pending 一致）
    # 第 4 次 → 超 3 次上限 → 锁
    assert _pm.bump_restart_counter_for_memory_restart() == 4
    _st2 = _pm.memory_restart_bucket_state()
    assert _st2["counter"] == 4
    assert _st2["locked"] is True
    # 越上限首次锁死应写入冷却起点
    import os
    assert os.path.exists(os.path.join(str(tmp_path), "restart_blocked_at.txt"))


def test_cooldown_hours_reads_own_key(tmp_path):
    _pm = _make_patch_mgr(tmp_path)
    assert _pm._memory_restart_cooldown_hours() == 24.0


# --------------------------------------------------------------------------
# 3) main._maybe_memory_restart 触发/互斥/零回归
# --------------------------------------------------------------------------
def _framework_stub():
    _f = types.SimpleNamespace()
    _f._patch_restart_requested = False
    return _f


def test_maybe_restart_returns_false_when_disabled():
    import main
    _stub = types.SimpleNamespace(ENABLE_MEMORY_AUTO_RESTART=False)
    _pm_inst = types.SimpleNamespace(
        memory_restart_bucket_state=lambda: {"max": 3, "locked": False, "remain_sec": 0},
        bump_restart_counter_for_memory_restart=lambda: 1,
    )
    with patch.dict(sys.modules, {"config": _stub}), \
         patch.object(main, "_spawn_self_restart") as _spawn, \
         patch("nucleus.reasoning.PatchManager.PatchManager", return_value=_pm_inst):
        _rc = main._maybe_memory_restart(_framework_stub())
    assert _rc is False
    _spawn.assert_not_called()


def test_maybe_restart_triggers_when_armed_and_bucket_open():
    import main
    _stub = types.SimpleNamespace(ENABLE_MEMORY_AUTO_RESTART=True)
    _pm_inst = types.SimpleNamespace(
        memory_restart_bucket_state=lambda: {"max": 3, "locked": False, "remain_sec": 0},
        bump_restart_counter_for_memory_restart=lambda: 1,
    )
    with patch.dict(sys.modules, {"config": _stub}), \
         patch.object(main, "_spawn_self_restart") as _spawn, \
         patch("nucleus.reasoning.PatchManager.PatchManager", return_value=_pm_inst):
        _rc = main._maybe_memory_restart(_framework_stub())
    assert _rc is True
    _spawn.assert_called_once()


def test_maybe_restart_mutex_with_patch_restart():
    import main
    _stub = types.SimpleNamespace(ENABLE_MEMORY_AUTO_RESTART=True)
    _pm_inst = types.SimpleNamespace(
        memory_restart_bucket_state=lambda: {"max": 3, "locked": False, "remain_sec": 0},
        bump_restart_counter_for_memory_restart=lambda: 1,
    )
    _f = _framework_stub()
    _f._patch_restart_requested = True  # 补丁重启在途
    with patch.dict(sys.modules, {"config": _stub}), \
         patch.object(main, "_spawn_self_restart") as _spawn, \
         patch("nucleus.reasoning.PatchManager.PatchManager", return_value=_pm_inst):
        _rc = main._maybe_memory_restart(_f)
    assert _rc is False
    _spawn.assert_not_called()


def test_maybe_restart_suppressed_when_bucket_locked():
    import main
    _stub = types.SimpleNamespace(ENABLE_MEMORY_AUTO_RESTART=True)
    _pm_inst = types.SimpleNamespace(
        memory_restart_bucket_state=lambda: {"max": 3, "locked": True, "remain_sec": 3600},
        bump_restart_counter_for_memory_restart=lambda: 4,
    )
    with patch.dict(sys.modules, {"config": _stub}), \
         patch.object(main, "_spawn_self_restart") as _spawn, \
         patch("nucleus.reasoning.PatchManager.PatchManager", return_value=_pm_inst):
        _rc = main._maybe_memory_restart(_framework_stub())
    assert _rc is False
    _spawn.assert_not_called()
