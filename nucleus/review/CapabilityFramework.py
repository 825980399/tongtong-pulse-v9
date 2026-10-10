# -*- coding: utf-8 -*-
"""
CapabilityFramework.py —— 能力框架

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 框架能力评估与能力矩阵
机制: 基于CapabilityStatus类实现，包含10个核心方法
定位: 评估治理层
"""

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from nucleus.logger import get_module_logger

_logger = get_module_logger("CapabilityFramework")


class CapabilityStatus(Enum):
    """能力状态"""
    AVAILABLE = "available"      # 可用
    INITIALIZING = "initializing"  # 初始化中
    UNAVAILABLE = "unavailable"  # 不可用
    LEARNING = "learning"        # 学习中


@dataclass
class Capability:
    """能力定义"""
    name: str                    # 能力名称
    description: str             # 能力描述
    category: str                # 分类：code_review/code_analysis/script/environment/tool/orchestration
    instance: Any = None         # 能力实例
    status: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    success_count: int = 0
    failure_count: int = 0
    avg_execution_time: float = 0.0
    last_used: float = 0.0
    capabilities: list = field(default_factory=list)  # 具体能力列表
    init_func: Callable | None = None   # 初始化函数


@dataclass
class CapabilityResult:
    """能力执行结果"""
    capability: str = ""
    success: bool = False
    output: Any = None
    error: str = ""
    execution_time: float = 0.0
    metadata: dict = field(default_factory=dict)


