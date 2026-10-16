# -*- coding: utf-8 -*-
"""★第250批 刀1+刀2 隔离测试：S1蒸馏断链修复 + S2指标键入运行时。

- 刀1：双写开启下 record_remediation_success 必须真正蒸馏（remediation.distilled 计数+1），
      且双写关闭时不蒸馏（守卫）。
- 刀2：LLMDependencyMetrics._load 对旧持久化态（缺 remediation.returned/missed）做防御性合并，
      加载后新键仍存在且计数正确；record_remediation_returned/missed 能命中新键。
"""
import json
import tempfile

import nucleus.LLMDependencyMetrics as _m_mod
from nucleus.LLMDependencyMetrics import LLMDependencyMetrics
from unittest.mock import MagicMock


def _make_indexer(enabled=True):
    from nucleus.reasoning.ReasoningExperienceIndexer import ReasoningExperienceIndexer
    exp = MagicMock()
    exp._experiences = []

    def _grow(*_a, **_k):
        exp._experiences.append(1)

    exp.record.side_effect = _grow
    exp.extract_structural_features.return_value = {}

    class _Pool:
        def __init__(self):
            self.added = []

        def add(self, n):
            self.added.append(n)
            return f"n{len(self.added)}"

        def get(self, n):
            return None

    class _Queue:
        def __init__(self):
            self.sub = []

        def submit(self, n, v):
            self.sub.append((n, v))

    idx = ReasoningExperienceIndexer(
        reasoning_exp=exp, node_pool=_Pool(), encode_queue=_Queue())
    idx.set_enabled(enabled)
    return idx, exp


def _stub_side_effects():
    """替换蒸馏计数与 L2 落盘为内存桩，保持测试密闭（不污染生产指标/语义缓存）。"""
    orig_distilled = _m_mod.record_remediation_distilled
    import nucleus.llm.semantic_cache as _sc
    orig_store = _sc.store_l2
    _sc.store_l2 = lambda *a, **k: None
    return orig_distilled, orig_store


def _restore_side_effects(orig_distilled, orig_store):
    _m_mod.record_remediation_distilled = orig_distilled
    import nucleus.llm.semantic_cache as _sc
    _sc.store_l2 = orig_store


def test_indexer_distills_when_double_write_enabled():
    m = LLMDependencyMetrics(base_dir=tempfile.mkdtemp(prefix="m250_"), auto_hourly_log=False)
    orig_distilled, orig_store = _stub_side_effects()
    _m_mod.record_remediation_distilled = m.record_remediation_distilled
    try:
        idx, _ = _make_indexer(enabled=True)
        before = m._counters["remediation"]["distilled"]
        idx.record_remediation_success("q1", "a1")
        after = m._counters["remediation"]["distilled"]
        assert after == before + 1, f"distilled 未+1: {before}->{after}"
        # 旁路哨兵：成功增量与蒸馏增量同步+1，缺口应为 0（未出现未蒸馏的补救成功）
        assert idx._sentinel_distilled == idx._sentinel_success, \
            f"哨兵缺口: success={idx._sentinel_success} distilled={idx._sentinel_distilled}"
    finally:
        _restore_side_effects(orig_distilled, orig_store)


def test_indexer_no_distill_when_double_write_disabled():
    m = LLMDependencyMetrics(base_dir=tempfile.mkdtemp(prefix="m250b_"), auto_hourly_log=False)
    orig_distilled, orig_store = _stub_side_effects()
    _m_mod.record_remediation_distilled = m.record_remediation_distilled
    try:
        idx, _ = _make_indexer(enabled=False)
        before = m._counters["remediation"]["distilled"]
        idx.record_remediation_success("q2", "a2")
        after = m._counters["remediation"]["distilled"]
        assert after == before, f"双写关闭却蒸馏了: {before}->{after}"
    finally:
        _restore_side_effects(orig_distilled, orig_store)


def test_load_merges_new_remediation_keys():
    d = tempfile.mkdtemp(prefix="m250c_")
    m = LLMDependencyMetrics(base_dir=d, auto_hourly_log=False)
    # 写旧持久化态：remediation 只有旧三键（模拟 241 之前启动的进程落盘）
    old = {"started_at": "x",
           "counters": {"remediation": {"attempt": 179, "success": 165, "distilled": 0}}}
    with open(m._save_path, "w", encoding="utf-8") as f:
        json.dump(old, f)
    m2 = LLMDependencyMetrics(base_dir=d, auto_hourly_log=False)
    rem = m2._counters["remediation"]
    assert "returned" in rem and "missed" in rem, f"新键缺失: {list(rem.keys())}"
    assert rem["returned"] == 0 and rem["missed"] == 0


def test_returned_missed_count_and_persist():
    m = LLMDependencyMetrics(base_dir=tempfile.mkdtemp(prefix="m250d_"), auto_hourly_log=False)
    m.record_remediation_returned(2)
    m.record_remediation_missed(3)
    rem = m._counters["remediation"]
    assert rem["returned"] == 2, rem
    assert rem["missed"] == 3, rem
    m.save()
    m3 = LLMDependencyMetrics(base_dir=m._base_dir, auto_hourly_log=False)
    assert m3._counters["remediation"]["returned"] == 2
    assert m3._counters["remediation"]["missed"] == 3
