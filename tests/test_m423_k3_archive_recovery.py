# -*- coding: utf-8 -*-
"""423 批刀3 门控单测：归档回收。

判据来源：
  * archive / pending 全部由测试自己构造（`tmp_path`，每用例唯一目录）；
  * 「应回收哪几条」由构造时写死的 id 列表比对，不拿 `recover()` 的统计反推。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.evolution import archive_recovery as ar

NOW = 1_800_000_000.0
DAY = 86400.0


def _mk(id_, *, status="archived", rv=None, applied=False, o3dead=None,
        age_d=3.0, before=None, **kw):
    r = {"id": id_, "file": "nucleus/x.py", "method": "m", "line": 10,
         "status": status, "runtime_verified": rv, "applied": applied,
         "created_at": NOW - age_d * DAY}
    if o3dead is not None:
        r["o3_dead_code"] = o3dead
    if before is not None:
        r["status_before_archive"] = before
    r.update(kw)
    return r


def _write(p, doc):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


def _paths(tmp_path):
    d = tmp_path / "data" / "patches"
    d.mkdir(parents=True, exist_ok=True)
    return {
        "root": str(tmp_path),
        "archive": str(d / "pending_patches_archive.json"),
        "pending": str(d / "pending_patches.json"),
        "dead": str(d / "apply_retry_422.json"),
        "ledger": str(d / "archive_recovery_log.jsonl"),
    }


def test_01_flatten_accepts_three_historical_shapes():
    """archive 历史上被两个写入者写成三种形状，摊平必须都认。

    ★不认的后果：生产当前正是「批次 + 裸 patch 混在同一个 list 里」的混合形状，
      只按 items 摊平会漏掉 9 条裸 patch（423 T0 实测：8 → 17）。
    """
    _flat = [_mk("a"), _mk("b")]
    rows, shape = ar.flatten_archive(_flat)
    assert shape == "list_of_patches" and [r["id"] for r in rows] == ["a", "b"]

    _batch = [{"version": "m411", "items": [_mk("c"), _mk("d")]},
              {"version": None, "items": []}]
    rows, shape = ar.flatten_archive(_batch)
    assert shape == "list_of_batches" and [r["id"] for r in rows] == ["c", "d"]

    _doc = {"version": "m411", "items": [_mk("e")]}
    rows, shape = ar.flatten_archive(_doc)
    assert shape == "dict_items" and [r["id"] for r in rows] == ["e"]

    # 混合（生产就是这个）：批次里的 items + 同层的裸 patch
    _mixed = [{"version": "m411", "items": [_mk("f")]}, _mk("g"), _mk("h")]
    rows, shape = ar.flatten_archive(_mixed)
    assert shape == "list_of_batches"
    assert [r["id"] for r in rows] == ["f", "g", "h"]

    assert ar.flatten_archive(None) == ([], "unknown")
    assert ar.flatten_archive("坏文档") == ([], "unknown")


def test_02_criteria_matrix_counts_every_reject_reason():
    """六类排除 + 两类命中，每类都要单独计数（报告要按这个写）。"""
    rows = [
        _mk("ok_status", status="runtime_verified"),        # ← 回收（status 口径）
        _mk("ok_field", rv=True),                            # ← 回收（字段口径）
        _mk("no_rv", status="archived", rv=None),            # status/字段都不满足
        _mk("rv_false", status="archived", rv=False),
        _mk("already_applied", status="runtime_verified", applied=True),
        _mk("o3_dead", status="runtime_verified", o3dead=True),
        _mk("too_old", status="runtime_verified", age_d=45),
        _mk("no_ts", status="runtime_verified", age_d=0,
            created_at=None, generated_at=None, saved_at=None, timestamp=None),
        _mk("in_pending", status="runtime_verified"),
        _mk("in_dead", status="runtime_verified"),
    ]
    picked, st = ar.select_recoverable(
        rows, NOW, pending_ids={"in_pending"}, dead_ids={"in_dead"})
    assert [r["id"] for r in picked] == ["ok_status", "ok_field"], [r["id"] for r in picked]
    assert st["scanned"] == 10
    assert st["recoverable"] == 2
    assert st["skip_not_runtime_verified"] == 2
    assert st["skip_applied"] == 1
    assert st["skip_o3_dead_code"] == 1
    assert st["skip_too_old"] == 1
    assert st["skip_no_timestamp"] == 1
    assert st["skip_already_pending"] == 1
    assert st["skip_marked_dead"] == 1
    assert st["via_status"] == 1 and st["via_field"] == 1


def test_03_status_based_rv_is_recognised():
    """★本批最大的坑：`status == "runtime_verified"` 但字段是 None 的，必须算"验过"。"""
    assert ar.is_runtime_verified({"status": "runtime_verified",
                                   "runtime_verified": None}) is True
    assert ar.is_runtime_verified({"status": "archived",
                                   "runtime_verified": True}) is True
    assert ar.is_runtime_verified({"status": "archived",
                                   "runtime_verified": None}) is False
    assert ar.is_runtime_verified({"status": "runtime_failed"}) is False
    assert ar.is_runtime_verified({}) is False
    assert ar.is_runtime_verified(None) is False


def test_04_write_restores_status_marks_and_is_idempotent(tmp_path):
    """真写一遍：入池条数、标记、状态恢复、审计账、幂等，逐项对。"""
    p = _paths(tmp_path)
    _write(p["archive"], [{"version": "m411", "items": [
        _mk("r1", status="runtime_verified", before="runtime_verified"),
        _mk("r2", status="runtime_verified", before="pending"),
        _mk("skip_me", status="runtime_verified", o3dead=True),
    ]}])
    _write(p["pending"], [{"id": "old1", "status": "approved", "file": "a.py"}])

    r = ar.recover(p["root"], now=NOW, archive_path=p["archive"],
                   pending_path=p["pending"], dead_path=p["dead"],
                   ledger_path=p["ledger"])
    assert r["status"] == "written"
    assert r["recovered"] == 2 and r["recovered_ids"] == ["r1", "r2"]
    assert (r["pending_before"], r["pending_after"]) == (1, 3)

    with open(p["pending"], encoding="utf-8") as f:
        rows = json.load(f)
    _new = [x for x in rows if x.get(ar.RECOVERED_FLAG)]
    assert [x["id"] for x in _new] == ["r1", "r2"]
    assert _new[0]["status"] == "runtime_verified", "必须恢复归档前状态"
    assert _new[1]["status"] == "pending", "status_before_archive 优先"
    assert all(x["applied"] is False for x in _new)
    assert all(x["recovered_by"] == ar.RECOVERED_BY for x in _new)
    assert all("archive_reason_before_recover" in x for x in _new)
    assert not any(x["status"] == "approved" for x in _new), "回收件不许直接变 approved"

    assert os.path.exists(p["ledger"]), "真写必须落审计账"
    with open(p["ledger"], encoding="utf-8") as f:
        _led = [json.loads(x) for x in f if x.strip()]
    assert len(_led) == 1 and _led[0]["recovered"] == 2

    # 幂等：再跑一次不得重复入池
    r2 = ar.recover(p["root"], now=NOW, archive_path=p["archive"],
                    pending_path=p["pending"], dead_path=p["dead"],
                    ledger_path=p["ledger"])
    assert r2["status"] == "nothing_to_recover" and r2["recovered"] == 0
    with open(p["pending"], encoding="utf-8") as f:
        assert len(json.load(f)) == 3


def test_05_dry_run_and_switch_off_write_nothing(tmp_path, monkeypatch):
    """dry_run 与开关关闭都不许碰文件（生产零污染的两道保险）。"""
    p = _paths(tmp_path)
    _write(p["archive"], [{"version": "m411", "items": [
        _mk("r1", status="runtime_verified")]}])
    _write(p["pending"], [])
    _before = os.path.getmtime(p["pending"])

    r = ar.recover(p["root"], now=NOW, dry_run=True, archive_path=p["archive"],
                   pending_path=p["pending"], dead_path=p["dead"],
                   ledger_path=p["ledger"])
    assert r["status"] == "dry_run" and r["recovered"] == 1
    assert os.path.getmtime(p["pending"]) == _before, "dry_run 不许写 pending"
    assert not os.path.exists(p["ledger"]), "dry_run 不许落账"

    import config
    monkeypatch.setattr(config, ar.RECOVER_SWITCH, False, raising=False)
    r3 = ar.recover(p["root"], now=NOW, archive_path=p["archive"],
                    pending_path=p["pending"], dead_path=p["dead"],
                    ledger_path=p["ledger"])
    assert r3["status"] == "disabled"
    with open(p["pending"], encoding="utf-8") as f:
        assert json.load(f) == [], "开关关闭时必须零写入"


def test_06_wired_into_main_startup():
    """`main.py` 启动流程里必须真的调了它（否则整刀是死代码）。"""
    with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as f:
        src = f.read()
    assert "from nucleus.evolution import archive_recovery" in src
    assert "archive_recovery" in src and ".recover(" in src
    _i_call = src.find("_m423_ar.recover(")
    _i_loop = src.rfind("while not exit_requested:")
    assert 0 < _i_call < _i_loop, "回收必须在主循环之前（启动时一次性动作）"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
