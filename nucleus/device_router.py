# -*- coding: utf-8 -*-
"""
device_router.py —— 设备路由器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 硬件设备请求路由与负载均衡
机制: 函数式模块，包含10个工具函数
定位: 硬件抽象层
"""

from __future__ import annotations

import threading
import time
from typing import Any
from nucleus._silent_except import silent_exc


# ── 每类运算的「每元素浮点运算数」（用于估算 FLOPs）──
#   余弦 = 归一化(3·N·D) + 点积(2·N·D)，取 4 作保守近似。
_OP_FLOPS_PER_ELEM: dict[str, float] = {
    "vector_search": 4.0,
    "matrix_mul": 2.0,
    "embedding": 8.0,
    "clustering": 6.0,
}

# ── 无法实测时的保守兜底（宁估慢 GPU，也不要误用）──
_FALLBACK = {
    "pcie_bytes_per_s": 6e9,      # 6 GB/s 有效带宽（PCIe3 x16 保守值）
    "gpu_flops": 1.5e12,          # 1.5 TFLOPS FP32（GTX 1050 Ti 量级）
    "gpu_launch_ms": 0.30,        # kernel launch + 同步开销
    "cpu_bytes_per_s": 20e9,      # 20 GB/s（DDR4 多核实际可达）
    "cpu_flops": 8e10,            # 80 GFLOPS（多核保守）
}

# ★必须是可重入锁：should_use_gpu 持锁后会调用 get_capability，
#   而后者也要拿同一把锁。用普通 Lock 会自死锁（实测踩过，进程直接挂死）。
_ROUTER_LOCK = threading.RLock()

_STATE: dict[str, Any] = {
    "cap": None,             # 硬件能力画像（懒探测，进程内一次）
    "cap_probed": False,
    "gpu_enabled": True,     # 总闸（可被熔断置 False）
    "slower_streak": 0,      # GPU 连续跑输计数
    "decisions": 0,          # 决策次数
    "gpu_chosen": 0,         # 选了 GPU 的次数
    "cpu_chosen": 0,
    # 后验校正因子：实测/预估 的 EWMA。>1 说明实际比预估慢。
    "calib_gpu": 1.0,
    "calib_cpu": 1.0,
    "last_error": "",
}


def _safe_float(v: Any, default: float) -> float:
    try:
        _f = float(v)
        return _f if _f > 0 and _f == _f else default   # 排除 0/负/NaN
    except Exception as e:
        silent_exc(e, "nucleus/device_router.py:62:设备路由异常", level="warning")
        return default


def _cpu_cores() -> int:
    try:
        import os

        import psutil  # type: ignore
        return int(psutil.cpu_count(logical=False) or os.cpu_count() or 1)
    except Exception:
        try:
            import os
            return int(os.cpu_count() or 1)
        except Exception:
            return 1


