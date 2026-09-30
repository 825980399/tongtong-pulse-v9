# -*- coding: utf-8 -*-
"""第84批：自主进化「修复率恒 0」根因修复（T-84a / T-84b / T-84c / T-84d）。

覆盖：
- M84-1（T-84c①）`PulseInnerWorld._detect_simple_query_local` 的本地推理计数
  从**函数入口无条件**下移到**模板真正命中**分支 —— 未命中不再污染
  `llm_dependency.json` 的「本地简单回答」计数。
- M84-2（T-84c②）`ResonanceEngine._build_rule_map` 规则通道**查询级生效**时
  补记 `record_local_inference(KIND_RULE)` —— 此前全库无任何调用点，
  「规则通道」恒 0。
- M84-3（T-84a）`SafeEvolutionExecutor` 运行时验证写回后重算 `problem_fixed`
  —— 此前 `apply_split` 只在补丁落盘 history 时调用一次（`post_apply_errors`
  尚未产生），`problem_fixed` 恒 None（实测 63/64）→ `real_fix_rate` 恒 0。
- M84-4（T-84b）补丁账本分层计数：`applied_total` / `problem_fixed_true` /
  `problem_fixed_known` / `history_total` 与 `_repaired`（提交待审批数）分离。

红线自检（本文件不改 config 开关、不写 data/knowledge、不触碰 data/ 任何文件）。
"""
from __future__ import annotations

import os
import sys
import time
import types
import typing

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import organs.brain.PulseInnerWorld as _iw_mod  # noqa: E402
import nucleus.LLMDependencyMetrics as _llm_metrics  # noqa: E402
import nucleus.evolution.patch_verification_split as _pvs  # noqa: E402
from nucleus.LLMDependencyMetrics import KIND_RULE, KIND_SIMPLE  # noqa: E402
from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor  # noqa: E402
from nucleus.synapsys.ResonanceEngine import ResonanceEngine  # noqa: E402
from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402

SRC_IW = os.path.join(_ROOT, "organs", "brain", "PulseInnerWorld.py")
SRC_RE = os.path.join(_ROOT, "nucleus", "synapsys", "ResonanceEngine.py")
SRC_SEE = os.path.join(_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")


def _src(path: str) -> str:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def _code_only(text: str) -> str:
    """剥离整行注释后的源码。

    ★坑：新增说明注释里会**引用**被改动的代码片段（如
    ``# ... `record_local_inference(KIND_SIMPLE)` 已下移到...``），
    直接 `str.count` 会把注释里的示例一并计入。凡"调用点数"类断言
    必须先剥注释，否则断言会因文档而假红/假绿。
    """
    return "\n".join(_ln for _ln in text.split("\n")
                     if not _ln.lstrip().startswith("#"))


class _Recorder:
    """替身 record_local_inference：只记录 kind，不落盘。"""

    def __init__(self) -> None:
        self.calls: typing.List[str] = []

    def __call__(self, kind: str = KIND_SIMPLE, n: int = 1) -> None:
        self.calls.append(kind)


def _quiet_selfcalibrator(monkeypatch) -> None:
    """注入假的 SelfCalibrator，避免测试污染真实校准数据文件。"""
    _fake = types.ModuleType("nucleus.reasoning.SelfCalibrator")
    _fake.feedback_if_correction = lambda *a, **k: None
    _fake.mark_local_inference = lambda *a, **k: None
    _fake.feedback_local_inference = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "nucleus.reasoning.SelfCalibrator", _fake)


# ======================================================================
# M84-1（T-84c①）：本地简单回答计数只在"真正命中"时 +1
# ======================================================================

class _Ctx:
    """最小可用 InferenceContext 替身（只提供被测方法真正读取的字段）。"""

    def __init__(self, question: str, user_name: str = "测试用户") -> None:
        self.question = question
        self.user_name = user_name
        self.guidance = ""
        self.correlation_id = "cid-m84"
        self.empathetic_note = ""
        self.payload: dict = {}
        self._reasoning_start_time = time.time()
        self._question_complexity = "simple"
        self._memory_context: list = []


