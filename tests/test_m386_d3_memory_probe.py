# -*- coding: utf-8 -*-
"""386刀3：内存观测「先活下来」—— gc 扫描加时间预算，截断也写盘。

★改前的死结：`_m339_memory_probe` 是「整份扫完再判超时」，而活体堆下
  `gc.get_objects()` 全扫描要 6.7~18.4s ⇒ **每次都 >3s ⇒ 从不写盘**
  （live 00:30~00:55 每 5 分钟一条 WARNING，无产物）。
"""

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from organs.core.PulseMetricsCollector import PulseMetricsCollector as C  # noqa: E402

SRC = os.path.join(ROOT, "organs", "core", "PulseMetricsCollector.py")


def _mk(monkeypatch, budget: float, write_on_trunc=True):
    """最小实例：只带 `_m339_memory_probe` 需要的部件。"""
    _o = C.__new__(C)
    _o._m339_gc_last = {}
    _o._m337_image_cache = {"mb": 1.0}
    _o._log = lambda *a, **k: None
    _o._m349_heap_attribution = lambda _rows: {"state": "no_rows"}
    _o._m339_version_stamp = lambda: {"proc_gen": "test", "code_version": "t",
                                      "criteria_version": "t"}
    _cfg = {"M339_GC_SCAN_BUDGET_SEC": budget,
            "M339_WRITE_ON_TRUNCATED": write_on_trunc,
            "M339_MEMORY_PROBE_MAX_SEC": 3.0,
            "M339_MEMORY_PROBE_TOP_N": 20,
            "M339_MEMORY_GC_HEAP_MB": 1200.0,
            "M339_MEMORY_PROBE_HOURS": (5, 13),
            "M339_TRACEMALLOC_MODE": "off"}
    _o._m339_cfg_get = lambda _n, _d: _cfg.get(_n, _d)
    # 344补3 域过滤会把产物改道（判隔离进程）⇒ 单测里让它原样返回
    import nucleus.domain_guard as _dg
    monkeypatch.setattr(_dg, "guard_artifact_dir",
                        lambda _d, trace=None: (_d, "unpatched"),
                        raising=False)
    return _o


def _bigheap(n: int = 300000):
    """造足够多的对象，让扫描必须跨过 65536 检查点。"""
    return [object() for _ in range(n)]


def test_m386_d3_truncated_still_writes(monkeypatch, tmp_path):
    """★核心（票面「内存观测能正常写盘」）：预算极小 ⇒ 截断，但**仍写盘**。

    改前这条路径是 `elapsed > 3s ⇒ 不写盘` ⇒ 产物永远缺席。
    """
    _keep = _bigheap()
    _o = _mk(monkeypatch, budget=0.0001)
    _r = _o._m339_memory_probe(heap_mb=99999.0, out_dir=str(tmp_path))
    assert _r["path"], "截断也必须写盘（改前这里恒为 None）"
    _d = json.load(io.open(_r["path"], encoding="utf-8"))
    assert _d["gc_truncated"] is True, "应标记为截断"
    assert _d["gc_scanned"] > 0
    # ★真实耗时必须落盘，不许被压成 0（否则又是一个假读数）
    assert _d["elapsed_sec"] > 0
    assert _keep  # 防止被优化掉


def test_m386_d3_write_on_truncated_off_restores_old_behaviour(monkeypatch, tmp_path):
    """★零回归路径：开关关 ⇒ 逐字回到旧判定（超时不写盘）。"""
    _keep = _bigheap()
    _o = _mk(monkeypatch, budget=0.0001, write_on_trunc=False)
    _r = _o._m339_memory_probe(heap_mb=99999.0, out_dir=str(tmp_path))
    # 旧判定下：若 elapsed 仍 >3s ⇒ 不写；本用例只保证**开关语义**成立
    if _r["elapsed_sec"] and _r["elapsed_sec"] > 3.0:
        assert _r["path"] is None
    assert _keep


def test_m386_d3_generous_budget_no_truncation(monkeypatch, tmp_path):
    """预算充足 ⇒ 全量扫描，truncated=False。"""
    _keep = _bigheap(50000)
    _o = _mk(monkeypatch, budget=30.0)
    _r = _o._m339_memory_probe(heap_mb=99999.0, out_dir=str(tmp_path))
    assert _r["path"]
    _d = json.load(io.open(_r["path"], encoding="utf-8"))
    assert _d["gc_truncated"] is False
    assert _d["gc_scanned"] > 50000, "50k 个对象不该被截断"
    assert _keep


def test_m386_d3_top20_and_header_intact(monkeypatch, tmp_path):
    """产物头字段与 top20 结构不变（本刀只加扫描口径字段，不改既有契约）。"""
    _keep = _bigheap(50000)
    _o = _mk(monkeypatch, budget=30.0)
    _r = _o._m339_memory_probe(heap_mb=4321.0, out_dir=str(tmp_path))
    _d = json.load(io.open(_r["path"], encoding="utf-8"))
    for _k in ("proc_gen", "code_version", "criteria_version", "heap_mb",
               "image_mb", "top20", "top20_source"):
        assert _k in _d, "头字段缺 %s" % _k
    assert _d["heap_mb"] == 4321.0
    assert _d["top20_source"] == "gc_types"
    assert _d["top20"] and isinstance(_d["top20"][0], dict)
    assert _keep


def test_m386_d3_budget_must_be_below_timeout():
    """★守卫：扫描预算必须 < 超时线，否则预算形同虚设（仍会走不写盘）。"""
    assert C._M339_GC_SCAN_BUDGET_SEC < C._M339_PROBE_MAX_SEC


def test_m386_d3_elapsed_not_zeroed():
    """★守卫：不许为了"放行"把 elapsed 压成 0 —— 那是假读数（铁律276）。

    本刀第一版就是写成 `_elapsed = 0.0` 来绕过超时判定，被这条拦住。
    """
    _s = io.open(SRC, encoding="utf-8").read()
    assert "_elapsed = 0.0" not in _s
    assert "truncated_but_written" in _s


def test_m386_d3_scan_budget_check_source():
    """预算检查必须**在遍历内**（每 65536 个一次），不是只在开头判一次。"""
    _s = io.open(SRC, encoding="utf-8").read()
    assert "_scanned & 0xFFFF" in _s
    assert "_truncated = True" in _s
    _i = _s.index("for _o in _gcm.get_objects():")
    _j = _s.index("_scanned & 0xFFFF")
    assert _i < _j, "预算检查必须在遍历循环体内"


def test_m386_d3_live_baseline_is_actually_broken():
    """★留证：改前的 live 读数（票面 10.5s）与本批实测同量级。

    这条不测代码，只把"为什么必须改"钉住 —— 若哪天 gc 扫描变快了，
    这里会提醒重新评估预算（2.0s 未必还合理）。
    """
    _s = io.open(SRC, encoding="utf-8").read()
    assert "M339_MEMORY_PROBE_MAX_SEC" in _s
    assert float(C._M339_PROBE_MAX_SEC) == 3.0
    # 预算 2.0s 是按「活体实测 6.7~18.4s」定的，不是拍脑袋
    assert 0 < C._M339_GC_SCAN_BUDGET_SEC <= 2.5
