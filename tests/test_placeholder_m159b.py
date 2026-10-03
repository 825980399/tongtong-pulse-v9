# -*- coding: utf-8 -*-
"""★第159批 上B 刀B（T-对话模板-1）防回归单测（隔离标记 m159b）。

钉住刀B① 判据与刀B③ 出口净化的关键不变量：
  1. 刀B①：裸父路径（space_path="/自我理解/器官别名"）且 value 含 [器官别名]
     → 必须命中（修前谓词带尾斜杠恒漏检，即 P2-26 从未兑现的结构性原因）。
  2. 刀B① ★防误伤（实测校准所得）：正常前缀标记 [数据流·X] / [代码关联·X] /
     [综合] **不得**被判为占位符——初版泛化判据曾误判 3911 + 1613 个正常节点。
  3. 刀B③：含占位符 → 剥离；剥离后过短 → 降级「未命中相关知识」引导语
     （★不伪造内容）；未命中占位符 → 原样返回（零副作用）。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.knowledge.PlaceholderSanitizer import (  # noqa: E402
    PLACEHOLDER_FALLBACK_TEXT,
    sanitize_placeholder_text,
)
from tools.cleanup_alias_placeholder_nodes import (  # noqa: E402
    is_placeholder_alias_node,
)


class TestCleanupPredicate(unittest.TestCase):
    """刀B①：识别面修正（去尾斜杠）+ 防误伤。"""

    def test_bare_parent_path_hit(self):
        """裸父路径 + value 含 [器官别名] → 命中（修前恒漏检）。"""
        _n = {"space_path": "/自我理解/器官别名",
              "value": "[器官别名] [器官别名] 人格内核（PulsePersonalityKernel）是..."}
        self.assertTrue(is_placeholder_alias_node(_n))

    def test_path_placeholder_hit(self):
        """路径末段是占位符 → 命中。"""
        _n = {"space_path": "/自我理解/器官别名/[器官别名]", "value": "正常内容"}
        self.assertTrue(is_placeholder_alias_node(_n))

    def test_normal_code_marker_not_hit(self):
        """★防误伤：/自我理解/代码 下 [数据流·X] 正常标记不得命中。"""
        _n = {"space_path": "/自我理解/代码/PulseLiver",
              "value": "[数据流·PulseLiver] 已理解45个方法。入口方法: start、stop"}
        self.assertFalse(is_placeholder_alias_node(_n),
                         "正常代码认知前缀标记不得被判为占位符")

    def test_normal_summary_marker_not_hit(self):
        """★防误伤：/综合 下 [综合] 正常前缀不得命中。"""
        _n = {"space_path": "/综合",
              "value": "[综合] 深度探索: 自我优化: 通用领域 ...（由24条认知融合而成）"}
        self.assertFalse(is_placeholder_alias_node(_n),
                         "「[综合]」为正常前缀标记，不得被判为占位符")

    def test_non_dict_safe(self):
        self.assertFalse(is_placeholder_alias_node(None))
        self.assertFalse(is_placeholder_alias_node({}))


class TestSanitizePlaceholderText(unittest.TestCase):
    """刀B③：出口占位符净化（只对方括号占位符**字面量**生效）。"""

    def test_strip_placeholder(self):
        _out = sanitize_placeholder_text(
            "[器官别名] [器官别名] 肝脏（PulseLiver）是框架中的仿生器官。")
        self.assertNotIn("[器官别名]", _out)
        self.assertIn("PulseLiver", _out, "剥离后应保留实质正文")

    def test_fallback_when_too_short(self):
        """剥离后无实质内容 → 降级引导语（★不伪造内容）+ DEBUG 留痕。"""
        _logs = []
        _out = sanitize_placeholder_text("[器官别名]", log=_logs.append)
        self.assertEqual(_out, PLACEHOLDER_FALLBACK_TEXT)
        self.assertTrue(_logs, "降级须有 DEBUG 留痕")

    def test_no_placeholder_returns_unchanged(self):
        """★零副作用：未命中占位符字面量 → 原样返回（红线：不按长度/前缀判定）。"""
        _src = "我了解到，这是真正的内容，不含任何占位符。"
        self.assertEqual(sanitize_placeholder_text(_src), _src)

    def test_long_normal_text_not_stripped(self):
        """★红线：不得因「长 + 我了解到前缀」被净化（该组合会误伤真回答）。"""
        _src = "我了解到，" + "真实内容" * 100
        self.assertEqual(sanitize_placeholder_text(_src), _src)


if __name__ == "__main__":
    unittest.main()
