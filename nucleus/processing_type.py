# -*- coding: utf-8 -*-
"""processing_type.py —— 输出端「处理类型」标识（第345批 刀3）。

## 洞鉴 #3 的问题

现有输出只有 `来源=inner_world`，它回答的是「哪个模块发的话」，
**不回答「这句话是曈曈自己组织的、还是大模型生成的、还是混合的」**——
后者是正交维度。全仓此前无 `processing_type` / `handled_by` / `llm_ratio` 类字段。

## 四个取值

- `self_rule`：本地规则/模板直接拼装，整句不经过大模型（算术、问候、时间、QICA 规则推理…）
- `llm_generated`：整句由大模型生成（深度思考、沉思、认知推理…）
- `hybrid`：框架给骨架/上下文，大模型填充正文（思维纪律、任务分解、检索后组织…）
- `sandbox_result`：结果来自沙箱子进程执行（补丁验证、代码试跑）

## 「不猜」的边界

分类表按 **method 名的方法语义**初判，每条都来自实际核对该方法在
`PulseInnerWorld` 里的实现路径。三条纪律：

1. **显式优先**：生成路径若在 payload 里带了 `processing_type`，一律以它为准
   （后续要把某条路径精确化，只改那条路径，不必改表）。
2. **表里没有 ⇒ hybrid**：既不声称「自己组织的」，也不声称「大模型写的」，
   取保守中间值，并在 `processing_type_source="default_hybrid"` 里标出来，
   让"判定依据不足"成为**可见事实**而不是悄悄归一类。
3. **不自称 LLM 的本地路径不得判成 llm_generated**：宁可 hybrid。
"""

from typing import Any

PT_SELF_RULE = "self_rule"
PT_LLM_GENERATED = "llm_generated"
PT_HYBRID = "hybrid"
PT_SANDBOX_RESULT = "sandbox_result"

ALL_PROCESSING_TYPES = (PT_SELF_RULE, PT_LLM_GENERATED, PT_HYBRID, PT_SANDBOX_RESULT)

#: 本地规则/模板直接产出的 method（实现里无大模型调用）
_SELF_RULE_METHODS = frozenset({
    "simple_query_local",   # 精确短语模板（问候/身份/时间/情感/算术）
    "arithmetic",           # 安全求值
    "pure_emotion_fast_track",  # 情绪短语快通道
    "rule",                 # 规则通道
    "simple_logic",         # 符号推理
    "symbolic_reason",      # 符号推理
    "qica_rule_reason",     # QICA 规则推理
    "meta_honest",          # 诚实拒答（规则）
    "meta_not_search",      # 不该搜（规则判定）
    "inference_honest",     # 诚实兜底
    "none",                 # 无方法（空结果）
    "health_check",         # 健康自检
})

#: 整句由大模型生成的 method
_LLM_METHODS = frozenset({
    "deep_think",
    "deep_think_forced",
    "deep_think_fallback",
    "multi_branch_deep_think",
    "contemplation",
    "contemplation_semantic",
    "qica_cognitive",
})

#: 框架骨架 + 大模型填充（或检索结果再组织）的 method
_HYBRID_METHODS = frozenset({
    "thinking_discipline_quick",
    "thinking_discipline_standard",
    "thinking_discipline_deep",
    "thinking_discipline_deep_fallback",
    "decompose",
    "creative_solution",
    "multi_step_execute",
    "search_with_lung_fallback",
    "inference_fallback_to_lung",
    "file_analysis",
    "long_term_evolution",
    "meta_cognitive_report",
    "knowledge",
    "qica_knowledge",
    "explicit_search",
})

#: ★346刀3（补表）：实测"落在 default_hybrid"的 7 个 method —— 逐个读实现后归类。
#:
#: 为什么是追加而不是改表体：345 的初版表是"按方法语义初判"的基线，本批补的是
#: **实测覆盖率**这一层（346 批 AST 扫全仓 49 个 RESULT 发射点、45 个 method 字面量，
#: 判定表命中 38、落 default_hybrid 7）。两张表都保留，读者能看出哪条是补的。
#:
#: 依据（每个都在 346 批当场读过实现，不是按名字猜 —— 铁律153）：
#:   · branch_expand_request —— 从缓存/知识节点拼展开文本（`_enhance_answer` 是
#:     本地模板增强，`PulseExpression` 全文无 LLM 调用）
#:   · code_call_chain —— 调用图上查询结果拼文本
#:   · cognitive_compute —— `_cognitive_compute` → `AutonomousDeriver`（957 行，无 LLM）
#:   · composite_logic —— `_composite_logical_reason`，与已在表的 `simple_logic` 同族
#:   · conflict_exclusive —— `_derive_conflict_resolution`，本地符号判定
#:   · deep_contemplation —— `_contemplative_reason`：知识节点推演，实测无 LLM
#:   · deep_review_report —— 骨架本地拼（node_pool + InsightBoard），但正文可能直接
#:     取 `deep_self_review` 的洞察内容，那段是 LLM 生成的 ⇒ 不敢称自己组织的，
#:     也不敢称 LLM 写的，取保守中间值 hybrid
_SELF_RULE_METHODS = _SELF_RULE_METHODS | frozenset({
    "branch_expand_request",
    "code_call_chain",
    "cognitive_compute",
    "composite_logic",
    "conflict_exclusive",
    "deep_contemplation",
})