class CapabilityFramework:
    """能力无上限架构"""

    def __init__(self, project_root: str | None = None):
        """
        初始化能力框架

        Args:
            project_root: 项目根目录
        """
        if project_root is None:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.project_root = project_root
        self._capabilities: dict = {}  # name -> Capability
        self._execution_history: list = []
        self._learning_data: dict = {}  # 学习数据
        self._initialized = False
        _logger.info("能力无上限架构初始化")

    def register_capability(self, name: str, description: str, category: str,
                            init_func: Callable, capabilities: list | None = None) -> bool:
        """
        注册能力

        Args:
            name: 能力名称
            description: 能力描述
            category: 分类
            init_func: 初始化函数
            capabilities: 具体能力列表

        Returns:
            是否注册成功
        """
        if name in self._capabilities:
            _logger.warning(f"能力已注册，覆盖: {name}")

        cap = Capability(
            name=name,
            description=description,
            category=category,
            init_func=init_func,
            capabilities=capabilities or [],
            status=CapabilityStatus.UNAVAILABLE,
        )
        self._capabilities[name] = cap
        _logger.info(f"注册能力: {name} ({category}) - {description}")
        return True

    def initialize_all(self) -> dict:
        """
        初始化所有已注册的能力

        Returns:
            初始化结果 {success, failed, skipped}
        """
        results = {"success": [], "failed": [], "skipped": []}

        for name, cap in self._capabilities.items():
            if cap.status == CapabilityStatus.AVAILABLE:
                results["skipped"].append(name)
                continue

            cap.status = CapabilityStatus.INITIALIZING
            try:
                if cap.init_func:
                    cap.instance = cap.init_func()
                    cap.status = CapabilityStatus.AVAILABLE
                    results["success"].append(name)
                    _logger.info(f"能力初始化成功: {name}")
                else:
                    cap.status = CapabilityStatus.UNAVAILABLE
                    results["failed"].append(name)
                    _logger.warning(f"能力无初始化函数: {name}")
            except Exception as e:
                cap.status = CapabilityStatus.UNAVAILABLE
                results["failed"].append(name)
                _logger.error(f"能力初始化失败: {name} - {e}")

        self._initialized = True
        _logger.info(f"能力初始化完成: 成功={len(results['success'])}, "
                     f"失败={len(results['failed'])}, 跳过={len(results['skipped'])}")
        return results

    def get_capability(self, name: str) -> Capability | None:
        """获取能力"""
        return self._capabilities.get(name)

    def get_available_capabilities(self, category: str | None = None) -> list:
        """获取所有可用能力"""
        available = [c for c in self._capabilities.values()
                     if c.status == CapabilityStatus.AVAILABLE]
        if category:
            available = [c for c in available if c.category == category]
        return available

    def execute_capability(self, name: str, method: str, *args, **kwargs) -> CapabilityResult:
        """
        执行能力的指定方法

        Args:
            name: 能力名称
            method: 方法名
            *args: 位置参数
            **kwargs: 关键字参数

        Returns:
            执行结果
        """
        result = CapabilityResult(capability=name)
        start_time = time.time()

        cap = self._capabilities.get(name)
        if cap is None:
            result.error = f"能力未注册: {name}"
            _logger.error(result.error)
            return result

        if cap.status != CapabilityStatus.AVAILABLE:
            result.error = f"能力不可用: {name} (状态={cap.status.value})"
            _logger.error(result.error)
            return result

        if cap.instance is None:
            result.error = f"能力实例为空: {name}"
            _logger.error(result.error)
            return result

        try:
            func = getattr(cap.instance, method, None)
            if func is None or not callable(func):
                result.error = f"能力方法不存在: {name}.{method}"
                _logger.error(result.error)
                return result

            output = func(*args, **kwargs)
            result.success = True
            result.output = output
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            cap.success_count += 1
            _logger.debug(f"能力执行成功: {name}.{method}")

        except Exception as e:
            result.error = str(e)
            result.success = False
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            cap.failure_count += 1
            _logger.error(f"能力执行失败: {name}.{method} - {e}")

        finally:
            result.execution_time = time.time() - start_time
            cap.last_used = time.time()
            # 更新平均执行时间
            total = cap.success_count + cap.failure_count
            if total > 0:
                cap.avg_execution_time = (
                    (cap.avg_execution_time * (total - 1) + result.execution_time) / total
                )

        # 记录执行历史
        self._execution_history.append({
            "capability": name,
            "method": method,
            "success": result.success,
            "execution_time": result.execution_time,
            "error": result.error,
            "timestamp": time.time(),
        })
        # 历史记录上限
        if len(self._execution_history) > 10000:
            self._execution_history = self._execution_history[-5000:]

        return result

    def discover_and_install(self, requirement: str) -> dict:
        """
        根据需求发现并安装缺失的能力/工具

        Args:
            requirement: 需求描述（如"需要类型检查"、"需要数据分析"）

        Returns:
            处理结果 {found, installed, available}
        """
        result = {"found": [], "installed": [], "available": [], "suggestion": ""}

        # 1. 匹配已有能力
        requirement_lower = requirement.lower()
        for name, cap in self._capabilities.items():
            if any(kw in requirement_lower for kw in [name.lower(), cap.category, cap.description.lower()]):
                if cap.status == CapabilityStatus.AVAILABLE:
                    result["available"].append(name)
                else:
                    result["found"].append(name)

        # 2. 尝试初始化找到的能力
        for name in result["found"]:
            cap = self._capabilities[name]
            try:
                if cap.init_func:
                    cap.instance = cap.init_func()
                    cap.status = CapabilityStatus.AVAILABLE
                    result["installed"].append(name)
                    _logger.info(f"按需初始化能力: {name}")
            except Exception as e:
                _logger.warning(f"按需初始化失败: {name} - {e}")

        # 3. 如果没有匹配，给出建议
        if not result["found"] and not result["available"]:
            result["suggestion"] = f"未找到匹配的能力，建议注册新能力或安装相关工具: {requirement}"
            _logger.info(result["suggestion"])

        return result

    def execute_complex_task(self, task_description: str, steps: list) -> dict:
        """
        执行复杂任务（多能力组合）

        Args:
            task_description: 任务描述
            steps: 步骤列表，每项为 {capability, method, args, kwargs, name}

        Returns:
            执行结果汇总
        """
        _logger.info(f"开始执行复杂任务: {task_description} ({len(steps)}个步骤)")

        results = []
        all_success = True
        start_time = time.time()

        for i, step in enumerate(steps):
            step_name = step.get("name", f"step_{i}")
            capability = step.get("capability")
            method = step.get("method")
            args = step.get("args", ())
            kwargs = step.get("kwargs", {})

            _logger.info(f"步骤 {i+1}/{len(steps)}: {step_name} ({capability}.{method})")

            result = self.execute_capability(capability, method, *args, **kwargs)
            results.append({
                "step": step_name,
                "capability": capability,
                "method": method,
                "success": result.success,
                "execution_time": result.execution_time,
                "error": result.error,
            })

            if not result.success:
                all_success = False
                if step.get("stop_on_failure", True):
                    _logger.error(f"步骤失败，终止任务: {step_name}")
                    break

        summary = {
            "task": task_description,
            "total_steps": len(steps),
            "completed_steps": len(results),
            "all_success": all_success,
            "total_time": time.time() - start_time,
            "steps": results,
        }

        _logger.info(f"复杂任务完成: 成功={all_success}, "
                     f"完成={len(results)}/{len(steps)}, "
                     f"耗时={summary['total_time']:.1f}秒")
        return summary

    def learn_from_execution(self, result: CapabilityResult):
        """
        从执行结果中学习

        Args:
            result: 执行结果
        """
        cap_name = result.capability
        if cap_name not in self._learning_data:
            self._learning_data[cap_name] = {
                "total_calls": 0,
                "success_rate": 0.0,
                "avg_time": 0.0,
                "common_errors": {},
            }

        data = self._learning_data[cap_name]
        data["total_calls"] += 1
        total = data["total_calls"]

        # 更新成功率
        success = 1 if result.success else 0
        data["success_rate"] = (data["success_rate"] * (total - 1) + success) / total

        # 更新平均时间
        data["avg_time"] = (data["avg_time"] * (total - 1) + result.execution_time) / total

        # 记录常见错误
        if result.error:
            error_type = result.error.split(":")[0][:50]
            data["common_errors"][error_type] = data["common_errors"].get(error_type, 0) + 1

    def get_learning_report(self) -> dict:
        """获取学习报告"""
        report = {
            "total_capabilities": len(self._capabilities),
            "available_capabilities": len(self.get_available_capabilities()),
            "total_executions": len(self._execution_history),
            "capabilities": {},
        }

        for name, cap in self._capabilities.items():
            report["capabilities"][name] = {
                "status": cap.status.value,
                "success_count": cap.success_count,
                "failure_count": cap.failure_count,
                "success_rate": cap.success_count / max(cap.success_count + cap.failure_count, 1),
                "avg_execution_time": round(cap.avg_execution_time, 3),
                "last_used": cap.last_used,
            }

        return report

    def get_status_summary(self) -> str:
        """获取状态摘要"""
        available = self.get_available_capabilities()
        lines = [
            f"能力框架状态: {len(available)}/{len(self._capabilities)} 可用",
            f"总执行次数: {len(self._execution_history)}",
        ]
        for cap in available:
            rate = cap.success_count / max(cap.success_count + cap.failure_count, 1)
            lines.append(f"  {cap.name} ({cap.category}): 成功率={rate:.0%}, "
                         f"平均耗时={cap.avg_execution_time:.2f}s")
        return "\n".join(lines)


