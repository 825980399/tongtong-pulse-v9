# -*- coding: utf-8 -*-
"""utils/atomic_io.py —— 418 刀3⑥：Windows 下原子替换的重试封装。

【为什么要这个】
Windows 的 `os.replace()` 在**目标文件被任何进程/线程打开着**时会抛
`PermissionError: [WinError 5] 拒绝访问`（POSIX 的 rename 没有这个问题）。
实测事故：`call_recorder::_m352b_update_daily` 每 60s 报一次
`'…\\daily_tokens.json.tmp' -> '…\\daily_tokens.json'` 拒绝访问 ——
写的人不止一个（框架写、外部排查脚本也读/写），恰好撞上就是一次失败。

【修法】
替换失败就**退避重试**（默认 6 次 / 50ms 起，总计约 1.6s），失败到最后才抛出。
调用方原有的"失败即降级、绝不阻断主流程"语义不变 —— 本模块只负责多给几次机会。

【为什么不是加锁】
锁只在**同进程内**有效；这里的竞争方是别的进程（脚本、杀软、索引服务），
进程内互斥锁一个都拦不住。重试是唯一不改变语义的修法。
"""
from __future__ import annotations

import errno
import os
import time

try:
    from nucleus._silent_except import silent_exc
except Exception:  # pragma: no cover - 独立单测时无项目内依赖
    def silent_exc(e, where="", level="debug"):
        pass

__all__ = ["replace_with_retry", "REPLACE_ATTEMPTS", "REPLACE_BASE_SLEEP"]

#: 重试次数与首次退避（秒）。总等待 ≈ 0.05+0.1+0.2+0.4+0.8 = 1.55s
REPLACE_ATTEMPTS = 6
REPLACE_BASE_SLEEP = 0.05

#: 值得重试的 errno（Windows 的 WinError 5/32/33 在 Python 里都归到这些）
_RETRY_ERRNOS = frozenset(
    _e for _e in (getattr(errno, "EACCES", None), getattr(errno, "EPERM", None),
                  getattr(errno, "EBUSY", None)) if _e is not None)


def replace_with_retry(tmp: str, dst: str, attempts: int = REPLACE_ATTEMPTS,
                       base_sleep: float = REPLACE_BASE_SLEEP) -> bool:
    """把 `tmp` 原子替换到 `dst`，失败退避重试。

    Returns:
        True = 成功；False = 重试用尽仍失败（**异常不抛**，由调用方决定怎么降级）。
    """
    _last = None
    for _i in range(max(1, int(attempts))):
        try:
            os.replace(tmp, dst)
            return True
        except OSError as e:
            _last = e
            _retryable = (getattr(e, "errno", None) in _RETRY_ERRNOS
                          or getattr(e, "winerror", None) in (5, 32, 33))
            if not _retryable and _i == 0:
                # 不可重试的错（路径不存在/名字非法）不要空转 6 次
                silent_exc(e, where="utils.atomic_io::replace_with_retry non_retryable", level="debug")
                return False
            time.sleep(base_sleep * (2 ** _i))
    if _last is not None:
        silent_exc(_last, where="utils.atomic_io::replace_with_retry exhausted", level="debug")
    return False
