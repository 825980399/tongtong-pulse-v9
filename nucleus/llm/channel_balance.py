# -*- coding: utf-8 -*-
"""channel_balance.py —— 353 刀1：渠道池「起点均衡」（按并发余量挑起点）

【要解决的问题】
`config.get_llm_call_target()` 恒取 priority 最小的那条（`zhipu-47-flash`，
`max_concurrent=1`）。多器官并行高峰时，所有**单点**调用都挤同一个渠道：首渠道
并发满即 429，而后面几条免费渠道空着。`PulseLung._call_via_channels` 早在第24批
就有「并发满即 `continue`」的循环，但那是**多目标轮询**才有的路径；6 处绕行点
（灵核/知识/控制器/OCR）走的是单目标口，拿不到这个循环。

【本模块只做一件事】
给定「候选行列表」，返回**起点下标**：
从前往后找第一条信号量还有空位的行；找不到就退回 0（首选）。
`reason` 三态：`default` / `concurrency_busy` / `all_busy`。

【两条硬约束】
1. **不推进到付费渠道**。扫描窗口在第一条付费渠道前截止 —— 均衡绝不能把免费调用
   变成付费调用；付费渠道仍只在「前面全失败」时由调用方按原兜底链兜到。
2. **只 peek 不 acquire**。本模块只看 `available()`，不占许可；真正占用仍由调用侧
   `acquire()` 负责。两边都占就是双重占用（铁律181 分泄漏/吊住）。

【依赖关系】
只依赖 `ChannelConcurrency`（纯 stdlib）与 `config.PAID_CHANNELS`，二者都不反向
依赖本模块 ⇒ 无循环导入。日志走 stdlib `logging`，**不 import `nucleus.logger`**
（铁律195：`import nucleus.logger` 即成日志轮转执行者）。
"""
from __future__ import annotations

import logging
from typing import Any

try:
    from nucleus._silent_except import silent_exc
except Exception:  # pragma: no cover - 兜底：独立单测时无项目内依赖
    def silent_exc(e, where="", level="debug"):
        pass

__all__ = ["BALANCE_TAG", "channel_has_room", "pick_balanced_index", "log_pick"]

#: 留痕前缀（任务书刀1 规定格式：`[353均衡] caller= picked= reason=`）
BALANCE_TAG = "[353均衡]"

_LOG = logging.getLogger("pulse.llm.balance")


def _manager():
    """取渠道并发管理器单例；不可用返回 None（调用方按「全放行」处理）。"""
    try:
        from nucleus.llm.ChannelConcurrency import get_channel_concurrency_manager
        return get_channel_concurrency_manager()
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::_manager")
        return None


def _paid_channels() -> frozenset:
    """付费渠道口径直接复用 `config.PAID_CHANNELS`（352 定稿），不另立一份。"""
    try:
        import config as _cfg
        _p = getattr(_cfg, "PAID_CHANNELS", frozenset())
        return frozenset(str(x) for x in _p)
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::_paid_channels")
        return frozenset()


def channel_has_room(manager: Any, name: str) -> bool:
    """该渠道此刻是否还有并发余量。

    与 `ChannelConcurrencyManager.acquire()` 的「未注册即放行」语义保持一致：
    未注册 / 取不到信号量 ⇒ 视为有裕量（True），否则均衡会把所有未登记渠道
    全判成忙，退回首选 —— 那是配置问题，不该由本模块静默改写。
    """
    if manager is None or not name:
        return True
    try:
        _sem = manager.get_semaphore(name)
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::channel_has_room.get_semaphore")
        return True
    if _sem is None:
        return True
    try:
        return int(_sem.available()) > 0
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::channel_has_room.available")
        return True


def pick_balanced_index(rows: list, caller: str = "") -> tuple:
    """按并发余量挑起点下标。

    Args:
        rows: `config.get_llm_call_targets()` 形态的行（含 `channel` 键）。
        caller: 仅用于留痕（不参与判定）。

    Returns:
        `(index, reason)`，reason ∈ `no_candidates` / `default` /
        `concurrency_busy` / `all_busy`。
    """
    _n = len(rows or [])
    if _n <= 1:
        return 0, "no_candidates" if _n == 0 else "default"

    _mgr = _manager()
    # 灰度口径复用渠道并发开关：关着就完全保持改造前行为（一开关一语义，铁律165）
    if _mgr is None or not bool(getattr(_mgr, "enabled", True)):
        return 0, "default"

    _paid = _paid_channels()
    _end = _n
    for _i in range(_n):
        if str((rows[_i] or {}).get("channel") or "") in _paid:
            _end = _i
            break
    if _end <= 1:
        # 首选就是付费渠道、或整个池只有付费渠道 ⇒ 不做均衡
        return 0, "default"

    for _i in range(_end):
        if channel_has_room(_mgr, str((rows[_i] or {}).get("channel") or "")):
            return _i, ("concurrency_busy" if _i > 0 else "default")
    return 0, "all_busy"


def log_pick(caller: str, rows: list, index: int, reason: str) -> None:
    """按任务书格式落 `[353均衡] caller= picked= reason=` 留痕。"""
    _picked = ""
    try:
        _picked = str((rows[index] or {}).get("channel") or "")
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::log_pick")
    try:
        _LOG.info("%s caller=%s picked=%s reason=%s",
                  BALANCE_TAG, caller or "-", _picked or "-", reason)
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::log_pick.emit")
