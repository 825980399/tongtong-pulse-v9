# -*- coding: utf-8 -*-
"""第164批 刀A2：推理指标闭环 —— 门控单测。

覆盖：
  - LLMDependencyMetrics 三联动派生（local_intercept_rate / remediation_rate /
    remediation_distill_rate）+ 分场景北极星 scene_llm_dependency_ratio
  - get_snapshot 扩展四键
  - remediation_rate 复用 verification_learning_hub 唯一口径（一致性）
  - 模块级便捷入口（record_remediation_*）委托到单例，cw2 零新增 handler
  - ReasoningExperienceIndexer.record_remediation_success 触发蒸馏沉淀埋点
  - StrategySelector 连续 3 次失败→降级 / 连续 5 次通过→恢复；select_strategy 尊重降级标记

隔离策略：
  - LLMDependencyMetrics 用 tempfile.mkdtemp 真实实例化（auto_hourly_log=False，不启线程、
    不落盘真实 data/）；remediation_rate 对 verification_learning_hub 走 mock。
  - ReasoningExperienceIndexer 复用 A1 的 stub 注入（MagicMock reasoning_exp 不落盘）。
"""
from __future__ import annotations

import os

import pytest

import tempfile
from unittest.mock import MagicMock, patch

from nucleus.LLMDependencyMetrics import (
    KIND_GUARD,
    KIND_SIMPLE,
    LLMDependencyMetrics,
    SCENE_LUNG,
    record_remediation_attempt,
    record_remediation_distilled,
    record_remediation_success,
)
from nucleus.reasoning.ReasoningExperienceIndexer import (
    ReasoningExperienceIndexer,
    _STATE_PATH,
)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_indexer_state():
    """★第165批 刀A5：测试前清空计数持久化状态文件，避免跨测试 base 累计污染断言。"""
    if os.path.exists(_STATE_PATH):
        os.remove(_STATE_PATH)
    yield
    if os.path.exists(_STATE_PATH):
        os.remove(_STATE_PATH)



def _make_metrics():
    d = tempfile.mkdtemp(prefix="a2_metrics_")
    return LLMDependencyMetrics(base_dir=d, auto_hourly_log=False)


class _StubNodePool:
    def __init__(self):
        self.added = []

    def add(self, node):
        self.added.append(node)
        return f"node-{len(self.added)}"

    def get(self, nid):
        return None


class _StubQueue:
    def __init__(self):
        self.submitted = []

    def submit(self, nid, value):
        self.submitted.append((nid, value))


def _make_indexer(enabled=True):
    exp = MagicMock()
    exp._experiences = []

    def _grow(*_a, **_k):
        exp._experiences.append(1)

    exp.record.side_effect = _grow
    exp.extract_structural_features.return_value = {}
    pool = _StubNodePool()
    queue = _StubQueue()
    idx = ReasoningExperienceIndexer(reasoning_exp=exp, node_pool=pool, encode_queue=queue)
    idx.set_enabled(enabled)
    return idx, exp, pool, queue


# ---------------------------------------------------------------------------
# 三联动派生指标
# ---------------------------------------------------------------------------
def test_local_intercept_rate_computation():
    m = _make_metrics()
    m.record_local_inference(KIND_GUARD, 4)      # 本地拦截 4
    m.record_local_inference(KIND_SIMPLE, 2)     # 本地其他 2
    m.record_llm_call(SCENE_LUNG, 6)             # LLM 6
    # answer_requests = llm_total + local_total = 6 + 6 = 12
    assert m.local_total() == 6
    assert m.llm_total() == 6
    assert m.answer_requests() == 12
    # 方法内部 round(x, 4)，故与「四舍五入到 4 位」比对
    assert abs(m.local_intercept_rate() - round(4 / 12, 4)) < 1e-9


def test_local_intercept_rate_no_sample():
    m = _make_metrics()
    assert m.local_intercept_rate() == 0.0


def test_remediation_distill_rate_computation():
    m = _make_metrics()
    m.record_remediation_success(3)
    m.record_remediation_distilled(2)
    # 2 / 3，方法内部 round(x, 4)
    assert abs(m.remediation_distill_rate() - round(2 / 3, 4)) < 1e-9


def test_remediation_distill_rate_no_success():
    m = _make_metrics()
    m.record_remediation_distilled(5)
    assert m.remediation_distill_rate() == 0.0


def test_scene_llm_dependency_ratio():
    m = _make_metrics()
    m.record_llm_call(SCENE_LUNG, 5)
    m.record_local_inference(KIND_SIMPLE, 3)
    # SCENE_LUNG: 5 / (5 + 3) = 0.625
    assert abs(m.scene_llm_dependency_ratio(SCENE_LUNG) - (5 / 8)) < 1e-9
    # 其它场景无样本 → 0.0
    assert m.scene_llm_dependency_ratio("其他") == 0.0


def test_scene_llm_dependency_ratio_no_sample():
    m = _make_metrics()
    assert m.scene_llm_dependency_ratio(SCENE_LUNG) == 0.0


def test_remediation_rate_reuses_hub_single_source():
    """remediation_rate 必须复用 verification_learning_hub.get_stats() 唯一口径。"""
    import nucleus.mnemosyne.verification_learning_hub as vhub
    m = _make_metrics()
    _hub = MagicMock()
    _hub.get_stats.return_value = {"total": 10, "remediation_count": 4}
    with patch.object(vhub, "get_verification_learning_hub", return_value=_hub):
        assert abs(m.remediation_rate() - 0.4) < 1e-9
        # 快照 derived 与直接调用一致（同一口径）
        assert abs(m.get_snapshot()["derived"]["remediation_rate"] - 0.4) < 1e-9


