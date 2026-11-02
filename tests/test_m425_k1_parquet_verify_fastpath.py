# -*- coding: utf-8 -*-
"""425 刀1 门控单测：Parquet 校验 footer 快路径（等价性 + 开关 + 增量兜底）。

三条核心断言：
  ① 快路径与深路径**判定逐条一致** —— 好档都过、坏档（缺列/版本/塌缩）都拒；
  ② footer 损坏时入口**自动回退深校验**并如实给 -1（绝不能把「读不出 footer」当「没有 parquet」）；
  ③ 增量日志损坏 → `load()` 自动退回全量快照（任务书刀1 必验的兜底项）。

★每个用例都用 tmp_path（任务书：实例化后跑生产方法，第一行必须改路径）。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot

PROD_PARQUET = os.path.join(ROOT, "data", "knowledge", "parquet")
PROD_SNAPSHOT = os.path.join(ROOT, "data", "knowledge", "pulse_knowledge_snapshot.json")


def _snap(root):
    """实例化 + 第一行改路径。"""
    return PulseSnapshot(snapshot_path=str(root / "pulse_knowledge_snapshot.json"))


def _mk_parquet(snap, path, n_rows, level="L2", metadata=None, drop_col=None):
    req = list(snap._m81_parquet_required_columns())
    if drop_col:
        req = [c for c in req if c != drop_col]
    cols = {}
    for c in req:
        if c == "evol_level":
            cols[c] = pa.array([level] * n_rows, pa.string())
        elif c == "node_id":
            cols[c] = pa.array([f"n{i}" for i in range(n_rows)], pa.string())
        else:
            cols[c] = pa.array(["x"] * n_rows, pa.string())
    tbl = pa.table(cols)
    if metadata:
        tbl = tbl.replace_schema_metadata(metadata)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(tbl, str(path))


def _fast(snap, base):
    return snap._m425_verify_parquet_meta(
        pq, str(base), set(snap._m81_parquet_required_columns()),
        snap._m81_parquet_expected_schema_version())


def _deep(snap, base):
    return snap._m68_verify_parquet_deep(
        pq, str(base), set(snap._m81_parquet_required_columns()),
        snap._m81_parquet_expected_schema_version())


def test_01_fastpath_matches_deep_on_real_parquet(tmp_path):
    """真实生产档：快慢两路行数必须相同，且快路径快一个数量级以上。"""
    if not os.path.isdir(PROD_PARQUET):
        pytest.skip("无生产 parquet 目录")
    import time
    snap = _snap(tmp_path)
    t0 = time.perf_counter()
    f = _fast(snap, PROD_PARQUET)
    tf = time.perf_counter() - t0
    d = _deep(snap, PROD_PARQUET)
    assert f == d, f"快路径 {f!r} 与深路径 {d!r} 不一致"
    assert f > 0, f"生产 parquet 应有行数，实得 {f!r}"
    assert tf < 0.5, f"快路径应远快于整表读，实测 {tf:.3f}s"


def test_02_missing_required_column_rejected_by_both(tmp_path):
    """缺一个必需标量列 ⇒ 两路都 -1（不能有一路放行）。"""
    snap = _snap(tmp_path)
    base = tmp_path / "parquet"
    md = {b"m81_schema_version": snap._m81_parquet_expected_schema_version().encode()}
    _mk_parquet(snap, base / "evol_level=L2" / "a.parquet", 3, metadata=md,
                drop_col="trust_score")
    assert _fast(snap, base) == -1
    assert _deep(snap, base) == -1


def test_03_schema_version_mismatch_rejected_by_both(tmp_path):
    snap = _snap(tmp_path)
    base = tmp_path / "parquet"
    _mk_parquet(snap, base / "evol_level=L2" / "a.parquet", 3,
                metadata={b"m81_schema_version": b"v0.0-old"})
    assert _fast(snap, base) == -1
    assert _deep(snap, base) == -1


def test_04_level_collapse_rejected_by_both(tmp_path):
    """元数据声称 L1 有 500 行而 L1 分区为空 ⇒ 两路都判塌缩 -1。"""
    snap = _snap(tmp_path)
    base = tmp_path / "parquet"
    md = {b"m81_schema_version": snap._m81_parquet_expected_schema_version().encode(),
          b"l1_count": b"500"}
    _mk_parquet(snap, base / "evol_level=L2" / "a.parquet", 3, metadata=md)
    (base / "evol_level=L1").mkdir(parents=True, exist_ok=True)
    assert _fast(snap, base) == -1
    assert _deep(snap, base) == -1


def test_05_broken_footer_falls_back_and_reports_minus_one(tmp_path):
    """footer 被截断 ⇒ 入口必须自动回退深校验并给 -1，不能当成「没有 parquet」返回 0。"""
    snap = _snap(tmp_path)
    base = tmp_path / "parquet"
    md = {b"m81_schema_version": snap._m81_parquet_expected_schema_version().encode()}
    p = base / "evol_level=L2" / "a.parquet"
    _mk_parquet(snap, p, 3, metadata=md)
    with open(p, "r+b") as fh:
        fh.truncate(20)
    assert snap._m68_verify_parquet(root=str(base)) == -1


def test_06_switch_off_bypasses_fastpath(tmp_path, monkeypatch):
    """开关关 ⇒ 不允许调用快路径（用替身计数证明走了深校验）。"""
    snap = _snap(tmp_path)
    base = tmp_path / "parquet"
    md = {b"m81_schema_version": snap._m81_parquet_expected_schema_version().encode()}
    _mk_parquet(snap, base / "evol_level=L2" / "a.parquet", 3, metadata=md)

    calls = {"fast": 0, "deep": 0}
    _orig_fast, _orig_deep = snap._m425_verify_parquet_meta, snap._m68_verify_parquet_deep

    def _f(*a, **k):
        calls["fast"] += 1
        return _orig_fast(*a, **k)

    def _d(*a, **k):
        calls["deep"] += 1
        return _orig_deep(*a, **k)

    monkeypatch.setattr(snap, "_m425_verify_parquet_meta", _f)
    monkeypatch.setattr(snap, "_m68_verify_parquet_deep", _d)

    import config
    monkeypatch.setattr(config, "SNAPSHOT_PARQUET_VERIFY_FASTPATH", False, raising=False)
    assert snap._m68_verify_parquet(root=str(base)) == 3
    assert calls["fast"] == 0 and calls["deep"] == 1

    calls["fast"] = calls["deep"] = 0
    monkeypatch.setattr(config, "SNAPSHOT_PARQUET_VERIFY_FASTPATH", True, raising=False)
    assert snap._m68_verify_parquet(root=str(base)) == 3
    assert calls["fast"] == 1 and calls["deep"] == 0


def test_07_corrupt_incremental_log_falls_back_to_full(tmp_path):
    """增量日志损坏（截断行 + 乱码）⇒ load() 仍返回全量快照，绝不少节点也绝不抛。"""
    if not os.path.exists(PROD_SNAPSHOT):
        pytest.skip("无生产全量快照可取样本")
    with open(PROD_SNAPSHOT, encoding="utf-8") as f:
        prod = json.load(f)
    l1 = [json.loads(json.dumps(n)) for n in (prod.get("nodes") or [])
          if str(n.get("evol_level", "")).upper() == "L1" and not n.get("ephemeral")][:3]
    assert len(l1) == 3, "样本不足"

    full_p = tmp_path / "pulse_knowledge_snapshot.json"
    snap_data = {
        "version": "v9.5", "created_at": "2026-11-02T15:00:00",
        "total_nodes_all": 3, "l2_l3_count": 0, "l1_saved_count": 3,
        "save_mode": "full", "node_count_at_save": 3, "node_list_checksum": "probe",
        "freq_index": {}, "hebbian_weights": {}, "inference_cache": {},
        "extra_state": {}, "nodes": l1,
    }
    with open(full_p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(snap_data, f, ensure_ascii=False)

    # 坏 jsonl：一行截断 + 一行二进制乱码
    with open(tmp_path / "snapshot_incremental.jsonl", "wb") as f:
        f.write(b'{"action": "upsert", "node_id": "x", "ts": 1, "da\n')
        f.write(b"\x88\x99 not json \n")

    snap = PulseSnapshot(snapshot_path=str(full_p))
    got = snap.load()
    assert len(got) == 3, f"坏 jsonl 后应退回全量 3 节点，实得 {len(got)}"
