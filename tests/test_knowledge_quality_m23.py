# -*- coding: utf-8 -*-
"""test_knowledge_quality_m23.py —— 主线第23批门控单测。

覆盖 6 组共 19 例：
  ① 知识一致性（直接矛盾/数值冲突/因果矛盾/定义不一致）      4 例
  ② 知识覆盖率（领域/深度/关联/时间）                        4 例
  ③ 知识老化（时效性/版本关联/矛盾驱动）                      3 例
  ④ 综合评分模型（加权计算/等级划分/趋势对比）                3 例
  ⑤ 自我认知引擎集成（集成调用/开关关闭兼容）                 2 例
  ⑥ 边界与性能（空库/单节点/1000节点性能）                    3 例
"""
import os
import sys
import time
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from nucleus.self_awareness.KnowledgeQualityAnalyzer import (  # noqa: E402
    KnowledgeQualityAnalyzer,
)
from nucleus.self_awareness.SelfAwarenessEngine import (  # noqa: E402
    SelfAwarenessEngine,
    reset_self_awareness_engine,
)

_NOW = 1_700_000_000.0     # 固定基准时间，保证时间相关断言可复现
_DAY = 86400.0


def _node(node_id: str, value: str, space_path: str = "/测试领域",
          evol_level: str = "L2", linked: list | None = None,
          age_days: float = 1.0, idle_days: float | None = None,
          activation_count: int = 1, importance: str = "B") -> dict:
    """构造一个符合 PulseNode 快照字段结构的测试节点。"""
    _created = _NOW - age_days * _DAY
    _activated = _NOW - (idle_days if idle_days is not None else age_days) * _DAY
    return {
        "node_id": node_id,
        "value": value,
        "keywords": ["测试"],
        "evol_level": evol_level,
        "importance": importance,
        "abstraction": 0.3,
        "created_at": _created,
        "updated_at": _created,
        "last_activated": _activated,
        "source_time": _created,
        "acquired_time": _created,
        "source_timestamp": 0.0,
        "activation_count": activation_count,
        "space_path": space_path,
        "state": "active",
        "source_organ": "test",
        "linked_nodes": list(linked or []),
        "semantic_relations": [],
        "hebbian_weight": 0.0,
        "cooccurrence_count": 0,
        "trust_score": 50.0,
        "version": 1,
        "checksum": "",
        "instinct": False,
        "ephemeral": False,
        "view_mode": "OUTER_VIEW",
        "trigger_reason": "",
        "frequency_signature": 0.0,
        "quality_flag": "clean",
        "quality_reason": "",
        "evidence_chain": [],
        "verification_history": [],
    }


def _analyzer() -> KnowledgeQualityAnalyzer:
    """固定 now 的分析器（不触碰真实知识库，节点全靠注入）。"""
    return KnowledgeQualityAnalyzer(now=_NOW)


class _Switch:
    """临时改写 config 开关。"""

    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        for _k, _v in self._kw.items():
            self._old[_k] = getattr(config, _k, None)
            setattr(config, _k, _v)
        return self

    def __exit__(self, *exc):
        for _k, _v in self._old.items():
            if _v is None:
                if hasattr(config, _k):
                    delattr(config, _k)
            else:
                setattr(config, _k, _v)
        return False


