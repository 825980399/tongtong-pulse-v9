# -*- coding: utf-8 -*-
"""387刀1：内存自动重启保命开关（P0）的门禁用例。

背景：386 晚内存 12GB→36GB 卡死，三路自救全断 ⇒ 先开自动重启这一路。
"""

import logging
import sys
import types
from unittest.mock import patch

import config  # noqa: E402


class _FakeField:
    def __init__(self, rss_mb: float):
        self._rss = rss_mb

    def _m169_memory_pressure(self):
        return {"ok": True, "process_rss_mb": self._rss,
                "system_percent": 60.0, "reason": ""}


def _make_collector(info_field):
    from organs.core.PulseMetricsCollector import PulseMetricsCollector
    _c = PulseMetricsCollector.__new__(PulseMetricsCollector)
    _c.info_field = info_field
    _c._mem_restart_streak = 0
    _c._logger = logging.getLogger("test.m387.restart")
    return _c


def test_m387_d1_switch_is_on():
    """★核心：`ENABLE_MEMORY_AUTO_RESTART` 必须是 **True**（保命）。"""
    assert config.ENABLE_MEMORY_AUTO_RESTART is True


def test_m387_d1_thresholds():
    """★核心：阈值 12GB、冷却 6 小时（票面指定值）。"""
    assert float(config.MEMORY_AUTO_RESTART_RSS_MB) == 12288.0
    assert float(config.MEMORY_RESTART_COOLDOWN_HOURS) == 6.0


def test_m387_d1_threshold_above_gc_line():
    """★守卫：重启线必须**高于** GC 线（8192），否则"先回收再重启"的梯度没了。

    原设计注释写「= GC 线 8192 + 1GB 梯度」；抬到 12288 后梯度变成 4GB，
    仍满足"先 GC 救灾、扛不住才重启"——这条钉的是**顺序**，不是具体数值。
    """
    assert float(config.MEMORY_AUTO_RESTART_RSS_MB) > float(config.MEMORY_AUTO_GC_RSS_MB)


def test_m387_d1_armed_after_two_consecutive_over():
    """★核心：RSS 连续 2 次越阈 ⇒ armed（与票面「连续 2 次 GC 仍越阈」一致）。

    判据走**真实阈值**（12288），不是 m176 里的 9216 stub。
    """
    with patch.dict(sys.modules, {"config": config}):
        _c = _make_collector(_FakeField(20000.0))     # 20GB >> 12288
        _r1 = _c._collect_memory_pressure()
        assert _r1["restart_count"] == 1
        assert _r1["restart_armed"] is False, "第 1 次越阈不该 armed"
        _r2 = _c._collect_memory_pressure()
        assert _r2["restart_armed"] is True, _r2
        assert _c._last_restart_armed is True


def test_m387_d1_not_armed_below_new_threshold():
    """★反向：RSS 在旧阈值(9216)与新阈值(12288)**之间** ⇒ **不** armed。

    这正是抬阈值的意义：9GB~12GB 这个区间原本会触发重启，现在只报警不重启，
    避免正常波动误杀。
    """
    with patch.dict(sys.modules, {"config": config}):
        _c = _make_collector(_FakeField(10000.0))     # 10GB：> 9216 但 < 12288
        for _ in range(3):
            _r = _c._collect_memory_pressure()
        assert _r["restart_armed"] is False, _r
        assert _r["restart_count"] == 0, _r


def test_m387_d1_maybe_restart_returns_true_when_enabled():
    """★核心（票面验收第 5 条）：开关开 + 桶未锁 ⇒ `_maybe_memory_restart` 返 True。"""
    import main

    _pm_inst = types.SimpleNamespace(
        memory_restart_bucket_state=lambda: {"max": 3, "locked": False, "remain_sec": 0},
        bump_restart_counter_for_memory_restart=lambda: 1,
    )
    _f = types.SimpleNamespace(_patch_restart_requested=False)
    with patch.dict(sys.modules, {"config": config}), \
         patch.object(main, "_spawn_self_restart") as _spawn, \
         patch("nucleus.reasoning.PatchManager.PatchManager", return_value=_pm_inst):
        _rc = main._maybe_memory_restart(_f)
    assert _rc is True
    _spawn.assert_called_once()


def test_m387_d1_cooldown_reads_own_key():
    """冷却窗口读 `MEMORY_RESTART_COOLDOWN_HOURS`（自己的键），不是回退到补丁桶。"""
    import nucleus.reasoning.PatchManager as _pm_mod
    _pm = _pm_mod.PatchManager.__new__(_pm_mod.PatchManager)
    _pm._patch_dir = ""
    _pm._max_restart_count = 3
    with patch.dict(sys.modules, {"config": config}):
        assert _pm._memory_restart_cooldown_hours() == 6.0


def test_m387_d1_cooldown_only_applies_after_bucket_lock():
    """★语义边界（防误读）：冷却**只在计数桶达 3 次上限被锁后**才生效，
    前 3 次重启之间没有最小间隔。缩到 6h 影响的是「锁死后多久能再自愈」。

    这条不是测代码，是把"改这个键到底改了什么"钉住 ——
    否则后人会以为 6h 是「两次重启至少隔 6 小时」。
    """
    _src = _pm_src()
    assert "locked" in _src and "remain_sec" in _src
    assert "MEMORY_RESTART_COOLDOWN_HOURS" in _src


def _pm_src() -> str:
    import io
    import os
    _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "nucleus", "reasoning", "PatchManager.py")
    return io.open(_p, encoding="utf-8", errors="ignore").read()


def test_m387_d1_restart_path_preserves_snapshot():
    """★数据安全守卫：内存重启走 `sys.exit(0)`（不是 os._exit），
    SystemExit 会触发主循环 finally ⇒ `framework.stop()` 保存快照。

    若哪天改成 `os._exit` 或 `sys.exit` 被移到 finally 之外，
    14146 个知识库节点就会随重启丢失 ⇒ 这条必须红。
    """
    import io
    import os
    _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "main.py")
    _s = io.open(_p, encoding="utf-8", errors="ignore").read()
    _i = _s.index("if _maybe_memory_restart(framework):")
    assert "sys.exit(0)" in _s[_i:_i + 120], "内存重启必须走 sys.exit(0)（触发清理）"
    assert "os._exit" not in _s[_i:_i + 200]
