# -*- coding: utf-8 -*-
"""utils/native_thread_caps.py —— 437 刀1：**原生线程池**上限的唯一收敛点。

【为什么要这个模块（实测证据，2026-11-03 路灯）】
437 任务书的前提是「ONNX Runtime 自己起了 96 条原生计算线程」。逐库普查后
**归属是错的**，实测（`tmp/_m437_src.py`，16 核机器，OS 线程数由
`psutil.Process().num_threads()` 读，含原生线程）：

    baseline                     4
    +numpy                      19   (+15)  ← scipy-openblas 自带 16 线程池
    +torch                      22   (+3)   ← 之后 torch.mm 再 +7（OpenMP 池）
    +faiss                      37   (+15)  ← 自有 OpenMP 池
    +sklearn                    52   (+15)  ← 自有 OpenMP 池
    tokenizers.encode_batch     +16         ← rust rayon 池（首次并行编码时建）
    onnxruntime session(intra=2) +1         ← ORT 反而是最小的那个

生产实测 `pid 32860`：OS 线程 **165**，`threading.active_count()` **70**
⇒ **95 条原生线程**（与烛微报的 96 吻合，但来源不是 ORT）。
`VectorEncoder` 早在 360 批就已给 fastembed 传 `threads=2`，ORT 侧没有问题。

【口径】
本模块只做「在建池**之前**把上限写进环境变量」这一件事，**不建线程、不改调度**。
必须早于 `import numpy` 调用：OpenBLAS 在**被导入时**就建池，晚了就无效。

    OMP_NUM_THREADS / OPENBLAS_NUM_THREADS / MKL_NUM_THREADS / NUMEXPR_NUM_THREADS
        → faiss / sklearn / torch 的 OpenMP 池 + numpy 的 OpenBLAS 池
    RAYON_NUM_THREADS
        → tokenizers(rust) 的 rayon 池；rayon 在**首次并行任务**时读一次

实测（`tmp/_m437_perf2.py`，16 核）：不限制 39 条 → 限制后 18 条，
且编码**更快**（1.193s → 0.928s / 256 条）——线程少了上下文切换反而省了。

【已设过的不覆盖】
外部显式设置的变量（运维调参、CI、用户 shell）优先，本模块只补空缺，
除非 `force=True`。`ENABLE_M437_NATIVE_THREAD_CAP=False` 时整体不动作。
"""
from __future__ import annotations

import logging
import os
import sys

# 本模块可能在 `nucleus.logger` 装配**之前**被 main.py 调用 ⇒ 只用标准库 logging，
# 且不 import 任何 nucleus 模块（避免早期循环导入）。
_log = logging.getLogger("pulse.module.native_thread_caps")

# OpenMP / BLAS 系：同一批调用方共享这几个变量语义
_OMP_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)

# 本次进程实际写入的值（只读展示用；供快照 / 单测断言）
_APPLIED: dict[str, str] = {}
_APPLIED_ONCE = False


def _cfg_bool(name: str, default: bool) -> bool:
    """读 config 的同名开关；config 未加载 / 取值异常 ⇒ 用 default（不抛）。"""
    try:
        _c = sys.modules.get("config")
        if _c is None:
            return default
        return bool(getattr(_c, name, default))
    except Exception as _e:
        # 读配置失败不该阻断限流，但必须可见（铁律 4：不许静默 except）
        _log.debug("[437刀1] 读 config.%s 失败，用缺省 %r: %s: %s",
                   name, default, type(_e).__name__, _e)
        return default


def _cfg_int(name: str, default: int) -> int:
    """读 config 的同名整数；非法值 ⇒ default。"""
    try:
        _c = sys.modules.get("config")
        if _c is None:
            return default
        return int(getattr(_c, name, default))
    except Exception as _e:
        _log.debug("[437刀1] 读 config.%s 失败，用缺省 %r: %s: %s",
                   name, default, type(_e).__name__, _e)
        return default


def omp_threads() -> int:
    """OpenMP/BLAS 池上限（缺省 4）。0 或负数按 1 处理。"""
    return max(1, _cfg_int("M437_NATIVE_OMP_THREADS", 4))


def rayon_threads() -> int:
    """tokenizers(rayon) 池上限（缺省 2）。0 或负数按 1 处理。"""
    return max(1, _cfg_int("M437_NATIVE_RAYON_THREADS", 2))


def ort_threads() -> int:
    """ONNX Runtime intra/inter-op 线程数（缺省 4）。360 批硬编码的 2 走这里。"""
    return max(1, _cfg_int("M437_ORT_INTRA_THREADS", 4))


def apply_native_caps(force: bool = False) -> dict:
    """把原生线程池上限写进环境变量。幂等，可重复调用。

    Args:
        force: True 时连「已被外部设置」的变量也覆盖（CLI / 测试用）。

    Returns:
        dict：``{"enabled": bool, "set": {...}, "kept": {...}, "why": str}``
        —— `set` 是本次真正写入的，`kept` 是已有外部值而**未覆盖**的。
    """
    global _APPLIED_ONCE

    if not _cfg_bool("ENABLE_M437_NATIVE_THREAD_CAP", True):
        return {"enabled": False, "set": {}, "kept": {}, "why": "开关关闭"}

    _omp = str(omp_threads())
    _ray = str(rayon_threads())
    _want: dict[str, str] = {_k: _omp for _k in _OMP_VARS}
    _want["RAYON_NUM_THREADS"] = _ray

    _set: dict[str, str] = {}
    _kept: dict[str, str] = {}
    for _k, _v in _want.items():
        _cur = os.environ.get(_k)
        if _cur and _cur != _v and not force:
            _kept[_k] = _cur          # 尊重外部显式设置
            continue
        if _cur == _v:
            _kept[_k] = _cur          # 已经是目标值，无需再写
            continue
        os.environ[_k] = _v
        _set[_k] = _v

    if _set:
        _APPLIED.update(_set)
    _APPLIED_ONCE = True
    return {
        "enabled": True,
        "set": dict(_set),
        "kept": dict(_kept),
        "why": "已写入" if _set else "全部已存在，未覆盖",
    }


def applied() -> dict[str, str]:
    """本进程内由本模块写入的环境变量快照（只读，用于 health_snapshot / 单测）。"""
    return dict(_APPLIED)


def applied_once() -> bool:
    """本进程是否调用过 apply_native_caps（无论是否真的写入）。"""
    return _APPLIED_ONCE
