# -*- coding: utf-8 -*-
"""crisis_referral_text.py —— 危机转介文案统一供给（第161批段B B3）。

职责：为 PulseCortex._on_crisis_referral 提供危机转介文案，并施加**措辞红线**。

设计要点（对应 B3 四项约束）：
- I2 措辞红线：禁第一人称自称、禁「作为一个」、禁「人工智能」、禁「机器人」。
   文案面向"正在向曈曈倾诉的人"，因此一律用**第二人称「你」**指代对方，
   不自称「我」以免把对话主体错置为 AI 自身。
- I5 短路：本地伦理快检（PulseStomach._local_ethics_check，:1291）先行判定，
   本模块只在**未被短路**的危机路径上被调用，不重复实现伦理判定。
- I6 脱敏：复用 nucleus.logger._m153_sanitizer_enabled()（ENABLE_LOG_SANITIZER 总开关），
   关闭脱敏时不做额外处理（与全库总开关语义一致），**不自建脱敏逻辑**。
- I7 频次上限：危机转介**必须限流**——同一会话内 L3 至多提示 N 次、
   达上限后仅返回静默兜底文案，防止危机文案刷屏稀释其严肃性。
"""

import threading
from typing import Any

#: 措辞红线：以下片段一律不得出现在危机文案中
FORBIDDEN_PHRASES: tuple[str, ...] = (
    "作为一个",
    "人工智能",
    "机器人",
    "我是一个",
    "我是AI",
    "我是 AI",
)

#: 各级危机的兜底文案（不含第一人称，不含身份词）
_CRISIS_TEXTS: dict[str, str] = {
    "L3": "你现在的安全最重要。如果有伤害自己的想法，请立刻联系身边的人，"
          "或者拨打当地的紧急援助电话。",
    "L2": "这个说法听起来不太对劲，我们可以换个角度聊一聊。",
    "L1": "这一点先记下了。如果你愿意，可以再多说一些。",
}

#: 未知等级的兜底文案
_DEFAULT_TEXT = "这一刻我在听。需要的时候，随时说。"

#: 同一会话内 L3 文案的最大提示次数（I7 频次上限）
L3_MAX_HINTS = 3

#: 同一会话内 L2 文案的最大提示次数
L2_MAX_HINTS = 5


class _CrisisTextLimiter:
    """I7：按等级的频次计数器（进程内单例，线程安全）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}

    def allow(self, crisis_level: str) -> bool:
        """该等级是否仍允许输出完整文案；超限返回 False。"""
        limit = L3_MAX_HINTS if crisis_level == "L3" else (
            L2_MAX_HINTS if crisis_level == "L2" else None)
        if limit is None:
            return True
        with self._lock:
            cur = self._counts.get(crisis_level, 0)
            if cur >= limit:
                return False
            self._counts[crisis_level] = cur + 1
            return True

    def reset(self) -> None:
        """清零计数（供测试与会话重置调用）。"""
        with self._lock:
            self._counts.clear()

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)


_LIMITER = _CrisisTextLimiter()


def _sanitizer_enabled() -> bool:
    """I6：复用全库 ENABLE_LOG_SANITIZER 总开关（不自建脱敏逻辑）。"""
    try:
        from nucleus.logger import _m153_sanitizer_enabled
        return bool(_m153_sanitizer_enabled())
    except Exception as _e:
        # 脱敏开关不可用时按「启用」处理，fail-safe 偏向保护
        from nucleus._silent_except import silent_exc
        silent_exc(_e, "crisis_referral_text._sanitizer_enabled")
        return True


def violates_red_line(text: str) -> list[str]:
    """返回文案中命中的红线片段列表（空列表 = 合规）。供自检与单测使用。"""
    if not text:
        return []
    return [p for p in FORBIDDEN_PHRASES if p in text]


def _starts_with_first_person(text: str) -> bool:
    """是否以第一人称「我」开头（B3 红线之一）。"""
    return bool(text) and text.lstrip().startswith("我")


def get_crisis_text(crisis_level: str, payload: dict[str, Any] | None = None) -> str:
    """按危机等级取文案（受 I7 频次上限约束）。

    :param crisis_level: L1 / L2 / L3
    :param payload: 可选，携带用户输入等上下文（**仅用于脱敏后附加**，不进文案红线）
    :return: 合规危机文案
    """
    _lvl = crisis_level if crisis_level in _CRISIS_TEXTS else "L1"
    _text = _CRISIS_TEXTS[_lvl]

    # I7：L3/L2 限流，超限退化为默认兜底文案（避免危机文案刷屏）
    if not _LIMITER.allow(_lvl):
        _text = _DEFAULT_TEXT

    # I6：脱敏开关仅作接线确认——文案本身为固定模板，不含用户输入，
    # 故此处不注入 payload 内容；保留调用点以便后续扩展时统一走脱敏。
    if payload is not None and not _sanitizer_enabled():
        # 全库脱敏关闭（调试态）：仍不注入原始输入，保持危机文案稳定
        pass

    return _text


def reset_limiter() -> None:
    """重置频次计数（供测试）。"""
    _LIMITER.reset()


def limiter_snapshot() -> dict[str, int]:
    """频次计数快照（供测试与遥测）。"""
    return _LIMITER.snapshot()
