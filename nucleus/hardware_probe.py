# -*- coding: utf-8 -*-
"""
hardware_probe.py —— 硬件探测器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 探测系统硬件资源与可用性
机制: 函数式模块，包含8个工具函数
定位: 硬件抽象层
"""

from __future__ import annotations
from nucleus._silent_except import silent_exc
from config import TIMEOUT_CONFIG

import os
from typing import Any



def detect_hardware_tier() -> dict[str, Any]:
    """装配前探测硬件并评估 tier。

    返回:
        {
            "tier": "high" / "standard" / "minimal",
            "cores": int,          # 逻辑核心数
            "memory_gb": float,    # 总内存 GB
            "has_gpu": bool,       # 是否有可用 GPU
            "score": int,          # 综合评分（0-9）
            "source": str,         # 探测数据来源（psutil / os / unknown）
        }
    """
    cores = _detect_cores()
    memory_gb = _detect_memory_gb()
    has_gpu = _detect_gpu()

    # 评分：核心数(0-3) + 内存(0-4) + GPU(0-2)，与 PulseHardwareLauncher 语义一致
    # GPU 是「加分项」而非「必选项」：无 GPU 但 CPU/内存强，仍应 high。
    score = 0
    if cores >= 16:
        score += 3
    elif cores >= 8:
        score += 2
    elif cores >= 4:
        score += 1

    if memory_gb >= 32:
        score += 4
    elif memory_gb >= 16:
        score += 3
    elif memory_gb >= 8:
        score += 2
    elif memory_gb >= 4:
        score += 1

    if has_gpu:
        score += 2

    if score >= 7:
        tier = "high"
    elif score >= 4:
        tier = "standard"
    else:
        tier = "minimal"

    _deep = _detect_deep_hardware()
    return {
        "tier": tier,
        "cores": cores,
        "memory_gb": memory_gb,
        "has_gpu": has_gpu,
        "score": score,
        "source": "psutil" if _has_psutil() else "os",
        # ★v9.5深度探测：挖尽硬件资源（GPU显存/共享显存、物理核、内存余量、磁盘类型）
        "deep": _deep,
    }


def _has_psutil() -> bool:
    try:
        import psutil  # noqa: F401
        return True
    except ImportError as e:
        silent_exc(e, "nucleus/hardware_probe.py:85", level="warning")
        return False


def _detect_cores() -> int:
    """逻辑核心数，兜底 os.cpu_count。"""
    try:
        import psutil
        return psutil.cpu_count(logical=True) or os.cpu_count() or 1
    except Exception as e:
        silent_exc(e, "nucleus/hardware_probe.py:94", level="warning")
        return os.cpu_count() or 1


def _detect_memory_gb() -> float:
    """总内存 GB，兜底 0（表示未知）。"""
    try:
        import psutil
        vm = psutil.virtual_memory()
        return round(vm.total / (1024 ** 3), 1)
    except Exception as e:
        silent_exc(e, "nucleus/hardware_probe.py:104", level="warning")
        return 0.0


def _detect_gpu() -> bool:
    """是否有可用 GPU（nvidia-smi 探测，失败返回 False，不抛异常）。"""
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            check=False, capture_output=True, text=True, timeout=TIMEOUT_CONFIG['hardware_probe_fast'],
            encoding="utf-8", errors="replace",
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def _detect_deep_hardware() -> dict[str, Any]:
    """★v9.5深度硬件探测：挖尽能用资源。

    返回:
        {
            "physical_cores": int,      # 物理核数
            "mem_available_gb": float,  # 可用内存 GB（余量）
            "mem_usage_pct": float,     # 内存使用率（百分比）
            "gpu": {...},               # 显存/共享显存/计算能力
            "disk": {...},              # 磁盘容量与类型
        }
    任何项失败都保守回退默认值，不抛异常。
    """
    _deep: dict[str, Any] = {}
    try:
        import psutil
        # 物理核 + 可用内存余量
        _deep["physical_cores"] = psutil.cpu_count(logical=False) or 1
        _vm = psutil.virtual_memory()
        _deep["mem_available_gb"] = round(_vm.available / (1024 ** 3), 1)
        _deep["mem_usage_pct"] = round(_vm.percent, 1)
        # 磁盘：工作目录所在盘的容量与剩余
        import os as _os
        _target = _os.getcwd()
        if _os.name == "nt":
            _target = _os.path.splitdrive(_target)[0] + "\\"
        _du = psutil.disk_usage(_target)
        _deep["disk"] = {
            "total_gb": round(_du.total / (1024 ** 3), 1),
            "free_gb": round(_du.free / (1024 ** 3), 1),
            "usage_pct": round(_du.percent, 1),
        }
    except Exception:
        _deep.setdefault("physical_cores", 1)
        _deep.setdefault("mem_available_gb", 0.0)
        _deep.setdefault("mem_usage_pct", 100.0)

    # GPU 深度：显存总量/空闲/计算能力 + 共享显存
    _gpu = _detect_gpu_deep()
    if _gpu:
        _deep["gpu"] = _gpu
    return _deep


