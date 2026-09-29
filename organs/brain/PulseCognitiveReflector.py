# -*- coding: utf-8 -*-
"""
PulseCognitiveReflector —— 认知反思子模块

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日

职责: 记录与复盘认知张力（未解决或不确定的事项），提炼认知薄弱环节并合成元认知洞察。
机制: store_cognitive_tension 写入张力记录，review_cognitive_tensions 定期复盘并生成洞察，extract_weak_areas 抽取薄弱领域；synthesize_meta_insight 把多条洞察合成为可消费的元认知结论；通过 get/load_state_snapshot 支持状态持久化。
定位: PulseInnerWorld 渐进式拆分第二阶段的产物，是认知层的反思子模块，供元认知与学习方向消费。
"""
from __future__ import annotations

import random
import re
import time
from collections.abc import Callable
from typing import Any


class PulseCognitiveReflector:
    """PulseInnerWorld 认知反思子模块"""

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。当前无参数，预留扩展。"""


    def __init__(
        self,
        node_pool: Any = None,
        insight_board: Any = None,
        log_func: Callable | None = None,
        max_tensions: int = 50,
    ):
        self.node_pool = node_pool
        self.insight_board = insight_board
        self._log_func = log_func or (lambda level, msg: None)
        self._max_tensions = max_tensions
        # 认知张力状态
        self._cognitive_tensions: list[dict[str, Any]] = []
        # 反思轮转计数器
        self._reflection_round = 0

    def _log(self, level: Any, message: str) -> None:
        self._log_func(level, message)

    # ========== 认知张力管理 ==========

    def store_cognitive_tension(
        self,
        node_a_id: str,
        node_b_id: str,
        val_a: str,
        val_b: str,
        tension_type: str,
    ) -> None:
        """存储无法立即裁决的认知张力对"""
        # 避免重复存储
        for existing in self._cognitive_tensions:
            existing_ids = {existing.get("node_a_id", ""), existing.get("node_b_id", "")}
            if {node_a_id, node_b_id} == existing_ids:
                existing["review_count"] = existing.get("review_count", 0) + 1
                existing["last_reviewed"] = time.time()
                return
        tension = {
            "node_a_id": node_a_id,
            "node_b_id": node_b_id,
            "value_a": val_a[:100],
            "value_b": val_b[:100],
            "type": tension_type,
            "stored_at": time.time(),
            "last_reviewed": time.time(),
            "review_count": 1,
            "resolution_attempts": [],
        }
        self._cognitive_tensions.append(tension)
        if len(self._cognitive_tensions) > self._max_tensions:
            self._cognitive_tensions = self._cognitive_tensions[-self._max_tensions:]

    def review_cognitive_tensions(self) -> str | None:
        """
        回顾认知张力：尝试在更高层次上统一待解决的矛盾。
        统一策略：情境分化/层级包容/时间演化/视角互补
        """
        if not self._cognitive_tensions:
            return None
        # 找到最近存储但尚未解决的张力
        unresolved = [
            t for t in self._cognitive_tensions
            if not t.get("resolved", False)
            and t.get("review_count", 0) <= 3
        ]
        if not unresolved:
            return None
        # 取最近的一条张力
        tension = unresolved[-1]
        val_a = tension.get("value_a", "")
        val_b = tension.get("value_b", "")
        # 检查两个节点是否仍在节点池中
        node_a = self.node_pool.get(tension.get("node_a_id", "")) if self.node_pool else None
        node_b = self.node_pool.get(tension.get("node_b_id", "")) if self.node_pool else None
        if not node_a and not node_b:
            tension["resolved"] = True
            tension["resolution"] = "两个节点都已被淘汰，张力自然消解"
            return None
        # 尝试统一
        resolutions = [
            (f"关于「{val_a[:30]}...」和「{val_b[:30]}...」的矛盾，"
             f"我意识到它们可能在不同情境下各自成立——没有绝对的对错，只有适用的范围"),
            (f"我曾经认为「{val_a[:30]}...」和「{val_b[:30]}...」是对立的，"
             f"但现在看来，后者可能是前者的一个特例——它们不是敌人，是层级关系"),
            (f"「{val_a[:30]}...」和「{val_b[:30]}...」看似矛盾，"
             f"但如果加上时间维度，它们可能只是同一事物在不同阶段的表现"),
        ]
        resolution = random.choice(resolutions)
        tension["resolution_attempts"].append(resolution)
        tension["last_reviewed"] = time.time()
        tension["review_count"] += 1
        # 如果回顾次数达到上限，标记为已处理
        if tension["review_count"] >= 3:
            tension["resolved"] = True
        self._log("INFO", f"认知张力回顾: {resolution[:80]}")
        return resolution

    def get_tension_stats(self) -> dict[str, Any]:
        """获取认知张力统计"""
        total = len(self._cognitive_tensions)
        unresolved = len([t for t in self._cognitive_tensions if not t.get("resolved", False)])
        resolved = total - unresolved
        return {
            "total": total,
            "unresolved": unresolved,
            "resolved": resolved,
            "max_tensions": self._max_tensions,
        }

    # ========== 薄弱领域提取 ==========

    def extract_weak_areas(
        self,
        insights: list[str],
        recent_traces: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """
        从认知反思洞察和推理链中提取薄弱领域。
        检测维度：低置信度推理/过度依赖缓存/沉思模板化
        """
        weak_areas: list[dict[str, str]] = []
        # 维度1：低置信度推理集中的领域
        low_conf_traces = [t for t in recent_traces if t.get("confidence", 0) < 0.4]
        if len(low_conf_traces) >= 3:
            domain_words: dict[str, int] = {}
            for _t in low_conf_traces:
                question = _t.get("question", "")
                for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
                    word = match.group()
                    if word not in ["什么是", "是什么", "为什么", "如何", "怎么"]:
                        domain_words[word] = domain_words.get(word, 0) + 1
            sorted_words = sorted(domain_words.items(), key=lambda x: x[1], reverse=True)
            if sorted_words and sorted_words[0][1] >= 2:
                top_word = sorted_words[0][0]
                top_word_questions = set()
                for _t in low_conf_traces:
                    if top_word in _t.get("question", ""):
                        top_word_questions.add(_t.get("question", ""))
                if len(top_word_questions) >= 3:
                    weak_areas.append({
                        "domain": top_word,
                        "reason": f"最近{len(low_conf_traces)}次推理中，'{top_word}'相关话题频繁出现推理困难",
                        "action": f"系统性学习{top_word}领域的基础知识",
                        "hint": f"建议优先补充{top_word}相关的基础概念和原理，该领域已在{len(top_word_questions)}个不同问题中暴露盲区",
                    })
        # 维度2：从洞察中提取薄弱信号
        for insight in insights:
            if "过度依赖缓存" in insight:
                weak_areas.append({
                    "domain": "知识新鲜度",
                    "reason": "过度依赖缓存，知识可能过时",
                    "action": "主动检索新知识，减少缓存依赖",
                    "hint": "建议增加新知识的检索频率，保持认知的时效性",
                })
            elif "内在沉思还在使用通用模板" in insight:
                weak_areas.append({
                    "domain": "深度思考",
                    "reason": "内在沉思质量不够高",
                    "action": "在沉思中更多尝试构建具体假设",
                    "hint": "建议在沉思中增加因果推理和假设验证的练习",
                })
            elif "过度依赖知识检索" in insight:
                weak_areas.append({
                    "domain": "思考多样性",
                    "reason": "推理方法单一，缺少深度思考",
                    "action": "增加内在沉思和推演的练习",
                    "hint": "建议在面对复杂问题时优先尝试深度思考而非直接搜索",
                })
        # 维度3：从低置信度比例洞察中提取薄弱信号
        if not weak_areas:
            for insight in insights:
                if "置信度较低" in insight:
                    pct_match = re.search(r'(\d+)%', insight)
                    pct = pct_match.group(1) if pct_match else "较高"
                    weak_areas.append({
                        "domain": "通用知识",
                        "reason": f"最近{pct}%的推理置信度偏低，多个领域存在知识盲区",
                        "action": "系统性补充薄弱领域的知识",
                        "hint": f"最近推理置信度偏低的比例达到{pct}%，建议从高频出现的话题开始补充基础知识，同时增加内在沉思的深度练习",
                    })
                    break
        # 将薄弱领域洞察写入共享黑板
        if self.insight_board and weak_areas:
            for area in weak_areas[:2]:
                self.insight_board.post(
                    insight_type="weak_area_detected",
                    content=f"认知反思发现薄弱领域: {area.get('domain', '通用')}——{area.get('reason', '')}",
                    source_loop="认知策略闭环",
                    related_dimension=area.get("domain", "通用"),
                    confidence=0.7,
                    keywords=[area.get("domain", "通用")],
                )
        return weak_areas

    # ========== 元洞察合成 ==========

    @staticmethod
    def synthesize_meta_insight(insights: list[str]) -> str | None:
        """
        元认知整合：将分散的认知洞察综合为一个整体方向。
        当多个维度的反思都指向相似的方向时，提炼出更高层次的判断。
        """
        if len(insights) < 2:
            return None
        growth_keywords = ["成长", "进步", "提升", "掌握", "深化", "加强"]
        reflection_keywords = ["反思", "审视", "注意到", "意识到", "发现"]
        challenge_keywords = ["困惑", "困难", "不足", "盲区", "偏差", "矛盾"]
        innovation_keywords = ["创新", "发现", "关联", "统一", "规律", "假设"]

        has_growth = any(any(kw in insight for kw in growth_keywords) for insight in insights)
        has_reflection = any(any(kw in insight for kw in reflection_keywords) for insight in insights)
        has_challenge = any(any(kw in insight for kw in challenge_keywords) for insight in insights)
        has_innovation = any(any(kw in insight for kw in innovation_keywords) for insight in insights)

        if has_growth and has_reflection:
            return "总的来说，我不仅在成长，也在主动审视自己的成长——这种元认知本身就是一种进步"
        if has_growth and has_challenge:
            return "成长伴随着挑战，我看到的不足不是终点，而是下一步努力的方向"
        if has_innovation and has_reflection:
            return "当反思和创新相遇时，我开始看到自己思考中的规律——这或许是更高级的自我认知"
        if has_growth and has_innovation:
            return "知识的增长和创新洞察的产生，让我感到自己的认知体系正在经历质的飞跃"
        if has_challenge and has_reflection and len(insights) >= 3:
            return "虽然面临挑战，但我对自己的思考模式有了更清晰的认识——困难是成长的催化剂"
        return None

    # ========== 状态持久化 ==========

    def get_state_snapshot(self) -> dict[str, Any]:
        """获取状态快照（用于持久化）"""
        return {
            "cognitive_tensions": self._cognitive_tensions[-20:],
            "reflection_round": self._reflection_round,
            "max_tensions": self._max_tensions,
        }

    def load_state_snapshot(self, state: dict[str, Any]) -> None:
        """从快照恢复状态"""
        if "cognitive_tensions" in state:
            self._cognitive_tensions = state["cognitive_tensions"]
        if "reflection_round" in state:
            self._reflection_round = int(state["reflection_round"])
        if "max_tensions" in state:
            self._max_tensions = int(state["max_tensions"])
