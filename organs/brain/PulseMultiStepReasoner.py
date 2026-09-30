# -*- coding: utf-8 -*-
"""
PulseMultiStepReasoner —— 多步推理子模块

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 把复杂问题拆解为有序子步骤，并组织成带证据链的多步推理结构。
机制: decompose_complex_question 将长问题切分为子问题序列；build_evidence_chain_block 构造证据链文本块；attach_evidence_chain_to_node 把证据链挂回节点供后续复用；支持状态快照持久化。
定位: PulseInnerWorld 渐进式拆分第四阶段的产物，服务 multi_step_execute 类推理方法。
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any
from nucleus._silent_except import silent_exc


class PulseMultiStepReasoner:
    """PulseInnerWorld 多步推理子模块"""

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。当前无参数，预留扩展。"""


    def __init__(
        self,
        node_pool: Any = None,
        retrieve_func: Callable | None = None,
        log_func: Callable | None = None,
    ):
        self.node_pool = node_pool
        self._retrieve_func = retrieve_func
        self._log_func = log_func or (lambda level, msg: None)

    def _log(self, level: Any, message: str) -> None:
        self._log_func(level, message)

    # ========== 复杂问题拆解 ==========

    @staticmethod
    def decompose_complex_question(question: str) -> list[str] | None:
        """
        检测复杂问题并拆解为子问题。

        触发条件（满足任一即可）：
        1. 问题包含多个疑问词（如"什么是A？为什么B？"）
        2. 问题包含比较结构（"A和B有什么区别"）
        3. 问题包含因果链（"为什么A会导致B"）
        4. 问题长度超过60字且包含多个逗号/分号

        Returns:
            子问题列表，如果问题不够复杂则返回None
        """
        # 条件1: 包含比较结构——"A和B的区别/不同/对比"
        compare_patterns = [
            r'(.+?)和(.+?)的?(区别|不同|差异|对比|比较)',
            r'(.+?)与(.+?)的?(区别|不同|差异|对比|比较)',
            r'(.+?)跟(.+?)的?(区别|不同|差异|对比|比较)',
        ]
        for pattern in compare_patterns:
            match = re.search(pattern, question)
            if match:
                a = match.group(1).strip()
                b = match.group(2).strip()
                if len(a) >= 2 and len(b) >= 2:
                    return [
                        f"{a}是什么",
                        f"{b}是什么",
                        f"{a}和{b}在核心特征上有什么不同",
                    ]

        # 条件2: 多个疑问词——按问号/逗号/分号拆分
        if "?" in question or "？" in question:
            parts = re.split(r'[？?，,；;]', question)
            parts = [p.strip() for p in parts if len(p.strip()) >= 6]
            if len(parts) >= 2:
                return parts[:4]

        # 条件3: 问题长度超过60字且结构复杂
        if len(question) > 60:
            # 按逗号/分号拆分，取有实质内容的片段
            parts = re.split(r'[，,；;]', question)
            parts = [p.strip() for p in parts if len(p.strip()) >= 8]
            if len(parts) >= 2:
                return parts[:3]

        return None

    # ========== 证据链构建 ==========

    def build_evidence_chain_block(
        self,
        question: str,
        derivation_type: str,
        confidence: float,
    ) -> str:
        """
        生成结构化「依据链」文本块（机器可解析、人类可读）。
        仅当 use_evidence_trace 开关开启时调用。

        格式（追加在推理正文之后）：
            ⟦依据链⟧ type=deductive confidence=0.82
            evidence_source=<来源节点ID或关键词>
            推理路径: <每步规则> → <每步结论>
        """
        try:
            # 从知识检索中提取本问题命中的证据节点ID（若有）
            source_ids: list[str] = []
            try:
                if self._retrieve_func:
                    hits = self._retrieve_func(question, top_k=3)
                    if hits:
                        for h in hits[:3]:
                            nid = ""
                            if isinstance(h, dict):
                                nid = str(h.get("node_id", "")) or str(h.get("id", ""))
                            elif hasattr(h, "node_id"):
                                nid = str(h.node_id)
                            if nid:
                                source_ids.append(nid)
            except Exception as e:
                silent_exc(e, where="organs.brain.PulseMultiStepReasoner::build_evidence_chain_block L125")

            conf = max(0.0, min(1.0, float(confidence)))
            lines = ["", "⟦依据链⟧", f"type={derivation_type} confidence={conf:.2f}"]
            if source_ids:
                lines.append("evidence_source=" + ",".join(source_ids))
            lines.append(f"reasoning_path: {derivation_type}推理链 → 结论")
            return "\n".join(lines)
        except Exception as e:
            try:
                self._log("DEBUG", f"证据链块生成异常: {e}")
            except Exception as e:
                silent_exc(e, where="organs.brain.PulseMultiStepReasoner::build_evidence_chain_block L137")
            return ""

    # ========== 证据链挂载 ==========

    def attach_evidence_chain_to_node(
        self,
        question: str,
        derivation_type: str,
        confidence: float,
    ) -> None:
        """
        把本次推理的证据链写入最近一次落库的知识节点（若存在且开关开启）。
        让 L2/L3 节点携带「依据来源」，实现知识层级的可追溯性。
        """
        try:
            if not self.node_pool:
                return
            nodes = None
            # ★Dxxx/W4：优先取含冷驱逐节点的全集（超集），避免语义随时间静默流失
            if hasattr(self.node_pool, "get_all_including_evicted"):
                nodes = self.node_pool.get_all_including_evicted()
            elif hasattr(self.node_pool, "get_all"):
                nodes = self.node_pool.get_all()
            if not nodes:
                return
            # 取最近激活的节点
            node = None
            for n in nodes:
                if hasattr(n, "activation_count") and getattr(n, "activation_count", 0) > 0:
                    if node is None or getattr(n, "last_activated", 0) > getattr(node, "last_activated", 0):
                        node = n
            if node is None and nodes:
                node = nodes[0]
            chain = list(getattr(node, "evidence_chain", []) or [])
            chain.append({
                "role": "derivation",
                "type": derivation_type,
                "confidence": round(max(0.0, min(1.0, float(confidence))), 4),
                "question": question[:120],
                "ts": time.time(),
            })
            node.evidence_chain = chain[-8:]  # 保留最近 8 条，防膨胀
        except Exception as e:
            silent_exc(e, where="organs.brain.PulseMultiStepReasoner::attach_evidence_chain_to_node L180")

    # ========== 状态快照 ==========

    def get_state_snapshot(self) -> dict[str, Any]:
        """获取状态快照（用于持久化）"""
        return {
            "module": "PulseMultiStepReasoner",
            "has_node_pool": self.node_pool is not None,
            "has_retrieve_func": self._retrieve_func is not None,
        }