def _detect_gpu_deep() -> dict[str, Any]:
    """★v9.5：深度 GPU 探测（nvidia-smi）。

    返回:
        {"available": bool, "model": str, "memory_total_mb": int,
         "memory_free_mb": int, "compute_cap": str, "shared_memory_mb": int}
    共享显存在 Windows 上表现为 GPU 可借用系统内存的一部分，
    这里用显存空闲作参考口径，避免误判。
    """
    _info: dict[str, Any] = {"available": False}
    try:
        import subprocess
        _r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,memory.total,memory.free,compute_cap",
             "--format=csv,noheader,nounits"],
            check=False, capture_output=True, text=True, timeout=TIMEOUT_CONFIG['hardware_probe'],
            encoding="utf-8", errors="replace",
        )
        if _r.returncode == 0 and _r.stdout.strip():
            _parts = [s.strip() for s in _r.stdout.strip().split(",")]
            if len(_parts) >= 1:
                _info["available"] = True
                _info["model"] = _parts[0]
            if len(_parts) >= 2 and _parts[1].isdigit():
                _info["memory_total_mb"] = int(_parts[1])
            if len(_parts) >= 3 and _parts[2].isdigit():
                _info["memory_free_mb"] = int(_parts[2])
            if len(_parts) >= 4:
                _info["compute_cap"] = _parts[3]
            # 共享显存估算：独显可借用系统内存的约 50%作为安全参考，
            # 但不空想：存空闲显存为主可用于计算的量
            _mem_free = _info.get("memory_free_mb", 0)
            _info["shared_memory_mb"] = 0  # 独显不加载共享显存空想条（稳定优先）
            _info["usable_mb"] = _mem_free  # 实际可用于计算的显存
    except Exception as e:
        silent_exc(e, "nucleus/hardware_probe.py:201", level="warning")
    return _info


def compute_degraded_feature(feature: dict[str, Any], tier: str) -> dict[str, Any]:
    """根据硬件 tier 计算降级后的 FEATURE（浅拷贝，不改原配置）。

    只在 tier 为 minimal / standard 时降级非核心器官；high 时保持原样。

    非核心器官（可降级）：
        - enable_code_learner  代码学习
        - enable_vision        视觉皮层
        - enable_motor         运动系统（双手/双腿/代码沙箱/文件消化器）
        - enable_initiative    主动交互
        - enable_immune        免疫系统
        - enable_endocrine     内分泌（激素）
        - enable_spiritual_core 精神核心
        - enable_evolution     遗传系统
    """
    _degraded = dict(feature)  # 浅拷贝

    if tier == "high":
        return _degraded

    # minimal：极简保命模式，仅保留核心（心脏/大脑/内在世界/胃/嘴巴等无条件器官）
    # 关闭所有非核心可降级器官。
    if tier == "minimal":
        _degraded.update({
            "enable_code_learner": False,
            "enable_vision": False,
            "enable_motor": False,
            "enable_initiative": False,
            "enable_immune": False,
            "enable_endocrine": False,
            "enable_spiritual_core": False,
            "enable_evolution": False,
        })
        return _degraded

    # standard：保留大部分功能，仅关闭最耗资源的非核心器官。
    # 关闭视觉（图像处理重）、免疫（动态加载）、遗传（动态加载）、精神核心。
    _degraded.update({
        "enable_vision": False,
        "enable_immune": False,
        "enable_evolution": False,
        "enable_spiritual_core": False,
    })
    return _degraded


__all__ = [
    "compute_degraded_feature",
    "detect_hardware_tier",
]
