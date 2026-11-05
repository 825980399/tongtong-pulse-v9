# -*- coding: utf-8 -*-
"""456 刀6：compare / exclude 算子真执行（454 只打标不执行）。

覆盖任务书验收：
  · 跑一个对比类问题，step_results 里能看到 compare 算子真跑了（status/confidence）
  · insufficient_evidence 时后续依赖步骤标 skipped_dependency
  · 输入是前序证据文本、operands 自动拆、不编造
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from organs.brain.PulseInnerWorld import PulseInnerWorld as IW

_EV_TEXT = ("微服务性能：平均延迟 20 毫秒；复杂度：高，需要服务治理；运维：需要 K8s。\n"
            "单体架构性能：平均延迟 35 毫秒；复杂度：低，一个进程；运维：单机即可。")


# =====================================================================
# 纯函数：operands / dimensions / criteria 拆分
# =====================================================================
@pytest.mark.parametrize("q,expect", [
    ("请对比微服务与单体架构的优劣", ["微服务", "单体架构"]),
    ("比较 Redis 和 Memcached 的区别", ["Redis", "Memcached"]),
    ("A 跟 B 哪个好", ["A", "B"]),
    ("深度学习是什么", []),
    ("请分析一下这个流程", []),
])
def test_split_operands(q, expect):
    assert IW._m456_split_operands(q) == expect


def test_split_dimensions_prefers_words_in_question():
    _d = IW._m456_split_dimensions("对比两者的性能和成本", [])
    assert "性能" in _d and "成本" in _d


def test_split_dimensions_has_fallback():
    """★空 dimensions 会让 compare 直接 bad_input（等于没接）。"""
    assert IW._m456_split_dimensions("对比 A 与 B", []) == ["差异"]


def test_split_criteria_from_exclusion_phrase():
    _c = IW._m456_split_criteria("排除掉不稳定的方案")
    assert _c.get("must"), _c


def test_split_criteria_empty_when_no_phrase():
    assert IW._m456_split_criteria("对比 A 与 B") == {}


# =====================================================================
# 证据收集：只收成功步骤
# =====================================================================
def test_collect_evidence_only_success(iw):
    _rows = [
        {"result": "第一步结论" * 20, "success": True, "confidence": 0.8},
        {"result": "失败的降级叙述", "success": False, "confidence": 0.1},
        {"result": "", "success": True},
        {"result": "第二步结论" * 20, "success": True, "confidence": 0.9},
    ]
    _ev = iw._m456_collect_evidence(_rows)
    assert len(_ev) == 2
    assert _ev[0]["id"] == "E1" and _ev[1]["id"] == "E2"
    assert all("失败的降级叙述" not in e["text"] for e in _ev)


def test_collect_evidence_honours_max(iw, monkeypatch):
    monkeypatch.setattr(config, "M456_OP_MAX_EVIDENCE", 2, raising=False)
    _rows = [{"result": f"结论{i}" * 30, "success": True} for i in range(6)]
    assert len(iw._m456_collect_evidence(_rows)) == 2


# =====================================================================
# 算子真跑
# =====================================================================
def test_compare_really_runs(iw):
    _r = iw._m456_run_local_op(
        "compare", "请对比微服务与单体架构的性能、复杂度、运维",
        {"step_id": "S3", "name": "对比分析"},
        [{"result": _EV_TEXT, "success": True, "confidence": 0.8}])
    assert _r is not None
    assert _r["status"] == "ok", _r
    assert _r["ok"] is True
    assert _r["step_result"]["op"] == "compare"
    assert _r["step_result"]["source"] == "local_op:compare"
    assert 0.0 < float(_r["step_result"]["confidence"]) <= 1.0
    assert "逐维对照" in _r["step_result"]["output_digest"]
    assert _r["trace_row"]["op"] == "compare"
    assert _r["trace_row"]["status"] == "ok"


def test_compare_insufficient_when_no_evidence(iw):
    """无前序证据 ⇒ 算子自己判 insufficient（不编造相似/差异）。"""
    _r = iw._m456_run_local_op("compare", "请对比 A 与 B",
                               {"step_id": "S3", "name": "对比分析"}, [])
    assert _r["ok"] is False
    assert _r["status"] == "insufficient_evidence"
    assert _r["step_result"]["success"] is False
    assert _r["trace_row"]["fail_reason"]


def test_compare_insufficient_when_operands_unsplittable(iw):
    _r = iw._m456_run_local_op("compare", "深度学习是什么",
                               {"step_id": "S3", "name": "对比分析"},
                               [{"result": _EV_TEXT, "success": True}])
    assert _r["status"] == "insufficient_evidence"


def test_exclude_really_runs(iw):
    _r = iw._m456_run_local_op(
        "exclude", "排除掉不稳定的方案",
        {"step_id": "S4", "name": "结论建议"},
        [{"result": _EV_TEXT, "success": True}])
    assert _r is not None
    assert _r["status"] in ("ok", "all_excluded")
    assert _r["step_result"]["op"] == "exclude"
    assert "按标准筛选" in _r["step_result"]["output_digest"]


def test_op_digest_exclude_mentions_counts():
    _res = {"status": "ok", "kept": [1], "dropped": [],
            "counts": {"total": 3, "kept": 2, "dropped": 1}, "warning": None}
    _txt = IW._m456_op_digest("exclude", _res)
    assert "保留 2" in _txt and "共 3" in _txt


# =====================================================================
# v2 接线（端到端：造最小实例，让 compare 步骤真跑）
# =====================================================================
@pytest.fixture()
def iw():
    _i = IW.__new__(IW)
    _i._log = lambda *a, **k: None
    _i._inference_count = 0
    return _i


def _mk_v2(monkeypatch, plan):
    """造一个能跑完 v2 的最小实例：检索类步骤给假结果，算子步骤保持真实。"""
    _i = IW.__new__(IW)
    _i._log = lambda *a, **k: None
    _i._inference_count = 0
    _i._m455_rows = []
    monkeypatch.setattr(IW, "_m31_operational_admission_on", staticmethod(lambda: False))
    monkeypatch.setattr(IW, "_m34_op_recheck_on", staticmethod(lambda: False))
    monkeypatch.setattr(IW, "_m29_has_multi_step_signal", staticmethod(lambda q: False))
    monkeypatch.setattr(IW, "_generate_task_plan", lambda self, q: [dict(s) for s in plan])
    monkeypatch.setattr(IW, "_m31_extract_key_terms",
                        staticmethod(lambda q, limit=3: ["微服务", "单体"]))
    monkeypatch.setattr(IW, "_m35_entry_probe", lambda self, *a, **k: True)
    monkeypatch.setattr(IW, "_knowledge_retrieve", lambda self, p: _EV_TEXT)
    monkeypatch.setattr(IW, "_validate_step_result", lambda self, r, p: bool(r))
    monkeypatch.setattr(IW, "_reframe_step_prompt", lambda self, p, f: p)
    monkeypatch.setattr(IW, "_is_valid_branch_content", lambda self, r: True)
    monkeypatch.setattr(IW, "_generate_branch_with_model", lambda self, **k: "")
    monkeypatch.setattr(IW, "_summarize_step_for_next", lambda self, r: "")
    monkeypatch.setattr(IW, "_m176_a1_multistep_partial_on", lambda self: False)
    monkeypatch.setattr(IW, "_synthesize_step_results",
                        lambda self, sr, q: "SYNTH:" + ",".join(s["name"] for s in sr))
    _i._m455_flush_trace_calls = []
    monkeypatch.setattr(IW, "_m455_flush_trace",
                        lambda self, plan, rows: _i._m455_flush_trace_calls.append(
                            ([dict(x) for x in (plan or [])], [dict(y) for y in (rows or [])])))
    return _i


def _plan_compare():
    return [
        {"name": "现状分析", "prompt": "微服务 现状", "fallback": "", "step_id": "S1",
         "op": "retrieve", "depends_on": []},
        {"name": "对比分析", "prompt": "对比 微服务 单体", "fallback": "", "step_id": "S2",
         "op": "compare", "depends_on": ["S1"]},
        {"name": "结论建议", "prompt": "结论", "fallback": "", "step_id": "S3",
         "op": "exclude", "depends_on": ["S2"]},
    ]


def test_v2_runs_compare_and_exclude_for_real(monkeypatch):
    """★验收：v2 里 compare/exclude 步骤真的进 _step_results，且 source 是 local_op。"""
    _i = _mk_v2(monkeypatch, _plan_compare())
    _captured = {}

    def _syn(sr, q):
        _captured["sr"] = [dict(s) for s in sr]
        return "答案"

    monkeypatch.setattr(IW, "_synthesize_step_results", lambda self, sr, q: _syn(sr, q))
    _out = _i._multi_step_execute_v2("请对比微服务与单体架构的性能、复杂度、运维")
    assert _out == "答案"
    _sr = _captured["sr"]
    assert len(_sr) == 3, _sr
    assert _sr[0]["source"] != "local_op:compare"
    _cmp = [s for s in _sr if s.get("op") == "compare"]
    _exc = [s for s in _sr if s.get("op") == "exclude"]
    assert _cmp and _cmp[0]["source"] == "local_op:compare", _sr
    assert _cmp[0]["status"] == "ok", _cmp[0]
    assert _exc and _exc[0]["source"] == "local_op:exclude", _sr


def test_v2_skips_dependent_after_insufficient(monkeypatch):
    """前序算子 insufficient ⇒ 依赖它的后续步骤标 skipped_dependency。

    ★全步都没产出 ⇒ v2 返回 None（P2-196：交回上层单次兜底），所以这里不读
      合成结果，改读 `_m455_flush_trace` 落下的 rows —— 那才是本刀的验收面。
    """
    _plan = _plan_compare()
    _plan[0]["op"] = "compare"        # 第一步就没有证据 ⇒ insufficient
    _i = _mk_v2(monkeypatch, _plan)
    assert _i._multi_step_execute_v2("请对比微服务与单体架构的性能") is None
    assert _i._m455_flush_trace_calls, "没有落 trace"
    _plan_i, _rows = _i._m455_flush_trace_calls[-1]
    assert _rows[0]["status"] == "insufficient_evidence", _rows
    assert _rows[1]["status"] == "skipped_dependency", _rows
    assert _rows[2]["status"] == "skipped_dependency", _rows


def test_v2_trace_rows_carry_op_and_status(monkeypatch):
    _i = _mk_v2(monkeypatch, _plan_compare())
    monkeypatch.setattr(IW, "_synthesize_step_results", lambda self, sr, q: "答案")
    _i._multi_step_execute_v2("请对比微服务与单体架构的性能、复杂度、运维")
    assert _i._m455_flush_trace_calls, "没有落 trace"
    _plan_i, _rows = _i._m455_flush_trace_calls[-1]
    _ops = [r.get("op") for r in _rows]
    assert "compare" in _ops and "exclude" in _ops, _rows
    assert all(r.get("status") for r in _rows), _rows
