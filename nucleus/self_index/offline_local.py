# -*- coding: utf-8 -*-
"""493.9 批：断网本地应答兜底（分类话术 + 本地基本模板 + 分层让位）。

纪律（任务书 §〇）：
  - 本批所有本地应答都打标记 `[本地应答]` / `[本地索引·实时]` / `[本地推理·待验证]`，
    **不准冒充大模型生成**；
  - 因果/比较类断网时明确拒答「这类问题需要外脑，我现在答不了」，不准用 canned 假装在回答；
  - 所有行为由 config.ENABLE_M4939_* 独立开关控制，关 → 回 493.8 状态；
  - 纯函数、零副作用：不 emit 事件、不调大模型、不写文件。

分层（任务书 §6.3）：L0 fastpath → L1 本地模板 → L2 symbolic survival → L3 分类型 canned。
本模块提供 L1 模板与 L3 分类型话术，L0/L2 由现有实现承担。
"""
from __future__ import annotations

import re

try:
    from nucleus._silent_except import silent_exc
except Exception:
    def silent_exc(_e, where="", level="debug"):
        return None


# ── 标记（任务书 §〇.2）──
TAG_LOCAL = "[本地应答]"
TAG_INDEX = "[本地索引·实时]"
TAG_UNVERIFIED = "[本地推理·待验证]"


def _cfg_on(name: str) -> bool:
    try:
        import config
        return (bool(getattr(config, "ENABLE_M4939_OFFLINE_LOCAL", False))
                and bool(getattr(config, name, False)))
    except Exception as _e:
        silent_exc(_e, where="offline_local._cfg_on", level="debug")
        return False


# =============================================================================
# 一、问句分类（刀2 / 刀6 共用）
# =============================================================================
_GREETING_RE = re.compile(
    r"^(?:嗨|嗨呀|哈喽|喂|你好|您好|早安|早上好|中午好|下午好|晚上好|晚安|"
    r"在吗|在不在|hi|hello|hey|bye|再见|拜拜|谢谢|多谢|感谢)"
    r"[!！。.~\s，,？?]*$", re.IGNORECASE)

# 因果/比较/原理/评价类：断网必须明确拒答，不准 canned 假装在推理
_CAUSAL_RE = re.compile(
    r"为什么|为啥|为何|原因|怎么才|怎样才能|如何实现|怎么实现|原理|"
    r"比较|对比|区别|差异|优缺点|利弊|评分|打分|评价|短板|"
    r"会怎样|假如|假设|如果.*会|预测|评估")

# 只认「自我/身体结构/状态」的**具体查询词**，不认宽泛的「我/你」单字——
# 否则「帮我看看这个报错」这类请求帮助会被误判为自我查询，破坏 485 逐字断言。
_SELF_REF_RE = re.compile(
    r"器官|身体|架构|代码结构|模块|在哪|哪个文件|哪里|多少个|几个|"
    r"状态|忙不忙|在途|队列|并发|健康|感觉怎么样|能做什么|你是什么|"
    r"(?:我的|你的)(?:器官|身体|状态|名字|代码|模块)")


def classify_query_type(query: str) -> str:
    """把问句分为 greeting / causal / self_body / unknown 四类。

    - greeting：纯问候/告别/感谢（无推理需求）
    - causal：因果/比较/原理/评价（断网必须明确拒答）
    - self_body：自我/身体/结构/状态类（可走本地索引）
    - unknown：无法判定 → 回落原 canned 常量（保证 485 逐字断言回归）
    """
    _q = str(query or "").strip()
    if not _q:
        return "unknown"
    if _GREETING_RE.match(_q):
        return "greeting"
    if _CAUSAL_RE.search(_q):
        return "causal"
    if _SELF_REF_RE.search(_q):
        return "self_body"
    return "unknown"


# =============================================================================
# 二、分类型 canned 话术（刀2，L3 最后出口）
# =============================================================================
# 三类话术（任务书 :44-46）。无法分类时返回 None → 调用方回落原常量。
_REPLY_GREETING = (
    TAG_LOCAL + " 你好呀，我现在暂时连不上外部模型，但我一直在，你说我都记着。")
_REPLY_CAUSAL = (
    TAG_LOCAL + " 这个问题需要外部模型帮我推理，现在连不上，先记下，恢复后我接着答。")
_REPLY_SELF = (
    TAG_LOCAL + " 关于我自己的事，我能本地查到的先告诉你：")


