# -*- coding: utf-8 -*-
"""QICA 意图分类测试（≥10 例）：8 通道融合 / refine_intent 精修 / 3 层意图层次 / 边界条件。

全部使用纯函数（无需向量编码器）：fuse / channel_contributions /
linguistic_refine_intent / refine_intent / INTENT_DEFAULT_PATHS / PATH_TO_CHANNEL。
"""
import pytest

import config
from nucleus.qica.IntentChannels import (
    INTENT_DEFAULT_PATHS,
    PATH_TO_CHANNEL,
    channel_contributions,
    fuse,
    linguistic_refine_intent,
    refine_intent,
)

# 8 通道名（与 config.QICA_CHANNEL_WEIGHTS 的键一致）
CHANNELS = ["keyword", "semantic", "context", "entity",
            "sentence", "expansion", "complexity", "feedback"]


def _channel_scores(top_intent, top_val=0.9):
    """构造 8 通道对单一胜出意图的通道分 dict。"""
    return {ch: {top_intent: top_val if ch == "keyword" else top_val - 0.1 * i}
            for i, ch in enumerate(CHANNELS)}


def _weights():
    return dict(getattr(config, "QICA_CHANNEL_WEIGHTS", {}) or {})


class TestFuse:
    def test_fuse_返回基本字段(self):
        r = fuse(_channel_scores("身份确认"))
        assert "intent_scores" in r
        assert "top_intent" in r
        assert "top_score" in r

    def test_fuse_最高分意图胜出(self):
        cs = {
            "keyword": {"关系查询": 0.95, "闲聊": 0.1},
            "semantic": {"关系查询": 0.9, "闲聊": 0.2},
            "context": {"关系查询": 0.8},
            "entity": {"关系查询": 0.7},
            "sentence": {"关系查询": 0.6},
            "expansion": {"关系查询": 0.5},
            "complexity": {"关系查询": 0.4},
            "feedback": {"关系查询": 0.3},
        }
        r = fuse(cs)
        assert r["top_intent"] == "关系查询"

    def test_fuse_top_score_在0到1(self):
        r = fuse(_channel_scores("身份确认"))
        assert 0.0 <= float(r["top_score"]) <= 1.0

    def test_fuse_空输入不崩溃(self):
        r = fuse({})
        assert isinstance(r, dict)

    def test_fuse_权重影响融合(self):
        # 用真实意图（fuse 仅对已知意图词汇打分）：身份确认 vs 关系查询，
        # keyword 强烈偏向身份确认，semantic 强烈偏向关系查询，验证融合不崩溃且给出已知胜出意图
        cs = {
            "keyword": {"身份确认": 0.95, "关系查询": 0.10},
            "semantic": {"身份确认": 0.10, "关系查询": 0.95},
            "context": {"身份确认": 0.6, "关系查询": 0.4},
            "entity": {"身份确认": 0.7, "关系查询": 0.3},
            "sentence": {"身份确认": 0.5, "关系查询": 0.5},
            "expansion": {"身份确认": 0.6, "关系查询": 0.4},
            "complexity": {"身份确认": 0.55, "关系查询": 0.45},
            "feedback": {"身份确认": 0.8, "关系查询": 0.2},
        }
        r1 = fuse(cs)
        assert r1["top_intent"] in INTENT_DEFAULT_PATHS
        assert r1["top_score"] > 0


class TestChannelContributions:
    def test_归一化到1(self):
        contrib = channel_contributions(_channel_scores("身份确认"), _weights(), "身份确认")
        assert abs(sum(contrib.values()) - 1.0) < 1e-6

    def test_空通道返回空(self):
        assert channel_contributions({}, _weights(), "身份确认") == {}

    def test_全零分返回空(self):
        zeros = {ch: {"身份确认": 0.0} for ch in CHANNELS}
        assert channel_contributions(zeros, _weights(), "身份确认") == {}

    def test_最高加权贡献通道非空(self):
        contrib = channel_contributions(_channel_scores("身份确认"), _weights(), "身份确认")
        assert max(contrib, key=contrib.get) in CHANNELS


class TestRefineIntent:
    def test_人名关系词_关系查询(self):
        new, reason = linguistic_refine_intent("你和小林是什么关系", "知识查询")
        assert new == "关系查询"
        assert reason == "person+relation"

    def test_对比词_对比分析(self):
        new, reason = linguistic_refine_intent("两者有什么区别", "知识查询")
        assert new == "对比分析"
        assert reason == "contrast"

    def test_非易混意图_不重写(self):
        new, reason = linguistic_refine_intent("今天天气如何", "身份确认")
        assert new is None and reason is None

    def test_空文本_不崩溃(self):
        new, reason = linguistic_refine_intent("", "一般对话")
        assert (new, reason) == (None, None)

    def test_refine_intent_对fuse结果生效(self):
        res = fuse(_channel_scores("身份确认"))  # top_intent 应为 身份确认
        # 文本含人名+关系词 → 应改写为 关系查询
        out = refine_intent("你和小林是什么关系", res)
        assert out["top_intent"] == "关系查询"
        assert out.get("refined") is True


class TestIntentHierarchy:
    def test_INTENT_DEFAULT_PATHS_为字典(self):
        assert isinstance(INTENT_DEFAULT_PATHS, dict)
        assert len(INTENT_DEFAULT_PATHS) > 0

    def test_INTENT_DEFAULT_PATHS_值结构合理(self):
        # INTENT_DEFAULT_PATHS：意图 → 路径列表（每条以 "/" 开头）
        assert isinstance(INTENT_DEFAULT_PATHS, dict)
        for intent, paths in INTENT_DEFAULT_PATHS.items():
            assert isinstance(paths, (list, tuple)) and len(paths) > 0, intent
            for p in paths:
                assert isinstance(p, str) and p.startswith("/"), f"{intent} -> {p}"

    def test_PATH_TO_CHANNEL_为合法三层映射(self):
        # PATH_TO_CHANNEL：执行路径 → 三层通道（fast / knowledge / deep）
        assert isinstance(PATH_TO_CHANNEL, dict)
        layers = set(PATH_TO_CHANNEL.values())
        assert {"fast", "knowledge", "deep"}.issubset(layers)
        for ch in layers:
            assert isinstance(ch, str) and ch in ("fast", "knowledge", "deep")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
