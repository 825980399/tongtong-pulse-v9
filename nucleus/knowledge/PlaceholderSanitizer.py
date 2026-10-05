"""★第159批 上B 刀B③（T-对话模板-1）：出口占位符净化（L-C 输出兜底）。

背景：
    对话出口曾把未替换的占位符字面量（如「[器官别名] [器官别名] 人格内核...」）
    直接输出给用户，形成「固定模板答非所问」。写入层 P2-26 已修、刀B② 封堵
    融合扩散源之后，存量与漏网内容仍可能在出口出现，故加本兜底。

设计（★红线）：
    * **只对「含方括号占位符字面量」生效**；
    * 严禁使用 ``len(text) > 200 and text.startswith("我了解到")`` 之类组合判定
      —— PollutionTagger 用该组合判污染，会误伤真回答（任务书风险③）；
    * 剥离后正文过短 → 降级为「未命中相关知识」引导语 + DEBUG 留痕，
      **绝不伪造内容**。
"""
from __future__ import annotations

import re

from nucleus._silent_except import silent_exc

# ★已知占位符字面量（与 tools/cleanup_alias_placeholder_nodes.py 同源）。
#   不泛化到所有方括号：[综合] / [数据流·X] / [代码关联·X] / [主动学习·X] 等
#   均为正常前缀标记，泛化会误伤大量真内容（★2026-10-03 两轮 dry-run 校准：
#   泛化判据曾虚高命中 4402 → 2104，收敛到字面量后 359 且裸父路径精确 306）。
PLACEHOLDER_LITERALS = ("[器官别名]",)
# 剥离后正文过短时的降级引导语（★不伪造内容）
PLACEHOLDER_FALLBACK_TEXT = "未命中相关知识"
# 剥离后正文保留的最小长度（低于此值判定为「无实质内容」→ 降级）
PLACEHOLDER_MIN_KEEP_LENGTH = 12

_WS_RE = re.compile(r"\s{2,}")
_TRIM_CHARS = " ，,。、；;"


# ★第161批下 刀4（T-占位符空槽泄漏-1）：空槽 / 截断占位形态。
#   活体验证题15 回复含空占位符「」+ 截断占位符 p... —— 既有字面量判据抓不到。
#   ★精确形态，严禁泛化（泛化会误伤大量真内容，见本文件 :24-27 的校准教训）。

#: 空槽：相邻的「」内部无实质内容（模板占位未被填充）
_EMPTY_SLOT_RE = re.compile(r"\u300c\s*\u300d")

#: 截断占位：`<字母>` 后紧跟省略号（如 p... / <SELF_NAME>...）
_TRUNCATED_RE = re.compile(r"[<\u300c]?[A-Za-z_]{1,32}[>\u300d]?\.\.\.")


def contains_empty_or_truncated_placeholder(value: str) -> bool:
    """★刀4：是否含**空槽**或**截断占位**（既有字面量判据覆盖不到的两种形态）。

    命中任一即 True：
      1. 空槽「」—— 模板占位未被填充（`「」` 内无实质内容）
      2. 截断占位 —— `p...` / `<NAME>...` 形态

    ★不误伤正常中文引号：正常文本中「」多为引用（「进化」「理解」），
      只有**相邻空「」**才判定为模板空槽。
    """
    if not value or not isinstance(value, str):
        return False
    if _EMPTY_SLOT_RE.search(value):
        return True
    return bool(_TRUNCATED_RE.search(value))


def contains_placeholder(text: str) -> bool:
    """是否含已知占位符字面量（供调用方复用，零副作用）。

    ★第161批下 刀4：本函数为**总入口**，在字面量之外也涵盖空槽与截断占位
    ⇒ 既有调用方（检索/排序层）零改动即获得刀4 的堵源能力。
    """
    if not text or not isinstance(text, str):
        return False
    if any(_lit in text for _lit in PLACEHOLDER_LITERALS):
        return True
    return contains_empty_or_truncated_placeholder(text)


def contains_placeholder_literal(value: str) -> bool:
    """★第160批 上A 刀1（票3 B 案·运行时内容判据·唯一口径）。

    检索层 / 排序层统一调用本函数，**禁止** support.py 与 VectorStore.py 各自
    定义第二套字面量（与 T-唯一口径件-1 同治）。

    命中以下任一即视同 ``placeholder_alias`` 处理（过滤 / 0.3 降权）：
      1. ``"[器官别名]"`` 字面量 —— 核心污染本体（裸父路径 306 节点）；
      2. value 以占位符前缀开头：
         ``("关联知识：。", "[归纳升华]", "[数据流·")`` ——
         融合/归纳产物在检索侧同样视为污染。

    ★设计要点（与 159 上B 区别）：**不依赖落盘 flag**——
    159 上B 的 ``placeholder_alias`` 标记被框架启动从 Parquet 覆盖丢失后，
    纯靠 flag 的过滤/降权双双空转；本判据按 value 内容现算，免疫该覆盖。
    """
    if not value or not isinstance(value, str):
        return False
    if "[器官别名]" in value:
        return True
    return value.startswith(("关联知识：。", "[归纳升华]", "[数据流·"))


def sanitize_placeholder_text(text: str, min_length: int | None = None,
                              log=None) -> str:
    """★出口占位符净化：剥离占位符字面量；剥离后过短则降级引导语。

    参数：
        text: 待净化文本。
        min_length: 剥离后正文最小保留长度，默认 PLACEHOLDER_MIN_KEEP_LENGTH。
        log: 可选日志回调（接受一个字符串），用于 DEBUG 留痕。

    返回：
        净化后文本；**未命中占位符字面量时原样返回**（零副作用）。
    """
    if not text or not isinstance(text, str):
        return text or ""
    if not contains_placeholder(text):
        return text  # ★未命中 → 原样返回（红线：不按长度/前缀判定）

    _min = PLACEHOLDER_MIN_KEEP_LENGTH if min_length is None else int(min_length)
    _cleaned = text
    for _lit in PLACEHOLDER_LITERALS:
        _cleaned = _cleaned.replace(_lit, "")
    _cleaned = _WS_RE.sub(" ", _cleaned).strip(_TRIM_CHARS)

    if len(_cleaned) < _min:
        # ★剥离后无实质内容 → 降级引导语（绝不伪造内容）
        if log is not None:
            try:
                log(f"出口占位符净化: 剥离后无实质内容(len={len(_cleaned)}<{_min})，"
                    f"降级为未命中引导语（不伪造内容）")
            except Exception as _e:
                silent_exc(_e, where="PlaceholderSanitizer::sanitize_placeholder_text log")
        return PLACEHOLDER_FALLBACK_TEXT
    return _cleaned
