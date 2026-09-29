# -*- coding: utf-8 -*-
"""
PulseReasoningFormatter —— 推理格式化辅助类

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 把各类规则推理产生的原始结果，格式化为可读的自然语言输出。
机制: 按推理类型分别提供静态方法：format_deductive_result（演绎）、format_inductive_result（归纳）、format_analogical_result（类比）、format_multi_variable_result（多变量）、format_conflict_result（冲突）、format_long_term_result（长期）、format_meta_replay_result（元复盘）、format_meta_reflection_result（元反思）、format_multi_condition_result（多条件）。
定位: P3 规则推理内部重构的产物，是纯格式化工具，不含任何推理决策逻辑。
"""
from __future__ import annotations

from typing import Any


class PulseReasoningFormatter:
    """PulseInnerWorld 推理格式化辅助类（纯函数式，无状态）"""

    @staticmethod
    def refresh_runtime_params():
        """★P1: 刷新运行时参数（热加载后调用）。当前无参数，预留扩展。"""


    @staticmethod
    def format_deductive_result(raw_result: str, question: str = "") -> str:
        """
        演绎推理标准化输出：确保包含完整的因果传递链和分步结论。
        输出结构：标题行/已知规则链/推导过程/串联推导结论/置信度说明
        """
        if "[演绎推理·即时推导]" in raw_result and "串联推导结论" in raw_result:
            return raw_result
        parts = [
            "[演绎推理·逻辑推导]",
            "",
            raw_result,
            "",
            "📌 置信度说明：演绎推理基于规则链的传递关系，结论的可靠程度取决于规则的完整性和共享概念的精准度。如果规则链中存在隐性的概念跳跃，结论需要进一步验证。",
        ]
        return "\n".join(parts)

    @staticmethod
    def format_inductive_result(raw_result: str, question: str = "") -> str:
        """
        归纳抽象标准化输出：确保包含样本分析、共同特征和归纳结论。
        输出结构：标题行/样本分析/共同特征/归纳结论/推广边界说明
        """
        if "[归纳升华·即时分析]" in raw_result:
            return raw_result
        parts = [
            "[归纳抽象·规律提炼]",
            "",
            raw_result,
            "",
            "📌 归纳边界说明：归纳结论的通用性取决于样本的覆盖范围和多样性。以上规律适用于当前样本集，推广到更广范围时需要更多样本验证。",
        ]
        return "\n".join(parts)

    @staticmethod
    def format_analogical_result(raw_result: str, question: str = "") -> str:
        """
        类比映射标准化输出：确保包含完整的维度对照和本质差异分析。
        输出结构：标题行/维度映射对照/共同概念/本质核心差异/迁移洞察
        """
        if "[类比迁移·多维映射]" in raw_result:
            return raw_result
        parts = [
            "[类比映射·跨领域对照]",
            "",
            raw_result,
        ]
        return "\n".join(parts)

    @staticmethod
    def format_multi_variable_result(raw_result: str, question: str = "") -> str:
        """
        多变量推演标准化输出：确保包含变量提取、行为清单和综合结论。
        输出结构：标题行/提取变量列表/行为推演/综合推演结论
        """
        if "[多变量推演]" in raw_result:
            return raw_result
        parts = [
            "[多变量推演·综合行为预测]",
            "",
            raw_result,
            "",
            "📌 推演说明：以上行为预测基于框架中已实现的系统规则和当前的变量状态。实际触发还受系统负载、外部交互等因素影响。",
        ]
        return "\n".join(parts)

    @staticmethod
    def format_conflict_result(raw_result: str, question: str = "") -> str:
        """
        冲突辨析标准化输出：强制三点固定结构。
        输出结构：场景判定/信任分调整/冲突跟踪流程
        """
        if "一、场景判定" in raw_result and "二、信任分调整" in raw_result and "三、冲突跟踪" in raw_result:
            return raw_result
        parts = [
            "[冲突辨析·标准化处理]",
            "",
            raw_result,
            "",
            "📌 冲突辨析说明：以上判定基于当前知识库中的信任分数和关键词重叠分析。如果后续有新证据出现，冲突判定可能需要重新评估。",
        ]
        return "\n".join(parts)

    @staticmethod
    def format_long_term_result(raw_result: str, question: str = "") -> str:
        """长期演化推演标准化输出。"""
        if "[长期演化推演]" in raw_result:
            return raw_result
        parts = [
            raw_result,
            "",
            "📌 推演声明：以上为基于当前框架运行机制的结构性趋势预测。实际演化路径受外部环境、交互密度、系统负载等多重因素影响，本推演给出的是方向性参考。",
        ]
        return "\n".join(parts)

    @staticmethod
    def format_meta_replay_result(raw_result: str, question: str = "") -> str:
        """推导元认知回放标准化输出。"""
        if "[推导元认知回放" in raw_result:
            return raw_result
        return raw_result

    @staticmethod
    def format_meta_reflection_result(raw_result: str, question: str = "") -> str:
        """元认知深度反思标准化输出。"""
        if "[元认知深度反思报告]" in raw_result:
            return raw_result
        return raw_result

    @staticmethod
    def format_multi_condition_result(results: list[dict[str, Any]]) -> str:
        """格式化多条件联立判断的结果"""
        positive_count = sum(1 for r in results if r["positive"])
        total = len(results)
        detail_parts = []
        for r in results:
            status = "✅" if r["positive"] else "❌"
            detail = r["result"] or "无法判断"
            detail_parts.append(f"{status} {r['condition']}: {detail}")
        detail_str = "；".join(detail_parts)
        if positive_count == total:
            return f"是的，所有{total}个条件都成立。{detail_str}。"
        if positive_count == 0:
            return f"不，所有{total}个条件都不成立。{detail_str}。"
        return f"{positive_count}/{total}个条件成立。{detail_str}。"
