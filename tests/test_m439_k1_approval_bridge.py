# -*- coding: utf-8 -*-
"""439 刀1 单测：审批桥（八项硬条件 / 只建议不改状态 / 决策落盘 / id 写口）。

★判据来自行为：构造补丁字典 → `decision()` / `maybe_approve()` → 看闸门与写口调用。
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
import nucleus.evolution.approval_bridge as ab


def _good(**kw):
    """一条**全条件满足**的补丁（只改一个非核心文件、baseline>0、problem_gone=True）。"""
    _b = {
        "id": "pilot_439",
        "file": "organs/motor/PulseController.py",
        "method": "refresh_runtime_params",
        "risk_level": "低",
        "trust_score": 75.0,
        "confidence": "high",
        "baseline_errors": 3,
        "problem_gone": True,
        "verified": True,
    }
    _b.update(kw)
    return _b


class _FakePM:
    def __init__(self, ok=True):
        self.calls = []
        self._ok = ok

    def approve_patch_by_id(self, pid, by=None, reason=None, judged_by=None):
        self.calls.append({"pid": pid, "by": by, "reason": reason, "judged_by": judged_by})
        return {"ok": self._ok, "reason": "fake", "patch_id": pid}


# ---------------------------------------------------------------- ① 全通过
def test_all_gates_pass():
    _d = ab.decision(_good())
    assert _d["approved"] is True, _d["reason"]
    assert len(_d["gates"]) == 8, f"闸门数={len(_d['gates'])}（应 8）"


# ---------------------------------------------------------------- ② 逐条否决
@pytest.mark.parametrize("mutate,gate", [
    (lambda p: p.update(risk_level="高"), "risk_low"),
    (lambda p: p.update(trust_score=59.9), "trust"),
    (lambda p: p.update(confidence="medium"), "confidence_high"),
    (lambda p: p.update(baseline_errors=0), "baseline_positive"),
    (lambda p: p.update(baseline_errors=None), "baseline_positive"),
    (lambda p: p.pop("baseline_errors"), "baseline_positive"),
    (lambda p: p.update(problem_gone=False), "problem_gone_true"),
    (lambda p: p.pop("problem_gone"), "problem_gone_true"),
    (lambda p: p.update(files=["a.py", "b.py"]), "blast_le_1"),
    (lambda p: p.update(file="nucleus/reasoning/PatchManager.py"), "not_core"),
    (lambda p: p.update(file="main.py"), "not_core"),
])
def test_single_gate_blocks(mutate, gate):
    _p = _good()
    mutate(_p)
    _d = ab.decision(_p)
    assert _d["approved"] is False
    assert _d["gates"][gate]["ok"] is False, f"{gate} 未拦住: {_d['gates'][gate]}"


def test_trust_threshold_reads_config(monkeypatch):
    monkeypatch.setattr(config, "M439_APPROVE_MIN_TRUST", 50.0, raising=True)
    assert ab.decision(_good(trust_score=55))["approved"] is True
    assert ab.decision(_good(trust_score=45))["approved"] is False


# ---------------------------------------------------------------- ③ 不改状态
def test_decision_is_pure():
    """`decision()` 只读：不得给补丁加任何字段。"""
    _p = _good()
    _before = dict(_p)
    ab.decision(_p)
    assert _p == _before, "decision() 改写了补丁字典"


def test_maybe_approve_does_not_write_when_blocked():
    _pm = _FakePM()
    _r = ab.maybe_approve(_good(baseline_errors=0), _pm)
    assert _r["approved"] is False
    assert _r["status_written"] is False
    assert _pm.calls == [], "被拦下的补丁仍调用了状态写口"


def test_maybe_approve_writes_only_via_patch_manager(tmp_path):
    _pm = _FakePM()
    _r = ab.maybe_approve(_good(), _pm, root=str(tmp_path))
    assert _r["approved"] is True
    assert _r["status_written"] is True
    assert len(_pm.calls) == 1 and _pm.calls[0]["pid"] == "pilot_439"
    assert _pm.calls[0]["by"] == "auto:bridge"


# ---------------------------------------------------------------- ④ 决策落盘
def test_decision_recorded_to_file(tmp_path):
    _pm = _FakePM()
    ab.maybe_approve(_good(), _pm, root=str(tmp_path))
    _p = os.path.join(str(tmp_path), ab.DECISION_REL)
    assert os.path.isfile(_p), "决策未落盘"
    with open(_p, encoding="utf-8") as _f:
        _doc = json.load(_f)
    assert len(_doc) == 1
    assert _doc[0]["patch_id"] == "pilot_439"
    assert _doc[0]["approved"] is True
    assert set(_doc[0]["gates"]) == {
        "risk_low", "trust", "confidence_high", "baseline_positive",
        "problem_gone_true", "blast_le_1", "not_core", "single_file"}


def test_record_appends_not_overwrites(tmp_path):
    ab.record(ab.decision(_good()), _good(), root=str(tmp_path))
    ab.record(ab.decision(_good(id="second")), _good(id="second"), root=str(tmp_path))
    with open(os.path.join(str(tmp_path), ab.DECISION_REL), encoding="utf-8") as _f:
        assert len(json.load(_f)) == 2


# ---------------------------------------------------------------- ⑤ 总闸与缺口
def test_switch_off_skips_status_write(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M439_APPROVAL_BRIDGE", False, raising=True)
    _pm = _FakePM()
    _r = ab.maybe_approve(_good(), _pm, root=str(tmp_path))
    assert _r["approved"] is True          # 判定仍执行
    assert _r["status_written"] is False   # 但不落状态
    assert _pm.calls == []


def test_missing_writer_reports_instead_of_crashing(tmp_path):
    _r = ab.maybe_approve(_good(), object(), root=str(tmp_path))
    assert _r["status_written"] is False
    assert "approve_patch_by_id" in _r["write_result"]["reason"]


# ---------------------------------------------------------------- ⑥ 桥不碰源码
def test_bridge_source_has_no_executor_or_write():
    import inspect
    _src = inspect.getsource(ab)
    for _bad in ("apply_patch", "SafeEvolutionExecutor", "open(.*\"w\".*pending",
                 "_save_patch_list", "write_text"):
        assert _bad not in _src, f"审批桥里出现越权写口: {_bad}"