# ======================================================================
# ① 知识一致性（4 例）
# ======================================================================
class TestConsistency(unittest.TestCase):
    def test_01_direct_contradiction(self):
        """直接矛盾：「X 是 Y」与「X 不是 Y」→ 检出并标记。"""
        _a = _analyzer()
        _nodes = [
            _node("n1", "光速是恒定的", evol_level="L3"),
            _node("n2", "光速不是恒定的", space_path="/物理", evol_level="L3"),
        ]
        _r = _a.analyze_consistency(_nodes)
        self.assertEqual(_r["by_type"].get("direct_contradiction"), 1)
        _c = _r["conflicts"][0]
        self.assertEqual(_c["type"], "direct_contradiction")
        self.assertEqual(_c["severity"], "high")
        self.assertEqual(sorted(_c["node_ids"]), ["n1", "n2"])
        self.assertLess(_r["score"], 100.0)

    def test_02_numeric_conflict(self):
        """数值冲突：同指标不同数值（相对差 > 5%）→ 检出。"""
        _a = _analyzer()
        _nodes = [
            _node("n1", "CPU使用率为80%"),
            _node("n2", "CPU使用率为50%"),
        ]
        _r = _a.analyze_consistency(_nodes)
        self.assertEqual(_r["by_type"].get("numeric_conflict"), 1)
        self.assertIn("CPU使用率", _r["conflicts"][0]["detail"])

    def test_03_numeric_no_false_positive(self):
        """数值冲突**不误报**：年份 / 无连接词 / 含虚词的伪指标。"""
        _a = _analyzer()
        _nodes = [
            _node("n1", "（由30条相关知识归纳）", space_path="/A"),
            _node("n2", "（由1118条相关知识归纳）", space_path="/B"),
            _node("n3", "知识学习是2026年的重要方向", space_path="/C"),
            _node("n4", "知识学习是2023年的重要方向", space_path="/D"),
        ]
        _r = _a.analyze_consistency(_nodes)
        self.assertEqual(_r["by_type"].get("numeric_conflict", 0), 0,
                         "年份/无连接词/虚词指标名不应被判为数值冲突")

    def test_04_causal_and_definition(self):
        """因果矛盾 + 定义不一致 同时检出。"""
        _a = _analyzer()
        _nodes = [
            _node("n1", "内存不足导致程序崩溃"),
            _node("n2", "内存不足导致程序不崩溃"),
            _node("n3", "元认知是指对自己思考过程的觉察和调控能力"),
            _node("n4", "元认知是指一种完全不同的测量外部世界的物理方法", space_path="/物理"),
        ]
        _r = _a.analyze_consistency(_nodes)
        self.assertEqual(_r["by_type"].get("causal_contradiction"), 1)
        _defs = [c for c in _r["conflicts"] if c["type"] == "definition_inconsistency"]
        self.assertEqual(len(_defs), 1, "同一概念应去重为 1 条")
        self.assertTrue(_defs[0]["needs_review"], "语义类矛盾应标记待人工确认")


# ======================================================================
# ② 知识覆盖率（4 例）
# ======================================================================
class TestCoverage(unittest.TestCase):
    def test_05_domain_coverage_blind_spot(self):
        """领域覆盖：低密度领域被识别为盲区。"""
        _a = _analyzer()
        _nodes = [_node(f"a{i}", f"内容{i}", space_path="/大领域") for i in range(8)]
        _nodes.append(_node("b1", "孤例", space_path="/小领域"))
        _r = _a.analyze_coverage(_nodes)
        _blind = [b["domain"] for b in _r["blind_spots"]]
        self.assertIn("小领域", _blind)
        self.assertLess(_r["sub_scores"]["domain"], 100.0)

    def test_06_depth_coverage(self):
        """深度覆盖：全 L1 领域 → 深度分低 + 列入浅层领域。"""
        _a = _analyzer()
        _nodes = [_node(f"n{i}", f"内容{i}", evol_level="L1") for i in range(8)]
        _r = _a.analyze_coverage(_nodes)
        self.assertEqual(_r["level_dist"], {"L1": 8})
        self.assertLess(_r["sub_scores"]["depth"], 30.0)
        self.assertEqual(len(_r["shallow_domains"]), 1)

    def test_07_association_coverage_islands(self):
        """关联覆盖：无任何关联的节点被识别为孤岛。"""
        _a = _analyzer()
        _nodes = [_node(f"i{i}", f"内容{i}") for i in range(5)]
        _nodes.append(_node("linked1", "有关联", linked=["i0"]))
        _r = _a.analyze_coverage(_nodes)
        _ids = {x["node_id"] for x in _r["islands"]}
        self.assertIn("i0", _ids)
        self.assertNotIn("linked1", _ids)
        self.assertLess(_r["sub_scores"]["association"], 100.0)

    def test_08_time_coverage(self):
        """时间覆盖：全部陈旧节点 → 近 90 天注入为 0。"""
        _a = _analyzer()
        _nodes = [_node(f"old{i}", f"内容{i}", age_days=400) for i in range(6)]
        _r = _a.analyze_coverage(_nodes)
        self.assertEqual(_r["recent_90d"], 0)
        self.assertEqual(_r["sub_scores"]["time"], 0.0)


