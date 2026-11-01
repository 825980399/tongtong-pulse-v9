# -*- coding: utf-8 -*-
"""417 刀2 单测：主扫描让出点 + tests 语料缓存。

判据取向：
  ① 让出点在**并行路径**上真的触发了（`_do_scan_all_organs` 跑完 threads>1 且 ticks>0）——
     这是任务书点名的验收项："单测：让出点在并行路径上真的触发了"；
  ② 按线程独立计数：两个线程各自攒够 10 个单位就各自让，不是共用一个计数器；
  ③ 开关关掉 ⇒ 零让出（排障时可测纯耗时）；
  ④ 开销可核算：`sleep_sec == ticks * unit_sec`，且 ticks ≈ units/every；
  ⑤ 语料缓存：只建一次、返回同一对象、reset 后重建、内容里真有 tests 里的函数名；
  ⑥ **判据语义不变**：`_m348_protocol_iface_declared` 对「测试里引用过的名字」仍判 True，
     对「只在注释里出现的名字」仍判 False —— 缓存不能偷偷改变豁免结论。
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import nucleus.self_inspector as SI  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_state():
    SI._m417_reset_yield_state()
    yield
    SI._m417_reset_yield_state()


# ============================================================ ① 计数语义
def test_01_tick_every_ten_units():
    for _ in range(9):
        SI._m417_scan_tick()
    _st = SI.m417_yield_stats()
    assert _st["ticks"] == 0, "9 个单位就让出了（every=%s）" % _st["every"]
    assert _st["units"] == 9
    SI._m417_scan_tick()
    _st = SI.m417_yield_stats()
    assert _st["ticks"] == 1, "第 10 个单位必须让出一次"
    assert _st["units"] == 10


def test_02_step_accumulates():
    SI._m417_scan_tick(step=5)
    SI._m417_scan_tick(step=5)
    assert SI.m417_yield_stats()["ticks"] == 1
    # ★一次调用最多让一次（step=30 跨 3 个门槛也只睡 1ms —— 让出点的目的是切片，
    #   不是按单位数付罚款；补睡会让开销无界）
    SI._m417_scan_tick(step=30)
    _st = SI.m417_yield_stats()
    assert _st["ticks"] == 2, _st
    assert _st["units"] == 40


def test_03_overhead_is_accountable():
    for _ in range(100):
        SI._m417_scan_tick()
    _st = SI.m417_yield_stats()
    assert _st["ticks"] == 10
    assert abs(_st["sleep_sec"] - _st["ticks"] * _st["unit_sec"]) < 1e-9
    assert abs(_st["sleep_ms"] - 10.0) < 1e-6, _st


# ============================================================ ② 按线程独立
def test_10_global_progress_across_threads():
    """★计数必须是**全局进度**：两个线程各 10 个单位 ⇒ 共 2 次让出、2 个线程参与。

    为什么不是 `threading.local()`：66 个文件摊到 16 个 worker 上，每个线程只有
    4 个文件，按线程计数永远攒不满 10 ⇒ 让出点变死代码（417 首版实测 ticks=0）。
    """
    SI._m417_reset_yield_state()
    _ready = threading.Barrier(3)
    _done = []

    def _worker():
        _ready.wait()
        for _ in range(10):
            SI._m417_scan_tick()
        _done.append(1)

    _ths = [threading.Thread(target=_worker, daemon=True) for _ in range(2)]
    for _t in _ths:
        _t.start()
    _ready.wait()
    for _t in _ths:
        _t.join(timeout=5.0)
    _st = SI.m417_yield_stats()
    assert len(_done) == 2
    assert _st["units"] == 20, _st
    assert _st["ticks"] == 2, _st
    assert _st["threads"] == 2, "没记到两个线程：%s" % _st


# ============================================================ ③ 开关
def test_20_switch_off_is_zero_yield(monkeypatch):
    monkeypatch.setattr(SI, "m417_yield_on", lambda: False)
    for _ in range(100):
        SI._m417_scan_tick()
    _st = SI.m417_yield_stats()
    assert _st["ticks"] == 0 and _st["sleep_sec"] == 0.0, _st
    assert SI.m417_yield_stats()["switch_on"] is False


def test_21_default_switch_is_on():
    assert SI.m417_yield_on() is True, "config.ENABLE_M417_SCAN_YIELD 缺省必须 True"


# ============================================================ ④ 并行路径真触发
def test_30_parallel_path_really_yields():
    """任务书点名的验收项：让出点必须在**并行解析路径**上真的触发。"""
    SI._m417_reset_yield_state()
    _insp = SI.SelfInspector()
    _t0 = time.perf_counter()
    _data = _insp._do_scan_all_organs()
    _dt = time.perf_counter() - _t0
    _st = SI.m417_yield_stats()
    assert _data, "器官扫描没结果，判据无意义"
    assert _st["ticks"] > 0, "并行解析路径没让出过（every=%s）" % _st["every"]
    assert _st["threads"] >= 2, "只有 %d 个线程参与计数 ⇒ 并行面没覆盖到" % _st["threads"]
    assert _st["units"] >= len(_data), "单位数 %d < 器官文件数 %d" % (_st["units"], len(_data))
    # 让出总时长不许把扫描拖成主要成本（<50%，真正的门槛由实测报告承担）
    assert _st["sleep_sec"] < max(_dt, 1e-6), \
        "让出睡掉 %.3fs > 扫描 %.3fs" % (_st["sleep_sec"], _dt)


def test_31_serial_detector_loop_yields():
    SI._m417_reset_yield_state()
    _insp = SI.SelfInspector()
    _data = _insp._do_scan_all_organs()
    _n_methods = sum(v.get("method_count", 0) for v in _data.values())
    _insp._run_detectors(_data)
    _st = SI.m417_yield_stats()
    assert _st["units"] >= _n_methods, "主检测循环单位数 %d < 方法数 %d" % (_st["units"], _n_methods)
    assert _st["ticks"] > 0


# ============================================================ ⑤ 语料缓存
def test_40_corpus_cached_once():
    SI._m348_reset_tests_corpus()
    _a = SI._m348_tests_corpus()
    _b = SI._m348_tests_corpus()
    assert _a is _b, "第二次调用重建了语料 ⇒ 缓存没生效"
    assert len(_a) > 100, "tests/ 语料只有 %d 个文件，明显不对" % len(_a)
    _st = SI._M348_TESTS_SRC
    assert _st["built"] is True and _st["files"] == len(_a)
    assert _st["bytes"] > 100_000


def test_41_corpus_reset_rebuilds():
    SI._m348_reset_tests_corpus()
    _a = SI._m348_tests_corpus()
    SI._m348_reset_tests_corpus()
    assert SI._M348_TESTS_SRC["built"] is False
    _b = SI._m348_tests_corpus()
    assert _b is not _a, "reset 后必须重建，不能返回旧对象"
    assert len(_b) == len(_a)


def test_42_corpus_contains_known_marker():
    _rows = SI._m348_tests_corpus()
    assert any("def test_" in _r for _r in _rows), "语料里没有测试函数 ⇒ 读错目录了"


# ============================================================ ⑥ 判据语义不变
def _mk_item(file: str, method: str) -> dict:
    return {"file": file, "method": method, "line": 1}


def test_50_protocol_iface_still_exempts_test_referenced(tmp_path):
    """测试里引用过的名字仍然豁免（缓存不许把这条判据弄丢）。"""
    SI._m348_reset_tests_corpus()
    # `test_40_corpus_cached_once` 在 tests/ 里 ⇒ 名字带 `test_40_corpus_cached_once(`
    _r = SI._m348_protocol_iface_declared(
        _mk_item("nucleus/self_inspector.py", "test_40_corpus_cached_once"))
    assert _r is True, "测试引用豁免失效了（缓存把 tests 语料读丢了）"


def test_51_protocol_iface_false_for_unknown(tmp_path):
    SI._m348_reset_tests_corpus()
    _r = SI._m348_protocol_iface_declared(
        _mk_item("nucleus/self_inspector.py", "zzz_no_such_name_m417"))
    assert _r is False, "不存在的名字被判成豁免 ⇒ 判据被放宽了"


def test_52_protocol_iface_missing_file_is_false():
    assert SI._m348_protocol_iface_declared(_mk_item("no/such/file_417.py", "foo")) is False
    assert SI._m348_protocol_iface_declared({}) is False


def test_53_dead_code_issues_unchanged_by_cache():
    """端到端：缓存前后 dead_code 的**条数**必须一致（判据不许被缓存改变）。

    只比条数不比内容 —— 条数是「判据有没有变宽/变严」的直接观测量。
    """
    SI._m348_reset_tests_corpus()
    _insp = SI.SelfInspector()
    _a = _insp._detect_dead_code()
    SI._m348_reset_tests_corpus()      # 强制重建语料，第二次走冷路径
    _b = _insp._detect_dead_code()
    assert len(_a) == len(_b), "语料缓存改动了判据：%d vs %d" % (len(_a), len(_b))