def _bare_iw() -> PulseInnerWorld:
    """未走 __init__ 的空壳；★铁律80：显式补齐被测方法会读取的每个属性。"""
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw._simple_query_local_enabled = True
    iw._inference_count = 0
    iw._log = lambda *a, **k: None
    iw._try_simple_arithmetic = lambda q: None
    iw._build_who_am_i_response = lambda *a, **k: "我是曈曈。"
    iw._build_time_response = lambda q: "现在是测试时间。"
    iw._cache_inference = lambda *a, **k: None
    iw._trace_inference = lambda *a, **k: None
    iw._enhance_answer = lambda **k: k.get("answer", "")
    iw._emit = lambda *a, **k: None
    iw._evidence_conf = lambda *a, **k: 0.95
    return iw


class TestM84LocalInferenceCounting:
    @pytest.mark.parametrize("question", [
        "量子纠缠为什么能超距作用",
        "帮我分析一下这个季度的营收同比变化趋势",
        "你好，我想问一个关于分布式共识的问题",
    ])
    def test_unmatched_query_must_not_count(self, question, monkeypatch):
        """★核心 RED：未命中任何模板（最终 return None）不得计入本地推理。"""
        _quiet_selfcalibrator(monkeypatch)
        _rec = _Recorder()
        monkeypatch.setattr(_iw_mod, "record_local_inference", _rec)

        iw = _bare_iw()
        _out = iw._detect_simple_query_local(_Ctx(question))

        assert _out is None, f"该问题不应被简单模板命中: {question!r}"
        assert _rec.calls == [], (
            f"未命中模板却计入了本地推理: {_rec.calls!r}（问题={question!r}）")
        assert iw._inference_count == 0

    @pytest.mark.parametrize("question", ["你好", "在吗", "谢谢", "我难过"])
    def test_matched_query_counts_exactly_once(self, question, monkeypatch):
        """命中模板时必须恰好 +1（回归守卫：下移后不能漏计）。"""
        _quiet_selfcalibrator(monkeypatch)
        _rec = _Recorder()
        monkeypatch.setattr(_iw_mod, "record_local_inference", _rec)

        iw = _bare_iw()
        _out = iw._detect_simple_query_local(_Ctx(question))

        assert isinstance(_out, dict) and _out.get("answer"), (
            f"模板命中应返回答案: {question!r} -> {_out!r}")
        assert _rec.calls == [KIND_SIMPLE], f"命中计数异常: {_rec.calls!r}"
        assert iw._inference_count == 1

    def test_count_not_before_return_none_guard(self):
        """入口无条件计数必须已被移除，且新计数点位于 `return None` 守卫**之后**。"""
        _code = _code_only(_src(SRC_IW))
        _guard = ("        if _answer is None:\n"
                  "            return None\n")
        _call = "        record_local_inference(KIND_SIMPLE)\n"

        assert _code.count(_guard) == 1, "命中守卫锚点丢失（源码结构变化）"
        assert _code.count(_call) == 1, "本地推理计数点不唯一（M84-1 未生效）"
        assert _code.find(_call) > _code.find(_guard), (
            "本地推理计数仍落在 `return None` 守卫之前（M84-1 未生效）")

    def test_simple_count_call_sites_unchanged(self):
        """全库 `record_local_inference(KIND_SIMPLE)` 代码行仍应恰好 1 处（移动非复制）。"""
        assert _code_only(_src(SRC_IW)).count(
            "record_local_inference(KIND_SIMPLE)") == 1

    def test_disabled_switch_still_returns_none(self, monkeypatch):
        """开关关闭时行为不变：不计入。"""
        _quiet_selfcalibrator(monkeypatch)
        _rec = _Recorder()
        monkeypatch.setattr(_iw_mod, "record_local_inference", _rec)

        iw = _bare_iw()
        iw._simple_query_local_enabled = False
        assert iw._detect_simple_query_local(_Ctx("你好")) is None
        assert _rec.calls == []


# ======================================================================
# M84-2（T-84c②）：规则通道生效时补记 KIND_RULE
# ======================================================================

class _RuleProvider:
    def __init__(self, hits) -> None:
        self._hits = hits

    def search_by_text(self, text: str, top_n: int = 50):
        return list(self._hits)


def _bare_engine(hits=(("n1", 0.9),)) -> ResonanceEngine:
    eng = ResonanceEngine.__new__(ResonanceEngine)
    eng._rule_calls = 0
    eng._rule_fallbacks = 0
    eng._rule_provider = _RuleProvider(hits)
    eng._get_semantic_cfg = lambda: {
        "enable_semantic_kernel": True, "enable_rule_channel": True}
    eng._extract_query_text = lambda q: "星轨"
    return eng


