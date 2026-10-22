# -*- coding: utf-8 -*-
"""growth_memory.py —— 成长记忆（第345批 刀5）。

## 病根（洞鉴 #1 双卡死）

生成侧：`_generate_self_vision` 的愿景基调由「价值观 TOP1」决定，而 TOP1 来自
`vision_map` 的三句候选 `random.choice`。方向表 `_convert_vision_to_learning` 更是
**常量映射**：`"守护" → "深入理解安全架构和风险管理"`，vision 怎么随机，
`plan` 都是同一句。

治理侧：`PulseLegs._add_learn_task` 对主题做相似度去重，同一句 plan 每次都被判重复跳过。

两侧一合 ⇒ 12 分钟内 12 次跳过、0 次新学习，**闭环死锁**。

## 本模块做两件事

1. **成长记忆第二输入源**（裁决③）：给愿景/学习方向提供"最近成长目标/成就/困惑"。
   它让 `f(价值观TOP1)` 变成 `f(价值观TOP1, 成长记忆)`。
2. **换源信号**（治理侧保去重但加换源）：连续 N 次同主题被跳过 ⇒ 置位 `reroute`，
   下游生成侧消费后换一个成长维度，而不是继续撞同一句。

## 状态归属

进程内单例，**不落盘**（成长记忆的持久载体是知识库节点与叙事自我，
本模块只做"当下这一程"的读取窗口与换源握手）。
`reset()` 供测试/重启清理。

## 不猜边界

- 没有 provider、没有本地记录 ⇒ `state="empty"`（不是 `ok` + 0 条）：
  "成长记忆是空的"与"读不到成长记忆"必须可区分（铁律156/166）。
"""

import threading
import time
from typing import Any, Callable

#: 连续同主题跳过多少次触发换源（任务书建议 3，可配）
DEFAULT_REROUTE_THRESHOLD = 3

#: 本地保留的成长条目上限（防御：别让"记忆"本身变成泄漏源）
MAX_LOCAL_RECORDS = 200

_LOCK = threading.RLock()

#: 本地成长记录：`{kind: [{"text","at"}]}`，kind ∈ goal/achievement/confusion
_LOCAL: dict[str, list] = {"goal": [], "achievement": [], "confusion": []}

#: 外部 provider（装配层注入，优先于本地记录）
_PROVIDER: Callable[[], dict] | None = None

#: 换源状态
_SKIP: dict[str, Any] = {"topic": "", "streak": 0, "reroute": False, "last_at": 0.0}

#: 换源计数（供验收/报告）
_ROUTE_STATS: dict[str, int] = {"skips": 0, "reroutes": 0}

_GROWTH_KINDS = ("goal", "achievement", "confusion")


def set_growth_provider(fn: Callable[[], dict] | None) -> None:
    """注入成长记忆 provider（返回 `{goal:[...], achievement:[...], confusion:[...]}`）。"""
    global _PROVIDER
    with _LOCK:
        _PROVIDER = fn


def record_growth(kind: str, text: str, now: float | None = None) -> bool:
    """记一条成长（目标/成就/困惑）。空文本、非法 kind 一律拒收（不猜）。"""
    _k = str(kind or "").strip()
    _t = str(text or "").strip()
    if _k not in _GROWTH_KINDS or not _t:
        return False
    with _LOCK:
        _lst = _LOCAL.setdefault(_k, [])
        _lst.append({"text": _t[:200], "at": float(now if now is not None else time.time())})
        if len(_lst) > MAX_LOCAL_RECORDS:
            del _lst[: len(_lst) - MAX_LOCAL_RECORDS]
    return True


