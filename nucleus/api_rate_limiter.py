# -*- coding: utf-8 -*-
"""
api_rate_limiter.py —— API限流器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 令牌桶算法限制外部API调用频率，防止超限
机制: 基于APIRateLimiter类实现，包含9个核心方法
定位: 外部调用防护层
"""

import contextlib
import threading


class APIRateLimiter:
    """远程API并发信号量控制器"""

    def __init__(self, max_concurrent: int = 3):
        self._semaphore = threading.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._total_acquired = 0
        self._total_released = 0
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 30.0) -> bool:
        """获取一个API调用许可，超时返回False"""
        if self._semaphore.acquire(timeout=timeout):
            with self._lock:
                self._total_acquired += 1
            return True
        return False

    def release(self):
        """释放API调用许可"""
        self._semaphore.release()
        with self._lock:
            self._total_released += 1

    def get_stats(self):
        with self._lock:
            in_use = self._total_acquired - self._total_released
        return {
            "max_concurrent": self._max_concurrent,
            "total_acquired": self._total_acquired,
            "total_released": self._total_released,
            "in_use": in_use,
        }


_api_limiter = None
_api_limiter_lock = threading.Lock()


def get_api_limiter() -> APIRateLimiter:
    global _api_limiter
    if _api_limiter is None:
        with _api_limiter_lock:
            if _api_limiter is None:
                # ★PHASE12-P1-5（2026-09-06）：20 → 8，并外置到 config。
                #   原硬编码 20 路并发打同一个 API Key，极易触发上游限流（429/超时），
                #   且失败重试会让并发路数不降反升。收敛到 8 并交由 config 控制，
                #   运维按上游配额直接调 REMOTE_API_CONFIG["max_concurrent"] 即可。
                _api_limiter = APIRateLimiter(max_concurrent=_read_max_concurrent())
    return _api_limiter


def _read_max_concurrent() -> int:
    """★PHASE12-P1-5：从 config.REMOTE_API_CONFIG 读取并发上限，失败回落 8。

    与 SafeEvolutionExecutor 的参数外置保持同一套路：
    延迟导入 config 规避循环导入；任何异常都回落保守值，绝不让配置问题
    拖垮远程调用链路（宁可用保守并发，也不能起不来）。
    """
    _default = 8
    try:
        import config as _cfg
        _raw = getattr(_cfg, "REMOTE_API_CONFIG", {})
        _val = _raw.get("max_concurrent", _default) if isinstance(_raw, dict) else _default
        if isinstance(_val, bool) or not isinstance(_val, (int, float)):
            return _default
        _val = int(_val)
        # 下界 1（至少允许一路），上界 64（超过即视为误填，无实际意义）
        return max(1, min(64, _val))
    except Exception:
        return _default


def shutdown_api_limiter() -> None:
    """★P0批次3：复位 APIRateLimiter 单例，满足器官零状态（规则4）。

    原停机流程未清理该全局单例，重启时会复用带残留占用计数的旧实例。
    此处显式置空，使下次获取重建全新零状态实例。
    """
    global _api_limiter
    _api_limiter = None


def get_llm_call_config() -> dict:
    """★主线第8批 任务3 P2-56/57/58：读取 LLM_CALL_CONFIG，缺失回落保守默认。

    与 _read_max_concurrent 同套路：延迟导入 config 规避循环导入；任何异常都回落
    到与原硬编码一致的默认值，保证「配置缺失也能正常运行」。
    浅合并 timeout_by_purpose，避免上游只改部分用途超时导致 KeyError。
    """
    _default = {
        "default_model": "deepseek-v4-flash",
        "timeout_by_purpose": {
            "evolution": 30,
            "reflection": 60,
            "general": 60,
            "inner_world_refine": 15,
            "inner_world_chat": 20,
            "spiritual": 30,
            "controller": 10,
        },
        "enable_rate_limit": True,
        "rate_limit_per_minute": 20,
    }
    try:
        import config as _cfg
        _raw = getattr(_cfg, "LLM_CALL_CONFIG", None)
        if isinstance(_raw, dict):
            _merged = dict(_default)
            _merged.update(_raw)
            _t = _raw.get("timeout_by_purpose")
            if isinstance(_t, dict):
                _merged["timeout_by_purpose"] = {**_default["timeout_by_purpose"], **_t}
            return _merged
    except Exception as e:
        print(f"[WARNING] api_rate_limiter.py:125: {type(e).__name__}: {e}")
    return _default


@contextlib.contextmanager
def api_rate_limited(enabled: bool = True, acquire_timeout: float = 30.0):
    """★主线第8批 任务3 P2-58：统一远程 API 并发限流接入点（上下文管理器）。

    在 enable_rate_limit 时获取/释放全局 API 并发许可（APIRateLimiter 信号量），
    与 PulseLung 既有的 acquire/release 行为一致；关闭时直接放行（零副作用）。
    各 LLM 调用方统一用 `with api_rate_limited(...)` 接入，取代散落的硬编码。
    """
    if not enabled:
        yield False
        return
    _limiter = get_api_limiter()
    _acquired = _limiter.acquire(timeout=acquire_timeout)
    try:
        yield _acquired
    finally:
        if _acquired:
            _limiter.release()