def _measure_gpu(cap: dict[str, Any]) -> None:
    """实测 GPU 的 PCIe 带宽与算力（只在首次探测时跑一次，几十毫秒）。

    为什么非要实测：显卡升级后，这两个数变了，盈亏平衡点才会跟着变。
    查表做不到「未来换卡自动适应」。
    """
    try:
        import torch  # type: ignore
        if not (hasattr(torch, "cuda") and torch.cuda.is_available()):
            return

        cap["gpu_name"] = str(torch.cuda.get_device_name(0))
        _cc = torch.cuda.get_device_capability(0)
        cap["compute_cap"] = f"{_cc[0]}.{_cc[1]}"
        try:
            cap["vram_total_mb"] = int(
                torch.cuda.get_device_properties(0).total_memory / 1024 ** 2)
        except Exception:
            pass

        # ① PCIe 带宽：拷 8MB 过去再拷回来，取较好值
        try:
            _n = 2 * 1024 * 1024            # 2M float32 = 8MB
            _src = torch.ones(_n, dtype=torch.float32, pin_memory=False)
            torch.cuda.synchronize()
            _t0 = time.perf_counter()
            _d = _src.to("cuda", non_blocking=False)
            torch.cuda.synchronize()
            _ms = (time.perf_counter() - _t0) * 1000.0
            if _ms > 0:
                cap["pcie_bytes_per_s"] = (_n * 4) / (_ms / 1000.0)
            del _d, _src
        except Exception:
            pass

        # ② GPU 算力：1024³ matmul（2·N³ FLOPs）
        try:
            _a = torch.randn(1024, 1024, device="cuda", dtype=torch.float32)
            _b = torch.randn(1024, 1024, device="cuda", dtype=torch.float32)
            torch.cuda.synchronize()
            _t0 = time.perf_counter()
            _c = _a @ _b
            torch.cuda.synchronize()
            _ms = (time.perf_counter() - _t0) * 1000.0
            if _ms > 0:
                cap["gpu_flops"] = (2.0 * 1024 ** 3) / (_ms / 1000.0)
            del _a, _b, _c
        except Exception:
            pass

        # ③ launch 固定开销：连续 20 次极小 kernel，取均值
        try:
            _x = torch.ones(1, device="cuda", dtype=torch.float32)
            torch.cuda.synchronize()
            _t0 = time.perf_counter()
            for _ in range(20):
                _y = _x * 2.0
            torch.cuda.synchronize()
            cap["gpu_launch_ms"] = ((time.perf_counter() - _t0) * 1000.0) / 20.0
            del _x, _y
        except Exception:
            pass
    except Exception as _e:
        cap["probe_error"] = f"{type(_e).__name__}: {str(_e)[:80]}"


def _measure_cpu(cap: dict[str, Any]) -> None:
    """实测 CPU 算力（小规模 matmul，避免拖慢启动）。"""
    try:
        import torch  # type: ignore
        _n = 512
        _a = torch.randn(_n, _n, dtype=torch.float32)
        _b = torch.randn(_n, _n, dtype=torch.float32)
        _t0 = time.perf_counter()
        _c = _a @ _b
        _ms = (time.perf_counter() - _t0) * 1000.0
        if _ms > 0:
            cap["cpu_flops"] = (2.0 * _n ** 3) / (_ms / 1000.0)
        del _a, _b, _c
    except Exception as e:
        silent_exc(e, where="nucleus.device_router::_measure_cpu L161")


def get_capability(force: bool = False) -> dict[str, Any]:
    """获取（并缓存）硬件能力画像。线程安全，只探测一次。"""
    with _ROUTER_LOCK:
        if _STATE["cap_probed"] and _STATE["cap"] is not None and not force:
            return dict(_STATE["cap"])

        cap: dict[str, Any] = {
            "has_gpu": False,
            "gpu_name": "",
            "compute_cap": "",
            "vram_total_mb": 0,
            "cores": _cpu_cores(),
            "pcie_bytes_per_s": _FALLBACK["pcie_bytes_per_s"],
            "gpu_flops": _FALLBACK["gpu_flops"],
            "gpu_launch_ms": _FALLBACK["gpu_launch_ms"],
            "cpu_bytes_per_s": _FALLBACK["cpu_bytes_per_s"],
            "cpu_flops": _FALLBACK["cpu_flops"],
            "probe_error": "",
        }
        try:
            import torch  # type: ignore
            cap["torch_version"] = str(getattr(torch, "__version__", ""))
            cap["has_gpu"] = bool(
                hasattr(torch, "cuda") and torch.cuda.is_available())
        except Exception as _e:
            cap["probe_error"] = f"torch 不可用: {type(_e).__name__}"

        if cap["has_gpu"]:
            _measure_gpu(cap)
            _measure_cpu(cap)
        else:
            # 无 GPU 也测一下 CPU，保证 CPU 侧预估不失真
            _measure_cpu(cap)

        # 兜底：实测值异常时回落到保守常数
        for _k, _d in (("pcie_bytes_per_s", _FALLBACK["pcie_bytes_per_s"]),
                       ("gpu_flops", _FALLBACK["gpu_flops"]),
                       ("gpu_launch_ms", _FALLBACK["gpu_launch_ms"]),
                       ("cpu_flops", _FALLBACK["cpu_flops"])):
            cap[_k] = _safe_float(cap.get(_k), _d)

        _STATE["cap"] = cap
        _STATE["cap_probed"] = True
        return dict(cap)


