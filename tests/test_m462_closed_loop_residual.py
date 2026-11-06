# -*- coding: utf-8 -*-
"""第462批：闭环剩余三项 · 单测

刀1 T0 前提核实（生成前证伪问题是否仍成立）
刀2 A/B 开关回退（同一 case 关/开各跑一次，要求有判别力，try/finally 恢复）
刀3 按根因类型分派（消费 459 voided_feature 的结构化 error_type）

所有写盘走 `root=tmp_path`；默认生产路径在 pytest 下由护栏挡住。
"""
from __future__ import annotations

import json
import os

import pytest

import config
from nucleus.evolution import behavior_ab as AB
from nucleus.evolution import premise_guard as PG
from nucleus.evolution import root_cause_router as RCR
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
    (tmp_path / "mod.py").write_text(
        "def periodic_task(self):\n" + FRAG + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "M452_FAILURE_EXPERIENCE_PATH",
                        str(tmp_path / "failure_experience.jsonl"))
    return tmp_path


def _issue(**kw):
    d = {"file": "mod.py", "method": "periodic_task",
         "issue_type": "periodic_task_no_reentry",
         "original_code": FRAG}
    d.update(kw)
    return d


# ==================== 刀1：T0 前提核实 ====================

def test_states_and_schema():
    assert PG.SCHEMA == "premise_guard/462.v1"
    assert (PG.P_CONFIRMED, PG.P_STALE, PG.P_UNKNOWN) == (
        "confirmed", "stale", "unknown")


def test_missing_file_is_stale(iso):
    r = PG.verify_premise(_issue(file="gone.py"), root=str(iso))
    assert r["premise"] == PG.P_STALE
    assert "目标文件不存在" in r["why"]


def test_missing_method_is_stale(iso):
    r = PG.verify_premise(_issue(method="no_such_method"), root=str(iso))
    assert r["premise"] == PG.P_STALE and "已不在源码" in r["why"]


def test_original_code_gone_is_stale(iso):
    (iso / "mod.py").write_text("def periodic_task(self):\n    return 1\n",
                                encoding="utf-8")
    r = PG.verify_premise(_issue(), root=str(iso))
    assert r["premise"] == PG.P_STALE and "原始代码片段" in r["why"]


def test_confirmed_when_detector_hits_snippet(iso):
    r = PG.verify_premise(_issue(), root=str(iso))
    assert r["premise"] == PG.P_CONFIRMED, r
    assert r["hits"] >= 1 and r["scope"] == "snippet"


def test_zero_detector_hits_is_unknown_not_stale(iso):
    """★462 首轮实测：整文件命中 0 不等于问题消失（non_atomic_write 同型）。"""
    (iso / "mod.py").write_text("x = 1\n", encoding="utf-8")
    r = PG.verify_premise(_issue(original_code="", method=""), root=str(iso))
    assert r["premise"] == PG.P_UNKNOWN
    assert "不冒充" in r["why"]


def test_no_file_is_unknown(iso):
    r = PG.verify_premise({"issue_type": "x"}, root=str(iso))
    assert r["premise"] == PG.P_UNKNOWN


def test_filter_issues_only_removes_stale(iso):
    issues = [_issue(), _issue(file="gone.py"), "not-a-dict"]
    kept, n, rows = PG.filter_issues(issues, root=str(iso))
    assert n == 1 and rows[0]["file"] == "gone.py"
    assert len(kept) == 2 and kept[1] == "not-a-dict"


