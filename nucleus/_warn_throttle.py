# -*- coding: utf-8 -*-
"""冷却闸（WARNING 刷屏限流）共享工具 —— 第126批 T-126a。

背景：若干 WARNING 处于高频循环（假死探测 ``while+sleep(10)``、快照统计每心跳一拍），
持续失败态会刷出约 8640 条/日 WARNING，淹没真实告警。本模块提供进程内单例限流，
复用 ``safe_read_json`` 的 ``_last_warning_time`` 思路：同一 key 在 ``interval`` 秒内只放行一次。

用法：
    from nucleus._warn_throttle import should_warn
    if should_warn("main:false_death_probe", 300):
        logging.getLogger("pulse").warning(...)
"""
import time

_LAST: dict[str, float] = {}


def should_warn(key: str, interval: float = 300.0) -> bool:
    """Return True 表示 ``key`` 此刻可放行一次 WARNING。

    同一 ``key`` 在 ``interval`` 秒内仅首次返回 True，其余返回 False（被限流）。
    返回 True 时会顺带更新该 key 的时间戳，调用方据此决定是否真正打日志。
    """
    now = time.time()
    last = _LAST.get(key, 0.0)
    if now - last >= interval:
        _LAST[key] = now
        return True
    return False
