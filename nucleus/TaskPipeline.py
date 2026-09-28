# -*- coding: utf-8 -*-
"""
TaskPipeline.py —— 任务流水线

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 任务流水线执行与阶段管理
机制: 基于TaskStage类实现，包含10个核心方法
定位: 任务调度层
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from enum import Enum
from typing import Any



class TaskStage(Enum):
    """元流程五阶段（通用处理骨架）。"""
    REQUIREMENT = "requirement"      # 需求归集
    DESIGN = "design"                # 方案设计
    IMPLEMENT = "implement"          # 落地实施
    VERIFY = "verify"                # 验证回归
    DISTILL = "distill"              # 知识沉淀


class TaskPattern(Enum):
    """任务模式归纳层——在「意图分类」之上的一层「处理范式」抽象。

    新问题先归纳到范式，再路由；未知问题落到「待归纳」，
    由工具创造/自主意图试探，成功若干次后固化为新范式。
    """
    RETRIEVAL = "检索式"    # 事实/定义/查询 → 检索 + 内在世界
    GENERATIVE = "生成式"   # 创作/设计/方案 → 深度思考 + 内在世界
    REPAIR = "修复式"       # bug/报错/异常 → 修复执行器 + 补丁管理
    DIAGNOSTIC = "诊断式"   # 状态/健康/元认知 → 系统状态 + 自我审视
    UNKNOWN = "待归纳"      # 未识别 → 工具创造/自主意图试探


# 五阶段合法流转关系（状态机约束）：
#   需求归集 → 方案设计 → 落地实施 → 验证回归 → 知识沉淀
#   验证回归失败 → 回流方案设计（或需求归集）
_VALID_TRANSITIONS: dict[TaskStage, set[TaskStage]] = {
    TaskStage.REQUIREMENT: {TaskStage.DESIGN},
    TaskStage.DESIGN: {TaskStage.IMPLEMENT, TaskStage.REQUIREMENT},
    TaskStage.IMPLEMENT: {TaskStage.VERIFY, TaskStage.DESIGN},
    TaskStage.VERIFY: {TaskStage.DISTILL, TaskStage.DESIGN, TaskStage.REQUIREMENT},
    TaskStage.DISTILL: set(),  # 终态
}


class TaskPipeline:
    """元流程实体——单个任务的全生命周期状态机。

    字段（对齐星轨 B1 要求）:
        task_id, task_pattern, stage, strategy, evidence, result,
        rollback_point, created_at, updated_at
    """

    def __init__(
        self,
        task_id: str | None = None,
        task_pattern: TaskPattern | str = TaskPattern.UNKNOWN,
        strategy: str = "",
        description: str = "",
    ) -> None:
        self.task_id: str = task_id or f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        self.task_pattern: TaskPattern = (
            task_pattern if isinstance(task_pattern, TaskPattern)
            else self._parse_pattern(task_pattern)
        )
        self.stage: TaskStage = TaskStage.REQUIREMENT
        self.strategy: str = strategy  # 采用的推理策略（接线 StrategySelector 后填充）
        self.description: str = description
        # 阶段产出的结构化证据（可追溯）
        self.evidence: list[dict[str, Any]] = []
        self.result: dict[str, Any] = {}
        self.rollback_point: str | None = None  # 回滚点（复用 PatchManager.rollback_patch）
        self.created_at: float = time.time()
        self.updated_at: float = self.created_at
        self._lock = threading.Lock()  # 状态机流转并发安全

    @staticmethod
    def _parse_pattern(value: str) -> TaskPattern:
        """将字符串转换为 TaskPattern（容错）。"""
        _mapping = {p.value: p for p in TaskPattern}
        return _mapping.get(value, TaskPattern.UNKNOWN)

    def transition_to(self, target: TaskStage | str) -> bool:
        """执行阶段流转，含合法性校验。

        Returns:
            True 表示流转成功，False 表示非法流转（被拒绝）。
        """
        _target = target if isinstance(target, TaskStage) else TaskStage(target)
        with self._lock:
            if _target not in _VALID_TRANSITIONS[self.stage]:
                return False
            self.stage = _target
            self.updated_at = time.time()
            return True

    def add_evidence(self, key: str, value: Any) -> None:
        """追加结构化证据（阶段产出），带时间戳。"""
        with self._lock:
            self.evidence.append({
                "key": key,
                "value": value,
                "stage": self.stage.value,
                "ts": time.time(),
            })
            self.updated_at = time.time()

    def set_result(self, result: dict[str, Any]) -> None:
        """设置任务结果（验证阶段产出）。"""
        with self._lock:
            self.result = dict(result or {})
            self.updated_at = time.time()

    def set_rollback_point(self, patch_id: str) -> None:
        """设置回滚点（复用 PatchManager.rollback_patch 的 patch_id）。"""
        with self._lock:
            self.rollback_point = patch_id
            self.updated_at = time.time()

    def get_stage(self) -> str:
        """返回当前阶段字符串（便于日志/序列化）。"""
        with self._lock:
            return self.stage.value

    def is_terminal(self) -> bool:
        """是否已到达终态（知识沉淀）。"""
        with self._lock:
            return self.stage == TaskStage.DISTILL

    def to_dict(self) -> dict[str, Any]:
        """序列化（可追溯、可持久化）。"""
        with self._lock:
            return {
                "task_id": self.task_id,
                "task_pattern": self.task_pattern.value,
                "stage": self.stage.value,
                "strategy": self.strategy,
                "description": self.description,
                "evidence": list(self.evidence),
                "result": dict(self.result),
                "rollback_point": self.rollback_point,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            }

    def __repr__(self) -> str:
        return (
            f"<TaskPipeline {self.task_id[:16]} "
            f"pattern={self.task_pattern.value} stage={self.stage.value}>"
        )


def infer_pattern_from_intent(intent: str) -> TaskPattern:
    """从现有「意图标签」归纳到「处理范式」（模式归纳层入口）。

    这是元流程实体的关键桥接点：把硬编码的意图标签映射到通用处理范式，
    使新意图能通过「范式」而非「白名单」找到处理路径。

    Args:
        intent: 意图标签（如「知识」「代码」「状态」「身份」等）。

    Returns:
        归纳出的处理范式；未匹配则返回 TaskPattern.UNKNOWN（待归纳）。
    """
    _mapping: dict[str, TaskPattern] = {
        "知识": TaskPattern.RETRIEVAL,
        "代码": TaskPattern.REPAIR,
        "状态": TaskPattern.DIAGNOSTIC,
        "身份": TaskPattern.RETRIEVAL,
        "关系": TaskPattern.RETRIEVAL,
        "创作": TaskPattern.GENERATIVE,
        "规划": TaskPattern.GENERATIVE,
        "分析": TaskPattern.GENERATIVE,
    }
    return _mapping.get(intent, TaskPattern.UNKNOWN)


# ========== 模块级任务记录器（供 health_ui 可观测 + 后续知识沉淀） ==========

_pipeline_registry: deque = deque(maxlen=200)  # 最近 200 个任务流水线
_registry_lock = threading.Lock()


def register_pipeline(pipeline: TaskPipeline) -> None:
    """★B5：把一个 TaskPipeline 实例登记到模块级环形缓冲（供 health_ui 端点读取）。"""
    # TODO: 预留接口，待未来功能使用（当前 TaskPipeline 未接入真实任务流，register_pipeline 无生产调用方）
    with _registry_lock:
        _pipeline_registry.append(pipeline)


def get_recent_pipelines(limit: int = 50) -> list[dict[str, Any]]:
    """★B5：返回最近 N 个 TaskPipeline 的状态流转记录（序列化）。"""
    with _registry_lock:
        _items = list(_pipeline_registry)[-limit:]
    return [p.to_dict() for p in _items]


def distill_pipeline(pipeline: TaskPipeline) -> dict[str, Any]:
    # TODO: 预留接口，待未来功能使用（详见下方 docstring 说明，当前不实际调用）
    """★B4【P2】知识沉淀接口预留——TaskPipeline 闭环后调用 verification_learning_hub.record。

    当前 TaskPipeline 尚未接入真实任务流（B1 刚建实体），本方法为「接口预留」，
    只定义沉淀的数据结构与接入点，不实际调用（避免空转写入学习枢纽）。

    接入点（后续 M4 落地时在此实现）：
        from nucleus.mnemosyne.verification_learning_hub import get_verification_learning_hub
        _hub = get_verification_learning_hub()
        _hub.record(
            organ="task_pipeline",
            task_type=f"pattern_{pipeline.task_pattern.value}",
            input_summary=pipeline.description,
            local_result=pipeline.to_dict(),
            confidence=0.5,
            relevance_score=0.5,
            needs_verification=pipeline.task_pattern == TaskPattern.UNKNOWN,
            verification_result={"stage": pipeline.get_stage(), "result": pipeline.result},
            lesson=f"任务模式={pipeline.task_pattern.value} 策略={pipeline.strategy}",
        )

    Returns:
        预留的沉淀数据结构（当前仅描述，不落库）。
    """
    return {
        "status": "reserved",
        "task_id": pipeline.task_id,
        "task_pattern": pipeline.task_pattern.value,
        "strategy": pipeline.strategy,
        "note": "接口预留：TaskPipeline 接入真实任务流后调用 verification_learning_hub.record 落库",
    }


# ========== 自测 ==========

if __name__ == "__main__":
    # 验证：合法流转 + 非法流转拒绝 + 序列化
    _tp = TaskPipeline(task_pattern="修复式", strategy="multi_step_execute")
    print(f"初始: {_tp}")

    assert _tp.transition_to(TaskStage.DESIGN), "需求→方案 应成功"
    assert _tp.transition_to(TaskStage.IMPLEMENT), "方案→实施 应成功"
    assert not _tp.transition_to(TaskStage.DISTILL), "实施→沉淀 应被拒绝（需先验证）"
    assert _tp.transition_to(TaskStage.VERIFY), "实施→验证 应成功"
    assert _tp.transition_to(TaskStage.DISTILL), "验证→沉淀 应成功"
    assert _tp.is_terminal(), "应到达终态"

    _tp.add_evidence("root_cause", "测试证据")
    _tp.set_result({"success": True})
    print(f"序列化: {_tp.to_dict()}")

    # 验证：模式归纳
    assert infer_pattern_from_intent("知识") == TaskPattern.RETRIEVAL
    assert infer_pattern_from_intent("代码") == TaskPattern.REPAIR
    assert infer_pattern_from_intent("状态") == TaskPattern.DIAGNOSTIC
    assert infer_pattern_from_intent("未知新意图") == TaskPattern.UNKNOWN
    print("模式归纳验证通过")

    print("TaskPipeline 自测通过 ✅")
