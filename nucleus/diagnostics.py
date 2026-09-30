# -*- coding: utf-8 -*-
"""
diagnostics.py —— 诊断工具集

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 框架运行时诊断与问题定位工具
机制: 基于FrameworkDiagnostics类实现，包含10个核心方法
定位: 运维支撑层
"""

import threading
import time
from typing import Any
from nucleus._silent_except import silent_exc



class FrameworkDiagnostics:
    """
    框架内部诊断器。
    
    提供统一的健康检查接口，让曈曈能审视自己的运行状态。
    不修改框架状态，只提供只读的诊断信息。
    """
    
    def __init__(self):
        self._last_diagnosis_time = 0.0
        self._diagnosis_cache = {}
        self._cache_ttl = 30.0  # 缓存30秒
    
    def get_full_diagnosis(self, info_field=None, node_pool=None, 
                           framework=None) -> dict[str, Any]:
        """
        获取完整的框架健康诊断报告。
        
        Args:
            info_field: InfoField实例（可选，用于脉冲统计）
            node_pool: PulseNodePool实例（可选，用于知识演化统计）
            framework: PulseFramework实例（可选，用于器官状态统计）
        
        Returns:
            结构化诊断报告
        """
        now = time.time()
        if now - self._last_diagnosis_time < self._cache_ttl and self._diagnosis_cache:
            return self._diagnosis_cache
        
        diagnosis = {
            "timestamp": now,
            "overall_health": "healthy",
            "issues": [],
            "warnings": [],
            "info": [],
            "metrics": {},
        }
        
        # 1. 脉冲通信健康度
        if info_field:
            try:
                field_stats = info_field.get_stats()
                diagnosis["metrics"]["pulse"] = {
                    "total_published": field_stats.get("total_published", 0),
                    "total_matched": field_stats.get("total_matched", 0),
                    "active_conditions": field_stats.get("active_conditions", 0),
                    "in_storm": field_stats.get("in_storm", False),
                    "high_load": field_stats.get("high_load", False),
                    "load_level": field_stats.get("load_level", "unknown"),
                    "cpu_usage": field_stats.get("cpu_usage", 0.0),
                    "mem_usage": field_stats.get("mem_usage", 0.0),
                    "gpu_usage": field_stats.get("gpu_usage", 0.0),
                }
                
                # 检查脉冲风暴
                if field_stats.get("in_storm"):
                    diagnosis["issues"].append("脉冲风暴正在进行中")
                    diagnosis["overall_health"] = "warning"
                
                # 检查高负载
                if field_stats.get("high_load"):
                    diagnosis["warnings"].append(f"系统负载偏高: CPU={field_stats.get('cpu_usage', 0):.1f}%, MEM={field_stats.get('mem_usage', 0):.1f}%")
                    
            except Exception as e:
                diagnosis["issues"].append(f"无法获取脉冲统计: {e}")
        
        # 2. 知识演化健康度
        if node_pool:
            try:
                pool_stats = node_pool.get_stats()
                evol_dist = pool_stats.get("evol_distribution", {})
                diagnosis["metrics"]["knowledge"] = {
                    "total_nodes": pool_stats.get("total_nodes", 0),
                    "L1_count": evol_dist.get("L1", 0),
                    "L2_count": evol_dist.get("L2", 0),
                    "L3_count": evol_dist.get("L3", 0),
                    "instinct_count": pool_stats.get("instinct_count", 0),
                }
                
                # 检查L1堆积
                l1_count = evol_dist.get("L1", 0)
                total = pool_stats.get("total_nodes", 1)
                if total > 50 and l1_count / total > 0.8:
                    diagnosis["warnings"].append(f"L1节点占比过高({l1_count/total:.0%})，可能需要触发压缩")
                
                # 检查L3节点过少
                l3_count = evol_dist.get("L3", 0)
                if total > 100 and l3_count < 3:
                    diagnosis["warnings"].append("L3智慧节点较少，知识深度有待提升")
                    
            except Exception as e:
                diagnosis["warnings"].append(f"无法获取知识统计: {e}")
        
        # 3. 器官健康度
        if framework and hasattr(framework, 'organs'):
            try:
                fused_organs = []
                for organ_name, organ in framework.organs.items():
                    if organ and hasattr(organ, 'status'):
                        if getattr(organ, 'status', '') == 'fused':
                            fused_organs.append(organ_name)
                
                diagnosis["metrics"]["organs"] = {
                    "total": len(framework.organs),
                    "fused": len(fused_organs),
                    "fused_list": fused_organs,
                }
                
                if fused_organs:
                    diagnosis["issues"].append(f"器官熔断: {', '.join(fused_organs)}")
                    diagnosis["overall_health"] = "critical"
                    
            except Exception as e:
                diagnosis["warnings"].append(f"无法获取器官状态: {e}")
        
        # 4. 外部操作健康度
        try:
            from nucleus.external_executor import get_external_executor
            executor = get_external_executor()
            exec_stats = executor.get_stats()
            diagnosis["metrics"]["external_ops"] = {
                "total_submitted": exec_stats.get("total_submitted", 0),
                "total_completed": exec_stats.get("total_completed", 0),
                "total_failed": exec_stats.get("total_failed", 0),
                "active_count": exec_stats.get("active_count", 0),
                "queue_size": exec_stats.get("queue_size", 0),
            }
            
            # 检查大量失败
            failure_rate = exec_stats.get("total_failed", 0) / max(1, exec_stats.get("total_submitted", 1))
            if failure_rate > 0.5 and exec_stats.get("total_submitted", 0) > 5:
                diagnosis["warnings"].append(f"外部操作失败率偏高({failure_rate:.0%})，可能网络存在问题")
                
        except Exception as e:
            silent_exc(e, "nucleus/diagnostics.py:154:诊断操作异常", level="warning")
        
        # 5. 整体健康判定
        if not diagnosis["issues"]:
            if diagnosis["warnings"]:
                diagnosis["overall_health"] = "warning"
            else:
                diagnosis["overall_health"] = "healthy"
        elif any("熔断" in issue for issue in diagnosis["issues"]):
            diagnosis["overall_health"] = "critical"
        else:
            diagnosis["overall_health"] = "warning"

        # ★F5：量化健康分 + 组件可用率 + 异常告警阈值
        _health_score, _availability = self._compute_health_score(diagnosis)
        diagnosis["health_score"] = _health_score
        diagnosis["health_level"] = self._health_level(_health_score)  # ★F5收尾：颜色分级标签
        diagnosis["component_availability"] = _availability
        diagnosis["alert_thresholds"] = self._get_alert_thresholds()

        # 缓存诊断结果
        self._diagnosis_cache = diagnosis
        self._last_diagnosis_time = now

        return diagnosis

    def _compute_health_score(self, diagnosis: dict[str, Any]) -> tuple[float, dict[str, float]]:
        """
        ★F5：把离散的 overall_health 标签量化为 0-100 的健康分，
        并计算各组件可用率，供统一监控与告警阈值判定。

        评分规则（从 100 分起扣）：
        - critical 熔断：每个 issue 扣 25 分
        - 普通 issue：每个扣 15 分
        - warning：每个扣 5 分
        - 脉冲风暴：额外扣 10 分
        - 高负载：额外扣 8 分
        下限 0，上限 100。
        """
        _score = 100.0
        _issues = diagnosis.get("issues", [])
        _warnings = diagnosis.get("warnings", [])
        _metrics = diagnosis.get("metrics", {})

        for _issue in _issues:
            if "熔断" in str(_issue):
                _score -= 25.0
            else:
                _score -= 15.0
        _score -= len(_warnings) * 5.0

        _pulse = _metrics.get("pulse", {})
        if _pulse.get("in_storm"):
            _score -= 10.0
        if _pulse.get("high_load"):
            _score -= 8.0

        _score = max(0.0, min(100.0, _score))

        # 组件可用率
        _availability: dict[str, float] = {}
        _organs = _metrics.get("organs", {})
        _total_organs = _organs.get("total", 0)
        if _total_organs > 0:
            _fused = _organs.get("fused", 0)
            _availability["organs"] = round((_total_organs - _fused) / _total_organs, 4)
        _ext = _metrics.get("external_ops", {})
        _submitted = _ext.get("total_submitted", 0)
        if _submitted > 0:
            _failed = _ext.get("total_failed", 0)
            _availability["external_ops"] = round((_submitted - _failed) / _submitted, 4)

        return round(_score, 1), _availability

    # ---------- ★主线第51批 T2（P0-4）：质量分 v2（只读，并行运行） ----------

    def get_quality_score_v2(self, record_trend: bool = False) -> dict:
        """质量分 **v2**：衡量「系统有效性」，每维度可证伪。

        与 ``_compute_health_score``（扣分制，衡量"运行时刻的组件健康"）**并行**，
        不是替代关系。v2 的五个维度（详见
        ``nucleus/self_awareness/quality_score_v2.py``）：

        ====================== ====== ====================================
        维度                     权重   公式
        ====================== ====== ====================================
        module_effectiveness    0.30   50×报告消费率 + 50×补丁真实修复率
        issue_severity          0.25   max(0, 100 − (P0×10 + P1×4 + P2×1))
        test_coverage           0.20   器官测试覆盖率 × 100
        static_health           0.15   max(0, 100 − ruffF×5)
        data_integrity          0.10   100×(1 − 0.5×污染率)×标记完整度
        ====================== ====== ====================================

        ★**不惩罚「不知道」**：维度数据不可用 → ``score=None``，不参与加权。

        Args:
            record_trend: 是否追加一次趋势记录（默认 ``False`` = 纯只读）。

        Returns:
            ``{score, dimensions, weights, available, reason, version}``
        """
        try:
            from nucleus.self_awareness.quality_score_v2 import (
                evaluate_v2, record_trend as _m51_rec)
            _r = evaluate_v2()
            if record_trend:
                _m51_rec(_r)
            return _r
        except Exception as _e:      # 只读入口，异常不得冒泡
            return {"score": None, "dimensions": {}, "available": [],
                    "error": "%s: %s" % (type(_e).__name__, _e)}

    @staticmethod
    def _get_alert_thresholds() -> dict[str, Any]:
        """
        ★F5：统一异常告警阈值，供监控面板与自检使用。
        ★F5收尾：补齐绿/黄/橙/红四档分级阈值（≥90绿/70-89黄/50-69橙/<50红）。
        """
        return {
            "health_score_critical": 40.0,   # 健康分低于此值 → 严重告警（红）
            "health_score_warning": 70.0,    # 健康分低于此值 → 一般告警（黄）
            "health_score_green": 90.0,      # ★F5收尾：≥90 绿色（健康）
            "health_score_yellow": 70.0,     # ★F5收尾：70-89 黄色（关注）
            "health_score_orange": 50.0,     # ★F5收尾：50-69 橙色（警告）
            "health_score_red": 50.0,        # ★F5收尾：<50 红色（严重）
            "org_availability_critical": 0.8,  # 器官可用率低于此值 → 告警
            "external_op_failure_rate": 0.5,   # 外部操作失败率高于此值 → 告警
            "l1_ratio_high": 0.8,            # L1 节点占比高于此值 → 提示压缩
        }

    @staticmethod
    def _health_level(score: float) -> str:
        """
        ★F5收尾：健康分 → 颜色分级标签。
        阈值：≥90 绿 / 70-89 黄 / 50-69 橙 / <50 红。
        """
        if score >= 90.0:
            return "green"
        if score >= 70.0:
            return "yellow"
        if score >= 50.0:
            return "orange"
        return "red"
    def get_extended_diagnosis(self, inner_world=None, subconscious=None,
                                insight_board=None, autonomous_deriver=None) -> dict[str, Any]:
        """
        扩展诊断：覆盖本轮新增模块的健康检查。
        
        诊断项：
        1. InsightBoard 状态
        2. 对话记忆库状态
        3. 自主推导引擎状态
        4. 代码理解进度
        5. 知识验证统计
        6. 长期目标状态
        7. 矛盾跟踪状态
        """
        results = {
            "overall_health": "healthy",
            "issues": [],
            "warnings": [],
            "modules": {},
        }
        
        # 1. InsightBoard 状态
        if insight_board:
            try:
                board_stats = insight_board.get_stats()
                results["modules"]["insight_board"] = {
                    "status": "active",
                    "total_entries": board_stats.get("total_entries", 0),
                    "active_entries": board_stats.get("active_entries", 0),
                }
                if board_stats.get("active_entries", 0) == 0 and board_stats.get("total_entries", 0) > 0:
                    results["warnings"].append("洞察黑板无活跃条目（所有洞察已过期）")
            except Exception as e:
                results["modules"]["insight_board"] = {"status": "error", "error": str(e)[:80]}
                results["issues"].append(f"洞察黑板检查失败: {str(e)[:60]}")
        else:
            results["modules"]["insight_board"] = {"status": "not_injected"}
        
        # 2. 对话记忆库状态
        if inner_world:
            try:
                mem_count = len(inner_world.get_conversation_memory())
                _mem_limit = inner_world.get_memory_limits().get("max_conversation_memory", 30)
                results["modules"]["conversation_memory"] = {
                    "status": "active",
                    "total_memories": mem_count,
                }
                if mem_count >= _mem_limit * 0.9:
                    results["warnings"].append(f"对话记忆库接近上限({mem_count}/{_mem_limit})")
            except Exception as e:
                results["modules"]["conversation_memory"] = {"status": "error", "error": str(e)[:80]}
        else:
            results["modules"]["conversation_memory"] = {"status": "not_initialized"}
        
        # 3. 自主推导引擎状态
        if autonomous_deriver:
            try:
                deriver_stats = autonomous_deriver.get_stats()
                results["modules"]["autonomous_deriver"] = {
                    "status": "active",
                    "total_derivations": deriver_stats.get("total_derivations", 0),
                    "types": deriver_stats.get("type_distribution", {}),
                }
            except Exception as e:
                results["modules"]["autonomous_deriver"] = {"status": "error", "error": str(e)[:80]}
        else:
            results["modules"]["autonomous_deriver"] = {"status": "not_injected"}
        
        # 4. 代码理解进度
        if inner_world:
            try:
                progress = inner_world.get_code_understanding_progress()
                understood = progress.get("understood", 0)
                total = progress.get("total_methods", 0)
                pct = int(understood / total * 100) if total > 0 else 0
                results["modules"]["code_understanding"] = {
                    "status": "in_progress" if understood < total else "completed",
                    "progress": f"{understood}/{total} ({pct}%)",
                }
            except Exception as e:
                results["modules"]["code_understanding"] = {"status": "error", "error": str(e)[:80]}
        else:
            results["modules"]["code_understanding"] = {"status": "not_initialized"}
        
        # 5. 知识验证统计
        if inner_world:
            try:
                all_contradictions = inner_world.get_contradiction_tracking()
                active = [t for t in all_contradictions if not t.get("resolved", False)]
                results["modules"]["knowledge_verification"] = {
                    "status": "active",
                    "total_contradictions_tracked": len(all_contradictions),
                    "active_contradictions": len(active),
                }
                if len(active) > 5:
                    results["warnings"].append(f"存在{len(active)}对未解决的知识矛盾")
            except Exception as e:
                results["modules"]["knowledge_verification"] = {"status": "error", "error": str(e)[:80]}
        else:
            results["modules"]["knowledge_verification"] = {"status": "not_initialized"}
        
        # 6. 长期目标状态
        if inner_world:
            try:
                goal = inner_world.get_active_learning_goal()
                if goal:
                    target = goal.get("target_area", "")
                    started = goal.get("started_at", 0)
                    hours = (time.time() - started) / 3600 if started > 0 else 0
                    results["modules"]["active_learning_goal"] = {
                        "status": "active",
                        "target": target,
                        "duration_hours": round(hours, 1),
                    }
                    if hours > 24:
                        results["warnings"].append(f"学习目标「{target}」已持续{hours:.0f}小时，建议检查是否需要调整")
                else:
                    results["modules"]["active_learning_goal"] = {"status": "idle"}
            except Exception as e:
                results["modules"]["active_learning_goal"] = {"status": "error", "error": str(e)[:80]}
        else:
            results["modules"]["active_learning_goal"] = {"status": "not_initialized"}
        
        # 7. 搜索经验库状态
        if inner_world:
            try:
                exp_count = len(inner_world.get_search_experience_all())
                _exp_limit = inner_world.get_memory_limits().get("search_experience_max", 100)
                results["modules"]["search_experience"] = {
                    "status": "active",
                    "total_entries": exp_count,
                }
                if exp_count >= _exp_limit * 0.9:
                    results["warnings"].append(f"搜索经验库接近上限({exp_count}/{_exp_limit})")
            except Exception as e:
                results["modules"]["search_experience"] = {"status": "error", "error": str(e)[:80]}
        else:
            results["modules"]["search_experience"] = {"status": "not_initialized"}
        
        # 汇总判定
        if results["issues"]:
            results["overall_health"] = "critical"
        elif len(results["warnings"]) >= 3:
            results["overall_health"] = "warning"
        
        return results    
    def get_health_summary(self, diagnosis: dict[str, Any] | None = None, 
                           info_field=None, node_pool=None, framework=None) -> str:
        """
        生成人类可读的健康摘要。
        
        Args:
            diagnosis: 已有的诊断报告（可选，为None则重新诊断）
            info_field, node_pool, framework: 同get_full_diagnosis
        
        Returns:
            健康摘要文本
        """
        if diagnosis is None:
            diagnosis = self.get_full_diagnosis(info_field, node_pool, framework)

        # ★主线第50批 T1（P0-1）：健康报告 → ReportBus。
        #   订阅者：health_anomaly_consumer（P0 异常 → 告警并标记需人工介入）。
        #   发布失败不得影响摘要生成。
        try:
            from nucleus.reporting.publishers import publish_health as _m50_ph
            _m50_ph(diagnosis, generator="FrameworkDiagnostics.get_health_summary")
        except Exception as _m50_he:
            # ★本模块无模块级 logger → 惰性取（避免新增全局依赖）
            try:
                from nucleus.logger import get_module_logger as _m50_gml
                _m50_gml("Diagnostics").debug(
                    "[M50-T1] 健康报告发布失败（已忽略）: %s",
                    type(_m50_he).__name__)
            except Exception:
                pass

        parts = []
        health = diagnosis.get("overall_health", "unknown")
        metrics = diagnosis.get("metrics", {})
        
        # 总体状态
        if health == "healthy":
            parts.append("我目前运行状态良好。")
        elif health == "warning":
            parts.append("我有一些需要注意的地方——")
        elif health == "critical":
            parts.append("我遇到了一些严重问题，需要关注——")
        
        # 知识状态
        knowledge = metrics.get("knowledge", {})
        if knowledge:
            total = knowledge.get("total_nodes", 0)
            l2 = knowledge.get("L2_count", 0)
            l3 = knowledge.get("L3_count", 0)
            instinct = knowledge.get("instinct_count", 0)
            parts.append(f"知识体系共有{total}个节点，其中{l2}条认知、{l3}条智慧、{instinct}条本能。")
        
        # 脉冲通信
        pulse = metrics.get("pulse", {})
        if pulse:
            active_organs = pulse.get("active_conditions", 0)
            load = pulse.get("load_level", "unknown")
            parts.append(f"当前有{active_organs}个活跃条件，系统负载为{load}级别。")
        
        # 器官状态
        organs = metrics.get("organs", {})
        if organs:
            total_organs = organs.get("total", 0)
            fused = organs.get("fused", 0)
            if fused > 0:
                parts.append(f"{total_organs}个器官中，{fused}个出现熔断。")
            else:
                parts.append(f"所有{total_organs}个器官运行正常。")
        
        # 外部操作
        external = metrics.get("external_ops", {})
        if external:
            completed = external.get("total_completed", 0)
            active = external.get("active_count", 0)
            parts.append(f"已完成{completed}次外部操作，当前有{active}个在执行。")
        
        # 问题汇总
        issues = diagnosis.get("issues", [])
        warnings = diagnosis.get("warnings", [])
        if issues:
            parts.append("发现的问题：" + "；".join(issues[:3]))
        elif warnings:
            parts.append("需要注意：" + "；".join(warnings[:3]))
        
        return "".join(parts)
    
    def get_specific_metric(self, metric_name: str, info_field=None, 
                           node_pool=None, framework=None) -> Any | None:
        """获取特定指标的当前值"""
        diagnosis = self.get_full_diagnosis(info_field, node_pool, framework)
        metrics = diagnosis.get("metrics", {})
        
        # 支持嵌套查询，如 "knowledge.total_nodes" 或 "pulse.load_level"
        parts = metric_name.split(".")
        value = metrics
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None
        return value


# ========== 模块级单例 ==========

_diagnostics: FrameworkDiagnostics | None = None
_diagnostics_lock = threading.Lock()


def get_diagnostics() -> FrameworkDiagnostics:
    """获取FrameworkDiagnostics单例"""
    global _diagnostics
    if _diagnostics is None:
        with _diagnostics_lock:
            if _diagnostics is None:
                _diagnostics = FrameworkDiagnostics()
    return _diagnostics


def shutdown_diagnostics() -> None:
    """★P1: 复位 FrameworkDiagnostics 单例，满足器官零状态（规则4）。"""
    global _diagnostics
    _inst = _diagnostics
    _diagnostics = None
    if _inst is not None:
        _sd = getattr(_inst, "shutdown", None) or getattr(_inst, "stop", None)
        if _sd is not None:
            try:
                _sd()
            except Exception as e:
                silent_exc(e, where="nucleus.diagnostics::shutdown_diagnostics L574")
# _m50_t1_diag_done

# _m51_t2_wire