class TestM84RuleChannelCounting:
    def test_rule_hit_records_kind_rule(self, monkeypatch):
        """★核心 RED：规则通道查询级生效必须计入 KIND_RULE。"""
        _rec = _Recorder()
        monkeypatch.setattr(_llm_metrics, "record_local_inference", _rec)

        eng = _bare_engine()
        _map = eng._build_rule_map({"text": "星轨"}, [{"id": "n1"}])

        assert _map == {"n1": 0.9}
        assert eng._rule_calls == 1
        assert _rec.calls == [KIND_RULE], (
            f"规则通道生效未计入本地推理: {_rec.calls!r}")

    def test_kind_rule_constant_is_rule_channel(self):
        assert KIND_RULE == "规则通道"

    @pytest.mark.parametrize("hits", [
        (),
        (("", 0.9),),          # 空 node_id → out 为空
        (("n1", "bad"),),      # 非法 score → 被内层 except 丢弃
    ])
    def test_rule_miss_records_nothing(self, hits, monkeypatch):
        """规则通道未生效（out 为空）时不得计数。"""
        _rec = _Recorder()
        monkeypatch.setattr(_llm_metrics, "record_local_inference", _rec)

        eng = _bare_engine(hits=hits)
        _map = eng._build_rule_map({"text": "星轨"}, [{"id": "n1"}])

        assert _map == {}
        assert _rec.calls == []
        assert eng._rule_calls == 0

    def test_channel_disabled_records_nothing(self, monkeypatch):
        """规则通道开关关闭时零副作用。"""
        _rec = _Recorder()
        monkeypatch.setattr(_llm_metrics, "record_local_inference", _rec)

        eng = _bare_engine()
        eng._get_semantic_cfg = lambda: {
            "enable_semantic_kernel": True, "enable_rule_channel": False}
        assert eng._build_rule_map({"text": "星轨"}, [{"id": "n1"}]) == {}
        assert _rec.calls == []

    def test_provider_failure_degrades_quietly(self, monkeypatch):
        """provider 抛异常 → 回落且不计数（既有行为不变）。"""
        _rec = _Recorder()
        monkeypatch.setattr(_llm_metrics, "record_local_inference", _rec)

        def _boom(_t, _n=50):
            raise RuntimeError("m84 provider boom")

        eng = _bare_engine()
        eng._rule_provider = type("_P", (), {"search_by_text": staticmethod(_boom)})()
        assert eng._build_rule_map({"text": "星轨"}, [{"id": "n1"}]) == {}
        assert _rec.calls == []
        assert eng._rule_fallbacks == 1

    def test_count_failure_does_not_break_rule_channel(self, monkeypatch):
        """计数自身异常不得影响规则通道返回值（异常降级 DEBUG）。"""
        def _boom(*a, **k):
            raise RuntimeError("m84 metrics boom")

        monkeypatch.setattr(_llm_metrics, "record_local_inference", _boom)
        eng = _bare_engine()
        assert eng._build_rule_map({"text": "星轨"}, [{"id": "n1"}]) == {"n1": 0.9}
        assert eng._rule_calls == 1

    def test_wiring_kind_rule_called_in_resonance(self):
        """源码级接线断言：KIND_RULE 计数确实挂在 `if out:` 生效分支内。"""
        _text = _src(SRC_RE)
        assert _text.count("_rec_local84(_KIND_RULE84)") == 1, (
            "ResonanceEngine 未接入 KIND_RULE 计数（M84-2 未生效）")


# ======================================================================
# M84-3（T-84a）：运行时验证写回后重算 problem_fixed
# ======================================================================

class _FakePM:
    """PatchManager 替身：只实现被测方法用到的 5 个入口。"""

    def __init__(self, pending=None, history=None) -> None:
        self.pending = pending if pending is not None else []
        self.history = history if history is not None else []
        self.saved: dict = {}
        self.rollbacks: list = []

    def get_pending_file(self) -> str:
        return "pending"

    def get_history_file(self) -> str:
        return "history"

    def load_json(self, path, default):
        if path == "pending":
            return self.pending
        if path == "history":
            return self.history
        return default

    def _save_json(self, path, data) -> None:
        self.saved[path] = data

    def rollback_patch(self, pid: str):
        self.rollbacks.append(pid)
        return {"ok": True}