def estimate(op: str, n: int, dim: int,
             resident: bool = False) -> tuple[float, float]:
    """估算 (gpu_ms, cpu_ms)。纯计算，无副作用。

    ★关键参数 resident——数据是否已常驻显存，这个差别是数量级的：

      一次性（resident=False）：每次都要把 N×D 的候选矩阵搬过 PCIe。
          cosine 是访存密集型，搬运成本常常压倒计算收益，
          这正是「小批量上 GPU 反而更慢」的根因。

      常驻（resident=True）：候选矩阵预先驻留显存（向量数据库的标准做法），
          每次只搬一条 query（D×4 字节，几十 KB），成本几乎为零。
          同一批候选被反复查询时，GPU 会从"亏本"直接翻成"碾压"。

    Args:
        op:       运算类型键（见 _OP_FLOPS_PER_ELEM）
        n:        批量规模（候选条数）
        dim:      单条维度
        resident: 候选数据是否已驻留 GPU 显存
    """
    _cap = get_capability()
    _fpe = _OP_FLOPS_PER_ELEM.get(op, 4.0)
    _bytes_all = max(1.0, float(n) * float(dim) * 4.0)
    _bytes_query = max(1.0, float(dim) * 4.0)
    _flops = max(1.0, float(n) * float(dim) * _fpe)

    # GPU = 固定开销 + PCIe 搬运 + 设备内计算
    #   常驻模式下搬运量从「整个候选矩阵」降到「一条 query」
    _gpu_bytes = _bytes_query if resident else _bytes_all
    _gpu_ms = (_cap["gpu_launch_ms"]
               + _gpu_bytes / _cap["pcie_bytes_per_s"] * 1000.0
               + _flops / _cap["gpu_flops"] * 1000.0)
    # CPU 侧不受 resident 影响（数据本来就在内存）
    _cpu_ms = (_bytes_all / _cap["cpu_bytes_per_s"] * 1000.0
               + _flops / _cap["cpu_flops"] * 1000.0)

    # 乘上后验校正因子（实测跑得比预估慢就调高）
    return _gpu_ms * _STATE["calib_gpu"], _cpu_ms * _STATE["calib_cpu"]


def should_use_gpu(op: str, n: int, dim: int,
                   win_ratio: float = 1.15,
                   resident: bool = False) -> tuple[bool, str, float, float]:
    """决策：这个任务该不该走 GPU。

    Args:
        op:        运算类型
        n:         批量规模
        dim:       维度
        win_ratio: GPU 要比 CPU 快多少倍才值得（>1 表示要有明显优势才切换，
                   避免在盈亏平衡点附近反复横跳）
        resident:  候选数据是否已常驻显存（见 estimate 的说明）

    Returns:
        (use_gpu, reason, est_gpu_ms, est_cpu_ms)
    """
    # 先取能力快照（首次会探测，较慢），再进临界区——缩短持锁时间
    _cap = get_capability()
    with _ROUTER_LOCK:
        _STATE["decisions"] += 1

        if not _STATE["gpu_enabled"]:
            _STATE["cpu_chosen"] += 1
            return False, "GPU 已被熔断停用（此前连续跑输 CPU）", 0.0, 0.0
        if not _cap.get("has_gpu"):
            _STATE["cpu_chosen"] += 1
            return False, "无可用 GPU", 0.0, 0.0
        if n <= 0 or dim <= 0:
            _STATE["cpu_chosen"] += 1
            return False, "规模为空", 0.0, 0.0

        try:
            _g, _c = estimate(op, int(n), int(dim), resident=resident)
        except Exception as _e:
            _STATE["cpu_chosen"] += 1
            _STATE["last_error"] = f"估算失败: {_e}"
            return False, "成本估算失败，保守走 CPU", 0.0, 0.0

        # 安全系数：GPU 必须明显更便宜才切（摊不平固定开销的任务坚决不切）
        if _g * win_ratio < _c:
            _STATE["gpu_chosen"] += 1
            return True, f"GPU 预估更快（{_g:.3f}ms vs CPU {_c:.3f}ms）", _g, _c
        _STATE["cpu_chosen"] += 1
        return False, f"CPU 预估更快（{_c:.3f}ms vs GPU {_g:.3f}ms）", _g, _c


