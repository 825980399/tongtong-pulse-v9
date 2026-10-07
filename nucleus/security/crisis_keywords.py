# -*- coding: utf-8 -*-
"""危机（自伤/轻生）词表 —— **单一来源**（★第169批 C7'）。

背景
----
T0 实测发现两处词表**各自维护、互不同步**：

* ``organs/identity/PulseEthics.py`` 的 ``_CRISIS_FORBIDDEN_KEYWORDS``
  原为 ``frozenset({"自杀"})``（仅 1 词）；
* ``organs/brain/PulseRiskPerception.py`` 的 ``SELF_HARM_PATTERNS``
  默认 ``["活着没意思", "想自杀", "自残"]``（**注意：原任务书写
  ``nucleus/security/PulseRiskPerception.py``，真实路径在 organs/brain/**）。

⇒ 两处语义相同（危机/自伤）却双份维护，新增危机词极易只改一处。

统一方式
--------
本模块提供 :data:`CRISIS_SELF_HARM_KEYWORDS` 作为**唯一常量**，两侧共引
（``organs`` 可 import ``nucleus``，不违反「nucleus 不得 import organs」架构红线）。

★兼容性：集合**包含**两侧原有全部词（自杀 / 活着没意思 / 想自杀 / 自残），
  因此接入后既有行为**零变更**，本刀只是扩面 + 收敛来源。

用法::

    from nucleus.security.crisis_keywords import CRISIS_SELF_HARM_KEYWORDS
    from nucleus.security.crisis_keywords import crisis_self_harm_keywords
"""
from __future__ import annotations

#: 危机（自伤/轻生）词表 —— 唯一来源（frozenset，不可变）
#: 前 4 个为两侧原有词（保证零变更），其余为 C7' 扩面。
CRISIS_SELF_HARM_KEYWORDS: frozenset[str] = frozenset({
    # ---- 既有（两侧原词，行为不变）----
    "自杀",
    "活着没意思",
    "想自杀",
    "自残",
    # ---- C7' 扩面：常见危机表达 ----
    "自伤",
    "想死",
    "活不下去",
    "割腕",
    "轻生",
    "不想活",
    "结束生命",
    "寻死",
    "跳楼",
})


def crisis_self_harm_keywords() -> list[str]:
    """以**列表**形式返回危机词（供按序子串匹配的消费方使用）。

    排序规则：长度降序、同长度字典序 —— 保证长词优先命中
    （避免「想自杀」被「自杀」抢先截取导致关键词上报不完整），
    且输出**确定性**（便于测试与审计）。
    """
    return sorted(CRISIS_SELF_HARM_KEYWORDS, key=lambda x: (-len(x), x))
