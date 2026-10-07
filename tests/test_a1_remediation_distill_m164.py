# -*- coding: utf-8 -*-
"""第164批 刀A1：推理补救沉淀 —— 门控单测。

验证 ReasoningExperienceIndexer 新增的：
  - record_remediation_success (补救成功 → L2 蒸馏 + 候选规则)
  - record_failure_mode (推理失败模式索引)
  - get_failure_modes / get_remediation_successes / get_stats 增量

隔离策略：构造注入 stub reasoning_exp（MagicMock，不落盘 data/）、
stub node_pool（记录 add 的 PulseNode）、stub encode_queue（记录 submit）。
node_pool.add 接收真实 PulseNode（纯内存数据结构，构造无副作用）。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from nucleus.mnemosyne.PulseNode import PulseNode
from nucleus.reasoning.ReasoningExperienceIndexer import (
    PATH_FAILURE,
    PATH_REMEDIATION,
    ReasoningExperienceIndexer,
)


class _StubNodePool:
    """记录 add 调用，返回确定性 node_id。"""

    def __init__(self):
        self.added = []

    def add(self, node):
        self.added.append(node)
        return f"node-{len(self.added)}"

    def get(self, nid):
        return None


class _StubQueue:
    """记录 submit 调用。"""

    def __init__(self):
        self.submitted = []

    def submit(self, nid, value):
        self.submitted.append((nid, value))


def _make_indexer(enabled=True):
    exp = MagicMock()
    # record 触发后让 _experiences 增长，使 record_with_index 的
    # 「写前/写后经验数」判据正确（模拟真实 ReasoningExperience.record）。
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


def test_remediation_writes_l2_node():
    idx, exp, pool, queue = _make_indexer(True)
    idx.record_remediation_success(question="1+1=?", correct_answer="2",
                                    rule_candidate="self_correction:verify")
    assert len(pool.added) == 1
    node = pool.added[0]
    assert isinstance(node, PulseNode)
    assert node.space_path == PATH_REMEDIATION
    assert node.evol_level == PulseNode.EVOL_L2
    assert node.value.startswith("补救成功｜问题：1+1=?")
    exp.record.assert_called_once()


def test_remediation_submits_to_queue():
    idx, exp, pool, queue = _make_indexer(True)
    idx.record_remediation_success(question="q", correct_answer="a")
    assert len(queue.submitted) == 1
    assert queue.submitted[0][0] == "node-1"


def test_remediation_records_json_and_memory():
    idx, exp, pool, queue = _make_indexer(True)
    idx.record_remediation_success(question="q", correct_answer="a", rule_candidate="r")
    succ = idx.get_remediation_successes()
    assert len(succ) == 1
    assert succ[0]["question"] == "q"
    assert succ[0]["answer"] == "a"
    assert succ[0]["rule"] == "r"
    stats = idx.get_stats()
    assert stats["remediation_count"] == 1


def test_failure_mode_writes_l1_node():
    idx, exp, pool, queue = _make_indexer(True)
    idx.record_failure_mode(pattern="输出相关性低", question="q",
                            context="cortex_output_blocked_to_lung")
    assert len(pool.added) == 1
    node = pool.added[0]
    assert node.space_path == PATH_FAILURE
    assert node.evol_level == PulseNode.EVOL_L1
    fm = idx.get_failure_modes()
    assert len(fm) == 1
    assert fm[0]["pattern"] == "输出相关性低"
    assert fm[0]["question"] == "q"
    assert fm[0]["context"] == "cortex_output_blocked_to_lung"
    stats = idx.get_stats()
    assert stats["failure_mode_count"] == 1


def test_double_write_disabled_only_json():
    idx, exp, pool, queue = _make_indexer(False)
    idx.record_remediation_success(question="q", correct_answer="a")
    assert len(pool.added) == 0
    assert len(queue.submitted) == 0
    exp.record.assert_called_once()
    assert len(idx.get_remediation_successes()) == 1


def test_node_pool_none_safe():
    exp = MagicMock()
    exp._experiences = []

    def _grow(*_a, **_k):
        exp._experiences.append(1)

    exp.record.side_effect = _grow
    idx = ReasoningExperienceIndexer(reasoning_exp=exp, node_pool=None, encode_queue=None)
    idx.set_enabled(True)
    idx.record_remediation_success(question="q", correct_answer="a")
    assert len(exp.record.call_args_list) == 1
    assert idx.get_stats()["remediation_count"] == 1


def test_empty_inputs_noop():
    idx, exp, pool, queue = _make_indexer(True)
    idx.record_remediation_success(question="", correct_answer="")
    idx.record_remediation_success(question="q", correct_answer="")
    idx.record_failure_mode(pattern="", question="")
    assert len(pool.added) == 0
    assert len(exp.record.call_args_list) == 0
    assert len(idx.get_failure_modes()) == 0
    assert len(idx.get_remediation_successes()) == 0


def test_record_with_index_regression_double_write():
    idx, exp, pool, queue = _make_indexer(True)
    res = idx.record_with_index("为什么天是蓝的", "causal", source="local", confidence=0.7)
    assert res["json_written"] is True
    assert res["node_written"] is True
    assert res["path"] == "/推理经验/因果/"
    assert len(pool.added) == 1


def test_class_exposes_a1_methods():
    assert hasattr(ReasoningExperienceIndexer, "record_remediation_success")
    assert hasattr(ReasoningExperienceIndexer, "record_failure_mode")
    assert hasattr(ReasoningExperienceIndexer, "get_failure_modes")
    assert hasattr(ReasoningExperienceIndexer, "get_remediation_successes")
    assert hasattr(ReasoningExperienceIndexer, "get_stats")


def test_failure_mode_distinct_from_remediation_paths():
    idx, exp, pool, queue = _make_indexer(True)
    idx.record_remediation_success(question="q1", correct_answer="a1")
    idx.record_failure_mode(pattern="p1", question="q2")
    assert len(pool.added) == 2
    assert pool.added[0].space_path == PATH_REMEDIATION
    assert pool.added[1].space_path == PATH_FAILURE
    assert pool.added[0].evol_level == PulseNode.EVOL_L2
    assert pool.added[1].evol_level == PulseNode.EVOL_L1
