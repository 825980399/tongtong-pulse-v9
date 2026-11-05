# -*- coding: utf-8 -*-
"""第460批 自裁决闭环 · 单测

覆盖星轨对话裁决的四条实质要求：
  ① 证据准入（不可判定 ⇒ 不放行；探针只补缺失、绝不覆盖既有证据）
  ② 真自动回退（字节级快照 + sha256 校验 + 行尾守恒）
  ③ 同类错误冷却（结构化 error_type + 连续同类自动停手）
  ④ 总闸可回退（关掉逐字节回到 460 前行为，便于 A/B 复现旧现场）

所有写盘都通过 `root=tmp_path` 隔离；`M452_FAILURE_EXPERIENCE_PATH` 由
monkeypatch 指向 tmp，绝不碰生产留痕。
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

import config
from nucleus.evolution import self_verdict as SV

_ORIG_CRLF = b"x = 1\r\ny = 2\r\n"


@pytest.fixture
def iso(tmp_path, monkeypatch):
    (tmp_path / "mod.py").write_bytes(_ORIG_CRLF)
    monkeypatch.setattr(config, "M452_FAILURE_EXPERIENCE_PATH",
                        str(tmp_path / "failure_experience.jsonl"))
    return tmp_path


def _patch(**kw):
    p = {
        "id": "p460_probe",
        "file": "mod.py",
        "method": "_f",
        "issue_type": "bare_except",
        "risk_level": "低",
        "trust_score": 50,
        "confidence": "high",
        "baseline_errors": 2,
        "problem_gone": True,
        "original_code": "def f():\n    try:\n        pass\n    except:\n        pass\n",
        "modified_code": "def f():\n    return 1\n",
    }
    p.update(kw)
    return p


def _good_evidence(**kw):
    e = {
        "ok": True,
        "reprobe_verdict": "true_pass",
        "problem_gone": True,
        "baseline_errors": 2,
        "baseline_source": "code_repro",
        "why": "复现实测",
        "patch": _patch(),
    }
    e.update(kw)
    return e


def _rows(root):
    p = SV.ledger_path(str(root))
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------------- ① 问题单准入 ----------------

def test_states_are_exact_literals():
    assert (SV.V_APPLY, SV.V_REJECT, SV.V_INCONCLUSIVE) == (
        "apply", "reject", "inconclusive")
    assert SV.SCHEMA == "self_verdict/460.v1"


def test_dedup_key_is_stable_and_matches_pool(iso):
    p = _patch()
    k1 = SV.dedup_key(p)
    k2 = SV.dedup_key(dict(p))
    assert k1 == k2 and len(k1) == 16
    from nucleus.audit_problem_pool import make_dedup_key
    assert k1 == make_dedup_key("mod.py", "_f", "bare_except")


def test_ticket_rejects_issue_without_evidence(iso):
    r = SV.make_ticket({"id": "blank"}, root=str(iso), now=1000.0)
    assert r["admitted"] is False
    assert "无定位证据" in r["why"]


def test_ticket_aggregates_same_key_in_window(iso, monkeypatch):
    monkeypatch.setattr(config, "M460_TICKET_AGGREGATE_SEC", 900.0)
    p = _patch()
    SV.note_ledger({"kind": "verdict", "at": 1000.0,
                    "dedup_key": SV.dedup_key(p)}, root=str(iso))
    r = SV.make_ticket(p, root=str(iso), now=1100.0)
    assert r["admitted"] is True
    assert r["merged"] is True and r["hits"] == 1
    assert r["ticket"]["dedup_key"] == SV.dedup_key(p)


def test_ticket_does_not_aggregate_outside_window(iso, monkeypatch):
    monkeypatch.setattr(config, "M460_TICKET_AGGREGATE_SEC", 900.0)
    p = _patch()
    SV.note_ledger({"kind": "verdict", "at": 100.0,
                    "dedup_key": SV.dedup_key(p)}, root=str(iso))
    r = SV.make_ticket(p, root=str(iso), now=5000.0)
    assert r["merged"] is False and r["hits"] == 0


# ---------------- ② 证据生产：绝不覆盖既有证据 ----------------

def test_collect_evidence_never_overwrites_existing_fields():
    """★460 首轮真实回归：探针如实给 0，把构造好的 baseline_errors=2 冲成 0
    ⇒ 八闸 baseline_positive 变红、447 三套测试被顶红。契约：只补缺失。
    """
    p = _patch(baseline_errors=2, problem_gone=True)
    e = SV.collect_evidence(p)
    assert e["ok"] is True
    assert e["patch"]["baseline_errors"] == 2, e
    assert e["patch"]["problem_gone"] is True, e
    assert e["baseline_errors"] == 2 and e["problem_gone"] is True


def test_collect_evidence_returns_copy_and_leaves_original_untouched():
    p = _patch()
    before = dict(p)
    e = SV.collect_evidence(p)
    assert e["patch"] is not p
    assert p == before, "证据生产不许改原 patch 对象"


def test_evidence_fills_missing_fields_only():
    p = _patch()
    del p["baseline_errors"]
    del p["problem_gone"]
    e = SV.collect_evidence(p)
    assert e["ok"] is True
    assert "reprobe_verdict" in e["patch"]
    assert isinstance(e["baseline_errors"], (int, type(None)))
    assert e["reprobe_verdict"], "现场探针必须给出结论字面量"


# ---------------- ③ 三态自裁决 ----------------

def test_all_evidence_pass_yields_apply(iso):
    r = SV.verdict(_good_evidence(), _patch(), root=str(iso), now=1000.0)
    assert r["verdict"] == SV.V_APPLY, r
    assert r["checks"]["problem_gone_true"] is True
    assert r["checks"]["baseline_positive"] is True


def test_false_pass_is_inconclusive_not_a_failure(iso):
    """修复前复现不出 ⇒ 无基线，不可判定；绝不猜成通过、也不罚成失败。"""
    r = SV.verdict(_good_evidence(reprobe_verdict="false_pass",
                                  problem_gone=None, baseline_errors=None),
                   _patch(), root=str(iso), now=1000.0)
    assert r["verdict"] == SV.V_INCONCLUSIVE, r
    assert "待补证重判" in r["reason"]


def test_inconclusive_has_retry_cap(iso, monkeypatch):
    monkeypatch.setattr(config, "M460_MAX_REPRO_RETRY", 3)
    e = _good_evidence(reprobe_verdict="false_pass", problem_gone=None,
                       baseline_errors=None)
    r = SV.verdict(e, _patch(), retry_count=3, root=str(iso), now=1000.0)
    assert r["verdict"] == SV.V_REJECT
    assert "重试已达上限" in r["reason"]


def test_partial_fix_is_rejected(iso):
    r = SV.verdict(_good_evidence(reprobe_verdict="partial_fix",
                                  problem_gone=False),
                   _patch(), root=str(iso), now=1000.0)
    assert r["verdict"] == SV.V_REJECT and "partial_fix" in r["reason"]


def test_blast_over_limit_is_rejected(iso, monkeypatch):
    monkeypatch.setattr(config, "M460_MAX_BLAST_FILES", 1)
    r = SV.verdict(_good_evidence(), _patch(files=["a.py", "b.py"]),
                   root=str(iso), now=1000.0)
    assert r["verdict"] == SV.V_REJECT and "影响面" in r["reason"]


def test_gate_failure_is_rejected(iso):
    gates = {"risk_low": {"ok": True}, "not_core": {"ok": False}}
    r = SV.verdict(_good_evidence(), _patch(), gates=gates,
                   root=str(iso), now=1000.0)
    assert r["verdict"] == SV.V_REJECT and "not_core" in r["reason"]


# ---------------- ④ 字节快照与真回退 ----------------

def test_snapshot_preserves_bytes_and_eol(iso):
    s = SV.snapshot_before(_patch(), root=str(iso), now=1000.0)
    assert s["ok"] is True, s
    m = s["files"][0]
    assert m["eol"] == "CRLF" and m["crlf"] == 2 and m["bare_lf"] == 0
    assert m["sha256"] == hashlib.sha256(_ORIG_CRLF).hexdigest()
    assert os.path.isfile(os.path.join(SV.snapshot_dir(str(iso)),
                                       s["snapshot_id"], "meta.json"))


def test_restore_writes_real_bytes_back(iso):
    s = SV.snapshot_before(_patch(), root=str(iso), now=1000.0)
    (iso / "mod.py").write_bytes(b"broken = (\n")
    r = SV.restore_snapshot(s, root=str(iso))
    assert r["ok"] is True, r
    assert (iso / "mod.py").read_bytes() == _ORIG_CRLF


def test_finalize_auto_rolls_back_on_verify_failure(iso):
    s = SV.snapshot_before(_patch(), root=str(iso), now=1000.0)
    (iso / "mod.py").write_bytes(b"broken = (\n")
    prep = {
        "admitted": True,
        "patch": _patch(),
        "verdict": {"verdict": SV.V_APPLY, "reason": "证据齐备",
                    "dedup_key": SV.dedup_key(_patch())},
        "snapshot": s,
    }
    fin = SV.finalize(prep, {"approved": True, "status_written": True},
                      root=str(iso), now=1001.0,
                      verify_fn=lambda p: {"ok": False, "why": "注入失败"})
    assert fin["applied"] is True and fin["verified"] is False
    assert fin["rolled_back"] is True, fin
    assert (iso / "mod.py").read_bytes() == _ORIG_CRLF, "必须真的写回"
    assert fin["lesson"]["error_type"] == "self_verdict:post_apply_verify_failed"
    assert any(r.get("kind") == "rollback" for r in _rows(iso))


def test_finalize_no_lesson_on_plain_gate_reject(iso):
    """八闸正常拒绝不是"改错了"，不许灌进经验库稀释提醒段。"""
    prep = {"admitted": True, "patch": _patch(),
            "verdict": {"verdict": SV.V_REJECT, "reason": "八闸未过",
                        "dedup_key": SV.dedup_key(_patch())},
            "snapshot": {}}
    fin = SV.finalize(prep, {"approved": False}, root=str(iso), now=1000.0)
    assert fin["rolled_back"] is False
    assert fin["lesson"] == {}
    assert not any(r.get("kind") == "lesson" for r in _rows(iso))


# ---------------- ⑤ 同类错误冷却 ----------------

def test_same_kind_failures_trigger_cooldown(iso, monkeypatch):
    monkeypatch.setattr(config, "M460_LESSON_COOLDOWN_N", 2)
    monkeypatch.setattr(config, "M460_LESSON_COOLDOWN_SEC", 86400.0)
    key = SV.dedup_key(_patch())
    r1 = SV.record_lesson(key=key, error_type="verify_failed",
                          reason="第一次", root=str(iso), now=1000.0)
    r2 = SV.record_lesson(key=key, error_type="verify_failed",
                          reason="第二次", root=str(iso), now=1001.0)
    assert r1["cooldown"] is False and r2["cooldown"] is True, (r1, r2)
    assert r2["same_kind_streak"] == 2
    assert SV.cooldown_active(key, root=str(iso), now=1002.0) > 1002.0
    fp = str(iso / "failure_experience.jsonl")
    with open(fp, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    assert len(rows) == 2
    assert all(r["error_type"] == "self_verdict:verify_failed" for r in rows)


def test_ticket_blocked_during_cooldown(iso, monkeypatch):
    monkeypatch.setattr(config, "M460_LESSON_COOLDOWN_N", 1)
    key = SV.dedup_key(_patch())
    SV.record_lesson(key=key, error_type="verify_failed", reason="x",
                     root=str(iso), now=1000.0)
    r = SV.make_ticket(_patch(), root=str(iso), now=1001.0)
    assert r["admitted"] is False and r["cooldown"] is True, r


# ---------------- ⑥ 总闸 + 接线 + 护栏 ----------------

def test_gate_off_returns_old_behavior(iso, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M460_SELF_VERDICT", False)
    p = _patch()
    r = SV.prepare(p, root=str(iso), now=1000.0)
    assert r["admitted"] is False
    assert r["patch"] == p, "关闸必须逐字节回到 460 前行为"
    assert _rows(iso) == [], "关闸不许写任何留痕"


def test_wiring_is_present_in_approval_bridge():
    """源码级防「接了又断」：审批写口两侧必须有 prepare / finalize。"""
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "nucleus", "evolution", "approval_bridge.py")
    with open(p, encoding="utf-8") as f:
        src = f.read()
    assert src.count("from nucleus.evolution.self_verdict import prepare") == 1
    assert src.count("from nucleus.evolution.self_verdict import finalize") == 1
    assert "maybe_approve(_m460_patch" in src


def test_default_production_path_is_not_written_under_pytest():
    assert SV._writable(None) is False
    assert SV.note_ledger({"kind": "probe"}, root=None) is False
