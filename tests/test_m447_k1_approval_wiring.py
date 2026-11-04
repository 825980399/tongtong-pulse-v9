# -*- coding: utf-8 -*-
"""447 刀1：审批桥接进 repair（安全边界 + 日限额 + 全留痕）。

判据来自任务书 §刀1：八闸判据**不改**，外面加四条安全边界；
每条决策（含被边界挡下的）都必须落 `approval_trace.jsonl`，且带完整前/后内容。
"""
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
from nucleus.evolution import approval_bridge as AB

_ORIG = "def target(self, x):\n    return x\n"
_MODI = "def target(self, x):\n    if x is None:\n        return 0\n    return x\n"


class _FakePM:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def approve_patch_by_id(self, pid, by="", reason="", judged_by=""):
        self.calls.append({"pid": pid, "by": by, "judged_by": judged_by})
        return {"ok": self.ok, "reason": "" if self.ok else "状态写口拒绝"}


def _good(**kw):
    _p = {
        "id": "patch_m447_t1", "file": "organs/body/PulseHeart.py",
        "method": "target", "issue_type": "silent_exception",
        "risk_level": "低", "trust_score": 75, "confidence": "high",
        "baseline_errors": 2, "problem_gone": True,
        "original_code": _ORIG, "modified_code": _MODI,
    }
    _p.update(kw)
    return _p


def _rows(tmp_path):
    _p = tmp_path / "data" / "patches" / "approval_trace.jsonl"
    if not os.path.exists(str(_p)):
        return []
    with open(str(_p), encoding="utf-8") as _f:
        return [json.loads(x) for x in _f if x.strip()]


# ---------------- 安全边界 ----------------

def test_scope_ok_accepts_low_risk_single_file():
    assert AB.scope_ok(_good()) == (True, "边界通过")


def test_scope_rejects_core_file():
    _ok, _why = AB.scope_ok(_good(file="nucleus/reasoning/PatchManager.py"))
    assert _ok is False and "核心文件" in _why, _why


def test_scope_rejects_high_risk():
    _ok, _why = AB.scope_ok(_good(risk_level="高"))
    assert _ok is False and "risk" in _why, _why


def test_scope_rejects_multi_file():
    _ok, _why = AB.scope_ok(_good(files=["a.py", "b.py"]))
    assert _ok is False and "文件数" in _why, _why


# ---------------- 日限额 ----------------

def test_daily_cap_from_config(monkeypatch):
    monkeypatch.setattr(config, "M447_APPROVE_DAILY_CAP", 3, raising=True)
    assert AB.daily_cap() == 3


def test_approved_today_counts_only_today(tmp_path, monkeypatch):
    _p = tmp_path / "data" / "patches" / "approval_trace.jsonl"
    os.makedirs(os.path.dirname(str(_p)), exist_ok=True)
    _today = time.strftime("%Y-%m-%d")
    with open(str(_p), "w", encoding="utf-8") as _f:
        _f.write(json.dumps({"day": _today, "approved": True}) + "\n")
        _f.write(json.dumps({"day": _today, "approved": False}) + "\n")
        _f.write(json.dumps({"day": "1999-01-01", "approved": True}) + "\n")
    assert AB.approved_today(str(tmp_path)) == 1


# ---------------- 留痕 ----------------

def test_note_approval_trace_has_eight_gates_and_full_content(tmp_path):
    _d = AB.decision(_good())
    assert len(_d["gates"]) == 8, _d["gates"]
    _ok = AB.note_approval_trace(_good(), _d, approved=True, scope="边界通过",
                                 root=str(tmp_path))
    assert _ok is True
    _r = _rows(tmp_path)[0]
    assert _r["patch_id"] == "patch_m447_t1"
    assert _r["gates_total"] == 8 and _r["gates_passed"] == 8, _r["gates"]
    assert _r["approved"] is True and _r["decision_reason"] == "全部通过", _r
    assert _r["original_code"] == _ORIG and _r["modified_code"] == _MODI, "前/后内容必须完整"
    assert _r["original_len"] == len(_ORIG), _r
    assert _r["baseline_errors"] == 2 and _r["problem_gone"] is True, _r


def test_note_approval_trace_records_each_gate_reason(tmp_path):
    _d = AB.decision(_good(baseline_errors=0))
    AB.note_approval_trace(_good(baseline_errors=0), _d, approved=False,
                           blocked="gates", root=str(tmp_path))
    _r = _rows(tmp_path)[0]
    assert _r["gates"]["baseline_positive"]["ok"] is False, _r["gates"]
    assert "baseline_errors=0" in _r["gates"]["baseline_positive"]["why"], _r["gates"]
    assert _r["approved"] is False and _r["blocked"] == "gates", _r


# ---------------- 外层入口 ----------------

def test_scoped_blocks_before_eight_gates(tmp_path):
    _pm = _FakePM()
    _r = AB.maybe_approve_scoped(_good(file="config.py"), _pm, root=str(tmp_path))
    assert _r["approved"] is False and _r["blocked"] == "scope", _r
    assert _r["gates"] == {}, "边界拦下不该跑八闸"
    assert _pm.calls == [], "边界拦下不该碰状态写口"
    assert _rows(tmp_path)[0]["blocked"] == "scope"


def test_scoped_blocks_on_daily_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "M447_APPROVE_DAILY_CAP", 1, raising=True)
    _pm = _FakePM()
    _first = AB.maybe_approve_scoped(_good(), _pm, root=str(tmp_path))
    assert _first["approved"] is True, _first
    _second = AB.maybe_approve_scoped(_good(id="patch_m447_t2"), _pm,
                                      root=str(tmp_path))
    assert _second["approved"] is False and _second["blocked"] == "daily_cap", _second
    assert _second["daily_used"] == 1, _second
    assert len(_pm.calls) == 1, "超限额不能再调写口"


def test_scoped_reaches_writer_when_all_gates_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "M447_APPROVE_DAILY_CAP", 5, raising=True)
    _pm = _FakePM()
    _r = AB.maybe_approve_scoped(_good(), _pm, root=str(tmp_path))
    assert _r["approved"] is True and _r["blocked"] == "", _r
    assert _pm.calls and _pm.calls[0]["pid"] == "patch_m447_t1", _pm.calls
    assert _pm.calls[0]["by"].startswith("auto:bridge_447"), _pm.calls
    _row = _rows(tmp_path)[0]
    assert _row["approved"] is True and _row["gates_passed"] == 8, _row


def test_scoped_marks_status_write_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "M447_APPROVE_DAILY_CAP", 5, raising=True)
    _r = AB.maybe_approve_scoped(_good(), _FakePM(ok=False), root=str(tmp_path))
    assert _r["approved"] is False and _r["blocked"] == "status_write", _r
    assert _r["trace"] is True, _r


def test_scoped_handles_empty_patch(tmp_path):
    _r = AB.maybe_approve_scoped({}, _FakePM(), root=str(tmp_path))
    assert _r["approved"] is False and _r["blocked"] == "scope", _r


# ---------------- 接线（源码级断言，防"接了又断"） ----------------

def test_repair_calls_bridge_after_save():
    _p = os.path.join(_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
    with open(_p, encoding="utf-8") as _f:
        _src = _f.read()
    assert "_m447_try_approve(_local_patch" in _src
    assert "_m447_try_approve(_llm_patch" in _src
    # 必须紧跟在 save_pending_patch 之后（同一 try 块内）
    _i = _src.index("save_pending_patch(_local_patch)")
    _j = _src.index("_m447_try_approve(_local_patch", _i)
    assert 0 < _j - _i < 400, (_i, _j)
