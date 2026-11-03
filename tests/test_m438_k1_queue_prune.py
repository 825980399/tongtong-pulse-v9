# -*- coding: utf-8 -*-
"""438 刀1 单测：队列治理（回收阈值同源 + prune_pending 真被执行）。

T0 事实（2026-11-03 实测）：
  · pending 9 条，`PatchAutoApprover.classify()` 判 **8 条 reject_stale + 2 条 obsolete**；
  · 但 `prune_pending()` **全仓无调用方** ⇒ 判定天天跑、队列从不回收；
  · 回收器 `MAX_AGE_DAYS=30`，审批器 `PATCH_AUTO_APPROVE_STALE_DAYS=7`
    ⇒ 10.3 天的件被捞回来、立刻判死（「回收即死」）。
"""
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
import nucleus.evolution.archive_recovery as ar
from nucleus.evolution.PatchAutoApprover import (
    DECISION_STALE,
    PatchAutoApprover,
)

_DAY = 86400.0


def _row(**kw):
    _b = {"id": "p1", "file": "organs/brain/X.py", "method": "m",
          "status": "runtime_verified", "applied": False,
          # original_code 留空 ⇒ is_obsolete() 直接 False（目标文件不存在的场景
          #   会判 obsolete，那是另一条判据，不该混进"按龄 stale"的用例）
          "original_code": "",
          "created_at": time.time() - 10 * _DAY}
    _b.update(kw)
    return _b


def _write(path, doc):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return path


# ---------------------------------------------------------------- ① 阈值同源
def test_max_age_is_seven_not_thirty():
    """模块常量必须是 7（与审批器同档），不能再是 30。"""
    assert ar.MAX_AGE_DAYS == 7.0, f"MAX_AGE_DAYS={ar.MAX_AGE_DAYS}，回收器会再次捞回过期件"


def test_max_age_follows_approver_config(monkeypatch):
    """`max_age_days_cfg()` 必须读 `PATCH_AUTO_APPROVE_STALE_DAYS`（同一源）。"""
    monkeypatch.setattr(config, "PATCH_AUTO_APPROVE_STALE_DAYS", 3, raising=True)
    assert ar.max_age_days_cfg() == 3.0
    monkeypatch.setattr(config, "PATCH_AUTO_APPROVE_STALE_DAYS", 7, raising=True)
    assert ar.max_age_days_cfg() == 7.0


def test_fallback_stays_seven(monkeypatch):
    """配置读不到时回落 7，**绝不回落 30**。"""
    monkeypatch.delattr(config, "PATCH_AUTO_APPROVE_STALE_DAYS", raising=False)
    assert ar.max_age_days_cfg() == 7.0


# ---------------------------------------------------------------- ② 回收不再捞过期件
def test_recover_skips_overaged_by_default(tmp_path):
    """10.3 天的归档件：改前（30 天阈值）会被捞回，改后（7 天）必须被拒。"""
    _arch = _write(str(tmp_path / "archive.json"), [_row()])
    _pend = _write(str(tmp_path / "pending.json"), [])
    _dead = _write(str(tmp_path / "dead.json"), [])
    _led = str(tmp_path / "ledger.jsonl")

    _r = ar.recover(str(tmp_path), archive_path=_arch, pending_path=_pend,
                    dead_path=_dead, ledger_path=_led, dry_run=True)
    assert _r["recovered"] == 0, f"10 天的件仍被回收: {_r.get('recovered_ids')}"
    assert _r["stats"]["skip_too_old"] >= 1
    assert _r["stats"]["max_age_days"] == 7.0, _r["stats"]


def test_recover_still_recovers_fresh(tmp_path):
    """3 天的归档件必须照常回收（不能把口子堵死）。"""
    _arch = _write(str(tmp_path / "archive.json"),
                   [_row(created_at=time.time() - 3 * _DAY)])
    _pend = _write(str(tmp_path / "pending.json"), [])
    _dead = _write(str(tmp_path / "dead.json"), [])
    _r = ar.recover(str(tmp_path), archive_path=_arch, pending_path=_pend,
                    dead_path=_dead, ledger_path=str(tmp_path / "l.jsonl"),
                    dry_run=True)
    assert _r["recovered"] == 1, _r["stats"]


# ---------------------------------------------------------------- ③ prune 真动作
def test_prune_pending_archives_stale(tmp_path):
    """`prune_pending()` 必须把按龄过期的件移出 pending（改前：无调用方，永不回收）。"""
    _p = str(tmp_path / "pending.json")
    _old = _row(id="old1", created_at=time.time() - 10 * _DAY,
                generated_at=time.time() - 10 * _DAY)
    _new = _row(id="new1", created_at=time.time() - 0.1 * _DAY,
                generated_at=time.time() - 0.1 * _DAY, trust_score=90.0,
                verification={"passed": True}, risk_level="低")
    _write(_p, [_old, _new])

    _pa = PatchAutoApprover()
    _pa._pending_path = _p
    _pa._base_dir = str(tmp_path)
    _r = _pa.prune_pending()
    assert _r.get("pruned") == 1, _r
    with open(_p, encoding="utf-8") as _f:
        _left = json.load(_f)
    assert [x["id"] for x in _left] == ["new1"], _left
    with open(str(tmp_path / "pending_patches_resolved.json"), encoding="utf-8") as _f:
        _arch = json.load(_f)
    assert [x["id"] for x in _arch] == ["old1"], "归档文件没写"


def test_prune_is_idempotent(tmp_path):
    """再跑一次不能再删（不能把剩的那条也吞掉）。"""
    _p = str(tmp_path / "pending.json")
    _write(_p, [_row(id="new1", created_at=time.time() - 0.1 * _DAY,
                     generated_at=time.time() - 0.1 * _DAY, trust_score=90.0,
                     verification={"passed": True}, risk_level="低")])
    _pa = PatchAutoApprover()
    _pa._pending_path = _p
    _pa._base_dir = str(tmp_path)
    assert _pa.prune_pending().get("pruned") == 0
    with open(_p, encoding="utf-8") as _f:
        assert len(json.load(_f)) == 1


# ---------------------------------------------------------------- ④ 端到端：回收后不该再有 reject_stale
def test_recovered_rows_are_not_immediately_stale(tmp_path):
    """核心判据：**回收进来的件，审批器不得判 reject_stale**（「回收即死」根治）。"""
    _arch_rows = [
        _row(id=f"a{i}", created_at=time.time() - (5 + i) * _DAY)
        for i in range(3)
    ]
    _arch = _write(str(tmp_path / "archive.json"), _arch_rows)
    _pend = _write(str(tmp_path / "pending.json"), [])
    ar.recover(str(tmp_path), archive_path=_arch, pending_path=_pend,
               dead_path=_write(str(tmp_path / "dead.json"), []),
               ledger_path=str(tmp_path / "l.jsonl"), dry_run=False)
    with open(_pend, encoding="utf-8") as _f:
        _back = json.load(_f)
    assert _back, "5~7 天的件应被回收"
    _pa = PatchAutoApprover()
    assert all(_pa.classify(r)[0] != DECISION_STALE for r in _back), \
        "回收进来的件又被判 stale ⇒ 30/7 阈值仍不同源"
