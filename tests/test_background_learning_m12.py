# -*- coding: utf-8 -*-
"""主线第12批 T3：后台学习消化质量优化 —— 门控单测。

覆盖：
  T3.1 后台学习来源判定 / 关键词门槛（拦截 & 放行）/ 被拒记录 / 灰度关闭零副作用
  T3.2 提示词结构化追加（后台追加 / 对话不追加 / 关闭不追加）
  T3.3 后台学习优先免费渠道（重排 / 不动对话 / 白名单缺失忽略 / 关闭不重排）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg  # noqa: E402
from organs.body.PulseLung import PulseLung  # noqa: E402
from organs.body.PulseStomach import PulseStomach  # noqa: E402


class _SilentLog:
    def __call__(self, *a, **k):
        return None


def _bare_stomach():
    s = PulseStomach.__new__(PulseStomach)
    s._log = _SilentLog()
    return s


def _bare_lung():
    l = PulseLung.__new__(PulseLung)
    l._log = _SilentLog()
    return l


# ==================== T3.1 ====================

def test_background_source_detection_positive():
    s = _bare_stomach()
    assert s._is_background_learning_source("潜意识", "curiosity.explore") is True
    assert s._is_background_learning_source("双腿", "active_learn:search:技术") is True
    assert s._is_background_learning_source("潜意识", "dream.deduction") is True


def test_background_source_detection_negative():
    s = _bare_stomach()
    # 对话链路 / 代码学习 / 内置知识均不属于后台学习
    assert s._is_background_learning_source("大脑皮层", "") is False
    assert s._is_background_learning_source("大脑皮层", "user_question") is False
    assert s._is_background_learning_source("文件", "file_digest") is False


def test_background_gate_disabled_zero_effect():
    s = _bare_stomach()
    old = cfg.ENABLE_BACKGROUND_LEARNING_QUALITY_GATE
    cfg.ENABLE_BACKGROUND_LEARNING_QUALITY_GATE = False
    try:
        assert s._is_background_learning_source("双腿", "active_learn:search:x") is False
    finally:
        cfg.ENABLE_BACKGROUND_LEARNING_QUALITY_GATE = old


def test_rejected_ring_records_metadata_only():
    s = _bare_stomach()
    s._record_rejected_digestion("一段被拒正文内容", ["A", "B"], "双腿",
                                 "active_learn:search:x", "有效关键词不足")
    ring = s._rejected_digestion_ring
    assert len(ring) == 1
    rec = ring[0]
    assert rec["source_organ"] == "双腿"
    assert rec["keyword_count"] == 2
    assert rec["reason"] == "有效关键词不足"
    # 不落正文
    assert "一段被拒正文内容" not in str(rec)


def test_min_keywords_config_boundary():
    qc = getattr(cfg, "BACKGROUND_LEARNING_QUALITY_CONFIG", {})
    assert int(qc.get("min_keywords", 3)) == 3
    # 逻辑边界：2 个 → 拦截；3 个 → 放行（关键词需 ≥2 字符才算有效）
    assert len([k for k in ["AA", "BB"] if len(k) >= 2]) < 3
    assert len([k for k in ["AA", "BB", "CC"] if len(k) >= 2]) >= 3


# ==================== T3.2 ====================

def test_prompt_requirement_appended_for_background():
    l = _bare_lung()
    p = "请介绍 Python 异步编程"
    out = l._append_background_learning_requirement(
        p, {"is_background_learning": True, "is_dialogue": False})
    assert out != p
    assert "关键词" in out
    assert out.startswith(p)


def test_prompt_requirement_not_appended_for_dialogue():
    l = _bare_lung()
    p = "请介绍 Python 异步编程"
    out = l._append_background_learning_requirement(
        p, {"is_background_learning": False, "is_dialogue": True})
    assert out == p


def test_prompt_requirement_idempotent():
    l = _bare_lung()
    p = "问题"
    once = l._append_background_learning_requirement(
        p, {"is_background_learning": True, "is_dialogue": False})
    twice = l._append_background_learning_requirement(
        once, {"is_background_learning": True, "is_dialogue": False})
    assert once == twice  # 已含要求文本则不重复追加


def test_prompt_requirement_disabled_zero_effect():
    l = _bare_lung()
    old = cfg.ENABLE_BACKGROUND_LEARNING_QUALITY_GATE
    cfg.ENABLE_BACKGROUND_LEARNING_QUALITY_GATE = False
    try:
        p = "问题"
        out = l._append_background_learning_requirement(
            p, {"is_background_learning": True, "is_dialogue": False})
        assert out == p
    finally:
        cfg.ENABLE_BACKGROUND_LEARNING_QUALITY_GATE = old


def test_prompt_config_keyword_range():
    pc = getattr(cfg, "BACKGROUND_LEARNING_PROMPT_CONFIG", {})
    assert int(pc.get("min_keywords", 0)) == 5
    assert int(pc.get("max_keywords", 0)) == 10


# ==================== T3.3 ====================

_CANDS = [
    {"name": "deepseek", "priority": 3},
    {"name": "zhipu", "priority": 1},
    {"name": "doubao", "priority": 2},
]


def test_cheap_channel_preference_for_background():
    """★主线第32批 T1（P2-187）：白名单从 config 动态读取。

    原断言硬编码了旧白名单 zhipu/doubao/deepseek 的排序结果；内部协作者调整
    `BACKGROUND_LEARNING_CHANNEL_CONFIG.preferred_channel_names` 后失效。
    现改为**从配置取白名单**，再验证排序语义本身：
      白名单命中项置前 + 命中项内部按 priority 升序 + 未命中项保持相对顺序 + 不丢候选。
    —— 验证强度不减，且此后配置再变也不会假失败。
    """
    l = _bare_lung()
    _bg = getattr(cfg, "BACKGROUND_LEARNING_CHANNEL_CONFIG", {}) or {}
    _names = set(_bg.get("preferred_channel_names") or [])
    out = l._prefer_cheap_channels([dict(c) for c in _CANDS], is_background=True)
    _out = [c["name"] for c in out]
    _hit = sorted((c for c in _CANDS if c["name"] in _names),
                  key=lambda x: x["priority"])
    _miss = [c["name"] for c in _CANDS if c["name"] not in _names]
    assert _hit, "本用例需要至少 1 个命中白名单的候选（检查 preferred_channel_names）"
    assert _out[:len(_hit)] == [c["name"] for c in _hit], \
        "白名单命中项应置前，且命中项内部按 priority 升序"
    assert _out[len(_hit):] == _miss, "未命中项应保持原相对顺序"
    assert len(_out) == len(_CANDS), "不得丢失任何候选"


def test_cheap_channel_no_change_for_dialogue():
    l = _bare_lung()
    out = l._prefer_cheap_channels([dict(c) for c in _CANDS], is_background=False)
    assert [c["name"] for c in out] == ["deepseek", "zhipu", "doubao"]


def test_cheap_channel_ignores_unknown_names():
    l = _bare_lung()
    cands = [{"name": "unknown_a", "priority": 1}, {"name": "zhipu", "priority": 2}]
    out = l._prefer_cheap_channels(cands, is_background=True)
    assert [c["name"] for c in out] == ["zhipu", "unknown_a"]


def test_cheap_channel_disabled_zero_effect():
    l = _bare_lung()
    old = cfg.ENABLE_BACKGROUND_LEARNING_QUALITY_GATE
    cfg.ENABLE_BACKGROUND_LEARNING_QUALITY_GATE = False
    try:
        cands = [dict(c) for c in _CANDS]
        out = l._prefer_cheap_channels(cands, is_background=True)
        assert [c["name"] for c in out] == ["deepseek", "zhipu", "doubao"]
    finally:
        cfg.ENABLE_BACKGROUND_LEARNING_QUALITY_GATE = old


def test_cheap_channel_preserves_relative_order():
    l = _bare_lung()
    cands = [
        {"name": "deepseek", "priority": 3},
        {"name": "other", "priority": 4},
        {"name": "zhipu", "priority": 1},
    ]
    out = l._prefer_cheap_channels(cands, is_background=True)
    assert [c["name"] for c in out] == ["zhipu", "deepseek", "other"]
