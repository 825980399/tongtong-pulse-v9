# -*- coding: utf-8 -*-
"""第108批 回归测试（门禁⑤-③ 先红后绿）。

专项验证 PulseLiver 代码学习 L1→L2 压缩通道的两处"数据毁灭"修复：
  ★T-108b 去重跳过禁删源：与已有 L2 关键词 100% 重叠时，旧代码置 merged=True
           并删除全部源 L1（"完全重复跳过但照样删除源节点"）；新代码 return False 不删源。
  ★T-108f 分块上限：单次压缩 >40 条时旧代码一次性吞噬全组（单批删上千 L1）；
           新代码切片为每块≤40、逐块压缩（每块一个 L2）。

全部外部依赖（节点池/知识树/快照/推理池/频率编码器）以 mock 隔离，**不写生产数据**。
运行方式见交付报告；本文件对【当前代码】应为全绿；对【备份(旧)代码】应为全红。
"""
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from organs.body.PulseLiver import PulseLiver  # noqa: E402
from nucleus.mnemosyne.PulseNode import PulseNode  # noqa: E402


class _FakeNodePool:
    """记录 add / remove 调用的轻量节点池，query 返回注入的既有节点。"""

    def __init__(self, existing=None):
        self.existing = list(existing) if existing else []
        self.added = []
        self.removed = []

    def query(self, **_kwargs):
        return self.existing

    def add(self, node):
        self.added.append(node)

    def remove(self, node_id):
        self.removed.append(node_id)


class TestT108bDedupSkipNoDeleteSource(unittest.TestCase):
    """★T-108b：完全重复跳过 → 不得删除源 L1 节点。"""

    def _make_liver(self, existing):
        _liver = PulseLiver(organ_name="肝")
        _liver.set_node_pool(_FakeNodePool(existing=existing))
        return _liver

    def test_01_full_overlap_returns_false(self):
        _existing = PulseNode(value="既有L2", keywords=["a", "b"], evol_level="L2")
        _liver = self._make_liver([_existing])
        _src = [PulseNode(value="v", keywords=["a", "b"], evol_level="L1")
                for _ in range(3)]
        _ret = _liver._try_merge_duplicate_l2(
            "/自我理解/代码/Foo", _src, ["a", "b"], ["v", "v", "v"], "Foo")
        self.assertFalse(_ret, "完全重复应返回 False（交还主路径完整压缩）")

    def test_02_source_nodes_not_removed(self):
        _existing = PulseNode(value="既有L2", keywords=["a", "b"], evol_level="L2")
        _liver = self._make_liver([_existing])
        _src = [PulseNode(value="v", keywords=["a", "b"], evol_level="L1")
                for _ in range(3)]
        _liver._try_merge_duplicate_l2(
            "/自我理解/代码/Foo", _src, ["a", "b"], ["v", "v", "v"], "Foo")
        _pool = _liver.node_pool
        self.assertEqual(_pool.removed, [], "完全重复跳过不得删除任何源 L1 节点")

    def test_03_existing_not_mutated(self):
        _existing = PulseNode(value="既有L2", keywords=["a", "b"], evol_level="L2")
        _liver = self._make_liver([_existing])
        _src = [PulseNode(value="v", keywords=["a", "b"], evol_level="L1")
                for _ in range(3)]
        _liver._try_merge_duplicate_l2(
            "/自我理解/代码/Foo", _src, ["a", "b"], ["v", "v", "v"], "Foo")
        # 既有节点未被改写（旧代码会执行合并分支改写 keywords / value）
        self.assertEqual(_existing.keywords, ["a", "b"])
        self.assertEqual(_existing.value, "既有L2")


class TestT108fChunkLimit(unittest.TestCase):
    """★T-108f：单次压缩 >40 条须切片为每块≤40、逐块压缩。"""

    def _run(self, liver, n):
        _nodes = [PulseNode(value="v%d" % _i, keywords=["a", "b"], evol_level="L1")
                  for _i in range(n)]
        _feats = {
            "all_keywords": ["a", "b"],
            "all_values": ["v"] * n,
            "total_abstraction": 0.5 * n,
            "quality_nodes": _nodes,
        }
        with mock.patch.object(PulseLiver, "_collect_group_features", return_value=_feats), \
                mock.patch.object(PulseLiver, "_build_l2_summary", return_value="summary"), \
                mock.patch.object(PulseLiver, "_assess_node_quality", return_value=0.8), \
                mock.patch.object(PulseLiver, "_try_merge_duplicate_l2", return_value=False), \
                mock.patch("nucleus.knowledge_noise_filter.check_self_consistency_for_node",
                           return_value={"contradiction_found": False, "trust_penalty": 0.0}):
            _ret = liver._compress_group("/自我理解/代码/Foo", _nodes)
        return _ret, liver.node_pool

    def test_01_hundred_nodes_split_into_three_l2(self):
        # 100 → 切片 [40, 40, 20] → 3 个 L2（旧代码无切片，仅 1 个 L2）
        _liver = PulseLiver(organ_name="肝")
        _liver.set_node_pool(_FakeNodePool())
        _liver.set_frequency_codec(None)
        _ret, _pool = self._run(_liver, 100)
        self.assertTrue(_ret)
        self.assertEqual(len(_pool.added), 3, "100条应切片为3块→3个L2（旧代码=1）")

    def test_02_all_sources_removed_across_chunks(self):
        _liver = PulseLiver(organ_name="肝")
        _liver.set_node_pool(_FakeNodePool())
        _liver.set_frequency_codec(None)
        _ret, _pool = self._run(_liver, 100)
        self.assertEqual(len(_pool.removed), 100, "全部源L1仍被清，但分3批执行")

    def test_03_boundary_40_stays_single_chunk(self):
        # 恰好 40 条：不触发切片（40 不 > 40），仍为 1 个 L2
        _liver = PulseLiver(organ_name="肝")
        _liver.set_node_pool(_FakeNodePool())
        _liver.set_frequency_codec(None)
        _ret, _pool = self._run(_liver, 40)
        self.assertTrue(_ret)
        self.assertEqual(len(_pool.added), 1, "40条=单块→1个L2")

    def test_04_boundary_41_splits(self):
        # 41 条：触发切片 → [40, 1] → 2 个 L2
        _liver = PulseLiver(organ_name="肝")
        _liver.set_node_pool(_FakeNodePool())
        _liver.set_frequency_codec(None)
        _ret, _pool = self._run(_liver, 41)
        self.assertTrue(_ret)
        self.assertEqual(len(_pool.added), 2, "41条应切片为2块→2个L2")


if __name__ == "__main__":
    unittest.main()
