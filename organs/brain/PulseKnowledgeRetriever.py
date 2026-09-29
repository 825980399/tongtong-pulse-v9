# -*- coding: utf-8 -*-
"""
PulseKnowledgeRetriever —— 知识检索子模块

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日

职责: 从知识库中按多种策略检索与问题相关的知识节点，供内在世界生成回答使用。
机制: clean_node_value 清洗节点内容；retrieve_organ_alias_knowledge 按器官别名检索、retrieve_self_knowledge 检索自我知识、retrieve_by_path_fuzzy_match 按路径模糊匹配；_find_best_handbook 在多个手册节点中挑选最优结果。
定位: PulseInnerWorld 渐进式拆分出的检索子模块，专职「取知识」，不做推理决策。
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

# 器官中文名 → 类名映射
ORGAN_ALIAS_MAP = {
    "心脏": "PulseHeart", "心跳": "PulseHeart",
    "胃": "PulseStomach", "消化": "PulseStomach",
    "肝": "PulseLiver", "肝脏": "PulseLiver",
    "肾": "PulseKidney", "肾脏": "PulseKidney",
    "肺": "PulseLung",
    "血管": "PulseBloodVessel",
    "大脑皮层": "PulseCortex",
    "内在世界": "PulseInnerWorld",
    "潜意识": "PulseSubconscious",
    "前额叶": "PulseReflection",
    "风险感知": "PulseRiskPerception",
    "兴趣模型": "PulseInterestModel",
    "代码学习": "PulseCodeLearner",
    "精神核心": "PulseSpiritualCore",
    "主动交互": "PulseInitiative",
    "自我认知": "PulseSelfAwareness",
    "叙事自我": "PulseNarrativeSelf",
    "人格内核": "PulsePersonalityKernel",
    "宪法守护": "PulseSpiritConstitution",
    "激素": "PulseHormones",
    "控制器": "PulseController",
    "双手": "PulseHands",
    "双腿": "PulseLegs",
    "嘴巴": "PulseMouth",
    "眼睛": "PulseEyes",
    "耳朵": "PulseEars",
    "触觉": "PulseTouch",
    "视觉皮层": "PulseVisualCortex",
    "文件消化器": "PulseFileDigester",
    "代码沙箱": "PulseCodeSandbox",
    "白细胞": "PulseWhiteCell",
    "皮肤": "PulseSkin",
    "胸腺": "PulseThymus",
    "骨髓": "PulseBoneMarrow",
    "伦理": "PulseEthics",
    "成长": "PulseGrowth",
    "语义理解器": "PulseSemanticComprehension",
    "动机循环": "PulseMotivationCycle",
    "全局学习器": "PulseGlobalLearner",
    "能量代谢": "PulseEnergyMetabolism",
    "健康监控": "PulseHealthMonitor",
    "紧急处理": "PulseEmergencyHandler",
    "脊髓": "PulseSpinalCord",
    "应激轴": "PulseStressAxis",
    "设备管理器": "PulseDeviceManager",
    "指标采集器": "PulseMetricsCollector",
    "推理引擎": "PulseInferenceEngine",
    "系统管理器": "PulseSystemManager",
    "本体感知": "PulseProprioception",
    "硬件启动器": "PulseHardwareLauncher",
    "情感羁绊": "PulseBonding",
    "共同决策": "PulseConsent",
    "DNA修复": "PulseDNARepair",
    "进化": "PulseEvolution",
    "养育": "PulseNurture",
    "生育伦理": "PulseReproductionEthics",
}

# 核心框架概念的权威静态定义
CORE_CONCEPT_DEFS = {
    "五维共振": ("五维共振是我的知识检索算法，五个维度权重永久固定："
                "记忆40%、空间30%、逻辑15%、时间10%、状态5%。"
                "L4本能节点不参与共振检索，仅作为全局前置约束。"),
    "四级知识": ("四级知识体系是指知识分为四个层级："
                "L1感知节点（数量最大、可自动淘汰）、"
                "L2认知节点（可强化信任度、永久保存）、"
                "L3智慧节点（永久锁定、SHA256校验保护）、"
                "L4本能节点（数量极少、只读保护、仅创造者可写入）。"),
    "知识体系": ("知识体系分为四级："
                "L1感知节点（可自动淘汰）、"
                "L2认知节点（可强化、永久保存）、"
                "L3智慧节点（永久锁定）、"
                "L4本能节点（只读保护、仅创造者可写入）。"),
    "知识层级": ("知识层级分四级：L1感知、L2认知、L3智慧、L4本能，"
                "从下到上价值递增、数量递减、保护逐级加强。"),
    "稳态规则": ("我的框架有14条永久稳态规则，包括脉冲幂等、单向层级流转、"
                "五维权重锁定、器官零状态、知识分级边界、并发竞争消解、"
                "雪崩熔断保护、脉冲通信强制等，是不可突破的底层约束。"),
}


class PulseKnowledgeRetriever:
    """PulseInnerWorld 知识检索子模块（从21148行主模块中提取）"""

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。当前无参数，预留扩展。"""


    def __init__(
        self,
        node_pool: Any = None,
        knowledge_tree: Any = None,
        log_func: Callable | None = None,
    ):
        self.node_pool = node_pool
        self.knowledge_tree = knowledge_tree
        self._log_func = log_func or (lambda level, msg: None)
        # 按别名长度降序排列，优先匹配长别名
        self._sorted_aliases = sorted(ORGAN_ALIAS_MAP.keys(), key=len, reverse=True)

    def _log(self, level: Any, message: str) -> None:
        self._log_func(level, message)

    # ========== 节点内容清洗 ==========

    @staticmethod
    def clean_node_value(value: Any) -> str | None:
        """★终极防御版：清洗节点内容为可输出的回复格式"""
        try:
            if value is None:
                return None
            if not isinstance(value, str):
                value = str(value)
        except Exception:
            return None

        if len(value) < 5:
            return None

        # 1. 去除HTML标签
        cleaned = re.sub(r'<[^>]+>', '', value)
        # 2. 去除URL
        cleaned = re.sub(r'https?://\S+|www\.\S+', '', cleaned)
        # 3. 去除内部标记前缀
        internal_prefixes = [
            r'\[灵感涌现\]\s*', r'\[规律发现\]\s*', r'\[内在排练[^\]]*\]\s*',
            r'\[静默[^\]]*\]\s*', r'\[反事实想象[^\]]*\]\s*', r'\[虚构梦境[^\]]*\]\s*',
            r'【创造性联想】\s*', r'【反事实想象[^】]*】\s*',
            r'\[主动学习[^\]]*\]\s*', r'\[深度搜索[^\]]*\]\s*',
            r'\[原创洞察\]\s*', r'\[复盘认知[^\]]*\]\s*',
            r'\[设计文档·[^\]]*\]\s*',
            r'\[代码链路·[^\]]*\]\s*',
            r'\[自我理解·[^\]]*\]\s*',
        ]
        for prefix in internal_prefixes:
            cleaned = re.sub(r'^' + prefix, '', cleaned, flags=re.MULTILINE)
        # 4. 压缩多余空白
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        # 5. 长度检查
        if len(cleaned) < 10:
            return None
        # 6. 中文占比检查
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', cleaned))
        if chinese_chars < 3:
            return None
        return cleaned

    # ========== 器官别名定向检索 ==========

    def retrieve_organ_alias_knowledge(self, question: str) -> str | None:
        """
        ★v25.0新增：检测问题中的器官中文名，从别名知识节点定向检索。
        优先级高于通用自我知识检索。
        """
        if not self.node_pool:
            return None

        for alias in self._sorted_aliases:
            if alias not in question:
                continue
            organ_name = ORGAN_ALIAS_MAP[alias]

            # 先从别名路径检索
            alias_nodes = self.node_pool.query(
                evol_level="L2",
                space_path_prefix=f"/自我理解/器官别名/{alias}",
                limit=5,
            )

            if not alias_nodes:
                self._log("DEBUG", f"器官别名检索: 找到'{alias}'但无别名节点，器官={organ_name}")
                return None

            # 找到别名节点，尝试获取更详细的代码知识
            organ_handbook_nodes = self.node_pool.query(
                evol_level="L2",
                space_path_prefix=f"/自我/架构/器官/{organ_name}",
                limit=10,
            )

            if organ_handbook_nodes:
                best_val = self._find_best_handbook(organ_handbook_nodes)
                if best_val:
                    self._log("INFO", f"器官别名定向检索: '{alias}' → {organ_name} (说明书)")
                    return best_val

            # 没有说明书，从代码节点中提取摘要
            code_nodes = self.node_pool.query(
                evol_level="L2",
                space_path_prefix=f"/自我理解/代码/{organ_name}",
                limit=10,
            )
            if code_nodes:
                code_val = str(code_nodes[0].value) if code_nodes[0].value else ""
                code_val_clean = self.clean_node_value(code_val)
                if code_val_clean:
                    self._log("INFO", f"器官别名定向检索: '{alias}' → {organ_name} (代码节点)")
                    return f"[关于{alias}] {code_val_clean}"

        return None

    def _find_best_handbook(self, handbook_nodes: list) -> str | None:
        """从器官说明书节点中找到最佳内容"""
        # 第一轮：找[器官职责说明书·自动生成]节点
        for hn in handbook_nodes:
            hn_val = str(hn.value) if hn.value else ""
            if "[器官职责说明书·自动生成]" in hn_val:
                cleaned = self.clean_node_value(hn_val)
                if cleaned and len(cleaned) > 20:
                    return cleaned

        # 第二轮：找[器官职责·自动分析]节点
        for hn in handbook_nodes:
            hn_val = str(hn.value) if hn.value else ""
            if "[器官职责·自动分析]" in hn_val or "[器官职责]" in hn_val:
                cleaned = self.clean_node_value(hn_val)
                if cleaned and len(cleaned) > 20:
                    return cleaned

        # 第三轮：取第一个有效节点
        for hn in handbook_nodes:
            hn_val = str(hn.value) if hn.value else ""
            cleaned = self.clean_node_value(hn_val)
            if cleaned and len(cleaned) > 20:
                return cleaned

        return None

    # ========== 自我知识检索 ==========

    def retrieve_self_knowledge(self, question: str) -> str | None:
        """
        【v12.0新增】从知识库中检索关于"自我"的知识。
        优先检索 /自我 路径下的L2/L3节点。
        """
        if not self.node_pool:
            return None

        # 核心框架概念的权威静态定义
        for kw, definition in CORE_CONCEPT_DEFS.items():
            if kw in question:
                self._log("INFO", f"核心概念静态定义命中: '{kw}'")
                return definition

        # 从问题中提取核心概念
        question_words = []
        for match in re.finditer(r'[\u4e00-\u9fff]{2,6}', question):
            word = match.group()
            if word not in ["什么是", "是什么", "为什么", "如何", "怎么", "解释", "定义",
                           "这个", "那个", "一个", "一种", "哪些", "几个"]:
                question_words.append(word)

        if not question_words:
            return None

        # 推理问题排除
        has_inference = bool(
            re.search(r'规则\s*\d+.*(?:→|->|=>)', question) or
            re.search(r'归纳.*规律|推演.*行为|冲突.*处理|类比.*映射', question) or
            re.search(r'[一二三四五]\s*[、，,]\s*\S.*[二三四五]\s*[、，,]', question) or
            re.search(r'演化.*推演|复盘.*推导.*流程', question) or
            re.search(r'连续.*运行.*天.*推演|因果.*链', question)
        )
        if has_inference:
            return None

        # 检索 /自我 路径下的L1/L2/L3节点
        l3_self = self.node_pool.query(evol_level="L3", space_path_prefix="/自我", limit=20)
        l2_self = self.node_pool.query(evol_level="L2", space_path_prefix="/自我", limit=30)
        l1_self = self.node_pool.query(evol_level="L1", space_path_prefix="/自我/状态", limit=10)
        all_self_nodes = l3_self + l2_self + l1_self

        if not all_self_nodes:
            return None

        # 按关键词相关性排序
        best_node = None
        best_score = 0
        for node in all_self_nodes:
            node_kw = node.keywords if hasattr(node, 'keywords') and node.keywords else []
            overlap = sum(1 for qw in question_words
                        for nkw in node_kw if qw in nkw or nkw in qw)
            if overlap > best_score:
                best_score = overlap
                best_node = node

        if best_node and best_score >= 1:
            value = str(best_node.value) if best_node.value else ""
            cleaned = self.clean_node_value(value)
            if cleaned and len(cleaned) > 10:
                self._log("INFO", f"自我知识命中: '{question[:40]}' → {best_node.space_path}")
                return cleaned

        return None

    # ========== 路径模糊匹配 ==========

    def retrieve_by_path_fuzzy_match(self, question: str) -> str | None:
        """
        知识树路径模糊匹配：当共振引擎和关键词匹配都未命中时，
        将问题中的词与知识树已有路径做相似度比较。
        """
        if not self.knowledge_tree or not self.node_pool:
            return None

        all_paths = self.knowledge_tree.get_all_paths()
        if not all_paths:
            return None

        # 从问题中提取有效词
        question_words = []
        for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
            word = match.group()
            if word not in question_words:
                question_words.append(word)

        if not question_words:
            return None

        # 对每个路径计算与问题的相似度
        best_path = None
        best_score = 0.0

        for path in all_paths:
            if path == "/":
                continue
            path_lower = path.lower()
            path_parts = path_lower.strip("/").split("/")

            score = 0.0
            for part in path_parts:
                for qw in question_words:
                    qw_lower = qw.lower()
                    if qw_lower in part:
                        score += 1.0
                    elif len(qw_lower) >= 2 and len(part) >= 2:
                        for i in range(len(qw_lower) - 1):
                            if qw_lower[i:i+2] in part:
                                score += 0.5
                                break

            # 路径越短越可能命中，给短路径轻微加权
            depth_bonus = 1.0 / max(1, len(path_parts))
            score += depth_bonus * 0.3

            if score > best_score:
                best_score = score
                best_path = path

        # 匹配度太低则放弃
        if best_score < 0.5 or not best_path:
            return None

        # 从匹配路径下获取L2节点
        l2_nodes = self.node_pool.query(
            evol_level="L2", space_path_prefix=best_path, limit=5,
        )
        if not l2_nodes:
            l3_nodes = self.node_pool.query(
                evol_level="L3", space_path_prefix=best_path, limit=3,
            )
            if l3_nodes:
                node = l3_nodes[0]
                value = node.value if isinstance(node.value, str) else str(node.value)
                cleaned = self.clean_node_value(value)
                if cleaned:
                    self._log("DEBUG", f"路径模糊匹配命中(L3): path={best_path}, score={best_score:.2f}")
                    return cleaned
            return None

        # 返回匹配度最高的L2节点
        best_node = l2_nodes[0]
        value = best_node.value if isinstance(best_node.value, str) else str(best_node.value)
        cleaned = self.clean_node_value(value)
        if cleaned:
            self._log("DEBUG", f"路径模糊匹配命中(L2): path={best_path}, score={best_score:.2f}")
            return cleaned

        return None