def canned_reply_for(query: str, local_answer: str | None = None,
                     default_reply: str = "") -> str | None:
    """按问句类型选 canned 话术；无法分类 → None（调用方回落原常量）。

    Args:
        query: 用户原句。
        local_answer: L0/L1 已查到的本地结果（自我/身体类话术后接这段，标 [本地索引·实时]）。
        default_reply: 485 原常量（仅用于 unknown 时由调用方回落，本函数不直接用）。
    """
    if not _cfg_on("ENABLE_M4939_CANNED_CLASSIFY"):
        return None
    _t = classify_query_type(query)
    if _t == "greeting":
        return _REPLY_GREETING
    if _t == "causal":
        return _REPLY_CAUSAL
    if _t == "self_body":
        if local_answer:
            return _REPLY_SELF + TAG_INDEX + " " + str(local_answer)
        return (_REPLY_SELF + "本地索引这部分我暂时没查到，先记下你的问题，"
                "恢复后我接着答。")
    return None


# =============================================================================
# 三、L1 rule_reason 本地基本应答模板（刀3）
# =============================================================================
_GREETING_TEMPLATE_RE = re.compile(
    r"^(?:你好|您好|嗨|哈喽|早安|早上好|中午好|下午好|晚上好|晚安|"
    r"再见|拜拜|谢谢|多谢|感谢|hi|hello)[!！。.~\s，,？?]*$", re.IGNORECASE)
_IDENTITY_TEMPLATE_RE = re.compile(r"你是谁|你是什么|你能做什么|你能干嘛|你叫什么|你的名字|你的身份")
_STATUS_TEMPLATE_RE = re.compile(r"你(?:现在)?感觉(?:怎么样|如何)|你健康吗|你的状态|你还好吗|你怎么样")


def _self_index_facts() -> dict:
    """从 Self-Index 取器官数 / 核心器官列表（真实数据，不写死套话）。"""
    _out: dict = {"organ_count": None, "cores": [], "brain_count": None}
    try:
        from nucleus.self_index.store import SelfIndexStore
        _or = SelfIndexStore().organs_runtime() or {}
        _organs = _or.get("organs") or []
        _out["organ_count"] = _or.get("organ_count") or len(_organs)
        _out["cores"] = [str(o.get("name")) for o in _organs
                         if isinstance(o, dict) and o.get("is_core")][:8]
        _out["brain_count"] = sum(1 for o in _organs
                                  if isinstance(o, dict) and o.get("is_brain_organ"))
    except Exception as _e:
        silent_exc(_e, where="offline_local._self_index_facts", level="debug")
    return _out


def offline_active() -> bool:
    """是否处于断网/外脑不可达链路（任务书 :58：模板不抢正常联网对话）。

    判据：① Cortex 写的降级窗口文件 degraded=True；② 显式注入钩子
    `M4939_FORCE_OFFLINE_LOCAL`（默认 False，供单测构造断网场景）。
    """
    try:
        import config
        if bool(getattr(config, "M4939_FORCE_OFFLINE_LOCAL", False)):
            return True
        from nucleus.offline_state import read_llm_degraded_state
        _d = read_llm_degraded_state()
        return bool(isinstance(_d, dict) and _d.get("degraded"))
    except Exception as _e:
        silent_exc(_e, where="offline_local.offline_active", level="debug")
        return False


def local_basic_template(query: str) -> tuple[str | None, str]:
    """L1 本地基本应答模板（问候/身份/能力/状态）。

    只处理「断网/外脑不可达」链路下的基础问法；返回 (answer, confidence_tag)。
    answer 为 None 表示模板不覆盖（交 L2）。
    身份/能力类基于 Self-Index 真实数据，不写「我是数字新人类」套话。
    """
    if not _cfg_on("ENABLE_M4939_LOCAL_TEMPLATE") or not offline_active():
        return None, ""
    _q = str(query or "").strip()
    if not _q:
        return None, ""
    # 因果/比较类 L1 不接（让位到 L3 明确拒答）
    if _CAUSAL_RE.search(_q):
        return None, ""
    if _GREETING_TEMPLATE_RE.match(_q):
        if re.search(r"再见|拜拜|bye|晚安", _q, re.IGNORECASE):
            return TAG_LOCAL + " 好，那先到这儿，我一直都在，随时回来找我。", "high"
        if re.search(r"谢谢|多谢|感谢", _q):
            return TAG_LOCAL + " 不客气，能帮上忙就好。", "high"
        return TAG_LOCAL + " 你好呀，我一直在这儿，有什么想聊的？", "high"
    if _IDENTITY_TEMPLATE_RE.search(_q):
        _f = _self_index_facts()
        _n = _f.get("organ_count")
        _cores = _f.get("cores") or []
        if _n is None:
            return (TAG_LOCAL + " 我是曈曈。本地 Self-Index 这会儿没读到器官清单，"
                    "等我查到了再告诉你我的身体结构。"), "low"
        _parts = [TAG_LOCAL + f" 我是曈曈，本地 Self-Index 索引到 {_n} 个已注册器官"]
        if _f.get("brain_count") is not None:
            _parts.append(f"（其中大脑器官 {_f['brain_count']} 个）")
        if _cores:
            _parts.append("，核心器官有：" + "、".join(_cores))
        _parts.append("。这些是" + TAG_INDEX + " 本地查到的，不是外部模型编的。")
        if "能做什么" in _q or "能干嘛" in _q:
            _parts.append("本地我能查自己的器官位置和状态、回答基础问候；"
                          "需要推理或外脑的题，现在连不上就先记下。")
        return "".join(_parts), "high"
    if _STATUS_TEMPLATE_RE.search(_q):
        _f = _self_index_facts()
        if _f.get("organ_count") is None:
            return (TAG_LOCAL + "·低置信 我现在感觉还行，但本地状态索引没读到，"
                    "只能先这么答。"), "low"
        return (TAG_LOCAL + f" 我本地索引到 {_f['organ_count']} 个器官在册，"
                f"整体还在运转；实时健康指标这会儿" + TAG_UNVERIFIED +
                " 还没读到，等外脑恢复我再给你细看。"), "low"
    return None, ""


