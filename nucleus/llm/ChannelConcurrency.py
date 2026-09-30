# -*- coding: utf-8 -*-
"""ChannelConcurrency.py —— 大模型渠道并发智能调度（主线第24批）

【背景（任务书 §0.1）】
框架原先允许同时发起 8 个大模型调用（全局信号量），但免费渠道实际并发只有 1
（智谱），导致 7 个调用超时/429；且渠道切换是「失败后重试」—— 每次失败要白等
30 秒超时才发现该换渠道。后台学习还会与用户对话抢同一个池子。

【本模块职责（T1/T2/T3/T7 的运行时内核）】
1. **按渠道独立并发控制**（T1）：每渠道一个可调信号量，初始值来自渠道配置
   ``max_concurrent``；全局信号量仍作为总上限保护（由调用方保留）。
2. **并发满即切换**（T2）：非阻塞 ``acquire``，拿不到许可 → 上层立即 ``continue``
   试下一个渠道，零延迟、不等待 30 秒超时。
3. **动态并发调整**（T3）：类 TCP 拥塞控制 —— 连续成功到阈值缓慢 +1；
   失败/限流/超时立即 -N；上下界保护（免费渠道上界单独设，避免打爆上游）。
4. **状态统计**（T7）：当前并发/上限/成功/失败/超时/限流/平均延迟/调整历史。

【★关键实现选择：自研可调信号量】
``threading.Semaphore`` **不支持修改上限**；任务书建议「调整时创建新信号量替换」，
但那会带来**在途计数泄漏**：若此刻有 3 个调用未释放，替换成 ``Semaphore(5)``
后这 3 次 release 会让可用许可变成 8。
→ 本模块用 ``AdjustableSemaphore``（``Condition`` + 计数器）实现**原地调上限**，
  在途占用完全不丢，比「替换法」更正确、也无需重建对象。

【铁律】
- 纯 stdlib（threading / time），零项目内依赖 → 可独立单测、无循环导入。
- 线程安全：信号量自身加锁；管理器状态用 ``RLock`` 保护。
- 灰度由调用方控制（``ENABLE_CHANNEL_CONCURRENCY``）；未注册渠道一律**放行**，
  保证关闭开关或渠道未纳入时行为与改造前完全一致。
"""

from __future__ import annotations

import threading
import time
from typing import Any

try:
    from nucleus._silent_except import silent_exc
except Exception:
    def silent_exc(e, where="", level="debug"):
        pass

__all__ = [
    "AdjustableSemaphore",
    "ChannelConcurrencyManager",
    "get_channel_concurrency_manager",
    "reset_channel_concurrency_manager",
]


class AdjustableSemaphore:
    """**上限可原地调整**的信号量（``threading.Semaphore`` 做不到这一点）。

    语义与 ``threading.Semaphore`` 一致：``acquire`` 占用一个许可，``release`` 归还。
    额外支持 ``set_max(new_max)`` —— 调整上限时**保留在途占用计数**，不会因重建对象
    造成「许可超发」。

    例如：上限 5、当前在途 3 时把上限调到 2 → 可用许可为 0（不会变成 2），
    等这 3 个归还会逐次触发等待者，超出新上限的部分自然被抑制。
    """

    def __init__(self, max_value: int = 1) -> None:
        self._cond = threading.Condition()
        self._max = max(1, int(max_value))
        self._in_use = 0
        self._total_acquired = 0

    # ---------------- 基本操作 ----------------
    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        """获取一个许可。

        Args:
            blocking: False 时立即返回（拿不到就 False）—— T2「并发满即切换」用它。
            timeout: 阻塞模式下的最长等待秒数（None = 无限等）。
        """
        with self._cond:
            if not blocking:
                if self._in_use < self._max:
                    self._in_use += 1
                    self._total_acquired += 1
                    return True
                return False
            _deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
            while self._in_use >= self._max:
                if _deadline is not None:
                    _remain = _deadline - time.monotonic()
                    if _remain <= 0:
                        return False
                    self._cond.wait(_remain)
                else:
                    self._cond.wait()
            self._in_use += 1
            self._total_acquired += 1
            return True

    def release(self) -> None:
        """归还一个许可（多余释放不会让计数变负）。"""
        with self._cond:
            if self._in_use > 0:
                self._in_use -= 1
            self._cond.notify()

    # ---------------- 动态调整 ----------------
    def set_max(self, new_max: int) -> int:
        """原地调整上限（保留在途占用），返回生效后的上限。"""
        with self._cond:
            self._max = max(1, int(new_max))
            self._cond.notify_all()
            return self._max

    # ---------------- 观测 ----------------
    @property
    def max_value(self) -> int:
        """当前并发上限（**可被 set_max 原地调整**，非初始值）。"""
        with self._cond:
            return self._max

    @property
    def in_use(self) -> int:
        """当前在途占用数（已 acquire 未 release 的许可数）。"""
        with self._cond:
            return self._in_use

    def available(self) -> int:
        """当前可用许可数（= 上限 - 在途；上限被调低到低于在途时返回 0，不返回负数）。"""
        with self._cond:
            return max(0, self._max - self._in_use)

    def stats(self) -> dict[str, int]:
        """快照统计：``max_value``（当前上限）/``in_use``（在途）/``available``/
        ``total_acquired``（历史累计获取次数）。"""
        with self._cond:
            return {
                "max_value": self._max,
                "in_use": self._in_use,
                "available": max(0, self._max - self._in_use),
                "total_acquired": self._total_acquired,
            }


