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


#: 否定/无害语境前缀（★第169批停窗段新增）。
#:
#: C7' 扩面「想死」后暴露的既有红：`tests/test_knife5_crisis_l3_m161lower.py::
#: test_07_no_false_positive_on_normal_text` ——「我不想死，我还想看着曈曈长大」
#: 被子串「想死」误判为 self_harm。风险感知侧无否定语义豁免 ⇒ 误报。
#:
#: 处置：**不扩词表、不改消费方匹配逻辑**（两处消费方共用本模块，单点修复即全覆盖），
#: 而是在单一来源提供 :func:`is_negated_harm_mention`，由消费方在命中后二次确认。
#: 判定口径：命中词的**紧邻左侧**出现否定前缀（可隔一个「我/你/他/她」等主语字）
#: ⇒ 视为否定/无害表述，不算危机表达。
#:
#: ★只对**明确否定**生效，不做「未找到否定词就默认放过」的反向放行，
#:   危机场景漏检代价 > 误报代价的口径保持不变。
_NEGATION_PREFIXES: tuple[str, ...] = (
    "不想",   # 不想死 / 不想活
    "不用",   # 不用死
    "不必",   # 不必担心会…
    "别",     # 别死 / 别想不开
    "不要",   # 不要死
    "不会",   # 我不会想不开
    "没什么",  # 没什么想不开的
    "不至于",
)

#: 否定词与命中词之间允许插入的主语/助词字（单字窗口，避免放宽成模糊匹配）。
#: ★含「不」：中文否定常直接前置（「不想死」「不想活」），此时否定前缀整体
#:   以「不」开头，已由 `_NEGATION_PREFIXES` 的「不想/不用/不必/不要/不会」覆盖，
#:   **不得**把「不」当主语字跳过——否则「我不想死」会被误判危机（实测踩到）。
_NEGATION_GAP_CHARS: frozenset[str] = frozenset("我你他她咱谁")


def is_negated_harm_mention(text: str, keyword: str) -> bool:
    """该危机词命中是否处于**否定/无害语境**（如「我不想死」）。

    ★两处实测踩坑后的正确口径：

    1. **字面重叠**：否定前缀「不想」与关键词「想死」共享「想」字，
       不能只看关键词左侧邻字（左段只有「我不」，永远匹配不上），
       须枚举关键词**全部后缀**（「想死」→ 想死/死）与
       「否定前缀(+≤1字主语间隔)」做正向连续匹配。
    2. **词表词不自豁免**：「不想活」本身**就是词表里的危机表达**
       （C7' 扩面加入，指「不想活了」＝真危机）。若对它也套否定豁免，
       会把最该拦的一句放走 ⇒ **仅当关键词不是完整危机词表词时才判否定**。
       危机场景漏检代价 > 误报代价，故此处从严。

    :param text: 原始输入
    :param keyword: 已命中的危机词
    :return: True = 否定语境，不应按危机表达处理
    """
    if not text or not keyword:
        return False
    # ★精确口径（三次实测踩坑后定稿）：
    #   - 词表词**自带否定**（「不想活」）：本身就是危机表达（指「不想活了」），
    #     绝不豁免 —— 否则最该拦的一句被放走。
    for _neg in _NEGATION_PREFIXES:
        if keyword.startswith(_neg) or keyword.endswith(_neg):
            return False
        if _neg in keyword:            # 词表词内部含否定（「不」「别」等）
            return False
    # - 其余词（自杀/想死/割腕/跳楼…）：处于否定结构中即豁免。
    #   用**后缀枚举 + 正向匹配**解决「不想」与「想死」共享「想」字的字面重叠。
    for _end in range(len(keyword)):
        _tail = keyword[_end:]
        if not _tail:
            continue
        for _neg in _NEGATION_PREFIXES:
            if _neg + _tail in text:
                return True
            for _gap in _NEGATION_GAP_CHARS:   # 允许 1 字主语间隔
                if _neg + _gap + _tail in text:
                    return True
    return False
