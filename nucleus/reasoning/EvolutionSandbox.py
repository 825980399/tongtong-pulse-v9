# -*- coding: utf-8 -*-
"""
EvolutionSandbox.py —— 进化沙箱

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 进化代码变更的沙箱验证环境
机制: 基于EvolutionSandbox类实现，包含10个核心方法
定位: 进化验证层
"""

import threading
import time
from typing import Any

from config import DEFAULT_BENEFIT_SCORE as _DEF_BENEFIT_SCORE  # ★第55批 T1
from nucleus._silent_except import silent_exc


class EvolutionSandbox:
    """
    自我进化策略推演引擎。
    
    在"沙箱"中推演优化方案，评估可行性、影响范围和预期收益。
    推演结果为文本报告，供创造者参考，不自动执行。
    """
    
    def __init__(self):
        self._simulation_log: list[dict[str, Any]] = []
        self._max_log = 30
    
    def simulate(self, issues: list[dict[str, Any]], 
                 context: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        执行策略推演。
        
        Args:
            issues: 深度审视发现的问题列表
            context: 框架上下文信息（知识统计、资源使用等）
        
        Returns:
            推演报告字典
        """
        if not issues:
            return {
                "status": "no_issues",
                "summary": "未发现需要优化的问题，框架运行良好。",
                "plans": [],
                "recommendations": [],
                "overall_risk": "low",
            }
        
        plans = []
        recommendations = []
        total_benefit = 0
        total_risk = 0
        
        for issue in issues[:5]:  # 最多处理5个问题
            plan = self._generate_plan(issue, context)
            if plan:
                plans.append(plan)
                total_benefit += plan.get("benefit_score", 0)
                total_risk += plan.get("risk_score", 0)
        
        # 按优先级排序
        plans.sort(key=lambda p: p.get("priority_score", 0), reverse=True)
        
        # 生成总体建议
        if total_risk > total_benefit:
            recommendations.append("当前优化风险较高，建议优先积累更多知识后再进行优化。")
        elif len(plans) > 0:
            recommendations.append(f"共{len(plans)}个优化方案可执行，建议从优先级最高的开始逐步推进。")
        
        if any(p.get("type") == "code_optimization" for p in plans):
            recommendations.append("代码优化方案需创造者手动执行，框架不自动修改代码。")
        
        overall_risk = "high" if total_risk > total_benefit * 0.7 else "medium" if total_risk > total_benefit * 0.3 else "low"
        
        report = {
            "status": "completed",
            "summary": f"推演完成：{len(plans)}个优化方案，整体风险等级={overall_risk}。",
            "plans": plans,
            "recommendations": recommendations,
            "overall_risk": overall_risk,
            "overall_benefit": round(total_benefit, 1),
            "simulated_at": time.time(),
        }
        
        self._simulation_log.append(report)
        if len(self._simulation_log) > self._max_log:
            self._simulation_log = self._simulation_log[-self._max_log:]
        
        return report
    
    def _generate_plan(self, issue: dict[str, Any], 
                        context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """
        为单个问题生成优化方案并进行推演。
        
        Args:
            issue: 问题描述
            context: 框架上下文
        
        Returns:
            优化方案字典
        """
        issue_type = issue.get("type", "unknown")
        issue.get("description", "")
        
        # 根据问题类型生成不同的优化方案
        if "silent_exception" in issue_type:
            plan = self._plan_silent_exception_fix(issue)
        elif "lock_with_emit" in issue_type:
            plan = self._plan_lock_emit_fix(issue)
        elif "long_method" in issue_type:
            plan = self._plan_method_split(issue)
        elif "status_request_duplicate" in issue_type:
            plan = self._plan_dedup_status_request(issue)
        elif "bare_except" in issue_type:
            plan = self._plan_bare_except_fix(issue)
        else:
            plan = self._plan_generic(issue)
        
        if plan:
            # 推演影响
            plan["impact_assessment"] = self._assess_impact(plan, context)
            # 计算综合优先级
            plan["priority_score"] = round(
                plan.get("benefit_score", _DEF_BENEFIT_SCORE) * 0.6 - plan.get("risk_score", 3) * 0.4, 1
            )
            # ★Kimi 设计参考借鉴（§9.1/§12.3）：失败升级检查
            # 该位置同类型问题已连续失败达阈值 → 标记需人工/换策略，避免同一模式无限重试
            # （绝不原样重复同一失败动作）。零冲突：仅附加标记+后台日志，不改变方案生成逻辑。
            try:
                import os as _os

                from nucleus.logger import get_module_logger as _get_ml
                from nucleus.reasoning.FailureTracker import get_failure_tracker
                _ft = get_failure_tracker()
                _file = issue.get("file", "")
                _sig = _ft.build_signature(_file, issue_type)
                if _ft.should_escalate(_sig):
                    plan["needs_manual_review"] = True
                    plan["escalation_reason"] = (
                        f"该位置问题已连续失败 {_ft.get_failure_count(_sig)} 次，"
                        f"同模式自动修复无效，需换策略或人工介入"
                    )
                    _get_ml("进化沙箱").warning(
                        f"问题已升级需人工介入 "
                        f"(file={_os.path.basename(_file) or '未知'}, type={issue_type}, "
                        f"failures={_ft.get_failure_count(_sig)}): {plan['escalation_reason']}"
                    )
            except Exception as e:
                silent_exc(e, where="nucleus.reasoning.EvolutionSandbox::_generate_plan L154")
        
        return plan
    
    def _plan_silent_exception_fix(self, issue: dict[str, Any]) -> dict[str, Any]:
        """为静默异常捕获生成修复方案"""
        return {
            "type": "code_optimization",
            "target": issue.get("organ", "未知器官"),
            "method": issue.get("method", "未知方法"),
            "description": f"为 {issue.get('organ', '?')}.{issue.get('method', '?')} 中的静默异常添加日志记录",
            "action": "在 except 块中添加 self._log(LogLevel.ERROR, ...) 并限定具体异常类型",
            "benefit_score": 7,
            "risk_score": 1,
            "risk_description": "低风险：仅增加日志，不改变业务逻辑",
            "expected_benefit": "提升问题排查效率，减少静默失败",
        }
    
    def _plan_lock_emit_fix(self, issue: dict[str, Any]) -> dict[str, Any]:
        """为锁内发射脉冲生成修复方案"""
        return {
            "type": "code_optimization",
            "target": issue.get("organ", "未知器官"),
            "method": issue.get("method", "未知方法"),
            "description": f"将 {issue.get('organ', '?')}.{issue.get('method', '?')} 中的 self._emit 移至锁外",
            "action": "将 with self._lock: 代码块中的 self._emit 调用移到锁外执行",
            "benefit_score": 9,
            "risk_score": 4,
            "risk_description": "中等风险：移动代码可能影响执行时序，需仔细验证",
            "expected_benefit": "消除潜在死锁风险，提升系统稳定性",
        }
    
    def _plan_method_split(self, issue: dict[str, Any]) -> dict[str, Any]:
        """为过长方法生成拆分方案"""
        return {
            "type": "code_optimization",
            "target": issue.get("organ", "未知器官"),
            "method": issue.get("method", "未知方法"),
            "description": f"拆分 {issue.get('organ', '?')}.{issue.get('method', '?')} 为多个小方法",
            "action": "提取重复逻辑为独立私有方法，保持公共接口不变",
            "benefit_score": 5,
            "risk_score": 3,
            "risk_description": "中等风险：方法拆分可能引入新的调用问题，建议逐个拆分",
            "expected_benefit": "提升代码可读性和可维护性",
        }
    
    def _plan_dedup_status_request(self, issue: dict[str, Any]) -> dict[str, Any]:
        """为代码重复生成修复方案"""
        return {
            "type": "code_optimization",
            "target": issue.get("organ", "未知器官"),
            "method": "_on_status_request",
            "description": f"简化 {issue.get('organ', '?')}._on_status_request 为调用 self.get_stats()",
            "action": "将 _on_status_request 方法体替换为 return self.get_stats()",
            "benefit_score": 4,
            "risk_score": 1,
            "risk_description": "低风险：仅消除重复代码，不改变行为",
            "expected_benefit": "减少代码重复，降低维护成本",
        }
    
    def _plan_bare_except_fix(self, issue: dict[str, Any]) -> dict[str, Any]:
        """为裸except生成修复方案"""
        return {
            "type": "code_optimization",
            "target": issue.get("organ", "未知器官"),
            "method": issue.get("method", "未知方法"),
            "description": f"将 {issue.get('organ', '?')}.{issue.get('method', '?')} 中的裸 except: 改为具体异常类型",
            "action": "将 except: 改为 except Exception: 并添加日志记录",
            "benefit_score": 6,
            "risk_score": 1,
            "risk_description": "低风险：仅规范异常捕获，不改变处理逻辑",
            "expected_benefit": "避免捕获系统级异常，提升异常处理的精确性",
        }
    
    def _plan_generic(self, issue: dict[str, Any]) -> dict[str, Any]:
        """为未知类型问题生成通用方案"""
        return {
            "type": "general_review",
            "target": issue.get("organ", "未知"),
            "method": issue.get("method", ""),
            "description": f"手动审查 {issue.get('organ', '?')} 中的 {issue.get('type', '未知')} 问题",
            "action": "建议创造者手动审查该位置代码",
            "benefit_score": 3,
            "risk_score": 1,
            "risk_description": "低风险：仅审查不修改",
            "expected_benefit": "发现潜在问题",
        }
    
    def _assess_impact(self, plan: dict[str, Any], 
                        context: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        推演方案执行后的影响。
        
        评估维度：
        1. 功能影响——是否改变现有功能行为
        2. 性能影响——是否影响框架运行性能
        3. 兼容性——是否影响其他器官
        4. 可逆性——执行后是否容易回滚
        """
        plan_type = plan.get("type", "general_review")
        
        if plan_type == "code_optimization":
            return {
                "function_impact": "低——不改变业务逻辑",
                "performance_impact": "无显著影响",
                "compatibility": "高——修改限于单个方法内部",
                "reversibility": "高——可通过代码回滚恢复",
            }
        elif plan_type == "general_review":
            return {
                "function_impact": "无——仅审查不修改",
                "performance_impact": "无影响",
                "compatibility": "不适用",
                "reversibility": "不适用",
            }
        else:
            return {
                "function_impact": "未知——需进一步评估",
                "performance_impact": "未知",
                "compatibility": "未知",
                "reversibility": "中等",
            }
    
    def get_stats(self) -> dict[str, Any]:
        """获取推演统计"""
        return {
            "total_simulations": len(self._simulation_log),
            "last_simulation": self._simulation_log[-1].get("simulated_at", 0) if self._simulation_log else 0,
        }


# 模块级单例

_evolution_sandbox: EvolutionSandbox | None = None
_evolution_sandbox_lock = threading.Lock()


def get_evolution_sandbox() -> EvolutionSandbox:
    """获取EvolutionSandbox单例"""
    global _evolution_sandbox
    if _evolution_sandbox is None:
        with _evolution_sandbox_lock:
            if _evolution_sandbox is None:
                _evolution_sandbox = EvolutionSandbox()
    return _evolution_sandbox


def shutdown_evolution_sandbox() -> None:
    """★P1: 复位 EvolutionSandbox 单例，满足器官零状态（规则4）。"""
    global _evolution_sandbox
    _inst = _evolution_sandbox
    _evolution_sandbox = None
    if _inst is not None:
        _sd = getattr(_inst, "shutdown", None) or getattr(_inst, "stop", None)
        if _sd is not None:
            try:
                _sd()
            except Exception as e:
                silent_exc(e, where="nucleus.reasoning.EvolutionSandbox::shutdown_evolution_sandbox L312")
