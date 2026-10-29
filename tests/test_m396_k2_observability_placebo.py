# -*- coding: utf-8 -*-
"""396 刀2（P1）验收：观测面安慰剂效应修复。

事故锚点（洞鉴391 / 391 现场快照）：
  · `data/monitor/health_snapshot.json` 里 `heartbeat.status = "搏动中"`，
    而同一份文件的 `beat_count = 35` 已经 46 分钟没动；
  · 原因不是"写错了"，是**写它的那条 handler 由心跳驱动**：心跳一停就再没人写，
    快照冻结在最后一帧，而那一帧永远乐观；
  · 同理，看门狗把「我调了 _schedule_next_beat」当成功（`rescheduled`），
    没有一条自愈代码的成功判据是"下游观测量变了"。

本文件守三条：
  ① status 由 `beat_gap < 3×interval` 现算，且**读时算**（不采信 payload / 缓存）；
  ② 心跳停摆时由看门狗把真相落盘（否则没人写）；
  ③ 器官数三口径（56/57/66）归一，只留 `declared` 一个主口径。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import organs.body.PulseHeart as PH
import organs.core.PulseMetricsCollector as PMC
from organs.body.PulseHeart import PulseHeart
from organs.core.PulseMetricsCollector import PulseMetricsCollector


# ============================================================ 采集器夹具
def _mk_collector(**kw):
    c = PulseMetricsCollector.__new__(PulseMetricsCollector)
    c._cache_lock = threading.Lock()
    c._obs_enabled = True
    # ★避免 `_on_heartbeat_observe` 走到写盘分支：它默认 interval=1 ⇒ 每拍都
    #   `_build_observability_snapshot()` + 写生产 data/monitor/health_snapshot.json。
    #   单测必须隔离（铁律：停机窗探针不许污染生产产物）。
    c._snapshot_interval = 1000
    c._cache_timestamp = 0.0
    c._cached_heartbeat = {}
    c._m396_last_beat_rx_at = 0.0
    c._m396_beat_rx_count = 0
    c._m396_explicit_stop = False
    c._cached_current_user = "访客"
    c._cached_organ_stats = {}
    c._cached_social_emotions = {}
    c._cached_top_interests = []
    c._cached_life_state = {}
    c._cached_emotion_state = {}
    c._dream_count = 0
    c._dream_last_topic = ""
    c._reflection_count = 0
    c._reflection_last_domain = ""
    c._liver_compress_count = 0
    c._liver_fuse_count = 0
    c._instinct_upgrade_count = 0
    c._headless_stats = {}
    c._snapshot_count = 0
    c._beat_count = 0
    c._metrics_history = []
    c._max_history = 50
    c._node_pool = None
    c.info_field = None
    c._logs = []
    c._log = lambda *a, **k: c._logs.append((a, k))
    for _k, _v in kw.items():
        setattr(c, _k, _v)
    return c


def _mk_heart(**kw):
    h = PulseHeart.__new__(PulseHeart)
    h._logs = []
    h._log = lambda level, msg, **k: h._logs.append((level, msg, k))
    h.is_running = True
    h.organ_name = "心脏"
    h.current_interval = 9.5
    h.base_interval = 9.5
    h._timer_lock = threading.Lock()
    h._next_timer = None
    h._beat_count = 35
    h._last_beat_time = time.time()
    h._heartbeat_in_progress = False
    h._heartbeat_started_at = 0.0
    h._scheduled_tasks = {}
    h._emit = lambda *a, **k: None
    for _k, _v in kw.items():
        setattr(h, _k, _v)
    return h


# ============================================================ ① status 按 gap 现算
def test_01_status_alive_when_gap_small():
    c = _mk_collector()
    c._m396_last_beat_rx_at = time.time() - 1.0
    _s, _gap = c._m396_heartbeat_status(9.5)
    assert _s == "搏动中", _s
    assert _gap is not None and _gap < 3 * 9.5


def test_02_status_stalls_when_gap_exceeds_3x_interval():
    """★核心判据：心跳停 46 分钟后，status 必须不再显示"搏动中"。"""
    c = _mk_collector()
    c._m396_last_beat_rx_at = time.time() - 46 * 60
    _s, _gap = c._m396_heartbeat_status(9.5)
    assert "停搏" in _s, _s
    assert "搏动中" not in _s
    assert _gap is not None and _gap >= 3 * 9.5
    # 文案必须带读数（否则又变成一句没有信息量的常量）
    assert "46" in _s or "276" in _s or "41" in _s or "40" in _s, f"停搏文案缺读数：{_s}"


def test_03_never_received_is_not_zero():
    """从未收到心跳 ⇒ `gap_s is None`（没采到），与"gap=0"分两个字段（铁律276）。"""
    c = _mk_collector()
    _s, _gap = c._m396_heartbeat_status(9.5)
    assert "起搏" in _s, _s
    assert _gap is None, "没采到不得报 0"


def test_04_explicit_stop_is_honoured():
    """显式 stop() 信号是唯一被采信的 payload 状态（它不是推断）。"""
    c = _mk_collector()
    c._on_heartbeat_alive({"interval": 9.5, "beat_count": 35, "status": "已停搏"})
    _s, _gap = c._m396_heartbeat_status(9.5)
    assert "已停搏" in _s, _s
    assert _gap is None, "显式停止下不存在 gap 读数"


def test_05_alive_payload_status_is_not_trusted():
    """★安慰剂反证：payload 自带 `status='搏动中'`，但 gap 已经 200s ⇒ 必须报停搏。

    这正是修复前的结构：PulseHeart 每拍发常量串、采集器原样缓存、快照原样落盘。
    """
    c = _mk_collector()
    c._on_heartbeat_alive({"interval": 9.5, "beat_count": 35, "status": "搏动中"})
    assert c._m396_last_beat_rx_at > 0
    # 把"最后收到时刻"推到 200s 前，模拟心跳死掉后再没有任何 handler 被调用
    c._m396_last_beat_rx_at = time.time() - 200.0
    _s, _gap = c._m396_heartbeat_status(9.5)
    assert "停搏" in _s, f"采信了 payload 的自报串：{_s}"
    assert _gap is not None and _gap >= 200


def test_06_observe_refreshes_rx_timestamp():
    c = _mk_collector()
    c._on_heartbeat_observe({"interval": 9.5, "beat_count": 36})
    assert c._m396_last_beat_rx_at > 0
    assert c._m396_beat_rx_count == 1
    assert c._cached_heartbeat.get("beat_count") == 36
    assert "status" not in c._cached_heartbeat, \
        "缓存里不得再存自报 status（存了就会被后续读方当真值）"


def test_07_resume_after_stall_flips_back():
    c = _mk_collector()
    c._m396_last_beat_rx_at = time.time() - 200.0
    assert "停搏" in c._m396_heartbeat_status(9.5)[0]
    c._on_heartbeat_observe({"interval": 9.5, "beat_count": 37})
    assert c._m396_heartbeat_status(9.5)[0] == "搏动中", "恢复后必须翻回搏动中"


# ============================================================ ① 快照接线
def test_08_snapshot_heartbeat_is_computed():
    """端到端：`_build_observability_snapshot()` 的 heartbeat 段必须是算出来的。"""
    c = _mk_collector()
    c._cached_heartbeat = {"interval": 9.5, "beat_count": 35}
    c._m396_last_beat_rx_at = time.time() - 300.0
    c._m396_beat_rx_count = 7
    c._m339_version_stamp = lambda: {}
    c._collect_memory_pressure = lambda: {}
    c._collect_knowledge_evolution = lambda _np: {}
    c._collect_organ_status_stats = lambda: {"total": 66, "running": 66, "fused": 0,
                                             "recovering": 0, "stopped": 0, "idle": 0}
    c._count_snapshot_backups = lambda: 0
    c._collect_link_status = lambda: {}
    _snap = c._build_observability_snapshot()
    _hb = _snap["heartbeat"]
    assert "停搏" in _hb["status"], _hb
    assert _hb["status_source"] == PMC._M396_HEARTBEAT_STATUS_SRC
    assert _hb["gap_s"] is not None and _hb["gap_s"] >= 300
    assert _hb["beat_rx_count"] == 7
    assert _hb["beat_count"] == 35, "缓存读数应保留（那是最后收到的真值）"


def test_09_snapshot_threshold_is_3x_interval():
    """阈值口径：3×interval，而不是固定秒数。"""
    assert PMC._M396_HEARTBEAT_STALL_FACTOR == 3.0
    c = _mk_collector()
    c._m396_last_beat_rx_at = time.time() - 100.0
    assert "停搏" in c._m396_heartbeat_status(30.0)[0], "gap 100s ≥ 3×30s 应判停搏"
    assert c._m396_heartbeat_status(60.0)[0] == "搏动中", "gap 100s < 3×60s 不应判停搏"


# ============================================================ ② 看门狗落盘
def _write_snap(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def test_10_watchdog_writes_stall_snapshot(tmp_path):
    """心跳停摆 ⇒ 看门狗把"停搏"落盘（否则文件永远停在最后一帧的乐观值）。"""
    p = str(tmp_path / "data" / "monitor" / "health_snapshot.json")
    _write_snap(p, {"beat_count": 35, "organs": {"total": 66},
                    "heartbeat": {"interval": 9.5, "beat_count": 35, "status": "搏动中"}})

    h = _mk_heart(_last_beat_time=time.time() - 600.0, _beat_count=35)
    h._m396_stall_snapshot_path = lambda: p
    r = h._m396_stall_snapshot()
    assert r and r["ok"], r

    with open(p, encoding="utf-8") as f:
        got = json.load(f)
    assert "停搏" in got["heartbeat"]["status"], got["heartbeat"]
    assert got["heartbeat"]["status_source"] == "watchdog@stall"
    assert got["heartbeat"]["beat_count"] == 35
    assert got["heartbeat_stall"]["beat"] == 35
    # 最小侵入：其他字段必须原样保留（不能整文件覆盖掉器官表）
    assert got["organs"]["total"] == 66, "只该改 heartbeat 段"
    assert got["beat_count"] == 35


def test_11_watchdog_quiet_when_heartbeat_healthy(tmp_path):
    p = str(tmp_path / "hs.json")
    _write_snap(p, {"heartbeat": {"status": "搏动中"}})
    h = _mk_heart(_last_beat_time=time.time() - 1.0)
    h._m396_stall_snapshot_path = lambda: p
    assert h._m396_stall_snapshot() is None, "gap 正常时不得改写快照"
    with open(p, encoding="utf-8") as f:
        assert json.load(f)["heartbeat"]["status"] == "搏动中"


def test_12_watchdog_stall_snapshot_throttled(tmp_path):
    """60s 节流：心跳已经死了，不需要每 15s 重写一次文件。"""
    p = str(tmp_path / "hs.json")
    _write_snap(p, {"heartbeat": {}})
    h = _mk_heart(_last_beat_time=time.time() - 600.0)
    h._m396_stall_snapshot_path = lambda: p
    assert h._m396_stall_snapshot() is not None
    assert h._m396_stall_snapshot() is None, "60s 内第二次必须跳过"
    assert h._m396_stall_snap_n == 1
    h._m396_stall_snap_at -= PH._M396_STALL_SNAPSHOT_EVERY_SEC + 1
    assert h._m396_stall_snapshot() is not None
    assert h._m396_stall_snap_n == 2


def test_13_watchdog_loop_calls_stall_snapshot():
    """必须接在看门狗循环里——不接就等于这个函数永不执行（过去 46 分钟的成因）。"""
    import inspect

    _src = inspect.getsource(PH.PulseHeart._m365_watchdog_loop)
    assert "self._m396_stall_snapshot()" in _src, "看门狗循环未接停搏落盘"


def test_14_beat_emit_status_is_computed_not_constant():
    """心跳体内发的 ALIVE.status 也必须现算，且必须在刷新 `_last_beat_time` 之前算。"""
    import inspect

    _src = inspect.getsource(PH.PulseHeart._trigger_heartbeat)
    assert '"status": _m396_status' in _src, "ALIVE 仍发常量串"
    assert "搏动中" not in _src, "心跳体内不得再出现硬编码的「搏动中」"
    assert _src.index("_m396_status = self._m396_beat_status()") < \
        _src.index("self._last_beat_time = now"), \
        "status 必须先算再刷新 last_beat_time，否则 gap 恒 0"


def test_15_beat_status_reflects_long_body():
    """本体卡了 100s 才走到发信号 ⇒ 该拍 status 必须如实报停搏。"""
    h = _mk_heart(_last_beat_time=time.time() - 100.0, current_interval=9.5)
    assert "停搏" in h._m396_beat_status()
    h._last_beat_time = time.time() - 1.0
    assert h._m396_beat_status() == "搏动中"


# ============================================================ ③ 器官数归一
def test_16_declared_comes_from_validate_report(monkeypatch):
    from nucleus import organ_assembler as OA

    monkeypatch.setattr(OA, "_LAST_VALIDATE_REPORT", None, raising=False)
    OA.set_last_validate_report({"organ_count": 56, "ok": True,
                                 "errors": [], "warnings": ["w"],
                                 "stats": {"always_online_count": 40,
                                           "degradable_count": 16}})
    try:
        c = _mk_collector()
        _d = c._m396_declared_organ_count()
        assert _d["declared"] == 56
        assert _d["declared_ok"] is True
        assert _d["declared_errors"] == 0
        assert _d["declared_warnings"] == 1
        assert "validate()" in _d["declared_source"]
    finally:
        OA.set_last_validate_report(None)


def test_17_declared_unknown_is_not_faked(monkeypatch):
    """没登记 validate 报告 ⇒ 报 unknown，**不许**拿活体 66 凑数。"""
    from nucleus import organ_assembler as OA

    monkeypatch.setattr(OA, "_LAST_VALIDATE_REPORT", None, raising=False)
    c = _mk_collector()
    _d = c._m396_declared_organ_count()
    assert _d["declared"] is None
    assert _d["declared_source"] == "not_scanned"


def test_18_three_counts_get_named(monkeypatch):
    """三口径归一：只有 `declared` 叫器官数，66 改名为 runtime_registered 并保留 total。"""
    from nucleus import organ_assembler as OA

    OA.set_last_validate_report({"organ_count": 56, "ok": True, "errors": [],
                                 "warnings": [], "stats": {}})
    try:
        c = _mk_collector()
        _s = c._m396_attach_organ_counts({"total": 66, "running": 66, "fused": 0,
                                          "recovering": 0, "stopped": 0, "idle": 0})
        assert _s["declared"] == 56, "唯一器官数主口径"
        assert _s["runtime_registered"] == 66
        assert _s["total"] == 66, "旧值必须保留（否则 30 天趋势线断代）"
        assert "不是器官数" in _s["total_meaning"]
        assert "declared" in _s["counts_note"]
        assert _s["running"] == 66, "状态分布字段不受影响"
    finally:
        OA.set_last_validate_report(None)


def test_19_snapshot_organs_carries_declared(monkeypatch):
    from nucleus import organ_assembler as OA

    OA.set_last_validate_report({"organ_count": 56, "ok": True, "errors": [],
                                 "warnings": [], "stats": {}})
    try:
        c = _mk_collector()
        c._cached_heartbeat = {"interval": 9.5, "beat_count": 1}
        c._m396_last_beat_rx_at = time.time()
        c._m339_version_stamp = lambda: {}
        c._collect_memory_pressure = lambda: {}
        c._collect_knowledge_evolution = lambda _np: {}
        c._count_snapshot_backups = lambda: 0
        c._collect_link_status = lambda: {}
        _snap = c._build_observability_snapshot()
        assert _snap["organs"]["declared"] == 56
        assert _snap["organs"]["runtime_registered"] == 0
    finally:
        OA.set_last_validate_report(None)


def test_20_main_pins_validate_report():
    """main.py 装配后必须钉一次 validate —— 否则采集器永远读到 not_scanned。"""
    with open("main.py", encoding="utf-8") as f:
        _src = f.read()
    assert "set_last_validate_report" in _src, "main.py 未登记 validate 报告"
    assert "_asm.validate()" in _src, "登记的不是 validate() 的结果"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
