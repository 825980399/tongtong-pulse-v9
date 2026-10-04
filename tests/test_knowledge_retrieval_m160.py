# -*- coding: utf-8 -*-
"""★第160批 上A 刀2（票1①②·T-知识检索相关性-1）防回归单测。

验证三件事：
1. 全局语义检索「命中判据」灰度开关 KNOWLEDGE_GLOBAL_HIT_THRESHOLD 存在且默认 0.5；
   路径前缀兜底应急开关 KNOWLEDGE_INFER_PREFIX_FALLBACK_BROAD 存在且默认 False。
2. _infer_path_prefixes 兜底不再返回覆盖 78% 全库的宽前缀
   ['/知识','/综合','/自我理解']，无具体命中即返回空集（交全库共振/肺渠道）。
3. narrowing 保留：已知关键词仍映射到具体小粒度前缀
   （如 '架构'→'/技术/架构'、'心脏'→'/自我理解/器官别名/心脏'）。

标签：@pytest.mark.m160
"""
import importlib

import pytest

from organs.brain.pulse_inner_world_support import PulseInnerWorldSupportMixin


@pytest.mark.m160
class TestKn160Knife2Config:
    def test_hit_threshold_switch_exists_default(self):
        import config
        importlib.reload(config)  # 确保读到最新常量
        assert hasattr(config, "KNOWLEDGE_GLOBAL_HIT_THRESHOLD")
        assert config.KNOWLEDGE_GLOBAL_HIT_THRESHOLD == 0.5
        assert hasattr(config, "KNOWLEDGE_INFER_PREFIX_FALLBACK_BROAD")
        assert config.KNOWLEDGE_INFER_PREFIX_FALLBACK_BROAD is False


class _Stub:
    def _log(self, *args, **kwargs):
        return None


@pytest.mark.m160
class TestInferPathPrefixesNarrowFallback:
    def _call(self, question):
        return PulseInnerWorldSupportMixin._infer_path_prefixes(_Stub(), question)

    def test_no_broad_fallback_for_unmatched_questions(self):
        # 组件类问题（指标采集器/量子引力/递归思想）无具体关键词命中
        # → 不得出现宽兜底 ['/知识','/综合','/自我理解']（根因②）
        for q in ("你了解指标采集器吗？", "如何理解递归思想", "讲个故事听听"):
            res = self._call(q)
            assert "/知识" not in res
            assert "/综合" not in res
            assert "/自我理解" not in res

    def test_empty_when_no_keyword_hit(self):
        # 任何无地图关键词命中的中性提问 → 空集（交全库共振/肺渠道）
        assert self._call("讲个故事听听") == []
        assert self._call("如何理解递归思想") == []

    def test_known_keyword_maps_to_specific_narrow_prefix(self):
        # narrowing 保留：已知关键词仍映射具体小粒度前缀（非宽前缀）
        assert self._call("讲讲系统架构") == ["/技术/架构"]
        assert self._call("心脏是怎么工作的") == ["/自我理解/器官别名/心脏"]
        assert self._call("学编程有用吗") == ["/技术/编程"]
        assert self._call("聊聊身份与使命") == ["/身份"]