# ======================================================================
# ③ 知识老化（3 例）
# ======================================================================
class TestAging(unittest.TestCase):
    def test_09_obsolescence_and_dormant(self):
        """时效性 + 休眠：超 180 天且从未激活 → 两类老化。"""
        _a = _analyzer()
        _nodes = [_node("old1", "陈旧知识", age_days=400, activation_count=0,
                        idle_days=400)]
        _r = _a.analyze_aging(_nodes, consistency={"conflicts": []})
        _types = _r["aged_nodes"][0]["types"]
        self.assertIn("obsolescence", _types)
        self.assertIn("dormant", _types)
        self.assertLess(_r["score"], 100.0)

    def test_10_version_stale(self):
        """版本关联：引用低于当前的版本号 → 标记失效。"""
        _a = _analyzer()
        _nodes = [
            _node("v1", "框架 v9.0 的调用方式如下"),
            _node("v2", "框架 v9.5 的调用方式如下"),
        ]
        _r = _a.analyze_aging(_nodes, consistency={"conflicts": []})
        _ids = {x["node_id"] for x in _r["aged_nodes"]}
        self.assertIn("v1", _ids, "引用 v9.0（低于当前 9.5）应标记")
        self.assertNotIn("v2", _ids, "同版本不应标记")

    def test_11_conflict_driven(self):
        """矛盾驱动老化：与一致性冲突相关的节点列入待更新。"""
        _a = _analyzer()
        _nodes = [
            _node("c1", "光速是恒定的", evol_level="L3"),
            _node("c2", "光速不是恒定的", evol_level="L3"),
        ]
        _cons = _a.analyze_consistency(_nodes)
        _r = _a.analyze_aging(_nodes, consistency=_cons)
        _conf_nodes = [x for x in _r["aged_nodes"] if "conflict_driven" in x["types"]]
        self.assertEqual(len(_conf_nodes), 2)
        self.assertEqual(_conf_nodes[0]["action"], "review")


# ======================================================================
# ④ 综合评分模型（3 例）
# ======================================================================
class TestOverall(unittest.TestCase):
    def test_12_weighted_score(self):
        """加权计分：综合分 = 各维度按权重求和（口径可解释）。"""
        _a = _analyzer()
        _nodes = [_node(f"n{i}", f"内容{i}") for i in range(20)]
        _ov = _a.analyze_overall(_nodes)
        _d, _w = _ov["dimensions"], _ov["weights"]
        _expect = round(_d["consistency"] * _w["consistency"]
                        + _d["coverage"] * _w["coverage"]
                        + _d["aging"] * _w["aging"]
                        + _d["depth"] * _w["depth"]
                        + _d["breadth"] * _w["breadth"], 2)
        self.assertAlmostEqual(_ov["score"], _expect, places=1)
        self.assertEqual(sum(_w.values()), 1.0, "权重之和应为 1")

    def test_13_grade_levels(self):
        """等级划分：优秀/良好/一般/较差/危险 五档边界。"""
        from nucleus.self_awareness.KnowledgeQualityAnalyzer import _grade
        _cases = [(95, "优秀"), (85, "优秀"), (80, "良好"), (70, "良好"),
                  (60, "一般"), (40, "较差"), (29, "危险")]
        for _s, _exp in _cases:
            self.assertEqual(_grade(_s), _exp, f"{_s} 应判为 {_exp}")
        # analyze_overall 的 level 与 _grade 同口径
        _a = _analyzer()
        _ov = _a.analyze_overall([_node("n1", "内容", age_days=1)])
        self.assertEqual(_ov["level"], _grade(_ov["score"]))

    def test_14_compare_profiles(self):
        """趋势对比：变好/变坏/停滞 三类判定。"""
        _b = {"knowledge_quality": {"score": 60.0,
                                    "dimensions": {"consistency": 50.0, "coverage": 70.0}}}
        _c = {"knowledge_quality": {"score": 75.0,
                                    "dimensions": {"consistency": 50.2, "coverage": 60.0}}}
        _r = KnowledgeQualityAnalyzer.compare_profiles(_b, _c)
        self.assertIn("score", _r["improved"])
        self.assertIn("dim_coverage", _r["degraded"])
        self.assertIn("dim_consistency", _r["unchanged"])
        self.assertEqual(_r["delta"]["score"], 15.0)
        self.assertEqual(KnowledgeQualityAnalyzer.compare_profiles(None, _c),
                         {"improved": [], "degraded": [], "unchanged": [], "delta": {}})


