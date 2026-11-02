# -*- coding: utf-8 -*-
"""★417刀1：把「当前线程」降到最低优先级的唯一实现。

为什么单独成模块：416 在 `nucleus/self_inspector.py` 里写过一版（`_m416_set_thread_lowest`），
417 的心跳观测 worker 也要用。复制第二份会把「必须显式 argtypes」这个 Win32 坑埋两处：

    `ctypes.windll.kernel32` 默认按 `c_int` 传参，会把 64 位伪句柄
    `0xFFFFFFFFFFFFFFFE` 截成 `0x00000000FFFFFFFE` ⇒ `SetThreadPriority` 返回 0，
    看上去像"系统不支持"，实际是参数被截断。必须 `WinDLL(..., use_last_error=True)`
    并显式声明 `argtypes=[c_void_p, c_int]`，再用 `GetThreadPriority` 读回校验。

返回值是**后端名字符串**而不是 bool：bool 说不清"设了但没生效"和"压根没设"。
    win32_lowest      设成功且读回 -2（THREAD_PRIORITY_LOWEST）
    win32_failed      SetThreadPriority 返回 0
    win32_set_but_<n> 设了，但读回不是 -2（带实际值，便于定位被谁改回去）
    posix_nice19      POSIX 上 os.nice(19) 成功（注意：Linux 上 nice 是**进程级**的）
    unavailable       平台不支持 / 调用抛异常
"""
from __future__ import annotations

import sys

from nucleus._silent_except import silent_exc

#: Windows THREAD_PRIORITY_LOWEST（winbase.h）；IDLE=-15 只对 REALTIME 进程有效，不用它
THREAD_PRIORITY_LOWEST = -2

#: 合法返回值前缀（单测据此断言，不锁精确串）
BACKENDS = ("win32_lowest", "win32_failed", "win32_set_but_", "posix_nice19", "unavailable")


def set_current_thread_lowest() -> str:
    """把**调用它的那个线程**降到最低优先级。Returns: 后端名字符串（见模块 docstring）。"""
    if sys.platform == "win32":
        try:
            import ctypes
            _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            _k32.GetCurrentThread.restype = ctypes.c_void_p
            _k32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
            _k32.SetThreadPriority.restype = ctypes.c_int
            _k32.GetThreadPriority.argtypes = [ctypes.c_void_p]
            _k32.GetThreadPriority.restype = ctypes.c_int
            _h = ctypes.c_void_p(_k32.GetCurrentThread())
            if not _k32.SetThreadPriority(_h, THREAD_PRIORITY_LOWEST):
                return "win32_failed"
            _got = int(_k32.GetThreadPriority(_h))
            return "win32_lowest" if _got == THREAD_PRIORITY_LOWEST else "win32_set_but_%d" % _got
        except Exception as _e:
            silent_exc(_e, where="utils.thread_priority.set_current_thread_lowest", level="debug")
            return "unavailable"
    try:
        import os
        os.nice(19)
        return "posix_nice19"
    except Exception as _e:
        silent_exc(_e, where="utils.thread_priority.set_current_thread_lowest", level="debug")
        return "unavailable"


def version() -> str:
    return "417.1"
