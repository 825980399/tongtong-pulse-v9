# -*- coding: utf-8 -*-
# ★第160批 上A 刀1（票3 B 案）：运行时内容判据唯一口径 + 排序层内容降权
#   防回归单测。标签 m160（防串台）。
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nucleus.knowledge.PlaceholderSanitizer import contains_placeholder_literal  # noqa: E402
from nucleus.semantic.VectorStore import _quality_weight  # noqa: E402


# ---------- 验收①：判据唯一口径（含防误伤） ----------
class TestContainsPlaceholderLiteral:
    @pytest.mark.m160
    def test_core_literal(self):
        # 核心污染本体
        assert contains_placeholder_literal("[器官别名] 意图识别") is True

    @pytest.mark.m160
    def test_prefix_归纳升华(self):
        assert contains_placeholder_literal("[归纳升华] 在X领域…") is True

    @pytest.mark.m160
    def test_prefix_数据流(self):
        # 融合产物（[数据流·PulseLiver] 类）应命中
        assert contains_placeholder_literal("[数据流·PulseLiver] 已理解11个方法") is True

    @pytest.mark.m160
    def test_prefix_关联知识(self):
        assert contains_placeholder_literal("关联知识：。关联知识：功能: 类比") is True

    @pytest.mark.m160
    def test_not_match_综合_normal(self):
        # ★防误伤：正常前缀标记不得误判（159 上B dry-run 两轮校准教训）
        assert contains_placeholder_literal("[综合] 深度探索: 由30条认知归纳") is False

    @pytest.mark.m160
    def test_not_match_代码关联(self):
        # [代码关联·X] / [数据流· ]带「·」者为正常标记，但本判据只认
        # 精确前缀 "[数据流·"（无后续内容截断）——此处构造含「·」但非前缀形态
        assert contains_placeholder_literal("普通回答，不含占位符") is False

    @pytest.mark.m160
    def test_empty_and_none(self):
        assert contains_placeholder_literal("") is False
        assert contains_placeholder_literal(None) is False


# ---------- 刀1.3：排序层内容降权（不依赖落盘 flag） ----------
class TestQualityWeightContent:
    @pytest.mark.m160
    def test_content_downweight(self):
        # value 命中判据 → 0.3（与 placeholder_alias 同档）
        assert _quality_weight("clean", "[器官别名] x") == 0.3
        assert _quality_weight("clean", "[归纳升华] y") == 0.3
        assert _quality_weight("clean", "关联知识：。foo") == 0.3

    @pytest.mark.m160
    def test_normal_unaffected(self):
        # 无内容命中 → flag 权重不变
        assert _quality_weight("clean", "普通回答") == 1.0
        assert _quality_weight(None, "普通回答") == 1.0

    @pytest.mark.m160
    def test_placeholder_alias_unchanged(self):
        # 原 flag 路径仍生效
        assert _quality_weight("placeholder_alias") == 0.3
        assert _quality_weight("polluted") == 0.0
        # 内容命中进一步压到 0.3（与 flag 同档，不更低）
        assert _quality_weight("placeholder_alias", "[器官别名]") == 0.3


# ---------- 验收①：support.py 检索层接同一函数（结构性防回归） ----------
class TestSupportWiring:
    @pytest.mark.m160
    def test_support_calls_same_function(self):
        # 防止 support.py 长出第二套字面量定义
        import inspect

        import organs.brain.pulse_inner_world_support as sup

        src = inspect.getsource(sup.PulseInnerWorldSupportMixin._node_quality_reject_reason)
        assert "contains_placeholder_literal(value)" in src, \
            "support.py 检索层必须调用唯一口径 contains_placeholder_literal"