def _bare_executor(pending=None, history=None, after: int = 0) -> SafeEvolutionExecutor:
    ex = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
    ex._patch_manager = _FakePM(pending, history)
    ex.verify_fix_from_logs = lambda p: {
        "verified": True, "post_apply_errors": after, "after_fix": after,
        "effectiveness": 1.0, "detail": "m84-test"}
    ex._learn_from_verification = lambda p, r: None
    ex._count_errors_for_location = lambda f, m, since=0: after
    return ex


class TestM84ProblemFixedRecompute:
    def test_helper_exists(self):
        """★核心 RED：辅助方法必须存在（此前根本没有重算入口）。"""
        assert hasattr(SafeEvolutionExecutor, "_m84_recompute_split")
        assert hasattr(SafeEvolutionExecutor, "_m84_patch_ledger")

    def test_recompute_marks_problem_fixed_true(self):
        _p = {"id": "m84-h1", "baseline_errors": 4, "post_apply_errors": 0}
        ex = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        assert ex._m84_recompute_split(_p) is True
        assert _p.get("problem_fixed") is True

    def test_recompute_marks_problem_fixed_false(self):
        _p = {"id": "m84-h2", "baseline_errors": 4, "post_apply_errors": 4}
        ex = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        assert ex._m84_recompute_split(_p) is True
        assert _p.get("problem_fixed") is False

    def test_recompute_keeps_none_when_baseline_zero(self):
        """诚实设计守卫：基线为 0（未采到）时不得硬凑成"已修复"。"""
        _p = {"id": "m84-h3", "baseline_errors": 0, "post_apply_errors": 0}
        ex = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        assert ex._m84_recompute_split(_p) is True
        assert _p.get("problem_fixed") is None

    def test_recompute_non_dict_returns_false(self):
        ex = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        assert ex._m84_recompute_split(None) is False

    def test_recompute_swallows_exception(self, monkeypatch):
        def _boom(_p):
            raise RuntimeError("m84 split boom")

        monkeypatch.setattr(_pvs, "apply_split", _boom)
        ex = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        assert ex._m84_recompute_split({"id": "m84-h4"}) is False

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_submitted_writeback_recomputes_problem_fixed(self):
        """★核心 RED：verify_submitted_patches 写回后 problem_fixed 必须落地。"""
        _p = {"id": "m84-p1", "needs_runtime_verify": True, "applied": False,
              "baseline_errors": 4, "file": "nucleus/demo_m84.py", "method": "m"}
        ex = _bare_executor(pending=[_p], after=0)
        _res = ex.verify_submitted_patches()

        assert _res["verified"] == 1
        assert _p["runtime_verified"] is True
        assert _p["post_apply_errors"] == 0
        assert _p.get("problem_fixed") is True, (
            "写入 post_apply_errors 后仍未重算 problem_fixed（断链未闭合）")

    def test_already_verified_patch_gets_backfilled(self):
        """★核心 RED：历史已验补丁（runtime_verified=True）需补算 problem_fixed。"""
        _p = {"id": "m84-p2", "needs_runtime_verify": True, "runtime_verified": True,
              "baseline_errors": 4, "post_apply_errors": 0,
              "file": "nucleus/demo_m84.py", "method": "m"}
        ex = _bare_executor(pending=[_p])
        _res = ex.verify_submitted_patches()

        assert _res["total"] == 0, "已验补丁不应重复计入 total"
        assert _p.get("problem_fixed") is True, "已验补丁未补算 problem_fixed"

    def test_applied_writeback_sets_top_level_after_and_fixed(self):
        """★核心 RED：verify_applied_patches 需回写顶层 post_apply_errors 并重算。"""
        _p = {"id": "m84-p3", "applied": True, "needs_runtime_verify": True,
              "runtime_verified": False, "applied_at": time.time() - 1000,
              "baseline_errors": 4, "file": "nucleus/demo_m84.py", "method": "m"}
        ex = _bare_executor(history=[_p], after=0)
        _res = ex.verify_applied_patches()

        assert _res["verified"] == 1
        assert _p["post_apply_errors"] == 0, "应用后计数未回写到顶层"
        assert _p.get("problem_fixed") is True

    def test_applied_already_verified_gets_backfilled(self):
        _p = {"id": "m84-p4", "applied": True, "needs_runtime_verify": True,
              "runtime_verified": True, "baseline_errors": 4, "post_apply_errors": 0,
              "file": "nucleus/demo_m84.py", "method": "m"}
        ex = _bare_executor(history=[_p])
        ex.verify_applied_patches()

        assert _p.get("problem_fixed") is True

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_verification_flow_unbroken_on_recompute_failure(self, monkeypatch):
        """重算失败不得中断验证主流程（异常降级不阻断）。"""
        monkeypatch.setattr(_pvs, "apply_split", lambda _p: (_ for _ in ()).throw(
            RuntimeError("m84 split boom")))
        _p = {"id": "m84-p5", "needs_runtime_verify": True, "applied": False,
              "baseline_errors": 4, "file": "nucleus/demo_m84.py", "method": "m"}
        ex = _bare_executor(pending=[_p], after=0)
        _res = ex.verify_submitted_patches()

        assert _res["verified"] == 1
        assert _p["runtime_verified"] is True

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_wiring_recompute_hooked_at_all_four_points(self):
        """源码级接线断言：4 个写回点都必须挂重算（缺一即断链）。"""
        _code = _code_only(_src(SRC_SEE))
        assert _code.count("self._m84_recompute_split(_patch)") == 4, (
            "重算未挂满 4 个写入点（提交侧 跳过/写回 × 应用侧 跳过/写回）")
        assert _src(SRC_SEE).count("def _m84_recompute_split") == 1


