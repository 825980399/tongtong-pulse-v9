# -*- coding: utf-8 -*-
"""447 刀3：磁盘清理计划（保留策略 + 四道复核 + 保护名单）。

★本文件**不删任何真实文件**：全部在 tmp 造的假树上跑 `plan()`。
  真删由 `tools/m447_disk_cleanup.py --apply` 执行，且只对显式清单生效。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.m447_disk_cleanup import _guarded, plan


def _mk(path, size=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as _f:
        _f.write(b"x" * size)
    return path


def _tree(tmp_path):
    _root = str(tmp_path)
    _mem = os.path.join(_root, "data", "reports", "memory")
    _mk(os.path.join(_mem, "_tm_baseline_pid1_20261101-000000.snap"))
    time.sleep(0.01)
    _mk(os.path.join(_mem, "_tm_baseline_pid2_20261102-000000.snap"))
    _mk(os.path.join(_mem, "_tm_baseline.snap.tmp"))
    _kd = os.path.join(_root, "data", "knowledge")
    _mk(os.path.join(_kd, "snapshot_aaaa1111.json"), 200)
    _mk(os.path.join(_kd, "snapshot_bbbb2222.json"), 2)
    _mk(os.path.join(_kd, "pulse_knowledge_snapshot.json"), 5)
    _mon = os.path.join(_root, "data", "monitor")
    for _d in ("20261031", "20261101", "20261102", "20261103", "20261104"):
        _mk(os.path.join(_mon, f"pulse_pairs_{_d}.jsonl"))
    _ar = os.path.join(_root, "data", "_archive")
    _mk(os.path.join(_ar, "pulse_pairs_20261102.jsonl.trim.bak"))
    return _root


def test_plan_keeps_newest_tm_baseline(tmp_path):
    _root = _tree(tmp_path)
    _names = [os.path.basename(i["path"]) for i in plan(root=_root)]
    _kept = [n for n in os.listdir(os.path.join(_root, "data", "reports", "memory"))
             if n.endswith(".snap") and n not in _names]
    assert len(_kept) == 1, _kept
    assert "pid2" in _kept[0], "应保留 mtime 最新的那份"


def test_plan_deletes_orphan_tmp(tmp_path):
    _root = _tree(tmp_path)
    _rels = [i["rel"] for i in plan(root=_root)]
    assert any(r.endswith("_tm_baseline.snap.tmp") for r in _rels), _rels


def test_plan_orphan_threshold(tmp_path):
    _root = _tree(tmp_path)
    _rels = [i["rel"] for i in plan(root=_root, orphan_min_mb=0.0)]
    assert any("snapshot_aaaa1111" in r for r in _rels), "大孤儿该删"
    assert any("snapshot_bbbb2222" in r for r in _rels), "阈值放开后小孤儿也进清单"
    _rels2 = [i["rel"] for i in plan(root=_root)]
    assert not any("snapshot_bbbb2222" in r for r in _rels2), "缺省阈值下小孤儿不删"


def test_plan_keeps_three_days_of_pairs(tmp_path):
    _root = _tree(tmp_path)
    _rels = [i["rel"] for i in plan(root=_root)]
    _pairs = [r for r in _rels if "pulse_pairs_2026" in r and "trim" not in r]
    assert len(_pairs) == 2, _pairs          # 10-31 / 11-01 两天该删
    assert not any("20261102" in r or "20261103" in r or "20261104" in r
                   for r in _pairs), _pairs


def test_plan_never_touches_knowledge_main_file(tmp_path):
    _root = _tree(tmp_path)
    _rels = [i["rel"] for i in plan(root=_root, orphan_min_mb=0)]
    assert not any(r.endswith("pulse_knowledge_snapshot.json") for r in _rels), _rels


def test_plan_never_touches_models(tmp_path):
    _root = str(tmp_path)
    _mk(os.path.join(_root, "data", "models", "bge.bin"), 100)
    _rels = [i["rel"] for i in plan(root=_root)]
    assert not any("data/models/" in r for r in _rels), _rels


def test_plan_archive_keeps_newest_cold_backup(tmp_path):
    _root = str(tmp_path)
    _kb = os.path.join(_root, "data", "_archive", "knowledge_backups")
    _a = _mk(os.path.join(_kb, "old.bak.gz"))
    time.sleep(0.01)
    _b = _mk(os.path.join(_kb, "new.bak.gz"))
    _rels = [i["rel"] for i in plan(root=_root)]
    assert any("old.bak.gz" in r for r in _rels), _rels
    assert not any("new.bak.gz" in r for r in _rels), _rels
    assert os.path.exists(_a) and os.path.exists(_b), "plan 只读，不得删文件"


def test_guarded_rejects_new_file(tmp_path):
    _p = _mk(str(tmp_path / "fresh.jsonl"))
    _ok, _why = _guarded(_p, min_age_sec=3600.0, now=time.time())
    assert _ok is False and "太新" in _why, _why


def test_guarded_rejects_protected(tmp_path):
    _root = str(tmp_path)
    _p = _mk(os.path.join(_root, "data", "models", "x.bin"))
    os.utime(_p, (time.time() - 9000, time.time() - 9000))
    _ok, _why = _guarded(_p, min_age_sec=3600.0, now=time.time(), root=_root)
    assert _ok is False and _why == "保护名单", _why


def test_guarded_rejects_directory(tmp_path):
    _ok, _why = _guarded(str(tmp_path), min_age_sec=0.0, now=time.time())
    assert _ok is False and _why == "不是普通文件", _why


def test_guarded_accepts_old_plain_file(tmp_path):
    _p = _mk(str(tmp_path / "old.jsonl"))
    os.utime(_p, (time.time() - 9000, time.time() - 9000))
    _ok, _why = _guarded(_p, min_age_sec=3600.0, now=time.time())
    assert _ok is True and _why == "ok", _why