def test_remediation_rate_hub_empty():
    import nucleus.mnemosyne.verification_learning_hub as vhub
    m = _make_metrics()
    _hub = MagicMock()
    _hub.get_stats.return_value = {"total": 0, "remediation_count": 0}
    with patch.object(vhub, "get_verification_learning_hub", return_value=_hub):
        assert m.remediation_rate() == 0.0


# ---------------------------------------------------------------------------
# 快照扩展 + 计数
# ---------------------------------------------------------------------------
def test_snapshot_extends_three_link_and_scene():
    m = _make_metrics()
    m.record_local_inference(KIND_GUARD, 1)
    m.record_llm_call(SCENE_LUNG, 1)
    m.record_remediation_attempt(2)
    m.record_remediation_success(1)
    m.record_remediation_distilled(1)
    snap = m.get_snapshot()
    d = snap["derived"]
    for k in ("local_intercept_rate", "remediation_rate",
              "remediation_distill_rate", "scene_llm_dependency_ratio"):
        assert k in d, "快照 derived 缺失键: %s" % k
    assert isinstance(d["scene_llm_dependency_ratio"], dict)
    assert snap["counters"]["remediation"] == {"attempt": 2, "success": 1, "distilled": 1}


# ---------------------------------------------------------------------------
# 模块级便捷入口委托（cw2 零新增 handler）
# ---------------------------------------------------------------------------
def test_module_func_attempt_delegates():
    _mock = MagicMock()
    with patch("nucleus.LLMDependencyMetrics.get_llm_dependency_metrics",
               return_value=_mock):
        record_remediation_attempt(2)
        _mock.record_remediation_attempt.assert_called_once_with(2)


def test_module_func_success_delegates():
    _mock = MagicMock()
    with patch("nucleus.LLMDependencyMetrics.get_llm_dependency_metrics",
               return_value=_mock):
        record_remediation_success(3)
        _mock.record_remediation_success.assert_called_once_with(3)


def test_module_func_distilled_delegates():
    _mock = MagicMock()
    with patch("nucleus.LLMDependencyMetrics.get_llm_dependency_metrics",
               return_value=_mock):
        record_remediation_distilled(4)
        _mock.record_remediation_distilled.assert_called_once_with(4)


# ---------------------------------------------------------------------------
# ReasoningExperienceIndexer → 蒸馏沉淀埋点接线
# ---------------------------------------------------------------------------
def test_indexer_success_triggers_distilled():
    idx, _exp, _pool, _queue = _make_indexer(True)
    _distilled = MagicMock()
    with patch("nucleus.LLMDependencyMetrics.record_remediation_distilled",
               _distilled):
        idx.record_remediation_success(question="q", correct_answer="a")
        _distilled.assert_called_once_with(1)
    # 双写仍正常（A1 无回归）
    assert len(_pool.added) == 1


def test_indexer_double_write_off_no_distilled():
    idx, _exp, _pool, _queue = _make_indexer(False)
    _distilled = MagicMock()
    with patch("nucleus.LLMDependencyMetrics.record_remediation_distilled",
               _distilled):
        idx.record_remediation_success(question="q", correct_answer="a")
        _distilled.assert_not_called()


# ---------------------------------------------------------------------------
# StrategySelector 自评驱动升降级
# ---------------------------------------------------------------------------
def test_strategy_degrade_after_3_fails():
    from nucleus.reasoning.StrategySelector import StrategySelector
    sel = StrategySelector()
    s = "deep_think"
    for _ in range(3):
        sel.report_result(s, False)
    assert sel._degraded[s] is True
    r = sel.select_strategy(question="如何设计方案", question_type="方案设计")
    assert r["strategy"] != s


def test_strategy_restore_after_5_passes():
    from nucleus.reasoning.StrategySelector import StrategySelector
    sel = StrategySelector()
    s = "deep_think"
    for _ in range(3):
        sel.report_result(s, False)
    assert sel._degraded[s] is True
    for _ in range(5):
        sel.report_result(s, True)
    assert sel._degraded[s] is False
    r = sel.select_strategy(question="如何设计方案", question_type="方案设计")
    assert r["strategy"] == s


def test_strategy_consecutive_reset_on_mixed():
    from nucleus.reasoning.StrategySelector import StrategySelector
    sel = StrategySelector()
    s = "deep_think"
    sel.report_result(s, False)
    sel.report_result(s, True)   # 中断失败连击
    assert sel._consecutive_fail[s] == 0
    assert sel._degraded[s] is False
    sel.report_result(s, False)
    sel.report_result(s, False)  # 仅连续 2 次，未达 3 → 不降级
    assert sel._degraded[s] is False


def test_select_respects_degraded_flag():
    from nucleus.reasoning.StrategySelector import StrategySelector
    sel = StrategySelector()
    s = "deep_think"
    normal = sel.select_strategy(question="如何设计方案", question_type="方案设计")
    assert normal["strategy"] == s
    sel._degraded[s] = True
    degraded = sel.select_strategy(question="如何设计方案", question_type="方案设计")
    assert degraded["strategy"] != s