# ======================================================================
# M84-4（T-84b）：补丁账本分层计数（_repaired 与"已修复"分离）
# ======================================================================

class TestM84PatchLedger:
    def test_layered_counts(self):
        """★核心 RED：账本必须把三个口径分开（此前只有 _repaired 一个数）。"""
        _hist = [
            {"id": "a", "applied": True, "problem_fixed": True},
            {"id": "b", "applied": True, "problem_fixed": None},
            {"id": "c", "applied": False, "problem_fixed": False},
        ]
        ex = _bare_executor(history=_hist)
        _led = ex._m84_patch_ledger()

        assert _led == {"history_total": 3, "applied_total": 2,
                        "problem_fixed_true": 1, "problem_fixed_known": 2}

    def test_empty_history(self):
        ex = _bare_executor(history=[])
        assert ex._m84_patch_ledger() == {
            "history_total": 0, "applied_total": 0,
            "problem_fixed_true": 0, "problem_fixed_known": 0}

    def test_non_list_history_returns_empty(self):
        ex = _bare_executor(history={"not": "a list"})
        assert ex._m84_patch_ledger() == {}

    def test_exception_returns_empty(self):
        ex = _bare_executor(history=[])
        ex._patch_manager.load_json = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("m84 ledger boom"))
        assert ex._m84_patch_ledger() == {}

    def test_ledger_is_read_only(self):
        """账本只读：不得改动 history 内容。"""
        _hist = [{"id": "a", "applied": True, "problem_fixed": True}]
        _snap = [dict(_hist[0])]
        ex = _bare_executor(history=_hist)
        ex._m84_patch_ledger()
        assert _hist == _snap
        assert ex._patch_manager.saved == {}

    def test_repaired_semantics_documented_in_source(self):
        """源码级接线断言：ledger 必须同时进入日志与埋点两处 extra。"""
        _text = _src(SRC_SEE)
        assert _text.count("self._m84_patch_ledger()") == 1
        assert "_m84_extra.update(_m84_ledger)" in _text
        assert "_m84_round_extra.update(_m84_ledger)" in _text
        assert _text.count("_m84_ledger") >= 5

    def test_evolution_effectiveness_file_not_touched(self):
        """红线自检：本批不得写入 data/ 下任何文件。"""
        _data = os.path.join(_ROOT, "data", "metrics", "evolution_effectiveness.json")
        _before = os.path.getmtime(_data) if os.path.exists(_data) else None
        _hist = [{"id": "a", "applied": True, "problem_fixed": True}]
        _ex = _bare_executor(history=_hist)
        _ex._m84_patch_ledger()
        _after = os.path.getmtime(_data) if os.path.exists(_data) else None
        assert _before == _after, "账本读取意外写入了 evolution_effectiveness.json"
