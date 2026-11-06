# -*- coding: utf-8 -*-
"""第461批：让自修复闭环真正跑通一轮 · 单测

三刀覆盖：
  刀1 生成侧前置闸 + 统一同类错误冷却（实盘证伪：同一 no_def 作废 59 次）
  刀2 证据契约修复（实盘：片段 original_code ⇒ false_pass、LLM 补丁 confidence=medium
      结构性过不了八闸）
  刀3 主进程运行期派单 + 闭环结果回传（此前只有三源发现 + stats 日志）

所有写盘走 `root=tmp_path`；默认生产路径在 pytest 下由护栏挡住。
"""
from __future__ import annotations

import json
import os

import pytest

import config
from nucleus.evolution import lesson_cooldown as LC
from nucleus.evolution import runtime_dispatch as RD
from nucleus.evolution import self_verdict as SV

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FRAG = ("        if not self._running:\n"
        "            return\n"
        "        self._health_timer = threading.Timer(300, self._auto_health_check)\n"
        "        self._health_timer.daemon = True\n"
        "        self._health_timer.start()")
FULL = ("def periodic_task(self):\n"
        "    if not self._running:\n"
        "        return\n"
        "    if self._in_progress:\n"
        "        return\n"
        "    self._in_progress = True\n"
        "    try:\n"
        "        self._health_timer = threading.Timer(300, self._auto_health_check)\n"
        "        self._health_timer.daemon = True\n"
        "        self._health_timer.start()\n"
        "    finally:\n"
        "        self._in_progress = False")


@pytest.fixture
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "M452_FAILURE_EXPERIENCE_PATH",
                        str(tmp_path / "failure_experience.jsonl"))
    return tmp_path


def _patch(**kw):
    p = {"id": "p461", "file": "organs/core/PulseSystemManager.py",
         "method": "periodic_task", "issue_type": "periodic_task_no_reentry",
         "confidence": "medium", "baseline_errors": None}
    p.update(kw)
    return p


def _ledger_rows(root):
    p = LC.ledger_path(str(root))
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _rd_rows(root):
    p = RD.ledger_path(str(root))
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ==================== 刀1：统一冷却与生成侧硬拦 ====================

def test_schema_and_keys():
    assert LC.SCHEMA == "lesson_cooldown/461.v1"
    assert RD.SCHEMA == "runtime_dispatch/461.v1"
    assert LC.target_key("a/b.py", "m") == LC.target_key("a\\b.py", "m")
    assert LC.failure_key("a.py", "m", "e1") != LC.failure_key("a.py", "m", "e2")


def test_two_same_kind_failures_trigger_cooldown(iso, monkeypatch):
    monkeypatch.setattr(config, "M461_COOLDOWN_STREAK_N", 2)
    r1 = LC.record(file="PulseLiver.py", method="_compress_group",
                   error_type="voided_no_def", reason="缺 def",
                   source="converge_voided", root=str(iso), now=1000.0)
    r2 = LC.record(file="PulseLiver.py", method="_compress_group",
                   error_type="voided_no_def", reason="又缺 def",
                   source="converge_voided", root=str(iso), now=1001.0)
    assert r1["cooldown"] is False and r1["streak"] == 1
    assert r2["cooldown"] is True and r2["streak"] == 2
    assert LC.cooldown_until("PulseLiver.py", "_compress_group",
                             root=str(iso), now=1002.0) > 1002.0


def test_different_error_type_breaks_streak(iso, monkeypatch):
    monkeypatch.setattr(config, "M461_COOLDOWN_STREAK_N", 2)
    LC.record(file="a.py", method="m", error_type="e1", root=str(iso), now=1000.0)
    LC.record(file="a.py", method="m", error_type="e2", root=str(iso), now=1001.0)
    r = LC.record(file="a.py", method="m", error_type="e1",
                  root=str(iso), now=1002.0)
    assert r["streak"] == 1 and r["cooldown"] is False


def test_cooldown_applies_to_whole_target_not_only_same_error(iso, monkeypatch):
    """目标已被反复证明改不对 ⇒ 整体停手，不赌换个错误类型就能对。"""
    monkeypatch.setattr(config, "M461_COOLDOWN_STREAK_N", 2)
    LC.record(file="a.py", method="m", error_type="e1", root=str(iso), now=1000.0)
    LC.record(file="a.py", method="m", error_type="e1", root=str(iso), now=1001.0)
    ok, why = LC.guard_patch({"file": "a.py", "method": "m",
                              "issue_type": "totally_different"},
                             root=str(iso), now=1002.0)
    assert ok is False and "冷却" in why
    ok2, _ = LC.guard_patch({"file": "b.py", "method": "m"},
                            root=str(iso), now=1002.0)
    assert ok2 is True


