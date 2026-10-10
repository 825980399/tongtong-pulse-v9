# -*- coding: utf-8 -*-
"""第160批 下上 刀7（T-内在世界输出净化-1）验收测试。

覆盖任务书验收判据：
  7.1 qica_knowledge 出口复用 _sanitize_internal_content（return 已清洗 _final，与 emit 一致）
  7.2 清洗覆盖：占位符字面量/[BLUEPRINT_CONSTITUTION]、内部字段前缀（种子N(/[深度搜索·/
      [归纳升华]/深层原理：/直接知识：）、组装痕迹（由N条相关知识归纳/由N条认知融合而成/
      （由8条相关知识归纳））、碎片（这是我第一次醒来）
  7.3 DIALOG_SANITIZE_LEVEL 作用域扩展至该出口，structured 回退不剥离新增模式
  7.4 上述每个违规模式正反例（离线单测）
"""
from __future__ import annotations

import os
import sys

import pytest

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402


def _bare_iw():
    """构造未走 __init__ 的空壳，仅用于测纯文本处理方法。"""
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw._active_search_correlation = {}
    iw._log = lambda *a, **k: None
    return iw


# ---- 7.2 正向：每个违规模式被剥离 ----
@pytest.mark.parametrize("text,needle", [
    ("种子1(综合): 内部归纳内容", "种子"),
    ("[深度搜索·知识学习] 内部内容", "深度搜索"),
    ("[归纳升华] 内部归纳内容", "归纳升华"),
    ("[BLUEPRINT_CONSTITUTION] 内部内容", "BLUEPRINT_CONSTITUTION"),
    ("深层原理：内部原理内容", "深层原理"),
    ("直接知识：内部知识内容", "直接知识"),
    ("由8条相关知识归纳而成的内部内容", "相关知识归纳"),
    ("由92条认知融合而成的内容", "认知融合而成"),
    ("正文（由8条相关知识归纳）结尾", "由8条相关知识归纳"),
    ("这是我第一次醒来，然后说了内部内容", "第一次醒来"),
])
def test_knife7_leak_patterns_stripped(text, needle):
    out = _bare_iw()._sanitize_internal_content(text, "q")
    assert needle not in out, "残留泄漏模式 {!r} -> {!r}".format(needle, out)


# ---- 7.2 反向：合法正文不被误伤 ----
@pytest.mark.parametrize("text", [
    "今天天气很好，我想出去走走。",
    "种子选手在比赛中表现出色。",  # 无 数字+( 的"种子"是合法词
    "深层的内容值得反复思考。",      # 非"深层原理："前缀
    "直接告诉我答案吧。",            # 非"直接知识："前缀
])
def test_knife7_legit_text_preserved(text):
    out = _bare_iw()._sanitize_internal_content(text, "q")
    assert out.strip(), "合法正文被清空: {!r}".format(text)
    # 清洗器会剥离尾标点（既有行为），比对去尾标点后的核心文本
    _core = text.rstrip("。！？，、")
    assert out == _core, "合法正文被改动: {!r} -> {!r}".format(text, out)


def _iw_routing():
    """构造带最小 mock 的空壳，验证 qica_knowledge 出口走清洗器。"""
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw._active_search_correlation = {}
    iw._log = lambda *a, **k: None
    iw._inference_count = 0
    iw.node_pool = None
    iw._cache_inference = lambda *a, **k: None
    iw._trace_inference = lambda *a, **k: None
    iw._emit = lambda *a, **k: None
    iw._verify_knowledge_relevance = lambda *a, **k: 0.75  # 高相关，不降级
    iw._contemplative_reason = lambda *a, **k: None
    iw._generate_branch_with_model = lambda *a, **k: None
    # _enhance_answer 复用真实清洗器（验证路由确实经过清洗）
    iw._enhance_answer = lambda answer, question, **k: iw._sanitize_internal_content(answer, question)
    # 检索回退路径返回含泄漏模式的原始串
    iw._knowledge_retrieve = lambda *a, **k: (
        "种子1(综合): 内部归纳内容。[深度搜索·知识学习] 这是我第一次醒来，"
        "由8条相关知识归纳而成深层原理：内部原理")
    return iw


def _make_ctx():
    class _Ctx:
        pass
    ctx = _Ctx()
    ctx.question = "请问我的身份使命是什么"
    ctx.user_name = "test"
    ctx.payload = {}
    ctx.empathetic_note = ""
    ctx._reasoning_start_time = 0.0
    ctx._question_complexity = 0.5
    ctx._memory_context = {}
    ctx.correlation_id = "cid"
    return ctx


def test_knife7_qica_exit_goes_through_sanitizer():
    """7.1 出口 return 已清洗 _final（并非未清洗 _knowledge_result）。"""
    iw = _iw_routing()
    result = iw._ir_qica_knowledge_retrieve(_make_ctx(), [])
    assert result is not None
    assert result["status"] == "qica_knowledge"
    ans = result["answer"]
    # 原 _knowledge_result 含这些泄漏标记；清洗后必须全部消失
    for needle in ("种子", "深度搜索", "第一次醒来", "相关知识归纳", "深层原理"):
        assert needle not in ans, "出口仍泄漏 {!r} -> {!r}".format(needle, ans)


def test_knife7_qica_exit_rollback_in_structured():
    """7.3 structured 模式下新增模式不剥离（回退旧行为），但 [internal] 仍剥。"""
    import config
    _orig = getattr(config, "DIALOG_SANITIZE_LEVEL", "literal")
    config.DIALOG_SANITIZE_LEVEL = "structured"
    try:
        iw = _iw_routing()
        # 新增模式种子N( 在 structured 下应保留
        out = iw._sanitize_internal_content(
            "种子1(综合): 内部内容[internal]碎片", "q")
        assert "种子" in out, "structured 回退不应剥离种子N(, 但被剥: {!r}".format(out)
        assert "[internal]" not in out, "structured 仍须剥离 [internal]: {!r}".format(out)
    finally:
        config.DIALOG_SANITIZE_LEVEL = _orig
