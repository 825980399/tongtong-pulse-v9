# -*- coding: utf-8 -*-
"""
GPUCore.py —— GPU核心

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: GPU资源管理与计算任务调度
机制: 基于GPUCore类实现，包含7个核心方法
定位: 硬件抽象层
"""

import threading
import time
from typing import Any

from nucleus.const import LogLevel
from nucleus.logger import get_module_logger
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底


_module_logger = get_module_logger("GPUCore")

class GPUCore(SilentLogMixin):
    """GPU 计算加速核心。"""

    # 显存压力阈值（%）
    VRAM_HIGH_PCT = 95.0
    VRAM_MEDIUM_PCT = 80.0
    # GPU 探测/显存查询缓存时间（秒）
    PROBE_CACHE_TTL = 60.0

    def __init__(self) -> None:
        self._gpu_info: dict[str, Any] | None = None
        self._probe_time = 0.0
        self._lock = threading.Lock()
        self._cache: dict[str, Any] = {}
        self._cache_max = 128  # 缓存条目上限（保守）
        # 记录 torch 是否真正可用（避免每次重复 import 探测）
        self._torch_available: bool | None = None
        self._device = None

    # ========== 探测 ==========

    def probe_gpu(self, force: bool = False) -> dict[str, Any]:
        """torch 精确探测 GPU。带 60s 缓存，避免频繁初始化开销。

        Returns:
            {"available": bool, "model": str, "compute_cap": str,
             "vram_total_mb": int, "vram_free_mb": int, "vram_used_pct": float,
             "shared_hint_mb": int, "torch_version": str, "timestamp": float}
            不可用时 available=False 且不抛异常。
        """
        _now = time.time()
        if not force and self._gpu_info is not None and (_now - self._probe_time) < self.PROBE_CACHE_TTL:
            return self._gpu_info
        try:
            import torch  # type: ignore
            if not torch.cuda.is_available():
                self._torch_available = False
                self._gpu_info = {"available": False, "timestamp": _now}
                self._probe_time = _now
                return self._gpu_info
            self._torch_available = True
            _dev_count = torch.cuda.device_count()
            _dev = "cuda:0"
            _name = torch.cuda.get_device_name(0) if _dev_count > 0 else "cuda"
            _cap = ""
            try:
                _cap = torch.cuda.get_device_capability(0)
                _cap = ".".join(str(x) for x in _cap)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            _tot_mb, _free_mb = 0, 0
            try:
                _free, _tot = torch.cuda.mem_get_info(0)
                _tot_mb, _free_mb = int(_tot // (1024 * 1024)), int(_free // (1024 * 1024))
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            _used_pct = 0.0
            if _tot_mb > 0:
                _used_pct = round((_tot_mb - _free_mb) / _tot_mb * 100.0, 1)
            # 共享内存口径：Windows 上独显可借系统内存，这里用系统内存可用量作参考
            _shared_hint = 0
            try:
                import psutil
                _avail_gb = psutil.virtual_memory().available / (1024 * 1024 * 1024)
                # 保守：仅当显存接近满时共享内存才有意义，作为参考不参与判定
                if _used_pct > 80.0:
                    _shared_hint = int(min(_avail_gb, 8.0) * 1024)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            self._gpu_info = {
                "available": True,
                "model": _name,
                "compute_cap": _cap,
                "vram_total_mb": _tot_mb,
                "vram_free_mb": _free_mb,
                "vram_used_pct": _used_pct,
                "shared_hint_mb": _shared_hint,
                "torch_version": getattr(torch, "__version__", ""),
                "timestamp": _now,
            }
            self._probe_time = _now
            return self._gpu_info
        except Exception:
            self._torch_available = False
            self._gpu_info = {"available": False, "timestamp": _now}
            self._probe_time = _now
            return self._gpu_info

    def gpu_available(self) -> bool:
        """GPU 是否可用（torch.cuda）。"""
        _info = self.probe_gpu()
        return bool(_info.get("available"))

    def get_vram_pressure(self) -> str:
        """GPU 显存压力等级（low/medium/high）。

        high   = 显存 >95% 或 GPU 不可用且系统内存 >90%（回退压力判定）
        medium = 显存 >80%
        low    = 显存 <=80%
        任何异常回退 low（保守不误伤，不因 GPU 探测失败而误降级）。
        """
        try:
            _info = self.probe_gpu()
            if _info.get("available"):
                _used = _info.get("vram_used_pct", 0.0)
                if _used > self.VRAM_HIGH_PCT:
                    return "high"
                if _used > self.VRAM_MEDIUM_PCT:
                    return "medium"
                return "low"
            # GPU 不可用：回退系统内存压力（避免 GPU 缺失被误判为低压力）
            try:
                import psutil
                _mem = psutil.virtual_memory().percent
                if _mem > 90.0:
                    return "high"
                if _mem > 75.0:
                    return "medium"
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            return "low"
        except Exception:
            return "low"

    # ========== ★PHASE14：真正的 GPU 计算（此前本类只有「探测」） ==========

    def batch_cosine_gpu(self, query: list, candidates: list) -> list | None:
        """★PHASE14：GPU 批量余弦相似度——本类第一个**真正在 GPU 上算**的方法。

        背景：本类原有三个方法（probe_gpu / gpu_available / get_vram_pressure）
        全部只做探测，所谓 gpu_cache 也只是把数字转成 cuda 张量存起来、
        且全项目零调用。内部协作者实测「任务管理器里 GPU 利用率纹丝不动」，
        根因就在这里：GPU 被检测到，但从未被使用。

        实现要点：
          · 先按行归一化，再用一次矩阵乘法算出全部余弦（O(N·D) 一次 kernel）；
          · 全部走 float32——GTX 10 系（算力 6.1）的 float64 单元只有 1/32 性能，
            用 float64 会让 GPU 比 CPU 还慢，这是 1050 Ti 上最典型的坑；
          · 结果立刻取回 CPU 并释放显存，绝不长期占用 4GB 中的任何一块。

        返回：长度 = len(candidates) 的 float 列表；
              **任何环节不可用/失败一律返回 None**，由调用方回退 CPU，
              绝不让 GPU 故障影响检索正确性（★绝不阻断闭环）。
        """
        if not candidates:
            return None
        try:
            import torch  # type: ignore
            if not (hasattr(torch, "cuda") and torch.cuda.is_available()):
                return None

            _q = torch.as_tensor(query, dtype=torch.float32)
            _m = torch.as_tensor(candidates, dtype=torch.float32)
            if _m.dim() != 2 or _q.numel() != _m.shape[1]:
                return None

            _q = _q.to("cuda", non_blocking=True)
            _m = _m.to("cuda", non_blocking=True)

            # 行归一化后点积即余弦
            _qn = torch.nn.functional.normalize(_q.unsqueeze(0), p=2, dim=1)
            _mn = torch.nn.functional.normalize(_m, p=2, dim=1)
            _scores = torch.mm(_mn, _qn.t()).squeeze(1)

            _out = _scores.detach().to("cpu").tolist()
            del _q, _m, _qn, _mn, _scores
            return _out if isinstance(_out, list) else [_out]
        except Exception as _e:
            try:
                self._log("DEBUG", f"GPU 批量余弦失败，回退 CPU: {_e}")
            except Exception:
                pass
            return None

    # ========== GPU 显存缓存（可选） ==========

    def gpu_cache(self, key: str, compute: Any, ttl: float = 300.0) -> Any:
        """带 TTL 的 GPU 数值缓存。

        若 GPU 可用则将 compute() 结果缓存在 GPU 显存（torch 张量），
        否则退化为普通进程内缓存。用于频繁重复计算的大块数值数据。
        """
        with self._lock:
            _hit = self._cache.get(key)
            if _hit and (time.time() - _hit[0]) < ttl:
                return _hit[1]
        try:
            _val = compute()
            if self.gpu_available():
                try:
                    import torch  # type: ignore
                    if isinstance(_val, (list, tuple)) and len(_val) > 0 and all(isinstance(x, (int, float)) for x in _val):
                        _val = torch.tensor(_val, dtype=torch.float32, device="cuda:0")
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            with self._lock:
                if len(self._cache) >= self._cache_max:
                    # 简单清理最旧
                    _oldest = min(self._cache.keys(), key=lambda k: self._cache[k][0])
                    self._cache.pop(_oldest, None)
                self._cache[key] = (time.time(), _val)
            return _val
        except Exception:
            return None


# ========== 模块级单例 ==========
_gpu_core: GPUCore | None = None
_gpu_core_lock = threading.Lock()


def get_gpu_core() -> GPUCore:
    """获取 GPUCore 单例"""
    global _gpu_core
    if _gpu_core is None:
        with _gpu_core_lock:
            if _gpu_core is None:
                _gpu_core = GPUCore()
    return _gpu_core