class ChannelConcurrencyManager:
    """按渠道维护「独立并发上限 + 动态调整 + 统计」的调度器（PulseLung 使用）。

    Args:
        config: 动态并发配置；None 时从 ``config.DYNAMIC_CONCURRENCY_CONFIG`` 读取
                （读不到用内置默认值）。
    """

    #: 内置默认配置（与任务书 T3 的配置项一一对应）
    DEFAULT_CONFIG: dict[str, Any] = {
        "enabled": True,
        "success_threshold": 5,          # 每 N 次成功 +1
        "failure_decrement": 1,          # 每次失败 -N
        "min_concurrent": 1,             # 下界
        "max_concurrent_default": 10,    # 默认上界（付费渠道可用更大值）
        "free_channel_max": 10,          # 免费渠道上界（避免免费渠道无限增长）
        # 兜底；实际值由 __init__ 从 config.PAID_CHANNEL_NAMES 动态推导（P2-233，154批）
        "paid_channel_names": ["deepseek", "advanced"],
        "all_channels_full_wait": 1.0,   # 全部渠道并发满时的等待秒数
        "adjust_history_limit": 10,      # 调整历史保留条数
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._lock = threading.RLock()
        self._sems: dict[str, AdjustableSemaphore] = {}
        self._state: dict[str, dict[str, Any]] = {}
        self._cfg: dict[str, Any] = dict(self.DEFAULT_CONFIG)
        if config is None:
            config = self.load_project_config()
        if isinstance(config, dict):
            self._cfg.update(config)
        # ★P2-233（154批）：付费渠道名从 config.PAID_CHANNEL_NAMES 动态推导
        #   （与 P2-187 同族：渠道名一律从配置读取，杜绝硬编码副本漂移）。
        #   配置缺失回落保守默认 ["deepseek", "advanced"]。
        try:
            import config as _cfg
            _paid = getattr(_cfg, "PAID_CHANNEL_NAMES", None)
            if isinstance(_paid, (list, tuple)) and _paid:
                self._cfg["paid_channel_names"] = list(_paid)
        except Exception as _e:
            silent_exc(_e, where="ChannelConcurrencyManager.__init__:paid_channels")

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    @staticmethod
    def load_project_config() -> dict[str, Any]:
        """从 ``config.DYNAMIC_CONCURRENCY_CONFIG`` 读取（容错，失败返回空 dict）。"""
        try:
            import config as _cfg
            _raw = getattr(_cfg, "DYNAMIC_CONCURRENCY_CONFIG", None)
            return dict(_raw) if isinstance(_raw, dict) else {}
        except Exception:
            return {}

    @property
    def enabled(self) -> bool:
        """灰度开关（``DYNAMIC_CONCURRENCY_CONFIG["enabled"]``，默认 True）。

        关闭时 ``acquire`` 一律放行、``record_result`` 直接返回 disabled，
        即行为与「未接入本模块」完全一致。
        """
        return bool(self._cfg.get("enabled", True))

    def _upper_of(self, name: str) -> int:
        """渠道并发上限的**天花板**：付费渠道用默认上界，免费渠道用 free_channel_max。"""
        _paid = set(self._cfg.get("paid_channel_names") or [])
        if name in _paid:
            return max(1, int(self._cfg.get("max_concurrent_default", 10)))
        return max(1, int(self._cfg.get("free_channel_max", 10)))

    def _min_of(self) -> int:
        """并发下限（任何动态调整的结果都不会低于它，最小 1）。"""
        return max(1, int(self._cfg.get("min_concurrent", 1)))

    # ------------------------------------------------------------------
    # 渠道注册 / 信号量
    # ------------------------------------------------------------------
    def ensure_channel(self, name: str, initial_max: int | None = None) -> AdjustableSemaphore:
        """确保渠道已注册（幂等）。``initial_max`` 缺省时取渠道配置或默认 1。"""
        if not name:
            name = "?"
        with self._lock:
            _sem = self._sems.get(name)
            if _sem is not None:
                return _sem
            _init = initial_max
            if _init is None:
                _init = self._read_channel_max(name)
            _init = self._clamp(name, int(_init))
            _sem = AdjustableSemaphore(_init)
            self._sems[name] = _sem
            self._state[name] = self._new_state(name, _init)
            return _sem

    def _read_channel_max(self, name: str) -> int:
        """从渠道配置读 ``max_concurrent``（缺失回落 1）。"""
        try:
            import config as _cfg
            for _ch in getattr(_cfg, "REMOTE_API_CHANNELS", {}).get("default_channels", []):
                if isinstance(_ch, dict) and _ch.get("name") == name:
                    return int(_ch.get("max_concurrent", 1) or 1)
        except Exception:
            pass
        return 1

    def _clamp(self, name: str, value: int) -> int:
        """把并发值夹到 ``[min_concurrent, 该渠道天花板]`` 区间内。

        天花板按渠道类型区分（付费用 ``max_concurrent_default``，
        免费/其他用 ``free_channel_max``），避免免费渠道被动态涨到打爆上游。
        """
        return max(self._min_of(), min(self._upper_of(name), int(value)))

    def _new_state(self, name: str, init_max: int) -> dict[str, Any]:
        """新建渠道运行态（计数 / 延迟累加 / 调整历史）。

        注意 ``latency_sum`` + ``latency_samples`` 用「累加 + 样本数」而非保存全部样本，
        是为了让长期运行时内存恒定（平均延迟 = sum / samples）。
        """
        return {
            "name": name,
            "current_max": int(init_max),
            "initial_max": int(init_max),
            "success_count": 0,
            "failure_count": 0,
            "timeout_count": 0,
            "rate_limit_count": 0,
            "total_count": 0,
            "consecutive_failures": 0,
            "success_since_adjust": 0,
            "latency_sum": 0.0,
            "latency_samples": 0,
            "adjust_history": [],
        }

    def get_semaphore(self, name: str) -> AdjustableSemaphore | None:
        """取渠道信号量；**未注册渠道返回 None**（调用方据此放行，保持旧行为）。"""
        with self._lock:
            return self._sems.get(name)

    def register_channels(self, channels: list[dict]) -> int:
        """批量注册渠道（``ensure_channel`` 的便利封装），返回本次注册数量。"""
        _n = 0
        for _ch in channels or []:
            if not isinstance(_ch, dict):
                continue
            _name = _ch.get("name")
            if not _name:
                continue
            with self._lock:
                _exists = _name in self._sems
            self.ensure_channel(_name, _ch.get("max_concurrent"))
            if not _exists:
                _n += 1
        return _n

    # ------------------------------------------------------------------
    # 许可获取 / 释放（T1 + T2）
    # ------------------------------------------------------------------
    def acquire(self, name: str, blocking: bool = False,
                timeout: float | None = None) -> bool:
        """获取渠道许可。

        Returns:
            True = 已获取（调用方**必须**在 finally 中 release）；
            True = 渠道未注册（放行，无需 release —— 由 ``is_registered`` 区分）；
            False = 并发已满（调用方应立即切换下一渠道）。
        """
        if not self.enabled:
            return True
        _sem = self.get_semaphore(name)
        if _sem is None:
            return True          # 未注册 → 放行（旧行为）
        return _sem.acquire(blocking=blocking, timeout=timeout)

    def release(self, name: str) -> None:
        """归还渠道许可（未注册渠道静默忽略）。"""
        _sem = self.get_semaphore(name)
        if _sem is not None:
            _sem.release()

    def is_registered(self, name: str) -> bool:
        """渠道是否已注册。

        ★调用方用它区分 ``acquire()`` 返回 True 的两种含义：
        「真的拿到许可（必须 release）」vs「渠道未注册（放行，**不要** release）」。
        """
        with self._lock:
            return name in self._sems

    # ------------------------------------------------------------------
    # 动态调整（T3）
    # ------------------------------------------------------------------
    def record_result(self, name: str, success: bool, latency: float = 0.0,
                      is_rate_limit: bool = False, is_timeout: bool = False) -> dict[str, Any]:
        """记录一次渠道调用结果，并按策略动态调整并发上限。

        Returns:
            ``{"adjusted": bool, "old": int, "new": int, "reason": str}``
        """
        if not self.enabled:
            return {"adjusted": False, "old": 0, "new": 0, "reason": "disabled"}
        self.ensure_channel(name)
        with self._lock:
            _st = self._state[name]
            _st["total_count"] += 1
            if latency and latency > 0:
                _st["latency_sum"] += float(latency)
                _st["latency_samples"] += 1
            if success:
                _st["success_count"] += 1
                _st["consecutive_failures"] = 0
                _st["success_since_adjust"] += 1
                _thr = max(1, int(self._cfg.get("success_threshold", 5)))
                if _st["success_since_adjust"] >= _thr:
                    _st["success_since_adjust"] = 0
                    return self._change_max_locked(name, +1, f"成功{_thr}次")
                return {"adjusted": False, "old": _st["current_max"],
                        "new": _st["current_max"], "reason": ""}
            # ---- 失败 ----
            _st["failure_count"] += 1
            _st["consecutive_failures"] += 1
            _st["success_since_adjust"] = 0
            if is_rate_limit:
                _st["rate_limit_count"] += 1
            if is_timeout:
                _st["timeout_count"] += 1
            _dec = max(1, int(self._cfg.get("failure_decrement", 1)))
            _why = "限流" if is_rate_limit else ("超时" if is_timeout else "失败")
            return self._change_max_locked(
                name, -_dec, f"{_why}(连续{_st['consecutive_failures']}次)")

    def _change_max_locked(self, name: str, delta: int, reason: str) -> dict[str, Any]:
        """按 delta 调整上限（调用方需持有 ``self._lock``）。"""
        _st = self._state[name]
        _old = int(_st["current_max"])
        _new = self._clamp(name, _old + int(delta))
        if _new == _old:
            return {"adjusted": False, "old": _old, "new": _old, "reason": reason}
        _st["current_max"] = _new
        # ★原地调整上限，在途占用不丢（见模块 docstring）
        _sem = self._sems.get(name)
        if _sem is not None:
            _sem.set_max(_new)
        _hist = _st["adjust_history"]
        _hist.append({
            "ts": time.time(),
            "old": _old,
            "new": _new,
            "reason": reason,
        })
        _limit = max(1, int(self._cfg.get("adjust_history_limit", 10)))
        if len(_hist) > _limit:
            del _hist[:len(_hist) - _limit]
        return {"adjusted": True, "old": _old, "new": _new, "reason": reason}

    # ------------------------------------------------------------------
    # 统计（T7）
    # ------------------------------------------------------------------
    def get_stats(self) -> dict[str, Any]:
        """返回全部渠道的并发状态统计（供监控面板 / 自省消费）。"""
        with self._lock:
            _out: dict[str, Any] = {}
            for _name, _st in self._state.items():
                _sem = self._sems.get(_name)
                _samples = _st["latency_samples"]
                _out[_name] = {
                    "name": _name,
                    "current_concurrent": _sem.in_use if _sem else 0,
                    "available": _sem.available() if _sem else 0,
                    "max_concurrent": _st["current_max"],
                    "initial_max": _st["initial_max"],
                    "success_count": _st["success_count"],
                    "failure_count": _st["failure_count"],
                    "timeout_count": _st["timeout_count"],
                    "rate_limit_count": _st["rate_limit_count"],
                    "total_count": _st["total_count"],
                    "consecutive_failures": _st["consecutive_failures"],
                    "success_rate": round(
                        _st["success_count"] / _st["total_count"], 4)
                    if _st["total_count"] else None,
                    "avg_latency": round(_st["latency_sum"] / _samples, 3) if _samples else None,
                    "adjust_history": list(_st["adjust_history"]),
                }
            return _out

    def get_channel_stats(self, name: str) -> dict[str, Any]:
        """单个渠道的统计（字段同 ``get_stats()[name]``）；渠道不存在返回空 dict。"""
        return self.get_stats().get(name, {})

    def summary_line(self) -> str:
        """单行摘要（供 60 秒一次的渠道状态日志 / 控制台）。"""
        _all = self.get_stats()
        if not _all:
            return "[渠道并发] 无已注册渠道"
        _parts = []
        for _name, _s in sorted(_all.items()):
            _sr = _s.get("success_rate")
            _parts.append(
                f"{_name} {_s['current_concurrent']}/{_s['max_concurrent']}"
                f"(初{_s['initial_max']}, 成功{_s['success_count']}/失败{_s['failure_count']}"
                f"{', 成功率%.0f%%' % (_sr * 100) if isinstance(_sr, float) else ''})")
        return "[渠道并发] " + " | ".join(_parts)

    def reset(self) -> None:
        """清空全部状态（测试 / 停机复位用）。"""
        with self._lock:
            self._sems.clear()
            self._state.clear()


# ======================================================================
# 进程级单例
# ======================================================================
_manager: ChannelConcurrencyManager | None = None
_manager_lock = threading.Lock()


def get_channel_concurrency_manager(config: dict[str, Any] | None = None
                                    ) -> ChannelConcurrencyManager:
    """获取进程级单例（参数仅在首次创建生效）。"""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = ChannelConcurrencyManager(config)
    return _manager


def reset_channel_concurrency_manager() -> None:
    """复位单例（测试 / 停机用）。"""
    global _manager
    with _manager_lock:
        _manager = None