def test_filter_pending_blocks_only_cooled_items(iso, monkeypatch):
    monkeypatch.setattr(config, "M461_COOLDOWN_STREAK_N", 1)
    LC.record(file="a.py", method="m", error_type="e1", root=str(iso), now=1000.0)
    data = [{"id": "1", "file": "a.py", "method": "m", "status": "pending"},
            {"id": "2", "file": "b.py", "method": "n", "status": "pending"},
            "not-a-dict"]
    kept, n, rows = LC.filter_pending(data, root=str(iso), now=1001.0)
    assert n == 1 and rows[0]["id"] == "1"
    assert len(kept) == 2 and kept[0]["id"] == "2" and kept[1] == "not-a-dict"


def test_gate_off_returns_old_behavior(iso, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M461_LESSON_COOLDOWN", False)
    r = LC.record(file="a.py", method="m", error_type="e1", root=str(iso), now=1000.0)
    assert r["ok"] is False
    ok, _ = LC.guard_patch({"file": "a.py", "method": "m"}, root=str(iso))
    assert ok is True


def test_hint_for_prompt_is_soft_complement(iso, monkeypatch):
    monkeypatch.setattr(config, "M461_COOLDOWN_STREAK_N", 1)
    assert LC.hint_for_prompt("a.py", "m", root=str(iso), now=1000.0) == ""
    LC.record(file="a.py", method="m", error_type="e1", root=str(iso), now=1000.0)
    assert "不要为它生成补丁" in LC.hint_for_prompt(
        "a.py", "m", root=str(iso), now=1001.0)


def test_cooldown_wiring_covers_both_failure_paths():
    """源码级：作废路径与自裁决路径都必须写同一份冷却台账。"""
    with open(os.path.join(_ROOT, "nucleus", "evolution", "criteria_window.py"),
              encoding="utf-8") as f:
        cw = f.read()
    assert "lesson_cooldown" in cw and "_m461_lc.record(" in cw
    with open(os.path.join(_ROOT, "nucleus", "evolution", "self_verdict.py"),
              encoding="utf-8") as f:
        sv = f.read()
    assert "_m461_lc.record(" in sv


def test_guard_wired_into_unique_pending_writer():
    """源码级：守卫必须挂在 pending 唯一收口，而非某一条生成路径。"""
    with open(os.path.join(_ROOT, "nucleus", "reasoning", "PatchManager.py"),
              encoding="utf-8") as f:
        src = f.read()
    assert src.count("_m461_lc.filter_pending(data)") == 1
    i = src.find("_m461_lc.filter_pending(data)")
    assert src.find("if path == self._pending_file:") < i
    assert src.find("data = self._m280_converge_pending(data)") > i


# ==================== 刀2：证据契约修复 ====================

def test_snippet_normalization_makes_fragment_parseable():
    import ast
    wrapped, ok = SV.normalize_snippet(FRAG)
    assert ok is True
    assert ast.parse(wrapped)
    _, ok2 = SV.normalize_snippet(FULL)
    assert ok2 is False, "完整函数不该被包装"


def test_evidence_pipeline_upgrades_all_three_dimensions():
    """实盘 05:14 同款样本：片段 + medium + baseline=None → 三证齐备 + 提权。"""
    p = _patch(original_code=FRAG, modified_code=FULL)
    e = SV.collect_evidence(p)
    assert e["reprobe_scope"] == "snippet"
    assert e["reprobe_verdict"] == "true_pass"
    assert e["problem_gone"] is True
    assert e["baseline_errors"] == 1
    assert e["confidence_promoted"] is True
    assert e["patch"]["confidence"] == "high"
    assert e["patch"]["confidence_source"] == "m461_reprobe_true_pass"
    assert e["patch"]["original_code"] == FRAG, "留痕必须保留生成器原文"


def test_no_promotion_without_three_evidence():
    p = _patch(original_code="def f():\n    return 1\n",
               modified_code="def f():\n    return 2\n",
               confidence="medium")
    e = SV.collect_evidence(p)
    assert e["confidence_promoted"] is False
    assert e["patch"]["confidence"] == "medium"


def test_existing_high_confidence_is_never_downgraded():
    p = _patch(original_code=FRAG, modified_code=FULL, confidence="high")
    e = SV.collect_evidence(p)
    assert e["patch"]["confidence"] == "high"
    assert e["confidence_promoted"] is False


def test_baseline_never_downgraded():
    """460 首轮顶红的教训：探针给 0 不许把构造好的 2 冲掉。"""
    p = _patch(original_code=FRAG, modified_code=FULL,
               baseline_errors=2, problem_gone=True)
    e = SV.collect_evidence(p)
    assert e["patch"]["baseline_errors"] == 2


def test_snippet_switch_off_preserves_old_behavior(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M461_SNIPPET_NORMALIZE", False)
    e = SV.collect_evidence(_patch(original_code=FRAG, modified_code=FULL))
    assert e["reprobe_scope"] == "as_is"
    assert e["reprobe_verdict"] != "true_pass"


# ==================== 刀3：主进程派单与结果回传 ====================

def test_submit_requires_evidence(iso):
    r = RD.submit_runtime_issue({"id": "blank"}, root=str(iso), now=1000.0)
    assert r["admitted"] is False and "无证据" in r["why"]


def test_submit_aggregates_same_key_in_window(iso, monkeypatch):
    monkeypatch.setattr(config, "M461_RUNTIME_AGGREGATE_SEC", 900.0)
    issue = {"file": "a.py", "method": "m", "issue_type": "timeout",
             "evidence": "委派超时 92s", "source": "cortex_watchdog"}
    r1 = RD.submit_runtime_issue(issue, root=str(iso), now=1000.0)
    r2 = RD.submit_runtime_issue(issue, root=str(iso), now=1010.0)
    r3 = RD.submit_runtime_issue(issue, root=str(iso), now=10000.0)
    assert r1["merged"] is False
    assert r2["merged"] is True and r2["hits"] == 1
    assert r3["merged"] is False, "窗口外算新问题"


def test_ticket_claim_result_consume_roundtrip(iso):
    issue = {"file": "a.py", "method": "m", "issue_type": "timeout",
             "evidence": "委派超时 92s", "question": "为什么没回答",
             "source": "cortex_watchdog", "correlation_id": "cid-1"}
    r = RD.submit_runtime_issue(issue, root=str(iso), now=1000.0)
    key = r["ticket"]["dedup_key"]
    tickets = RD.pending_tickets(root=str(iso))
    assert len(tickets) == 1 and tickets[0]["correlation_id"] == "cid-1"
    assert RD.claim_ticket(key, root=str(iso), now=1001.0) is True
    assert RD.pending_tickets(root=str(iso)) == []

    out = RD.note_loop_result(patch_id="p1", dedup_key_=key,
                              verdict=SV.V_APPLY, applied=True, verified=True,
                              rolled_back=False, reason="证据齐备",
                              root=str(iso), now=1002.0)
    assert out["ok"] is True
    results = RD.consume_loop_results(since=1001.5, root=str(iso))
    assert len(results) == 1 and results[0]["verdict"] == "apply"
    assert results[0]["applied"] is True


def test_finalize_writes_loop_result(iso):
    prep = {"admitted": True, "patch": _patch(),
            "verdict": {"verdict": SV.V_REJECT, "reason": "八闸未过",
                        "dedup_key": SV.dedup_key(_patch())},
            "snapshot": {}}
    SV.finalize(prep, {"approved": False}, root=str(iso), now=1000.0)
    results = RD.consume_loop_results(since=0.0, root=str(iso))
    assert len(results) == 1
    assert results[0]["verdict"] == "reject"
    assert results[0]["rolled_back"] is False


def test_pending_tickets_blocked_under_pytest_default_path():
    assert RD.pending_tickets() == []


def test_dispatch_wiring_all_four_points():
    """源码级防「接了又断」：派单 / 第四源 / 结果回写 / 心跳消费。"""
    def src(rel):
        with open(os.path.join(_ROOT, rel.replace("/", os.sep)),
                  encoding="utf-8") as f:
            return f.read()

    cortex = src("organs/brain/PulseCortex.py")
    assert "runtime_dispatch as _m461_rd" in cortex
    assert "_m461_rd.submit_runtime_issue({" in cortex
    assert "self._m461_consume_loop_results()" in cortex
    assert "def _m461_consume_loop_results" in cortex
    loop = src("nucleus/evolution/EvolutionLoop.py")
    assert "_m461_rd.pending_tickets(limit=10)" in loop
    assert "_m461_rd.claim_ticket(" in loop
    sv = src("nucleus/evolution/self_verdict.py")
    assert "_m461_rd.note_loop_result(" in sv


def test_runtime_dispatch_gate_off(iso, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M461_RUNTIME_DISPATCH", False)
    r = RD.submit_runtime_issue({"evidence": "x"}, root=str(iso), now=1000.0)
    assert r["admitted"] is False