# 单例实例
_framework = None

def get_capability_framework() -> CapabilityFramework:
    """获取能力框架单例"""
    global _framework
    if _framework is None:
        _framework = CapabilityFramework()
    return _framework


def init_default_capabilities() -> CapabilityFramework:
    """
    初始化默认能力集（前6个模块）

    Returns:
        已初始化的能力框架
    """
    framework = get_capability_framework()

    # 注册6个核心能力
    capabilities = [
        ("code_review", "代码审查引擎", "code_review",
         lambda: __import__("nucleus.review", fromlist=["get_code_review_engine"]).get_code_review_engine(),
         ["ruff检查", "pyright检查", "增量审查", "修复计划生成"]),
        ("code_analysis", "代码分析引擎", "code_analysis",
         lambda: __import__("nucleus.review", fromlist=["get_code_analyzer"]).get_code_analyzer(),
         ["AST解析", "复杂度分析", "函数提取", "项目摘要"]),
        ("script_executor", "脚本执行引擎", "script",
         lambda: __import__("nucleus.review", fromlist=["get_script_executor"]).get_script_executor(),
         ["脚本生成", "脚本验证", "沙箱执行", "脚本库管理"]),
        ("environment_manager", "环境管理引擎", "environment",
         lambda: __import__("nucleus.review", fromlist=["get_environment_manager"]).get_environment_manager(),
         ["依赖检测", "自动安装", "环境健康度", "ensure_import"]),
        ("tool_installer", "工具安装引擎", "tool",
         lambda: __import__("nucleus.review", fromlist=["get_tool_installer"]).get_tool_installer(),
         ["工具检测", "自动安装", "能力注册", "分类管理"]),
        ("task_orchestrator", "任务编排引擎", "orchestration",
         lambda: __import__("nucleus.review", fromlist=["get_task_orchestrator"]).get_task_orchestrator("capability"),
         ["任务分解", "依赖管理", "失败重试", "pipeline执行"]),
    ]

    for name, desc, category, init_func, caps in capabilities:
        framework.register_capability(name, desc, category, init_func, caps)

    # 初始化所有能力
    framework.initialize_all()

    _logger.info("默认能力集初始化完成（6个核心能力）")
    return framework