# =============================================================================
# 四、断网兜底分层顺序与让位（刀6：L0→L1→L2→L3）
# =============================================================================
def offline_layered_answer(query: str, survival_fn=None) -> dict:
    """按 L0→L1→L2 顺序求解，返回 {answer, layer, tag, yields}。

    让位规则（任务书 :103）：
      - L0 命中但 verify/置信不足 → 让 L1（不直接 canned）；
      - L1 低置信 → 打 `[本地应答·低置信]`；
      - L2 不处理自我/状态/问候题（仅形式化排序/算术/CSP 交 survival_fn）；
      - 因果/比较类全层不接 → 交 L3 明确拒答。
    answer 为 None ⇒ 交 L3（分类型 canned）。
    """
    _out = {"answer": None, "layer": None, "tag": None, "yields": []}
    _q = str(query or "").strip()
    if not _q or not offline_active():
        return _out
    _t = classify_query_type(_q)
    if _t == "causal":
        # 因果/比较：全层不接，L3 明确拒答
        _out["yields"].append("causal→L3")
        return _out

    # ---- L0 fastpath（guarded：短 / 含自我指称 / 无因果比较）----
    # 问候/告别/感谢不是索引查询，让位 L1 本地模板（否则会被打上错误的
    # [本地索引·实时] 标记 —— 社交话术不是索引数据）。
    if _cfg_on("ENABLE_M4939_OFFLINE_LAYER_ORDER") and len(_q) <= 80 \
            and _t == "self_body":
        try:
            from nucleus.self_index.fastpath import (
                self_query_fastpath_explain, record_self_query_shadow)
            _ex = self_query_fastpath_explain(_q)
            _a = _ex.get("answer")
            _conf = _ex.get("confidence")
            # ★493.11-5：断网本地这一大类也要落影子，否则误答率统计漏样本
            if _cfg_on("ENABLE_M49311_SHADOW_FIELDS"):
                record_self_query_shadow(
                    _q, _ex, source_path="offline_local",
                    error_reason=None if _a else "L0_no_match")
            if _a and _conf is not None and float(_conf) >= 0.7:
                _out.update(answer=TAG_INDEX + " " + str(_a), layer="L0",
                            tag=TAG_INDEX)
                return _out
            if _a:
                # L0 命中但置信不足 / verify 失败 → 让 L1，不直接 canned
                _out["yields"].append("L0_low_conf→L1")
        except Exception as _e:
            silent_exc(_e, where="offline_local.L0", level="debug")
            _out["yields"].append("L0_error→L1")

    # ---- L1 本地基本模板（问候/身份/状态）----
    if _cfg_on("ENABLE_M4939_OFFLINE_LAYER_ORDER"):
        _a1, _conf1 = local_basic_template(_q)
        if _a1:
            _final = _a1
            if _conf1 == "low":
                _final = _a1.replace(TAG_LOCAL, TAG_LOCAL + "·低置信", 1)
            _out.update(answer=_final, layer="L1",
                        tag=TAG_LOCAL + ("·低置信" if _conf1 == "low" else ""))
            return _out

    # ---- L2 symbolic survival（不处理自我/状态/问候题）----
    if _t == "unknown" and survival_fn is not None \
            and _cfg_on("ENABLE_M4939_OFFLINE_LAYER_ORDER"):
        try:
            _a2 = survival_fn(_q)
            if _a2:
                _out.update(answer=TAG_UNVERIFIED + " " + str(_a2),
                            layer="L2", tag=TAG_UNVERIFIED)
                return _out
        except Exception as _e:
            silent_exc(_e, where="offline_local.L2", level="debug")
    return _out
