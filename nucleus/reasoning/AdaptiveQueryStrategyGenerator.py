# -*- coding: utf-8 -*-
"""
AdaptiveQueryStrategyGenerator.py —— 自适应查询策略生成器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 动态生成知识查询策略
机制: 基于AdaptiveQueryStrategyGenerator类实现，包含10个核心方法
定位: 推理检索层
"""

from __future__ import annotations

import hashlib
import time
from typing import Any
from config import DEFAULT_BENEFIT_SCORE as _DEF_BENEFIT_SCORE  # ★第55批 T1



class AdaptiveQueryStrategyGenerator:
    """自适应询问策略生成器——让框架学会"如何更好地向大模型提问"。"""

    def __init__(self):
        # 可复用询问模式库（从成功经验中沉淀）
        self._pattern_library: list[dict[str, Any]] = []
        # 策略效果历史（用于元学习）
        self._strategy_history: list[dict[str, Any]] = []
        # 元学习参数（自动调整）
        self._meta_params = {
            "context_ratio": 0.6,       # 上下文在prompt中的占比目标
            "analysis_depth": 0.5,      # 自我分析深度（0-1）
            "followup_threshold": 0.4,  # 触发追问的质量阈值
            "creativity_bias": 0.3,     # 创造性偏差（鼓励非标准问法）
        }

    def extract_issue_features(self, issue: dict[str, Any], context: str = "",
                                prior_attempts: list[dict] | None = None) -> dict[str, Any]:
        """★问题特征提取：从问题中提取关键特征，用于策略生成。"""
        _type = issue.get("type", "unknown")
        _desc = issue.get("description", "")
        _file = issue.get("file", "")
        _method = issue.get("method", "")
        _risk = issue.get("risk_score", 2)
        _benefit = issue.get("benefit_score", _DEF_BENEFIT_SCORE)

        # 复杂度评估（多维度）
        _complexity_signals = {
            "has_traceback": bool(issue.get("traceback")),
            "has_logs": bool(context),
            "multi_file": "、" in _file or "," in _file,
            "high_risk": _risk >= 3,
            "prior_failures": len(prior_attempts or []),
            "desc_length": len(_desc),
            "type_known": _type not in {"unknown", ""},
        }
        _complexity_score = sum([
            _complexity_signals["has_traceback"] * 0.2,
            _complexity_signals["has_logs"] * 0.1,
            _complexity_signals["multi_file"] * 0.2,
            _complexity_signals["high_risk"] * 0.15,
            min(_complexity_signals["prior_failures"], 3) * 0.1,
            min(_complexity_signals["desc_length"] / 500, 0.25),
        ])

        # 问题分类（不写死，基于特征动态推断）
        if any(k in _desc.lower() for k in ["超时", "timeout", "hang", "卡住"]):
            _category = "timeout_issue"
        elif any(k in _desc.lower() for k in ["异常", "exception", "error", "崩溃", "crash"]):
            _category = "exception_issue"
        elif any(k in _desc.lower() for k in ["性能", "performance", "慢", "优化", "optimize"]):
            _category = "performance_issue"
        elif any(k in _desc.lower() for k in ["架构", "设计", "重构", "architecture", "design"]):
            _category = "architecture_issue"
        elif any(k in _desc.lower() for k in ["竞态", "死锁", "race", "deadlock", "并发"]):
            _category = "concurrency_issue"
        elif _complexity_score > 0.6:
            _category = "complex_general"
        else:
            _category = "general_issue"

        return {
            "type": _type,
            "category": _category,
            "complexity": min(1.0, _complexity_score),
            "risk": _risk,
            "benefit": _benefit,
            "has_context": bool(context),
            "prior_attempts": len(prior_attempts or []),
            "signals": _complexity_signals,
        }

    def generate_strategy(self, features: dict[str, Any], local_analysis: str = "",
                           code_snippet: str = "", related_logs: str = "") -> dict[str, Any]:
        """★策略生成：基于问题特征动态组装询问策略，不写死模板。

        策略组成要素（动态选择和组合）：
        - 开场方式：直接提问/带着分析请教/请求验证/请求深度指导
        - 信息呈现：代码优先/日志优先/现象优先/分析优先
        - 提问结构：单问/分步问/对比问/追问式
        - 期望输出：代码-only/分析+代码/方案对比/教学式
        """
        _complexity = features["complexity"]
        _category = features["category"]
        _prior = features["prior_attempts"]

        # ★动态选择开场方式（基于复杂度和历史）
        if _prior > 0:
            _opening = "followup"  # 之前问过，现在追问
        elif _complexity > 0.7:
            _opening = "deep_guidance"  # 复杂问题，请求深度指导
        elif local_analysis and _complexity > 0.3:
            _opening = "verify_my_analysis"  # 有自己的分析，请求验证
        elif _complexity < 0.3:
            _opening = "direct_question"  # 简单问题，直接问
        else:
            _opening = "guided_question"  # 中等问题，引导式提问

        # ★动态选择信息呈现顺序
        _info_order = []
        if features["has_context"] and _complexity > 0.4:
            _info_order.append("logs_first")  # 有日志且复杂，日志优先
        if code_snippet:
            _info_order.append("code")
        if local_analysis:
            _info_order.append("my_analysis")
        if not _info_order:
            _info_order.append("description")

        # ★动态选择提问结构
        if _complexity > 0.7:
            _structure = "step_by_step"  # 复杂问题分步问
        elif _prior > 0:
            _structure = "targeted_followup"  # 追问式
        elif _category == "performance_issue":
            _structure = "compare_options"  # 性能问题对比方案
        else:
            _structure = "single_focused"  # 单一焦点

        # ★动态选择期望输出
        if _complexity > 0.7:
            _expected_output = "analysis_then_code"  # 先分析再给代码
        elif _category == "architecture_issue":
            _expected_output = "options_comparison"  # 方案对比
        else:
            _expected_output = "code_with_explanation"  # 代码+简要解释

        # ★动态决定是否需要多轮追问
        _max_followups = 0
        if _complexity > 0.7:
            _max_followups = 2
        elif _complexity > 0.4 or _prior > 0:
            _max_followups = 1

        # ★组装策略（创造性组合，不写死完整模板）
        _strategy = {
            "opening": _opening,
            "info_order": _info_order,
            "structure": _structure,
            "expected_output": _expected_output,
            "max_followups": _max_followups,
            "complexity": _complexity,
            "category": _category,
            # 元数据：用于效果评估和策略沉淀
            "strategy_id": hashlib.md5(
                f"{_opening}|{_structure}|{_expected_output}|{_category}".encode()
            ).hexdigest()[:12],
            "generated_at": time.time(),
        }
        return _strategy

    def build_prompt(self, strategy: dict[str, Any], issue: dict[str, Any],
                     local_analysis: str = "", code_snippet: str = "",
                     related_logs: str = "", prior_answer: str = "") -> str:
        """★根据策略动态组装prompt——不写死模板，用策略要素创造性组合。"""
        _parts = []
        _type = issue.get("type", "")
        _desc = issue.get("description", "")

        # 开场（根据opening策略）
        if strategy["opening"] == "followup":
            _parts.append("关于上一个问题，我还有疑问需要进一步请教。")
            if prior_answer:
                _parts.append(f"\n你之前的回答是：\n{prior_answer[:800]}\n")
        elif strategy["opening"] == "deep_guidance":
            _parts.append("我遇到一个比较复杂的问题，需要你的深度指导。请帮我系统性地分析。")
        elif strategy["opening"] == "verify_my_analysis":
            _parts.append("我对这个问题做了初步分析，想请你帮我确认分析是否正确，并给出更完善的方案。")
        elif strategy["opening"] == "direct_question":
            _parts.append("请帮我解决以下代码问题：")
        else:
            _parts.append("我遇到一个代码问题，请帮我分析和解决。")

        # 问题基本信息
        _parts.append(f"\n问题类型: {_type}")
        _parts.append(f"问题描述: {_desc}")

        # 信息呈现（按info_order动态排序）
        for _info_type in strategy["info_order"]:
            if _info_type == "logs_first" and related_logs:
                _parts.append(f"\n相关运行日志:\n```\n{related_logs[:1500]}\n```")
            elif _info_type == "code" and code_snippet:
                _parts.append(f"\n相关代码:\n```python\n{code_snippet[:2000]}\n```")
            elif _info_type == "my_analysis" and local_analysis:
                _parts.append(f"\n我的初步分析:\n{local_analysis}")
            elif _info_type == "description":
                pass  # 已在上面

        # 提问结构（根据structure策略）
        if strategy["structure"] == "step_by_step":
            _parts.append("\n请按以下步骤指导我：\n1. 分析根本原因（不要只看表面）\n2. 评估可能的修复方案及其权衡\n3. 给出推荐方案的完整修复代码\n4. 说明验证方法")
        elif strategy["structure"] == "targeted_followup":
            _parts.append("\n请针对以下点进一步说明：\n1. 修复的根因是否准确？\n2. 是否有副作用或边界情况？\n3. 请给出更完整的修复代码。")
        elif strategy["structure"] == "compare_options":
            _parts.append("\n请对比分析：\n1. 有哪些可能的优化方案？\n2. 各方案的性能/复杂度/风险权衡？\n3. 推荐方案及完整实现。")
        elif strategy["expected_output"] == "analysis_then_code":
            _parts.append("\n请先分析根本原因，再给出完整修复代码。")
        elif strategy["expected_output"] == "options_comparison":
            _parts.append("\n请给出2-3个方案对比，说明各自优缺点，最后推荐一个。")
        else:
            _parts.append("\n请给出修复代码，并简要说明关键改动点。")

        # ★主线第15批 T5/P2-96：硬性要求只输出 ASCII 补丁代码。
        #   实测 LLM 生成的补丁混入中文标点（「」/。/→ 等）导致语法错误 22 次。
        #   此处在提示词层面先掐断源头（代码层另有 _clean_llm_code 双重兜底）。
        _parts.append(
            "\n输出要求（硬性）：若需要给出代码补丁，代码部分**只使用 ASCII 字符**，"
            "不要使用任何中文/全角标点（如「」 。 、 → 《》等），"
            "标点一律用半角 , . ; : ( ) [ ] { } - > ；中文只允许出现在字符串字面量或注释里。")
        return "\n".join(_parts)

    def evaluate_answer_quality(self, answer: str, strategy: dict[str, Any],
                                 issue: dict[str, Any]) -> dict[str, Any]:
        """★效果评估：评估大模型回答的质量，用于策略优化。"""
        _score = 0.0
        _reasons = []

        # 长度评估
        if len(answer) > 50:
            _score += 0.2
        else:
            _reasons.append("回答过短")

        # 包含代码
        if "```" in answer or "def " in answer or "class " in answer:
            _score += 0.3
        else:
            _reasons.append("缺少代码")

        # 包含分析
        if any(k in answer for k in ["原因", "因为", "根因", "分析", "建议", "应该"]):
            _score += 0.2
        else:
            _reasons.append("缺少分析")

        # 针对复杂问题，评估是否有结构
        if strategy["complexity"] > 0.6:
            if any(k in answer for k in ["1.", "2.", "3.", "首先", "其次", "最后"]):
                _score += 0.2
            else:
                _reasons.append("复杂问题但回答缺乏结构")

        # 针对追问，评估是否回应了追问点
        if strategy["opening"] == "followup":
            if len(answer) > 200:
                _score += 0.1
            else:
                _reasons.append("追问回答不够详细")

        _score = min(1.0, _score)
        return {
            "score": _score,
            "reasons": _reasons,
            "needs_followup": _score < strategy.get("followup_threshold", 0.4),
            "followup_reason": _reasons[0] if _reasons else "",
        }

    def record_strategy_result(self, strategy: dict[str, Any], quality: dict[str, Any],
                                issue: dict[str, Any]) -> None:
        """★策略沉淀：记录策略效果，成功策略存入模式库。"""
        _record = {
            "strategy": strategy,
            "quality": quality["score"],
            "category": strategy["category"],
            "complexity": strategy["complexity"],
            "issue_type": issue.get("type", ""),
            "timestamp": time.time(),
        }
        self._strategy_history.append(_record)

        # 高质量策略存入模式库（去重）
        if quality["score"] >= 0.7:
            _exists = any(s["strategy_id"] == strategy["strategy_id"] for s in self._pattern_library)
            if not _exists:
                self._pattern_library.append({
                    "strategy_id": strategy["strategy_id"],
                    "strategy": strategy,
                    "success_count": 1,
                    "avg_quality": quality["score"],
                    "categories": [strategy["category"]],
                })
            else:
                for s in self._pattern_library:
                    if s["strategy_id"] == strategy["strategy_id"]:
                        s["success_count"] += 1
                        s["avg_quality"] = (s["avg_quality"] * (s["success_count"] - 1) + quality["score"]) / s["success_count"]
                        if strategy["category"] not in s["categories"]:
                            s["categories"].append(strategy["category"])
                        break

        # 元学习：根据历史效果调整参数
        self._meta_learn()

    def _meta_learn(self) -> None:
        """★元学习：从历史策略效果中学习，自动优化策略生成参数。"""
        if len(self._strategy_history) < 10:
            return  # 数据不足，不调整

        # 分析不同opening策略的平均效果
        _opening_scores: dict[str, list[float]] = {}
        for h in self._strategy_history[-50:]:
            _op = h["strategy"]["opening"]
            _opening_scores.setdefault(_op, []).append(h["quality"])

        # 如果某个opening策略效果持续较差，降低其使用概率（通过调整complexity阈值）
        for _op, scores in _opening_scores.items():
            if len(scores) >= 5 and sum(scores) / len(scores) < 0.4:
                # 该策略效果差，增加creativity_bias鼓励尝试新策略
                self._meta_params["creativity_bias"] = min(0.8, self._meta_params["creativity_bias"] + 0.05)

        # 如果追问效果好，降低追问阈值
        _followup_success = [h for h in self._strategy_history[-30:]
                             if h["strategy"]["opening"] == "followup" and h["quality"] > 0.6]
        if len(_followup_success) > 5:
            self._meta_params["followup_threshold"] = max(0.2, self._meta_params["followup_threshold"] - 0.02)

    def get_best_strategy_for_category(self, category: str) -> dict[str, Any] | None:
        """★从模式库中获取某类问题的最佳策略（用于快速匹配）。"""
        _candidates = [s for s in self._pattern_library if category in s["categories"]]
        if not _candidates:
            return None
        return max(_candidates, key=lambda s: s["avg_quality"])["strategy"]

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息。"""
        return {
            "pattern_library_size": len(self._pattern_library),
            "strategy_history_count": len(self._strategy_history),
            "meta_params": self._meta_params,
            "top_strategies": sorted(
                self._pattern_library, key=lambda s: s["avg_quality"], reverse=True
            )[:5],
        }


# 全局单例
_generator: AdaptiveQueryStrategyGenerator | None = None


def get_query_strategy_generator() -> AdaptiveQueryStrategyGenerator:
    global _generator
    if _generator is None:
        _generator = AdaptiveQueryStrategyGenerator()
    return _generator
