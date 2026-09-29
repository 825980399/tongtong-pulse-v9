# -*- coding: utf-8 -*-
"""
iw_text_guard —— 内在世界（PulseInnerWorld）搜索前缀文本守卫

★主线第137批 T-137 S1.5：从 organs/brain/PulseInnerWorld.py 外迁的守卫函数三连+常量。

背景：`_SEARCH_PREFIX_ALT_NEW` 是搜索前缀正则的**单一事实来源**。
缺陷：`[一下]?` 是**单字符类**，只能吃掉「一」或「下」中的**一个**字符 →
      「查一下人工智能最新进展」被切成「查一」+「下人工智能最新进展」，
      搜索主题被污染，下游据此切分才会产出「下人工智」「能最新进」碎片。
修正：`(?:一下|下)?` 把「一下」当整体优先匹配；并把「查找」放在「查」之前
      （否则「查找一下X」只吃掉「查」，残留「找一下X」）。

外迁后由 organs/brain/PulseInnerWorld.py 与 organs/brain/pulse_inner_world_support.py
共同 import 使用，保持行为零变化。
"""
from __future__ import annotations

_SEARCH_PREFIX_ALT_NEW = (
    r"搜索(?:一下|下)?|查找(?:一下|下)?|查(?:一下|下)?|帮我[找查]|搜一下|"
    r"百度(?:一下|下)?|谷歌(?:一下|下)?|上网查"
)
#: 修复前的原始写法（灰度关闭时逐字退回，保证零回归）
_SEARCH_PREFIX_ALT_OLD = (
    r"搜索[一下]?|查[一下]?|帮我[找查]|查找[一下]?|搜一下|百度[一下]?|谷歌[一下]?|上网查"
)
#: 截断后可能残留在主题开头的单字噪声（防御性剥离用）
_SEARCH_TOPIC_LEAD_NOISE = ("下", "一", "的", "了", "个", "呀", "啊", "吧", "呢", "请")


def _search_topic_guard_enabled() -> bool:
    """搜索主题守卫开关（灰度）：读不到时默认开启。"""
    try:
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_SEARCH_TOPIC_GUARD", True))
    except Exception:
        return True


def _search_prefix_pattern() -> str:
    """按开关返回搜索前缀正则体（不含 `^` 与 `\\s*`）。"""
    return (_SEARCH_PREFIX_ALT_NEW if _search_topic_guard_enabled()
            else _SEARCH_PREFIX_ALT_OLD)


def _detect_leading_search_noise(topic: str) -> str:
    """检测主题开头是否残留单字噪声，返回命中的噪声字（未命中返回空串）。

    ★设计取舍（重要）：这里**只检测、不改写**主题。
    曾考虑直接剥离（任务书要求「如果是则进一步剥离」），但实测会误伤正常输入：
      · 「查一下下一步怎么做」→ 正确主题就是「下一步怎么做」，剥离会变成「一步怎么做」
      · 「查一下一般情况下会不会」→ 正确主题以「一」开头，剥离会吃掉首字
    而**精确的正则修正（(?:一下|下)?）已从源头消除该残留**，无需再靠启发式剥离。
    故此处退化为「检测 + 日志」：真出现残留时留痕可查，绝不擅改用户语义。
    """
    _t = (topic or "").strip()
    if _t and len(_t) > 2 and _t[0] in _SEARCH_TOPIC_LEAD_NOISE:
        return _t[0]
    return ""
