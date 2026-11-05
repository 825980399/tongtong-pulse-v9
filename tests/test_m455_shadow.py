# -*- coding: utf-8 -*-
"""455 刀5/刀6：hypothesize shadow 接线（只进 trace，不改答案）。

★本文件守的四件事
  1. 触发判据：假设类关键词命中 **且** 不是事实型问题（「是什么/多少/哪一年」不触发）；
  2. 计划最多追加 **1 个** op=hypothesize 步骤，``depends_on`` 指向前序证据步骤；
  3. shadow 行只带**摘要**（status/count/avg），假设正文不进 ``step_results``、不进答案；
  4. trace 的 plan/step_results 由 v2 执行器自己落（454 的 set_plan 只写在 v1 路径，
     ``ENABLE_TRUE_MULTI_STEP=True`` 时 v2 从来没落过 —— 本批补的口必须真的通）。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import config
from nucleus import dialogue_process_trace as DT
from nucleus.reasoning import hypothesize as H
from organs.brain.PulseInnerWorld import PulseInnerWorld as IW

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IW_SRC = os.path.join(ROOT, "organs", "brain", "PulseInnerWorld.py")


def _base(names):
    return [{"name": n, "prompt": "p-" + n, "fallback": "f-" + n} for n in names]


def _inst(logs=None):
    """裸实例 + 假日志（不跑 __init__，避免拉起全部器官）。"""
    inst = object.__new__(IW)
    _bucket = logs if logs is not None else []
    inst._log = lambda lvl, msg: _bucket.append(str(msg))
    return inst


@pytest.fixture(autouse=True)
def _fresh_trace(tmp_path, monkeypatch):
    monkeypatch.setenv("M426_TRACE_ROOT", str(tmp_path))
    DT.clear_plan()
    H.end_request()
    yield
    DT.clear_plan()
    H.end_request()


# ---------------------------------------------------------------- 刀6：触发判据
def test_hypothesis_questions_trigger():
    for q in ("如果当初不采用微服务架构会怎样", "假设负载翻十倍会不会扛不住",
              "假如明天断电，数据会不会丢", "推测一下三年后的用量",
              "什么情况下缓存会击穿"):
        assert IW._m455_is_hypothesis_question(q) is True, q


def test_factual_questions_do_not_trigger():
    for q in ("深度学习是什么", "这个接口多少 QPS", "曈曈是哪一年上线的",
              "微服务和单体的区别", "如何分析日志"):
        assert IW._m455_is_hypothesis_question(q) is False, q


def test_factual_wins_over_keyword():
    """★「可能是什么原因」同时含「可能」和「是什么」—— 事实型优先，不做假设。"""
    assert IW._m455_is_hypothesis_question("可能是什么原因导致报错") is False
    assert IW._m455_is_hypothesis_question("") is False


# ---------------------------------------------------------------- 刀6：计划追加
def test_append_one_step_with_dependency():
    plan = IW._m454_annotate_plan(_base(["了解A", "了解B", "对比分析"]),
                                  "如果负载翻十倍会怎样")
    out = _inst()._m455_append_hypothesize_step(plan, "如果负载翻十倍会怎样")
    assert len(out) == len(plan) + 1
    last = out[-1]
    assert last["op"] == "hypothesize"
    assert last["step_id"] == f"S{len(plan) + 1}"
    assert last["depends_on"] == [plan[-1]["step_id"]], last
    assert last["goal"] and last["expected_evidence"]


def test_append_is_idempotent():
    plan = [{"name": "了解A", "prompt": "p", "fallback": "", "step_id": "S1",
             "op": "retrieve", "goal": "g", "depends_on": [],
             "expected_evidence": "e"},
            {"name": "假设推演", "prompt": "p", "fallback": "", "step_id": "S2",
             "op": "hypothesize", "goal": "g", "depends_on": ["S1"],
             "expected_evidence": "e"}]
    out = _inst()._m455_append_hypothesize_step(plan, "如果…会怎样")
    assert len(out) == 2, out


def test_append_at_most_once():
    plan = _base(["了解A", "了解B"])
    inst = _inst()
    once = inst._m455_append_hypothesize_step(plan, "如果…会怎样")
    twice = inst._m455_append_hypothesize_step(once, "如果…会怎样")
    assert sum(1 for s in twice if s.get("op") == "hypothesize") == 1


def test_append_skips_factual_question():
    plan = _base(["了解A", "对比分析"])
    out = _inst()._m455_append_hypothesize_step(plan, "深度学习是什么")
    assert out == plan


def test_append_skips_when_shadow_off(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M455_HYPOTHESIZE_SHADOW", False, raising=False)
    plan = _base(["了解A", "对比分析"])
    assert _inst()._m455_append_hypothesize_step(plan, "如果…会怎样") == plan


def test_generate_task_plan_wires_hypothesize(monkeypatch):
    """★刀6 验收：计划 v3 对假设类问题真的产出 op=hypothesize。"""
    monkeypatch.setattr(config, "ENABLE_M454_PLAN_V3", True, raising=False)
    inst = _inst()
    inst._generate_task_plan_base = lambda q: _base(["了解A", "了解B", "对比分析"])
    plan = inst._generate_task_plan("如果当初不采用微服务架构会怎样")
    assert [s["op"] for s in plan][-1] == "hypothesize", plan
    assert sum(1 for s in plan if s["op"] == "hypothesize") == 1


def test_generate_task_plan_factual_unchanged(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M454_PLAN_V3", True, raising=False)
    inst = _inst()
    inst._generate_task_plan_base = lambda q: _base(["了解A", "了解B", "对比分析"])
    plan = inst._generate_task_plan("深度学习是什么")
    assert "hypothesize" not in [s["op"] for s in plan], plan


# ---------------------------------------------------------------- 刀5：影子行
def _stub_hyp(monkeypatch, result):
    calls = []

    def _fake(question, evidence, **kw):
        calls.append({"question": question, "evidence": evidence, "kw": kw})
        return result

    monkeypatch.setattr(H, "hypothesize", _fake)
    return calls


_OK = {"status": H.STATUS_OK, "hypotheses": [{"claim": "延迟会上升"}],
       "hypothesis_count": 1, "avg_confidence": 0.62, "elapsed_ms": 7.9,
       "reason": "", "evidence_count": 1}


def test_shadow_row_only_carries_summary(monkeypatch):
    """★影子行只有摘要：假设正文（claim）不得出现在 trace 行里。"""
    _stub_hyp(monkeypatch, _OK)
    step = _inst()._m455_hypothesize_step("如果…会怎样", ["S1"])
    row = _inst()._m455_run_hypothesize_shadow(
        "如果…会怎样", step,
        [{"name": "了解A", "result": "A 的结论" * 3, "success": True, "source": "检索"}])
    assert row["op"] == "hypothesize"
    assert row["status"] == H.STATUS_OK
    assert row["hypothesis_count"] == 1
    assert row["avg_confidence"] == pytest.approx(0.62)
    assert "延迟会上升" not in str(row), "假设正文不能进 trace 行"


def test_shadow_evidence_from_successful_steps_only(monkeypatch):
    calls = _stub_hyp(monkeypatch, _OK)
    prev = [{"name": "A", "result": "成功结论" * 60, "success": True, "source": "检索"},
            {"name": "B", "result": "失败结论", "success": False, "source": "检索"},
            {"name": "C", "result": "  ", "success": True, "source": "检索"},
            {"name": "D", "result": "结论D", "success": True, "source": "模型"}]
    _inst()._m455_run_hypothesize_shadow("如果…会怎样", {"step_id": "S5"}, prev)
    ev = calls[0]["evidence"]
    assert [x["id"] for x in ev] == ["E1", "E2"], ev       # 失败/空结论被排除
    assert all(len(x["text"]) <= 200 for x in ev)          # 每条截 200 字
    assert calls[0]["kw"]["origin"] == "user_query"
    assert calls[0]["kw"]["max_hypotheses"] == 2


def test_shadow_evidence_capped_at_four(monkeypatch):
    calls = _stub_hyp(monkeypatch, _OK)
    prev = [{"result": f"结论{i}", "success": True} for i in range(9)]
    _inst()._m455_run_hypothesize_shadow("如果…会怎样", {}, prev)
    assert len(calls[0]["evidence"]) == 4


def test_shadow_exception_marks_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("端点炸了")
    monkeypatch.setattr(H, "hypothesize", _boom)
    row = _inst()._m455_run_hypothesize_shadow("如果…会怎样", {"step_id": "S1"}, [])
    assert row["status"] == "error"
    assert "RuntimeError" in row["fail_reason"]


def test_shadow_no_evidence_does_not_spend_llm(monkeypatch):
    """没前序证据 ⇒ 真的 hypothesize 会 insufficient，且不消耗券。"""
    from nucleus import evidence_injection as EI
    EI.fresh_state()
    H.begin_request(time.time())
    row = _inst()._m455_run_hypothesize_shadow("如果…会怎样", {"step_id": "S1"}, [])
    assert row["status"] == H.STATUS_INSUFFICIENT, row
    assert EI.llm_ops_used() == 0


def test_in_answer_switch_no_longer_warns(monkeypatch):
    """★456 刀1 改钉：455 批此处在「开关开」时只打一条 WARNING（本批不进答案）。

    456 批已实现进答案路径 ⇒ 不再告警；假设正文改走请求级 ContextVar，
    **不进 trace 行**（455 刀5「只存摘要」的纪律不破）。

    回滚点：若撤掉进答案实现，恢复 455 的「只告警」断言。
    """
    monkeypatch.setattr(config, "ENABLE_M455_HYPOTHESIZE", True, raising=False)
    _stub_hyp(monkeypatch, _OK)
    logs = []
    row = _inst(logs)._m455_run_hypothesize_shadow(
        "如果…会怎样", {"step_id": "S1"}, [{"result": "结论", "success": True}])
    assert row["status"] == H.STATUS_OK
    assert not any("只实现 shadow" in x for x in logs), logs
    # 正文不在 trace 行里，但仍被暂存到请求级上下文（供收口拼答案）
    assert "hypotheses" not in row
    assert IW._M456_HYPS.get(), "假设没有进请求级暂存"


# ---------------------------------------------------------------- 刀5：trace 落盘
def test_flush_trace_keeps_only_plan_fields():
    inst = _inst()
    inst._m455_flush_trace(
        [{"step_id": "S1", "op": "retrieve", "goal": "g", "depends_on": [],
          "expected_evidence": "e", "name": "不该落", "prompt": "也不该落"}],
        [{"idx": 1, "step_id": "S1", "op": "retrieve", "status": "retrieved"}])
    st = DT.get_plan()
    assert len(st["plan"]) == 1
    assert set(st["plan"][0]) == {"step_id", "op", "goal", "depends_on",
                                 "expected_evidence"}
    assert st["step_results"][0]["status"] == "retrieved"


def test_flush_trace_survives_bad_input():
    _inst()._m455_flush_trace(None, None)       # 不抛
    assert DT.get_plan()["plan"] == []


def test_shadow_only_writes_trace_and_returns_none(monkeypatch):
    """★刀5 验收：进不了 v2 主流程的「如果…」也能落 trace，且答案路径不变。"""
    _stub_hyp(monkeypatch, _OK)
    inst = _inst()
    assert inst._m455_shadow_only("如果当初不采用微服务架构会怎样") is None
    st = DT.get_plan()
    assert st["plan"] and st["plan"][0]["op"] == "hypothesize", st
    assert st["step_results"] and st["step_results"][0]["op"] == "hypothesize"


def test_shadow_only_skips_when_off(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M455_HYPOTHESIZE_SHADOW", False, raising=False)
    _inst()._m455_shadow_only("如果…会怎样")
    assert DT.get_plan()["plan"] == []


def test_shadow_only_skips_factual():
    _inst()._m455_shadow_only("深度学习是什么")
    assert DT.get_plan()["plan"] == []


def test_shadow_only_does_not_enter_answer_path():
    """★结构性保证：假设分支只写 _m455_rows 并 ``continue``，绝不进答案合成。"""
    with open(IW_SRC, encoding="utf-8") as f:
        src = f.read()
    _i = src.index('if str(_step.get("op") or "") == "hypothesize":')
    _block = src[_i:_i + 700]
    assert "continue" in _block
    assert "_m455_run_hypothesize_shadow" in _block
    assert "_step_results.append" not in _block.split("continue")[0]
    assert "_synthesize_step_results(_step_results, question)" in src
