# -*- coding: utf-8 -*-
"""IR 链核心组件最小结构测试（T150-4）：PulseKnowledgeRetriever。

不依赖真实知识库 / LLM：用轻量 mock 替换 node_pool / knowledge_tree。
锁定契约：
  - clean_node_value 阈值（<5 长度 / <10 清洗后 / 中文<3 → None）
  - 器官别名检索最长别名优先匹配
  - 核心概念静态定义命中
  - 路径模糊匹配（中文问题词命中中文路径段）
  - log_func 注入生效
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from organs.brain.PulseKnowledgeRetriever import (  # noqa: E402
    PulseKnowledgeRetriever,
    ORGAN_ALIAS_MAP,
)


class _Node:
    """极简知识节点 mock。"""

    def __init__(self, value, keywords=None, space_path=""):
        self.value = value
        self.keywords = keywords or []
        self.space_path = space_path


class _NodePool:
    """按 space_path_prefix 返回预设节点；其余返回空。"""

    def __init__(self, by_prefix=None):
        self.by_prefix = by_prefix or {}
        self.calls = []

    def query(self, evol_level=None, space_path_prefix=None, limit=None):
        self.calls.append((evol_level, space_path_prefix, limit))
        return self.by_prefix.get(space_path_prefix, [])


class _KnowledgeTree:
    def __init__(self, paths):
        self._paths = paths

    def get_all_paths(self):
        return list(self._paths)


class TestCleanNodeValue(unittest.TestCase):
    def test_none_and_short(self):
        self.assertIsNone(PulseKnowledgeRetriever.clean_node_value(None))
        self.assertIsNone(PulseKnowledgeRetriever.clean_node_value("abc"))   # 长度 <5
        self.assertIsNone(PulseKnowledgeRetriever.clean_node_value("abcd"))  # 长度 4 <5

    def test_too_short_after_clean(self):
        self.assertIsNone(PulseKnowledgeRetriever.clean_node_value("   ab   "))  # 清洗后 <10

    def test_low_chinese_ratio(self):
        # 长度足够但中文<3 → None
        self.assertIsNone(PulseKnowledgeRetriever.clean_node_value("abcdefghijklmnop"))

    def test_valid(self):
        out = PulseKnowledgeRetriever.clean_node_value("这是一段足够长的有效知识内容用于回复。")
        self.assertIsInstance(out, str)
        self.assertIn("知识", out)


class TestOrganAlias(unittest.TestCase):
    def test_longest_alias_priority(self):
        # “肝脏”应优先于“肝”被匹配 → PulseLiver；需别名路径与说明书路径都有节点
        handbook = _Node("[器官职责说明书·自动生成] 肝脏负责解毒与代谢，是核心消化器官。")
        pool = _NodePool({
            "/自我理解/器官别名/肝脏": [_Node("别名节点占位")],
            "/自我/架构/器官/PulseLiver": [handbook],
        })
        r = PulseKnowledgeRetriever(node_pool=pool)
        res = r.retrieve_organ_alias_knowledge("我的肝脏最近状态如何")
        self.assertIsNotNone(res)
        self.assertIn("肝脏", res)
        # 验证确为 PulseLiver（而非更短的“肝”别名误匹配）
        self.assertIn("消化", res)

    def test_no_node_pool_returns_none(self):
        r = PulseKnowledgeRetriever(node_pool=None)
        self.assertIsNone(r.retrieve_organ_alias_knowledge("肝脏"))

    def test_alias_map_covers_known_organs(self):
        # 映射表契约不应回退为空
        self.assertEqual(ORGAN_ALIAS_MAP["前额叶"], "PulseReflection")
        self.assertIn("肝", ORGAN_ALIAS_MAP)


class TestSelfKnowledge(unittest.TestCase):
    def test_core_concept_static_def(self):
        pool = _NodePool()  # 返回空，但核心概念静态定义应命中
        r = PulseKnowledgeRetriever(node_pool=pool)
        res = r.retrieve_self_knowledge("什么是五维共振")
        self.assertIsNotNone(res)
        self.assertIn("五维共振", res)

    def test_no_pool_none(self):
        r = PulseKnowledgeRetriever(node_pool=None)
        self.assertIsNone(r.retrieve_self_knowledge("什么是五维共振"))


class TestPathFuzzy(unittest.TestCase):
    def test_match_and_return(self):
        tree = _KnowledgeTree(["/自我/器官/肝脏", "/自我/状态"])
        node = _Node("肝脏是消化器官，负责代谢与解毒功能正常。")
        pool = _NodePool({"/自我/器官/肝脏": [node]})
        r = PulseKnowledgeRetriever(node_pool=pool, knowledge_tree=tree)
        res = r.retrieve_by_path_fuzzy_match("肝脏的功能是什么")
        self.assertIsNotNone(res)
        self.assertIn("肝脏", res)

    def test_missing_deps_none(self):
        r = PulseKnowledgeRetriever(node_pool=None, knowledge_tree=None)
        self.assertIsNone(r.retrieve_by_path_fuzzy_match("肝脏"))


class TestLogFuncInjection(unittest.TestCase):
    def test_log_called(self):
        calls = []
        pool = _NodePool()  # 别名命中但无别名节点 → 走 _log DEBUG
        r = PulseKnowledgeRetriever(
            node_pool=pool,
            log_func=lambda lvl, msg: calls.append((lvl, msg)),
        )
        r.retrieve_organ_alias_knowledge("胃不舒服")
        self.assertTrue(any("胃" in str(m) for _, m in calls), calls)


if __name__ == "__main__":
    unittest.main()