def test_premise_gate_off(iso, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M462_PREMISE_GUARD", False)
    r = PG.verify_premise(_issue(file="gone.py"), root=str(iso))
    assert r["premise"] == PG.P_UNKNOWN
    kept, n, _ = PG.filter_issues([_issue(file="gone.py")], root=str(iso))
    assert n == 0 and len(kept) == 1


def test_premise_filter_blocked_under_pytest_default_path():
    kept, n, _ = PG.filter_issues([_issue(file="gone.py")])
    assert n == 0 and len(kept) == 1


def test_premise_trace_written_to_isolated_root(iso):
    PG.filter_issues([_issue(file="gone.py")], root=str(iso))
    p = PG.trace_path(str(iso))
    assert os.path.isfile(p)
    with open(p, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    assert rows and rows[0]["premise"] == PG.P_STALE


def test_premise_wiring_in_evolution_loop():
    with open(os.path.join(_ROOT, "nucleus", "evolution", "EvolutionLoop.py"),
              encoding="utf-8") as f:
        src = f.read()
    assert src.count("_m462_pg.filter_issues(_final)") == 1


# ==================== 刀2：A/B 开关回退 ====================

def test_ab_compare_discriminative_and_restores():
    class Cfg:
        flag = True

    obj = Cfg()
    probe = lambda case: "on" if obj.flag else "off"
    r = AB.ab_compare(probe, switch_obj=obj, switch_name="flag",
                      off_value=False, on_value=True, case=None)
    assert r["ok"] is True and r["discriminative"] is True
    assert r["off"] == "off" and r["on"] == "on"
    assert r["restored"] is True and obj.flag is True


def test_ab_compare_no_discriminative_power():
    class Cfg:
        flag = True

    obj = Cfg()
    r = AB.ab_compare(lambda case: "same", switch_obj=obj, switch_name="flag",
                      off_value=False, on_value=True)
    assert r["discriminative"] is False
    assert "无判别力" in r["why"] and obj.flag is True


def test_ab_compare_cleans_up_new_attribute():
    class Cfg:
        pass

    obj = Cfg()
    r = AB.ab_compare(lambda case: 1, switch_obj=obj, switch_name="newflag",
                      off_value=False, on_value=True)
    assert r["restored"] is True and not hasattr(obj, "newflag")


def test_ab_compare_probe_exception_is_not_a_pass():
    class Cfg:
        flag = True

    obj = Cfg()

    def boom(case):
        raise RuntimeError("probe failed")

    r = AB.ab_compare(boom, switch_obj=obj, switch_name="flag")
    assert r["discriminative"] is False and "异常" in r["why"]
    assert obj.flag is True


def test_snippet_switch_ab_on_real_fragment():
    p = {"id": "x", "file": "organs/core/PulseSystemManager.py",
         "method": "periodic_task", "issue_type": "periodic_task_no_reentry",
         "original_code": FRAG, "modified_code": FULL}
    r = AB.probe_snippet_switch(p)
    assert r["discriminative"] is True, r
    assert r["off"]["verdict"] != r["on"]["verdict"], r
    assert r["restored"] is True
    assert config.ENABLE_M461_SNIPPET_NORMALIZE is True


def test_collect_evidence_records_ab_fields():
    p = {"id": "x", "file": "mod.py", "method": "periodic_task",
         "issue_type": "periodic_task_no_reentry", "confidence": "medium",
         "original_code": FRAG, "modified_code": FULL}
    e = SV.collect_evidence(p)
    assert e["ab_discriminative"] is True
    assert e["ab_restored"] is True
    assert e["confidence_promoted"] is True


def test_no_promotion_when_ab_has_no_discriminative_power(monkeypatch):
    monkeypatch.setattr(AB, "probe_snippet_switch",
                        lambda patch, **kw: {"discriminative": False,
                                             "restored": True, "why": "stub"})
    p = {"id": "x", "file": "mod.py", "method": "periodic_task",
         "issue_type": "periodic_task_no_reentry", "confidence": "medium",
         "original_code": FRAG, "modified_code": FULL}
    e = SV.collect_evidence(p)
    assert e["ab_discriminative"] is False
    assert e["confidence_promoted"] is False
    assert e["patch"]["confidence"] == "medium"


def test_ab_gate_off_keeps_old_behavior(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M462_BEHAVIOR_AB", False)
    p = {"id": "x", "file": "mod.py", "method": "periodic_task",
         "issue_type": "periodic_task_no_reentry", "confidence": "medium",
         "original_code": FRAG, "modified_code": FULL}
    e = SV.collect_evidence(p)
    assert e["ab_discriminative"] is None
    assert e["confidence_promoted"] is True


# ==================== 刀3：按根因类型分派 ====================

def test_router_schema_and_generic_default(iso):
    assert RCR.SCHEMA == "root_cause_router/462.v1"
    r = RCR.route({"issue_type": "never_seen_before"}, root=str(iso))
    assert r["action"] == "generic" and r["prompt_hint"] == ""


def test_structured_failure_takes_priority_over_issue_type(iso):
    p = os.path.join(str(iso), "failure_experience.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"error_type": "voided_no_def"}) + "\n")
    r = RCR.route({"issue_type": "non_atomic_write"}, root=str(iso))
    assert r["matched"] == "voided_no_def"
    assert r["prefer_strategy"] == "llm_full_def"
    assert "完整方法定义" in r["prompt_hint"]


def test_issue_type_route_when_no_failure_history(iso):
    r = RCR.route({"issue_type": "non_atomic_write"}, root=str(iso))
    assert r["matched"] == "non_atomic_write"
    assert "os.replace" in r["prompt_hint"]


def test_append_hints_is_idempotent_and_append_only(iso):
    p = os.path.join(str(iso), "failure_experience.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"error_type": "voided_no_def"}) + "\n")
    base = "ORIGINAL PROMPT"
    once = RCR.append_hints(base, {"issue_type": "x"}, root=str(iso))
    twice = RCR.append_hints(once, {"issue_type": "x"}, root=str(iso))
    assert once.startswith(base) and RCR.HEAD in once
    assert twice == once


def test_recent_error_types_reads_experience(iso):
    p = os.path.join(str(iso), "failure_experience.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"error_type": "a"}) + "\n")
        f.write(json.dumps({"error_type": "b"}) + "\n")
    assert RCR.recent_error_types(root=str(iso)) == ["b", "a"]


def test_router_gate_off(iso, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M462_ROOT_CAUSE_ROUTER", False)
    r = RCR.route({"issue_type": "non_atomic_write"}, root=str(iso))
    assert r["action"] == "generic"
    assert RCR.append_hints("PROMPT", {"issue_type": "x"}, root=str(iso)) == "PROMPT"


def test_router_trace_and_wiring(iso):
    RCR.route({"issue_type": "non_atomic_write"}, root=str(iso))
    assert os.path.isfile(RCR.trace_path(str(iso)))
    with open(os.path.join(_ROOT, "nucleus", "reasoning",
                           "SafeEvolutionExecutor.py"), encoding="utf-8") as f:
        src = f.read()
    assert src.count("_m462_rcr.append_hints(_prompt, issue)") == 1
    assert src.find("_m445_append_full_def_requirement(_prompt, issue)") < \
           src.find("_m462_rcr.append_hints(_prompt, issue)")
