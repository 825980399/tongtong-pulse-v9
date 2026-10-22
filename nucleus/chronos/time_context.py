# -*- coding: utf-8 -*-
"""time_context.py —— 框架时间注入（第345批 刀4）。

## 病根（洞鉴 #2）

`time.tick` 每拍广播当前时间（`TimeCore._broadcast`），`PulseCortex.on_time_tick`
也确实把它缓存在 `self._latest_time` —— **但这个缓存全仓零消费者**
（实测 `grep _latest_time` 只有 1 处定义、1 处写入）。

于是：QICA 能识别「时间」意图 → 建议 `/时间/当前时间` → **知识图里没有这个路径** →
检索断 → LLM 兜底 → LLM 手上没有当前时间 → 答"我不确定"、甚至给出「首启」口吻。

## 本模块做两件事

1. `context_time()`：取**框架时间**（`TimeCore.get_current_time()`，与 time.tick 同源），
   取不到退回墙钟但**标明来源**（`source=wall_clock_fallback`）——不假装是广播时间。
2. `context_time_text()`：渲染成一句可拼进 prompt 的话（日期+时间+星期）。

## 「不猜」边界

- `TimeCore` 未启动 / 未注入 ⇒ 用墙钟，但 `source` 明确写 `wall_clock_fallback`，
  让"这不是广播时间"成为可见事实（铁律156：读不到就说读不到，不假装）。
- 时间格式固定，不做自然语言美化（美化是 LLM 的事，本模块只给事实）。
"""

import datetime
import time
from typing import Any

_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def _from_time_core() -> dict | None:
    """取 TimeCore 的当前时间快照；不可用返回 None（不抛、不猜）。"""
    try:
        from nucleus.chronos.TimeCore import get_time_core
        _core = get_time_core()
        if _core is None:
            return None
        _snap = _core.get_current_time()
        if not isinstance(_snap, dict):
            return None
        if not _snap.get("wall_clock") and not _snap.get("ts"):
            return None
        return _snap
    except Exception as _e:
        from nucleus._silent_except import silent_exc
        silent_exc(_e, "time_context._from_time_core")
        return None


def context_time(now: float | None = None) -> dict:
    """返回 `{ts, wall_clock, weekday, text, source}`。

    优先框架时间（TimeCore 广播口径）；不可用则墙钟，`source` 标明降级。
    """
    _snap = _from_time_core()
    if _snap is not None:
        _ts = _snap.get("ts")
        try:
            _ts_f = float(_ts) if _ts else time.time()
        except (TypeError, ValueError) as _te:
            from nucleus._silent_except import silent_exc as _se
            _se(_te, "time_context.context_time 时间戳非数值，降级墙钟", level="debug")
            _ts_f = time.time()
        _wall = str(_snap.get("wall_clock") or "").strip()
        _src = "time_core"
    else:
        _ts_f = float(now if now is not None else time.time())
        _wall = ""
        _src = "wall_clock_fallback"
    # 注入点：显式 now 时覆盖框架时间（隔离验证要固定时刻，不能读真钟）
    if now is not None:
        _ts_f = float(now)
        _src = "injected"
        _wall = ""
    _dt = datetime.datetime.fromtimestamp(_ts_f)
    _wd = _WEEKDAYS[_dt.weekday()]
    return {
        "ts": _ts_f,
        "wall_clock": _wall or _dt.strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": _wd,
        "text": "%d年%d月%d日 %s %s" % (_dt.year, _dt.month, _dt.day, _wd,
                                        _dt.strftime("%H:%M")),
        "source": _src,
    }


def context_time_text(now: float | None = None) -> str:
    """一句可直接拼进 prompt 的当前时间（`source` 不进正文，见 `context_time()`）。"""
    return context_time(now)["text"]


def annotate_llm_prompt(prompt: str, now: float | None = None,
                        trace: Any = None) -> tuple[str, dict]:
    """给 LLM prompt 前置一行「当前时间」。

    Returns: `(新 prompt, 时间信息 dict)`。
    `prompt` 为空/非 str 时原样返回（不改行为）。
    """
    if not isinstance(prompt, str) or not prompt:
        return prompt, {}
    _info = context_time(now)
    _line = "当前时间：%s（这是你此刻真实所在的时刻，回答时间相关问题时以它为准）\n" % _info["text"]
    if trace is not None:
        try:
            trace("[345时] ctx_time=%s source=%s", _info["text"], _info["source"])
        except Exception as _e:
            from nucleus._silent_except import silent_exc
            silent_exc(_e, "time_context.annotate_llm_prompt trace")
    return _line + prompt, _info