#: ★346刀3：hybrid 补 1 个（见上方 deep_review_report 的归类依据）
_HYBRID_METHODS = _HYBRID_METHODS | frozenset({"deep_review_report"})

#: method 名里带这些前缀 ⇒ 沙箱结果（不按关键词猜语义，只认这个明确前缀）
_SANDBOX_PREFIXES = ("sandbox", "sandbox_")

#: ★346刀3：本地推导前缀。
#: `pulse_inner_world_knowledge.py` 发射的 method 是 `f"deriver_{_derivation_type}"`
#: （演绎/归纳/类比），**动态拼的字符串永远进不了静态表** ⇒ 不加前缀规则就永远落
#: default_hybrid。实测依据：`nucleus/reasoning/AutonomousDeriver.py` 全文无 LLM 调用，
#: 只在 node_pool 上做演绎/归纳/类比。
_SELF_RULE_PREFIXES = ("deriver_",)


def processing_type_of(method: Any = "", explicit: Any = None) -> tuple[str, str]:
    """返回 `(processing_type, source)`。

    `source` 取值：`explicit`（生成路径显式声明）/ `method_table`（按表命中）/
    `sandbox_method`（沙箱前缀）/ `default_hybrid`（表里没有，保守归 hybrid）。
    """
    if explicit:
        _e = str(explicit).strip()
        if _e in ALL_PROCESSING_TYPES:
            return _e, "explicit"
        # 非法显式值不静默接受，也不改动行为：退回按 method 判
        return processing_type_of(method, None)
    _m = str(method or "").strip()
    if not _m:
        return PT_HYBRID, "default_hybrid"
    if _m.startswith(_SANDBOX_PREFIXES):
        return PT_SANDBOX_RESULT, "sandbox_method"
    if _m in _SELF_RULE_METHODS:
        return PT_SELF_RULE, "method_table"
    if _m in _LLM_METHODS:
        return PT_LLM_GENERATED, "method_table"
    if _m in _HYBRID_METHODS:
        return PT_HYBRID, "method_table"
    if _m.startswith(_SELF_RULE_PREFIXES):
        return PT_SELF_RULE, "self_rule_prefix"
    return PT_HYBRID, "default_hybrid"


def annotate_payload(payload: Any) -> dict | None:
    """给推理结果 payload 补 `processing_type` / `processing_type_source`（原地 + 返回）。

    已有合法 `processing_type` 的 payload 不覆盖（生成路径优先）。
    非 dict 原样返回 None（不改行为）。

    ★346刀3 修一个**自噬**缺陷（本批隔离验收实测抓到）：原实现把
    `payload["processing_type"]` 一律当"生成路径的显式声明"，而我们自己上一轮
    刚写进去的就是这个键 ⇒ **第二次标注时它被当成 explicit**，
    `processing_type_source` 从 `method_table` 翻成 `explicit`。
    后果：这个字段再也回答不了"这条分类的依据是表还是生成方"——
    全仓唯一一次 `_emit` 收口会让它100% 变成 explicit，等于字段废掉。

    修法：只有 `processing_type_source == "explicit"`，或**根本没有 source 键**
    （=生成方直接塞了值、没走本模块）时，才当显式声明。
    """
    if not isinstance(payload, dict):
        return None
    _prev_src = str(payload.get("processing_type_source", "") or "")
    _declared = payload.get("processing_type")
    if _prev_src not in ("", "explicit"):
        # 这个值是本模块上一轮写的（source 明确标了来源），不是生成方声明
        _declared = None
    _pt, _src = processing_type_of(payload.get("method", ""), _declared)
    payload["processing_type"] = _pt
    # 显式来源不覆盖（可能是更上游的精确声明）
    if _src == "explicit" and _prev_src == "explicit":
        return payload
    payload["processing_type_source"] = _src
    return payload
