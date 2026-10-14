# -*- coding: utf-8 -*-
"""
SpikeExecutionBackend.py —— 脉冲执行后端抽象（第三层 · 接口隔离）

版本: v10 PulseNet
批次: 第202批 刀3
日期: 2026年10月13日

职责: 定义脉冲执行的后端抽象接口，隔离「脉冲语义」与「物理执行载体」
定位: 类脑架构三层预留架构 · 第三层（执行后端层）

三层预留架构:
    第一层 脉冲语义层   —— nucleus/pulse_types.py（芯片无关中间表示）
    第二层 权重表示层   —— nucleus/synapsys/ResonanceDetector.py（四元组）
    第三层 执行后端层   —— 本文件（接口隔离，现在预埋）

⚠️ 本批为**纯接口预埋**：
    - 不接入任何器官代码（P1 批的事）
    - 不实现 PhotonicBackend（远期，光脉冲硬件成熟时）
    - 不加任何门控（纯接口定义，未调用即零运行时开销）
"""

from abc import ABC, abstractmethod

# 后端名称常量（与 config.SPIKE_BACKEND 取值域对齐）
BACKEND_PYTHON = 'python'
BACKEND_PHOTONIC = 'photonic'


class SpikeExecutionBackend(ABC):
    """脉冲执行后端抽象基类。

    当前实现：PythonSNNBackend（纯 Python SNN 仿真）。
    未来实现：PhotonicBackend（光脉冲硬件）。
    """

    #: 后端名称，与 config.SPIKE_BACKEND 的取值对应
    name: str = ''

    @abstractmethod
    def emit(self, spike) -> None:
        """发射一个脉冲。"""

    @abstractmethod
    def on_spike(self, spike) -> None:
        """收到一个脉冲。"""

    @abstractmethod
    def update_weight(self, pre, post, delta: float) -> None:
        """更新权重（用于 STDP）。"""


class PythonSNNBackend(SpikeExecutionBackend):
    """当前实现：纯 Python SNN 仿真后端。

    212批·刀4：补读数出口——emit/on_spike/update_weight 各计一次，
    get_backend_stats() 返回三类计数（即使后端未启用也能返回计数，满足「不可验的闸不许开」前置）。
    计数开销极低（整数自增），不参与任何路由/门控。
    """

    name = BACKEND_PYTHON

    def __init__(self) -> None:
        self._emit_count = 0
        self._on_spike_count = 0
        self._update_weight_count = 0

    def emit(self, spike) -> None:
        """发射一个脉冲（TODO(P1批): 接入实际脉冲流）；212批·刀4 计次。"""
        self._emit_count += 1

    def on_spike(self, spike) -> None:
        """收到一个脉冲（TODO(P1批): 接入实际脉冲流）；212批·刀4 计次。"""
        self._on_spike_count += 1

    def update_weight(self, pre, post, delta: float) -> None:
        """更新权重（TODO(P1批): 接入实际权重更新）；212批·刀4 计次。"""
        self._update_weight_count += 1

    def get_backend_stats(self) -> dict:
        """212批·刀4：读数出口——返回 emit/on_spike/update_weight 计数快照。"""
        return {
            "backend": self.name,
            "emit": self._emit_count,
            "on_spike": self._on_spike_count,
            "update_weight": self._update_weight_count,
        }


def get_spike_backend(backend_name=None):
    """按名称取后端实例（工厂）。

    未显式传名时惰性读取 config.SPIKE_BACKEND（默认 'python'）。
    未知名称回退 PythonSNNBackend（不用 try/except，守铁律150 免新增静默 handler）。

    ⚠️ 本函数为**预埋工厂**，本批不被任何器官调用 —— 未调用即零行为。
    """
    if backend_name is None:
        import config
        backend_name = getattr(config, 'SPIKE_BACKEND', BACKEND_PYTHON)

    if backend_name == BACKEND_PHOTONIC:
        # PhotonicBackend 尚未实现（远期）→ 回退到 Python 后端
        return PythonSNNBackend()
    return PythonSNNBackend()


__all__ = [
    "BACKEND_PHOTONIC",
    "BACKEND_PYTHON",
    "PythonSNNBackend",
    "SpikeExecutionBackend",
    "get_spike_backend",
]