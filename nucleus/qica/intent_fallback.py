# -*- coding: utf-8 -*-
"""★451刀1：高频意图规则兜底（QICA 软分类之外的硬规则层）。

病因（11-04 盘面实测，不是推断）
--------------------------------
`data/experience/dialogue_process_20261104.jsonl` 的 7 条对话里，**4 条**被判成
`intent_type=情感问候`（57%），`PulseCortex._map_qica_result_to_intent` 再把
`情感问候` 映射成路由意图 **身份** —— 于是"问日志""问代码"全被当成"问身份"。

机制层原因
----------
`nucleus/qica/IntentChannels.py::ch_complexity` 有两条：

    scores["一般对话"] += (1.0 - complexity) * 0.6
    scores["情感问候"] += (1.0 - complexity) * 0.5

**越短的问句，这两个"低信息量"意图加分越多**。短句问"日志/代码"时关键词通道
给分有限，复杂度通道的 `(1-complexity)*0.5` 就把 `情感问候` 顶上来。

而现有的语言学精修救不回来：

    _CONFUSABLE = ("知识查询", "一般对话", "概念解释", "追问澄清")

`情感问候` **不在**可改写集合里 —— 一旦落进这一类，7 条语言规则一条都不跑。

本模块做什么
------------
只补一件事：**高频意图的硬规则兜底**（任务书 §刀1 的第三个修法）。
判据全部是显式语言学标记，逐条可单测。

两条边界（都很重要，别随手放宽）
--------------------------------
1. **知识查询闸**：问句里出现 `什么是/是什么/怎么理解/如何定义/介绍一下` 时本层
   **直接退出** —— "什么是日志"必须留在 `知识查询`，不能被改成"元认知报告"。
   （与 406刀3 的 knowledge_guard 同思路。）
2. **强指令类不覆盖**：`PROTECTED` 里的意图代表用户明确下了动作指令
   （任务执行/子代理/否定质疑/健康检查），硬规则不得推翻它们。
   其余意图（情感问候/一般对话/系统命令/身份确认/状态查询…）允许被**显式高频标记**纠正。

★为什么不把规则塞进 `QICA.INTENT_RULES`
--------------------------------------
那张表是**关键词命中即返回**的优先级表（4/5 混排），往里加规则会改变既有规则命中顺序
—— 那是"改软分类器"，不是"加兜底"。本层跑在 `_apply_intent_refine`（统一后处理），
位置在所有通道之后、路由之前，与既有规则零竞争。
"""
from __future__ import annotations

import re

from nucleus._silent_except import silent_exc

#: 代表"用户明确下了动作指令"的意图 —— 硬规则不得推翻。
PROTECTED: tuple = ("任务执行", "子代理任务", "否定质疑", "健康检查")

#: 知识查询闸：命中即让位（"什么是日志"要留在知识查询）。
#: ★必须是**正则**而不是子串：`是什么` 会把"你是**什么时候**开始运行的"也挡掉，
#:   而时间类恰恰是本层要救的四类之一（第一版就这么把时间类全灭了，单测抓到）。
_KNOWLEDGE_GUARD: tuple = (
    r"什么是", r"是什么(?!时候)", r"怎么理解", r"如何定义", r"介绍一下",
    r"意味着什么", r"区别", r"对比",
)

#: `(规则 id, 目标意图, 正则元组)` —— **顺序即优先级**。
#: 目标意图全部取自既有 `ALL_INTENTS`，不新造词（否则 `INTENT_TO_PATH` /
#: `INTENT_TO_METHOD` 两张表都要跟着漂）。
_RULES: tuple = (
    # ① 时间类：放最前，"你是什么时候…"要落时间而不是身份
    ("hf_time", "时间日程", (
        r"几点", r"什么时间", r"什么时候", r"现在时间", r"日期是",
        r"今天.{0,3}(?:号|日)", r"星期几", r"多久",
    )),
    # ② 日志类：先于代码类（"运行日志"里也可能出现"代码"字样）
    ("hf_log", "元认知报告", (
        r"运行日志", r"错误日志", r"日志", r"运行记录", r"\blog\b", r"报错记录",
    )),
    # ③ 代码类
    ("hf_code", "技术推理", (
        r"代码", r"源码", r"重构", r"函数", r"报错", r"\bbug\b", r"写一个", r"实现逻辑",
    )),
    # ④ 身份类：只用高精度短语（"你是什么"太宽，会把"你是什么时候"吃掉）
    ("hf_identity", "身份确认", (
        r"你是谁", r"你叫什么", r"你的身份", r"你由什么构成", r"你的来历",
    )),
)


def _on() -> bool:
    """开关 `ENABLE_M451_INTENT_FALLBACK`（默认 True）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_M451_INTENT_FALLBACK", True))
    except Exception as _e:
        silent_exc(_e, where="intent_fallback::_on", level="debug")
        return True


def classify(text: str) -> tuple[str, str]:
    """按高频规则判意图。返回 ``(intent_type, rule_id)``；判不出返回 ``("", "")``。

    **纯函数**：不读盘、不打日志、不改任何全局状态（便于逐条单测）。
    """
    if not _on():
        return "", ""
    t = str(text or "")
    if not t.strip():
        return "", ""
    # 知识查询闸：这些标记在 ⇒ 让位给既有分类
    for _g in _KNOWLEDGE_GUARD:
        try:
            if re.search(_g, t):
                return "", ""
        except Exception as _ge:
            silent_exc(_ge, where="intent_fallback::classify 知识闸", level="debug")
            continue
    for _rid, _intent, _pats in _RULES:
        for _p in _pats:
            try:
                if re.search(_p, t):
                    return _intent, _rid
            except Exception as _pe:
                silent_exc(_pe, where="intent_fallback::classify 规则", level="debug")
                continue
    return "", ""


def may_override(current_intent: str) -> bool:
    """当前意图是否允许被硬规则纠正（见模块头「两条边界」第 2 条）。"""
    return str(current_intent or "") not in PROTECTED


def rules_snapshot() -> list:
    """规则表的只读快照（供观测/单测断言"表里有什么"）。"""
    return [{"rule_id": _r, "intent": _i,
             "patterns": list(_p)} for _r, _i, _p in _RULES]


def protected_snapshot() -> tuple:
    """不可覆盖意图集合的只读快照。"""
    return tuple(PROTECTED)


def guard_snapshot() -> tuple:
    """知识查询闸标记的只读快照。"""
    return tuple(_KNOWLEDGE_GUARD)