def read_growth_memory(limit: int = 5) -> dict:
    """读成长记忆。返回 `{state, goal, achievement, confusion, source, total}`。

    `state`：`ok`（有数据）/ `empty`（provider 与本地都没有）/ `unavailable`（provider 抛错）。
    """
    _cap = max(0, int(limit))
    _from_provider: dict = {}
    _src = "local"
    _state = "empty"
    if _PROVIDER is not None:
        try:
            _raw = _PROVIDER()
            if isinstance(_raw, dict):
                _from_provider = _raw
                _src = "provider"
        except Exception as _e:
            from nucleus._silent_except import silent_exc
            silent_exc(_e, "growth_memory.read_growth_memory provider")
            _src = "provider_error"
    _out: dict[str, list] = {}
    _total = 0
    with _LOCK:
        for _k in _GROWTH_KINDS:
            _pv = _from_provider.get(_k)
            _items: list = []
            if isinstance(_pv, (list, tuple)):
                _items = [str(x).strip()[:200] for x in _pv if str(x or "").strip()]
            if not _items:
                _items = [str(r.get("text", "")) for r in _LOCAL.get(_k, [])
                          if isinstance(r, dict) and str(r.get("text", "")).strip()]
                if _items and _src == "provider":
                    _src = "provider+local"
            _out[_k] = _items[-_cap:] if _cap else []
            _total += len(_out[_k])
    if _total > 0:
        _state = "ok"
    elif _src == "provider_error":
        _state = "unavailable"
    return {"state": _state, "source": _src, "total": _total,
            "goal": _out["goal"], "achievement": _out["achievement"],
            "confusion": _out["confusion"]}


def growth_dimensions() -> list[str]:
    """把成长记忆压成"可当方向用的短语"（去重、保序）。

    落空的 kind 不补占位句——**没有就说没有**，由调用方决定怎么降级。
    """
    _m = read_growth_memory(limit=6)
    _dims: list[str] = []
    for _k, _label in (("goal", "成长目标"), ("achievement", "近期成就"),
                       ("confusion", "当前困惑")):
        for _it in _m.get(_k, []) or []:
            _s = str(_it).strip()
            if _s and _s not in _dims:
                _dims.append(_s)
    return _dims


def pick_growth_dimension(seed: Any = None, exclude: str = "") -> str:
    """按 seed 稳定挑一个成长维度（同 seed 同结果，便于复现）。空记忆返回空串。"""
    _dims = [d for d in growth_dimensions() if d != str(exclude or "")]
    if not _dims:
        return ""
    _s = 0
    for _ch in str(seed if seed is not None else ""):
        _s = (_s * 131 + ord(_ch)) % 1000003
    return _dims[_s % len(_dims)]


# ========== 治理侧换源握手 ==========


def note_dedup_skip(topic: str, threshold: int = DEFAULT_REROUTE_THRESHOLD,
                    now: float | None = None) -> dict:
    """治理侧上报"这个主题被去重跳过了"。连续同主题达阈值 ⇒ 置位 reroute。

    Returns: `{streak, reroute, topic}`（reroute 为本轮是否**首次**置位）。
    """
    _t = str(topic or "").strip()[:200]
    _th = max(1, int(threshold))
    _now = float(now if now is not None else time.time())
    with _LOCK:
        _ROUTE_STATS["skips"] += 1
        if _t and _t == _SKIP.get("topic"):
            _SKIP["streak"] = int(_SKIP.get("streak", 0)) + 1
        else:
            _SKIP["topic"] = _t
            _SKIP["streak"] = 1
        _newly = False
        if _SKIP["streak"] >= _th and not _SKIP.get("reroute"):
            _SKIP["reroute"] = True
            _ROUTE_STATS["reroutes"] += 1
            _newly = True
        _SKIP["last_at"] = _now
        return {"topic": _t, "streak": _SKIP["streak"], "reroute": _newly}


def should_reroute() -> bool:
    with _LOCK:
        return bool(_SKIP.get("reroute"))


def consume_reroute() -> dict:
    """生成侧消费换源信号（消费后清零，避免永久换源）。"""
    with _LOCK:
        _was = bool(_SKIP.get("reroute"))
        _old = str(_SKIP.get("topic", ""))
        _SKIP["reroute"] = False
        _SKIP["streak"] = 0
        return {"was_set": _was, "old_topic": _old}


def reroute_stats() -> dict:
    with _LOCK:
        return dict(_ROUTE_STATS)


def reset() -> None:
    """清空全部状态（测试/重启用）。"""
    global _PROVIDER
    with _LOCK:
        for _k in list(_LOCAL):
            _LOCAL[_k] = []
        _SKIP.update({"topic": "", "streak": 0, "reroute": False, "last_at": 0.0})
        _ROUTE_STATS.update({"skips": 0, "reroutes": 0})
        _PROVIDER = None
