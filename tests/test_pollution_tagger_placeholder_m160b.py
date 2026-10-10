# -*- coding: utf-8 -*-
"""★第160批 下·上 刀0（PollutionTagger 补 placeholder_alias 枚举）防回归单测。

覆盖任务书 0.1~0.6 与验收判据「clean= 不再吞占位符节点 / placeholder_alias 计数不降」：
  0.1 新增 FLAG_PLACEHOLDER_ALIAS 常量
  0.2 降权系数 0.3（与 polluted 同档）
  0.3 短路集合扩到 placeholder_alias（显式粘标优先）
  0.4 P0 占位符字面量现算判据（走 PlaceholderSanitizer 唯一口径）
  0.5 写回「严重度只升不降」护栏
  0.6 修好的节点自动摘标（DataQualityGuard.clear_flag，此前零调用点）
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.knowledge.PollutionTagger import (  # noqa: E402
    _FLAG_SEVERITY,
    FLAG_CLEAN,
    FLAG_PLACEHOLDER_ALIAS,
    FLAG_POLLUTED,
    FLAG_SUSPECT,
    PollutionTagger,
)


class _Node:
    """最小节点替身（非 dict，走 scan_* 的写回分支）。"""

    def __init__(self, nid, value, keywords=None, space_path="/知识/测试",
                 quality_flag=FLAG_CLEAN, quality_reason=""):
        self.node_id = nid
        self.value = value
        self.keywords = keywords or []
        self.space_path = space_path
        self.quality_flag = quality_flag
        self.quality_reason = quality_reason

    def to_dict(self):
        return {
            "node_id": self.node_id,
            "value": self.value,
            "keywords": self.keywords,
            "space_path": self.space_path,
            "quality_flag": self.quality_flag,
            "quality_reason": self.quality_reason,
        }


class TestFlagConstantAndFactor(unittest.TestCase):
    """0.1 / 0.2：常量与降权系数。"""

    def test_flag_constant(self):
        self.assertEqual(FLAG_PLACEHOLDER_ALIAS, "placeholder_alias")

    def test_downweight_factor_is_0_3(self):
        _t = PollutionTagger()
        _n = _Node("n1", "含[器官别名]的占位内容", quality_flag=FLAG_PLACEHOLDER_ALIAS)
        self.assertAlmostEqual(_t.downweight_factor(_n), 0.3)

    def test_severity_order(self):
        """0.5：placeholder_alias > polluted > suspect > clean。"""
        self.assertGreater(_FLAG_SEVERITY[FLAG_PLACEHOLDER_ALIAS], _FLAG_SEVERITY[FLAG_POLLUTED])
        self.assertGreater(_FLAG_SEVERITY[FLAG_POLLUTED], _FLAG_SEVERITY[FLAG_SUSPECT])
        self.assertGreater(_FLAG_SEVERITY[FLAG_SUSPECT], _FLAG_SEVERITY[FLAG_CLEAN])


class TestP0PlaceholderPredicate(unittest.TestCase):
    """0.4：P0 现算判据（唯一口径），且 clean 不再吞占位符节点。"""

    def setUp(self):
        self._t = PollutionTagger()

    def test_alias_literal_hits_p0(self):
        _flag, _reason = self._t.classify(
            {"node_id": "a1", "value": "这是[器官别名]节点", "keywords": [], "space_path": "/x"})
        self.assertEqual(_flag, FLAG_PLACEHOLDER_ALIAS)
        self.assertTrue(_reason.startswith("P0"))

    def test_derived_prefix_hits_p0(self):
        for _v in ("[归纳升华]融合产物内容", "[数据流·某器官]", "关联知识：。空壳"):
            _flag, _ = self._t.classify(
                {"node_id": "a2", "value": _v, "keywords": [], "space_path": "/x"})
            self.assertEqual(_flag, FLAG_PLACEHOLDER_ALIAS, "应命中 P0: {!r}".format(_v))

    def test_normal_node_stays_clean(self):
        """防误伤：正常知识不得被 P0 误判。"""
        _flag, _ = self._t.classify(
            {"node_id": "a3", "value": "共振引擎是五维打分的检索引擎", "keywords": ["共振"],
             "space_path": "/自我/架构"})
        self.assertEqual(_flag, FLAG_CLEAN)

    def test_placeholder_node_not_swallowed_by_clean(self):
        """核心判据：占位符节点不得再落入 clean。"""
        _n = _Node("a4", "裸父路径节点的[器官别名]内容", quality_flag=FLAG_CLEAN)
        _t2 = PollutionTagger()
        _t2.scan_nodes([_n], log_fn=lambda l, m: None)
        self.assertEqual(_n.quality_flag, FLAG_PLACEHOLDER_ALIAS)
        self.assertGreaterEqual(_t2.get_stats()["placeholder_alias"], 1)


class TestExplicitShortCircuit(unittest.TestCase):
    """0.3：显式 quality_flag=placeholder_alias 走短路，且可观测。"""

    def test_explicit_flag_short_circuits(self):
        _t = PollutionTagger()
        _flag, _reason = _t.classify({
            "node_id": "b1", "value": "完全正常的内容",
            "keywords": [], "space_path": "/x",
            "quality_flag": FLAG_PLACEHOLDER_ALIAS, "quality_reason": "159上B落盘",
        })
        self.assertEqual(_flag, FLAG_PLACEHOLDER_ALIAS)
        self.assertTrue(_reason.startswith("E1"))
        self.assertGreaterEqual(_t.get_stats()["explicit_hits"], 1)


class TestSeverityGuardAndAutoUnflag(unittest.TestCase):
    """0.5 只升不降 + 0.6 修好自动摘标。"""

    def test_repaired_node_auto_unflagged(self):
        """0.6：内容已判干净 + 旧粘标 placeholder_alias → 自动摘标回 clean。"""
        _n = _Node("c1", "这条内容已经被修好了，是正常知识",
                   quality_flag=FLAG_PLACEHOLDER_ALIAS, quality_reason="P0")
        PollutionTagger().scan_nodes([_n], log_fn=lambda l, m: None)
        self.assertEqual(_n.quality_flag, FLAG_CLEAN, "修好的节点应自动摘标")

    def test_still_polluted_node_not_downgraded(self):
        """0.5：仍含占位符的节点不得被降回 clean / polluted。"""
        _n = _Node("c2", "仍含[器官别名]的未修内容",
                   quality_flag=FLAG_PLACEHOLDER_ALIAS, quality_reason="P0")
        PollutionTagger().scan_nodes([_n], log_fn=lambda l, m: None)
        self.assertEqual(_n.quality_flag, FLAG_PLACEHOLDER_ALIAS, "严重度只升不降")

    def test_clean_never_overwrites_higher_severity(self):
        """0.5：既有更重标记（polluted）不得被本次 clean 结果覆盖。"""
        _n = _Node("c3", "内容看起来正常了", quality_flag=FLAG_POLLUTED, quality_reason="P1")
        PollutionTagger().scan_nodes([_n], log_fn=lambda l, m: None)
        self.assertEqual(_n.quality_flag, FLAG_POLLUTED, "clean 不得覆盖 polluted")


class TestPlaceholderAliasSwitchOff(unittest.TestCase):
    """刀0 灰度开关 placeholder_alias_enabled=False ⇒ **完全回退**到刀0 前行为。"""

    _OFF = {"placeholder_alias_enabled": False}

    def test_p0_skipped_when_disabled(self):
        """0.4 跳过：占位符内容节点回退判 clean（不再被判 placeholder_alias）。"""
        _t = PollutionTagger(config=self._OFF)
        _flag, _ = _t.classify(
            {"node_id": "d1", "value": "裸父路径节点的[器官别名]内容",
             "keywords": [], "space_path": "/x"})
        self.assertEqual(_flag, FLAG_CLEAN)

    def test_explicit_not_short_circuited_when_disabled(self):
        """0.3 缩回：显式 placeholder_alias 不再短路，explicit_hits 不增。"""
        _t = PollutionTagger(config=self._OFF)
        _flag, _ = _t.classify({
            "node_id": "d2", "value": "这是一条完全正常的知识内容，不含任何占位符",
            "keywords": ["正常"], "space_path": "/x",
            "quality_flag": FLAG_PLACEHOLDER_ALIAS, "quality_reason": "159上B落盘",
        })
        self.assertEqual(_flag, FLAG_CLEAN)
        self.assertEqual(_t.get_stats()["explicit_hits"], 0)

    def test_rollback_differential_still_polluted_node(self):
        """开关差分：仍污染节点 —— 开=受保护(placeholder_alias) / 关=回退(clean)。"""
        _n_on = _Node("d3", "仍含[器官别名]的未修内容", quality_flag=FLAG_PLACEHOLDER_ALIAS)
        PollutionTagger().scan_nodes([_n_on], log_fn=lambda l, m: None)
        self.assertEqual(_n_on.quality_flag, FLAG_PLACEHOLDER_ALIAS, "开关开：应受保护")

        _n_off = _Node("d4", "仍含[器官别名]的未修内容", quality_flag=FLAG_PLACEHOLDER_ALIAS)
        PollutionTagger(config=self._OFF).scan_nodes([_n_off], log_fn=lambda l, m: None)
        self.assertEqual(_n_off.quality_flag, FLAG_CLEAN, "开关关：完全回退（保护撤销）")


if __name__ == "__main__":
    unittest.main()