# ======================================================================
# ⑤ 自我认知引擎集成（2 例）
# ======================================================================
class TestEngineIntegration(unittest.TestCase):
    def test_15_integrate_and_report(self):
        """集成调用：知识质量写入 knowledge_health，报告含第五维与五维总览。"""
        _e = SelfAwarenessEngine()
        _prof = _e.get_profile()
        _prof.knowledge_health = {
            "section": "knowledge_quality", "score": 88.0, "level": "优秀",
            "dimensions": {"consistency": 90.0, "coverage": 85.0, "aging": 88.0,
                           "depth": 90.0, "breadth": 100.0},
            "stats": {"conflicts": {"total": 2, "severity": {"high": 0},
                                    "needs_review": 1},
                      "blind_spots": 3, "islands": 5, "aged": 7,
                      "level_dist": {"L1": 10, "L2": 20, "L3": 5}},
            "top_issues": [{"kind": "冲突", "severity": "medium", "detail": "示例冲突"}],
            "suggestions": ["建议示例"],
            "summary": "知识质量 88.0/100（优秀）",
        }
        _sec = SelfAwarenessEngine._report_knowledge_quality_section(_prof)
        self.assertTrue(any("知识质量健康度" in x for x in _sec))
        self.assertTrue(any("88.0" in x for x in _sec))
        _ov = SelfAwarenessEngine._report_five_dimension_overview(_prof)
        self.assertTrue(any("五维健康度总览" in x for x in _ov))
        self.assertTrue(any("知识质量健康度: 88.00" in x for x in _ov))
        _txt = _e.generate_report()
        self.assertIn("【知识质量健康度】", _txt)
        self.assertIn("【五维健康度总览】", _txt)

    def test_16_switch_off_zero_regression(self):
        """开关关闭：集成返回空 dict，报告显示「数据不可用」。"""
        _e = SelfAwarenessEngine()
        with _Switch(ENABLE_KNOWLEDGE_QUALITY_ANALYSIS=False):
            self.assertEqual(_e.integrate_knowledge_quality(), {})
        _prof = _e.get_profile()
        _prof.knowledge_health = {}
        _sec = SelfAwarenessEngine._report_knowledge_quality_section(_prof)
        self.assertTrue(any("不可用" in x for x in _sec))
        # 开关恢复后可用
        reset_self_awareness_engine()
        self.assertTrue(getattr(config, "ENABLE_KNOWLEDGE_QUALITY_ANALYSIS", False),
                        "默认应开启")


# ======================================================================
# ⑥ 边界与性能（3 例）
# ======================================================================
class TestBoundary(unittest.TestCase):
    def test_17_empty_library(self):
        """空知识库：各分析器不抛异常，评分给出保守值。"""
        _a = _analyzer()
        _c = _a.analyze_consistency([])
        self.assertEqual(_c["score"], 100.0)
        self.assertEqual(_c["stats"]["total"], 0)
        _cv = _a.analyze_coverage([])
        self.assertEqual(_cv["score"], 0.0)
        self.assertEqual(_cv["total_nodes"], 0)
        _ag = _a.analyze_aging([])
        self.assertEqual(_ag["total_nodes"], 0)
        _rep = _a.generate_report([])
        self.assertIn("score", _rep)

    def test_18_single_node(self):
        """单节点知识库：不崩、无冲突、孤岛计数为 1。"""
        _a = _analyzer()
        _nodes = [_node("only", "唯一的知识点")]
        _c = _a.analyze_consistency(_nodes)
        self.assertEqual(_c["stats"]["total"], 0)
        _cv = _a.analyze_coverage(_nodes)
        self.assertEqual(_cv["island_count"], 1)
        _ov = _a.analyze_overall(_nodes)
        self.assertIsInstance(_ov["score"], float)

    def test_19_performance_1000_nodes(self):
        """性能：1000 节点全量分析 < 10 秒（任务书约束）。"""
        _a = _analyzer()
        _nodes = []
        for _i in range(1000):
            _nodes.append(_node(
                f"p{_i}",
                f"这是第{_i}条知识，指标X为{_i % 90 + 10}%，描述内容若干",
                space_path=f"/领域{_i % 12}",
                evol_level=("L1", "L2", "L3")[_i % 3],
                linked=[f"p{_i - 1}"] if _i % 4 else [],
                age_days=float(_i % 500),
            ))
        _t0 = time.perf_counter()
        _ov = _a.analyze_overall(_nodes)
        _elapsed = time.perf_counter() - _t0
        self.assertLess(_elapsed, 10.0, f"1000 节点分析应 <10s，实测 {_elapsed:.2f}s")
        self.assertEqual(_ov["total_nodes"], 1000)
        self.assertIsInstance(_ov["score"], float)


if __name__ == "__main__":
    unittest.main(verbosity=2)