def record_actual(device: str, est_ms: float, actual_ms: float,
                  slower_limit: int = 5) -> None:
    """回写实测耗时，做后验校正 + 熔断判定。

    这是「运行全过程自判断」的落地：模型给先验，实测给后验，两者互相校准。
    """
    if est_ms <= 0 or actual_ms <= 0:
        return
    _ratio = actual_ms / est_ms
    # 限幅，防止单次离群值把模型带偏
    _ratio = max(0.2, min(5.0, _ratio))
    with _ROUTER_LOCK:
        if device == "gpu":
            _STATE["calib_gpu"] = _STATE["calib_gpu"] * 0.8 + _ratio * 0.2
        else:
            _STATE["calib_cpu"] = _STATE["calib_cpu"] * 0.8 + _ratio * 0.2

        # 熔断：GPU 实际耗时超过预估 2 倍以上算「跑输」
        if device == "gpu" and actual_ms > est_ms * 2.0:
            _STATE["slower_streak"] += 1
            if _STATE["slower_streak"] >= slower_limit:
                _STATE["gpu_enabled"] = False
        elif device == "gpu":
            _STATE["slower_streak"] = 0


def get_router_state() -> dict[str, Any]:
    """导出路由状态，供 health_ui / 运维观测消费。"""
    with _ROUTER_LOCK:
        _s = {k: v for k, v in _STATE.items() if k != "cap"}
        _s["cap"] = dict(_STATE["cap"]) if _STATE["cap"] else None
        return _s


def _breakeven_n(op: str, dim: int, resident: bool) -> int | None:
    """二分求盈亏平衡点；若在上界内 GPU 始终不划算则返回 None。"""
    _lo, _hi = 1, 20_000_000
    _g, _c = estimate(op, _hi, dim, resident=resident)
    if _g >= _c:
        return None          # 即便 2000 万条也不划算
    while _lo < _hi:
        _mid = (_lo + _hi) // 2
        _g, _c = estimate(op, _mid, dim, resident=resident)
        if _g < _c:
            _hi = _mid
        else:
            _lo = _mid + 1
    return int(_lo)


def describe_breakeven(op: str = "vector_search",
                       dim: int = 128) -> dict[str, Any]:
    """算出当前显卡在这类运算上的盈亏平衡点（多少条以上 GPU 才划算）。

    同时给出「一次性」与「常驻显存」两种模式的拐点——后者通常低几个数量级，
    这是判断「值不值得为 GPU 改造业务」的关键依据。

    这个数字就是「显卡升级后自动变化」的直观体现——换张更好的卡，
    它会变小；用核显跑，它会大到基本走不到。
    """
    try:
        _cap = get_capability()
        if not _cap.get("has_gpu"):
            return {"has_gpu": False, "breakeven_n": None, "note": "无 GPU"}
        return {
            "has_gpu": True,
            "op": op,
            "dim": dim,
            "gpu_name": _cap.get("gpu_name", ""),
            "breakeven_adhoc": _breakeven_n(op, dim, resident=False),
            "breakeven_resident": _breakeven_n(op, dim, resident=True),
            "pcie_gb_s": round(_cap.get("pcie_bytes_per_s", 0) / 1e9, 2),
            "gpu_tflops": round(_cap.get("gpu_flops", 0) / 1e12, 2),
            "cpu_gflops": round(_cap.get("cpu_flops", 0) / 1e9, 1),
        }
    except Exception as _e:
        return {"has_gpu": False, "error": str(_e)}


__all__ = [
    "OP_FLOPS_PER_ELEM",
    "describe_breakeven",
    "estimate",
    "get_capability",
    "get_router_state",
    "record_actual",
    "should_use_gpu",
]

OP_FLOPS_PER_ELEM = _OP_FLOPS_PER_ELEM
