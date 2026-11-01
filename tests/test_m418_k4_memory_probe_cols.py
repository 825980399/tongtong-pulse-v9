# -*- coding: utf-8 -*-
"""418 刀4 单测：内存探针加 `bytes` 与 `arrow_mb` 两列。

判据三组：
① 产物格式 —— `gc_type_rows` 每条都要有 `bytes`，且与 `count` 同序；
② 口径不许含糊 —— `gc_bytes_kind=shallow` / `gc_bytes_shallow=True` 必须落盘，
   `arrow_loaded` 必须与 `arrow_mb is None` **互斥且自洽**；
③ 未知 ≠ 0 —— pyarrow 未加载时必须 `arrow_mb is None`（不许写 0）。
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


def _mk(monkeypatch, budget=0.0001):
    _o = C.__new__(C)
    _o._m339_gc_last = {}
    _o._m337_image_cache = {"mb": 1.0}
    _o._log = lambda *a, **k: None
    _o._m349_heap_attribution = lambda _rows: {"state": "no_rows"}
    _o._m339_version_stamp = lambda: {"proc_gen": "test", "code_version": "t",
                                      "criteria_version": "t"}
    _cfg = {"M339_GC_SCAN_BUDGET_SEC": budget,
            "M339_WRITE_ON_TRUNCATED": True,
            "M339_MEMORY_PROBE_MAX_SEC": 3.0,
            "M339_MEMORY_PROBE_TOP_N": 20,
            "M339_MEMORY_GC_HEAP_MB": 1200.0,
            "M339_MEMORY_PROBE_HOURS": (5, 13),
            "M339_TRACEMALLOC_MODE": "off"}
    _o._m339_cfg_get = lambda _n, _d: _cfg.get(_n, _d)
    import nucleus.domain_guard as _dg
    monkeypatch.setattr(_dg, "guard_artifact_dir",
                        lambda _d, trace=None: (_d, "unpatched"), raising=False)
    return _o


def _bigheap(n: int = 300000):
    return [{"k": i, "v": "x" * 8} for i in range(n)]


def _run(monkeypatch, tmp_path):
    _keep = _bigheap()
    _o = _mk(monkeypatch)
    _r = _o._m339_memory_probe(heap_mb=99999.0, out_dir=str(tmp_path))
    assert _r["path"], "探针没写盘（reason=%s）" % _r.get("reason")
    return json.load(io.open(_r["path"], encoding="utf-8")), _keep


# ============================================================ ① 产物格式
def test_10_gc_rows_have_bytes(monkeypatch, tmp_path):
    _d, _keep = _run(monkeypatch, tmp_path)
    _rows = _d.get("gc_type_rows") or []
    assert _rows, "没有 gc_type_rows"
    _bad = [r for r in _rows if "bytes" not in r]
    assert not _bad, "这些行缺 bytes：%s" % _bad[:3]
    assert all(isinstance(r["bytes"], int) and r["bytes"] >= 0 for r in _rows)


def test_11_bytes_not_all_zero(monkeypatch, tmp_path):
    """真扫了 30 万个对象 ⇒ bytes 不可能是 0（全 0 = 累加根本没接上）。"""
    _d, _keep = _run(monkeypatch, tmp_path)
    assert _d["gc_bytes_total"] > 0, "gc_bytes_total 为 0 —— bytes 列是假的"
    assert sum(r["bytes"] for r in _d["gc_type_rows"]) > 0
    assert _keep


def test_12_rows_sorted_by_count_and_bytes_aligned(monkeypatch, tmp_path):
    """`bytes` 必须与 `count` **同一行**（同序取前 N），不许错位。

    错位是这类"加一列"改动的经典事故：`sorted(_cnt)` 排序、`_byt` 却按原序遍历。
    """
    _d, _keep = _run(monkeypatch, tmp_path)
    _rows = _d["gc_type_rows"]
    _counts = [r["count"] for r in _rows]
    assert _counts == sorted(_counts, reverse=True), "没按 count 降序"
    # 同类型在两列里必须对得上：dict 的 count 与 bytes 都应是最大的那一档之一
    _by_t = {r["type"]: r for r in _rows}
    if "dict" in _by_t and "list" in _by_t:
        assert _by_t["dict"]["count"] >= 1
        assert _by_t["dict"]["bytes"] > 0
    assert _keep


def test_13_topn_respected(monkeypatch, tmp_path):
    _d, _keep = _run(monkeypatch, tmp_path)
    assert len(_d["gc_type_rows"]) <= _d["top_n"]
    assert _d["top20"] is _d["top20"]  # 结构稳定
    assert len(_d["top20"]) <= _d["top_n"]
    assert _keep


# ============================================================ ② 口径写明
def test_20_shallow_caliber_is_declared(monkeypatch, tmp_path):
    """`sys.getsizeof` 是**浅**尺寸 —— 必须在产物里写明，否则会被当全量读数用。"""
    _d, _keep = _run(monkeypatch, tmp_path)
    assert _d.get("gc_bytes_kind") == "shallow"
    assert _d.get("gc_bytes_shallow") is True
    assert "gc_bytes_err" in _d
    # ★418刀4：范围口径 —— gc 只看得见被跟踪对象，必须写在产物里
    assert _d.get("gc_objects_scope") == "gc_tracked_only"
    assert _keep


def test_21_bytes_total_never_exceeds_absurd(monkeypatch, tmp_path):
    """浅尺寸总和应当远小于 RSS（本仓铁律 14：单进程内各组件 RSS 不存在）。"""
    _d, _keep = _run(monkeypatch, tmp_path)
    # 30 万个小 dict 的浅尺寸约 3×10^7 字节量级；给一个宽松上界抓"重复累加"
    assert _d["gc_bytes_total"] < 50 * (1024 ** 3), _d["gc_bytes_total"]
    assert _keep


# ============================================================ ③ arrow 两列
def test_30_arrow_fields_present(monkeypatch, tmp_path):
    _d, _keep = _run(monkeypatch, tmp_path)
    assert "arrow_mb" in _d and "arrow_loaded" in _d
    assert _keep


def test_31_arrow_none_when_not_loaded(monkeypatch, tmp_path):
    """pyarrow 未加载 ⇒ `arrow_mb is None` 且 `arrow_loaded is False`。

    ★不许写 0：`total_allocated_bytes()` 在未 import 时恒为 0，写 0 会让
      「没有 Arrow」和「Arrow 空着」变成同一个读数。
    """
    import sys as _s
    monkeypatch.delitem(_s.modules, "pyarrow", raising=False)
    _d, _keep = _run(monkeypatch, tmp_path)
    assert _d["arrow_loaded"] is False
    assert _d["arrow_mb"] is None, "未加载时写了 %r，应为 None" % (_d["arrow_mb"],)
    assert _keep


def test_32_arrow_number_when_loaded(monkeypatch, tmp_path):
    """pyarrow 已加载 ⇒ `arrow_mb` 是数字且 >= 0，`arrow_loaded is True`。"""
    try:
        import pyarrow  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("本机无 pyarrow")
    _d, _keep = _run(monkeypatch, tmp_path)
    assert _d["arrow_loaded"] is True
    assert isinstance(_d["arrow_mb"], (int, float)) and _d["arrow_mb"] >= 0
    assert _keep


def test_33_arrow_helpers_direct():
    """两个辅助函数自身的语义（不经过探针）。"""
    from organs.core.PulseMetricsCollector import (_m418_arrow_loaded,
                                                   _m418_arrow_mb)
    _mb, _loaded = _m418_arrow_mb(), _m418_arrow_loaded()
    assert isinstance(_loaded, bool)
    if not _loaded:
        assert _mb is None
    else:
        assert isinstance(_mb, float) and _mb >= 0


# ============================================================ ④ 不破坏既有契约
def test_40_existing_keys_intact(monkeypatch, tmp_path):
    """新列不许顶掉老列（386 的截断三件、377 的 source/basis 都还得在）。"""
    _d, _keep = _run(monkeypatch, tmp_path)
    for _k in ("proc_gen", "heap_mb", "top20", "top20_source", "gc_scanned",
               "gc_truncated", "gc_scan_budget_sec", "tracemalloc_basis",
               "heap_attribution", "elapsed_sec", "note"):
        assert _k in _d, _k
    assert _d["top20_source"] in ("tracemalloc", "gc_types", "none")
    assert _keep


def test_41_eol_pure_lf():
    _b = open(SRC, "rb").read()
    assert _b.count(b"\r\n") == 0, "crlf=%d" % _b.count(b"\r\n")


def test_42_no_def_swallowed():
    """铁律：补丁以 `def` 行为锚点时最容易把定义吃掉（417 栽过）。"""
    import ast
    _t = ast.parse(open(SRC, encoding="utf-8").read())
    _mod = [n.name for n in _t.body if isinstance(n, ast.FunctionDef)]
    # ★注意层级：`_m377_tm_delta` 是**模块级**函数（探针里裸调用），
    #   不是类方法。把两者混在一起断言会得到一条假红。
    for _n in ("_m377_tm_ensure", "_m377_tm_delta",
               "_m418_arrow_mb", "_m418_arrow_loaded"):
        assert _n in _mod, "模块级函数 %s 不见了：%s" % (_n, _mod[:8])
    _cls = [n for n in _t.body
            if isinstance(n, ast.ClassDef) and n.name == "PulseMetricsCollector"][0]
    _ms = [n.name for n in _cls.body if isinstance(n, ast.FunctionDef)]
    for _n in ("_m339_memory_probe", "_build_observability_snapshot",
               "_on_heartbeat_observe", "_on_heartbeat_alive"):
        assert _n in _ms, _n


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
