# -*- coding: utf-8 -*-
"""★主线第78批 T2：静默异常统一可见化。

把全项目的 `except ...: pass`（静默吞掉异常）改为 `silent_exc(e, where)`，
记录异常类型与信息，不再让错误无声消失（铁律10：核心文件禁 except:pass）。

灰度开关：config.FEATURE['enable_silent_except_logging']（默认 True=开启=记录）。
关闭时退回原 `pass` 行为（零回归）。所有调用均走 DEBUG 级，生产默认日志级别下
不刷屏，仅在开启 DEBUG 时可见——既「不再静默」又「不 intrusion 热路径」。
"""
import logging

try:
    from config import FEATURE as _FEATURE
except Exception:  # pragma: no cover - 配置缺失时安全降级
    _FEATURE = {}


def silent_exc(e: Exception, where: str = "") -> None:
    """记录一处被静默捕获的异常（类型 + 信息 + 位置）。

    where 形如 "main.py:29"，便于回溯。灰度关闭时直接返回（复现原 pass 行为）。
    """
    if not _FEATURE.get("enable_silent_except_logging", True):
        return
    logging.getLogger("pulse.silent_except").debug(
        f"[静默异常可见化] {where} {type(e).__name__}: {e}"
    )
