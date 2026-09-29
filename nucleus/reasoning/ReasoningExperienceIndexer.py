# -*- coding: utf-8 -*-
"""
ReasoningExperienceIndexer.py —— 推理经验索引器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 推理经验的索引与检索
机制: 基于ReasoningExperienceIndexer类实现，包含10个核心方法
定位: 记忆推理层
"""

from __future__ import annotations

import threading
from typing import Any

from nucleus.mnemosyne.ReasoningExperience import get_reasoning_experience


_lock = threading.Lock()
_indexer: ReasoningExperienceIndexer | None = None

# 节点副本路径（内部协作者 Q2 决策的 /推理经验/ 路径，按规则类型分三类）
PATH_CAUSAL = "/推理经验/因果/"
PATH_CONTRADICTION = "/推理经验/矛盾/"
PATH_ANALOGY = "/推理经验/类比/"

# 来源器官标记（写入节点副本的 source_organ，便于溯源）
SOURCE_ORGAN = "reasoning_experience_indexer"


class ReasoningExperienceIndexer:
    """推理经验双写索引器（规则通道层，不改 ReasoningExperience.py）。"""

    def __init__(self, reasoning_exp=None, node_pool=None,
                 vector_store=None, encode_queue=None):
        self._exp = reasoning_exp if reasoning_exp is not None else get_reasoning_experience()
        self._node_pool = node_pool
        self._store = vector_store
        self._queue = encode_queue
        self._double_write_enabled = False
        self._records = 0          # 双写次数（验收用）
        self._nodes_written = 0    # 节点副本写入次数（验收用）

    # ------------------------------------------------------------------
    # 依赖注入（main.py 装配时调用）
    # ------------------------------------------------------------------
    def set_dependencies(self, node_pool=None, vector_store=None, encode_queue=None):
        """注入知识树 / 向量库 / 编码队列依赖（可多次调用，逐步装配）。"""
        if node_pool is not None:
            self._node_pool = node_pool
        if vector_store is not None:
            self._store = vector_store
        if encode_queue is not None:
            self._queue = encode_queue

    def set_enabled(self, enabled: bool):
        """设置双写开关（main.py 从 config 读取后注入）。"""
        self._double_write_enabled = bool(enabled)

    @property
    def double_write_enabled(self) -> bool:
        return self._double_write_enabled

    # ------------------------------------------------------------------
    # 路径映射
    # ------------------------------------------------------------------
    @staticmethod
    def _derive_path(derivation_type: str, features: dict[str, Any] | None) -> str:
        """derivation_type + 结构特征 → 三类路径之一。

        优先级：矛盾 > 类比 > 因果（默认）。
        """
        _dt = str(derivation_type or "").lower()
        _f = features or {}
        if ("conflict" in _dt or "矛盾" in _dt or "contradict" in _dt
                or bool(_f.get("has_conflict_signal"))):
            return PATH_CONTRADICTION
        if ("analog" in _dt or "类比" in _dt or bool(_f.get("has_analogy_signal"))):
            return PATH_ANALOGY
        return PATH_CAUSAL

    @staticmethod
    def _build_node_value(question: str, derivation_type: str,
                          confidence: float, source: str) -> str:
        """节点副本的语义化文本（含推理问题 + 类型 + 置信度 + 来源，供向量检索命中）。"""
        return (f"推理经验｜问题：{question}｜推理类型：{derivation_type}"
                f"｜置信度：{confidence}｜来源：{source}")

    # ------------------------------------------------------------------
    # 双写：record_with_index
    # ------------------------------------------------------------------
    def record_with_index(self, question: str, derivation_type: str,
                          source: str = "local", confidence: float = 0.7) -> dict[str, Any]:
        """记录推理经验：先写 JSON，成功后（灰度开启时）写知识树节点副本。

        事务性：JSON 写失败（record 抛异常）则节点副本不写。
        与 JSON 版对齐：JSON 版走「已有相似经验则 update 不新增」分支时，
        节点副本也不重复写（通过对比写前后经验数判断）。

        Returns:
            {"json_written": bool, "node_written": bool, "node_id": str|None,
             "path": str|None, "error": str|None}
        """
        result: dict[str, Any] = {
            "json_written": False, "node_written": False,
            "node_id": None, "path": None, "error": None,
        }

        # ---- 1. 事务起点：写 JSON（ReasoningExperience.record）----
        try:
            _before = len(getattr(self._exp, "_experiences", []))
            self._exp.record(question, derivation_type, source=source,
                             confidence=confidence)
            _after = len(getattr(self._exp, "_experiences", []))
            result["json_written"] = True
        except Exception as _e:
            # JSON 写失败 → 事务中止，节点副本不写
            result["error"] = f"{type(_e).__name__}: {_e}"
            return result

        self._records += 1

        # ---- 2. 灰度开关关闭 → 只写 JSON，返回（零风险回退）----
        if not self._double_write_enabled:
            return result
        if self._node_pool is None:
            return result

        # ---- 3. JSON 走 update 分支（长度未增）时不重复写节点副本 ----
        #   （容量满时先淘汰后新增，长度可能不变——该边缘情况下节点副本不写，
        #    可接受：语义检索仍可通过 question 命中既有节点副本。）
        if _after <= _before:
            return result

        # ---- 4. 写知识树节点副本 ----
        try:
            _features = self._exp.extract_structural_features(question)
            _path = self._derive_path(derivation_type, _features)
            _value = self._build_node_value(question, derivation_type, confidence, source)

            from nucleus.mnemosyne.PulseNode import PulseNode
            _node = PulseNode(
                value=_value,
                keywords=[question] if question else [],
                source_organ=SOURCE_ORGAN,
                evol_level=PulseNode.EVOL_L1,
                importance=PulseNode.IMPORTANCE_B,
                space_path=_path,
            )
            _node_id = self._node_pool.add(_node)
            result["node_written"] = True
            result["node_id"] = _node_id
            result["path"] = _path
            self._nodes_written += 1

            # ---- 5. 提交语义编码（让新节点副本可被语义检索命中）----
            if self._queue is not None:
                try:
                    self._queue.submit(_node_id, _value)
                except Exception as _e:
                    # 编码入队失败不阻断主流程（节点已入知识树，对账会兜底）
                    result["error"] = f"encode_submit_failed: {_e}"
        except Exception as _e:
            result["error"] = f"{type(_e).__name__}: {_e}"

        return result

    # ------------------------------------------------------------------
    # 双读：search_with_semantic
    # ------------------------------------------------------------------
    def search_with_semantic(self, question: str, top_k: int = 5) -> list[dict[str, Any]]:
        """读取推理经验：JSON 精确匹配 + 语义检索知识树节点，合并去重、按分降序。

        Returns:
            list[dict]，每项：
              {"derivation_type", "confidence", "source", "example_question",
               "match_type": "exact"|"semantic", "score": float}
            按 score 降序。
        """
        results: list[dict[str, Any]] = []
        _seen_questions: set[str] = set()

        # ---- 1. JSON 精确匹配（原 search，权重最高）----
        try:
            _exact = self._exp.search(question)
            if _exact:
                _eq = _exact.get("matched_example", "") or question
                results.append({
                    "derivation_type": _exact.get("derivation_type", ""),
                    "confidence": _exact.get("confidence", 0.0),
                    "source": _exact.get("source", ""),
                    "example_question": _eq,
                    "match_type": "exact",
                    "score": float(_exact.get("confidence", 0.0)),
                })
                _seen_questions.add(_eq)
        except Exception:
            pass

        # ---- 2. 语义检索知识树 /推理经验/ 节点（需双写开启 + 有向量库）----
        if self._double_write_enabled and self._store is not None:
            try:
                _hits = self._store.search_by_text(question, top_k * 2) or []
                for _nid, _sim in _hits:
                    _node = None
                    if self._node_pool is not None:
                        _node = self._node_pool.get(_nid)
                    if _node is None:
                        continue
                    _spath = getattr(_node, "space_path", "") or ""
                    if not _spath.startswith("/推理经验/"):
                        continue   # 非推理经验节点，跳过
                    _val = getattr(_node, "value", "") or ""
                    # 去重：与 JSON 精确匹配的 question 相同则跳过
                    if _val in _seen_questions:
                        continue
                    _seen_questions.add(_val)
                    results.append({
                        "derivation_type": "",
                        "confidence": float(_sim),
                        "source": "knowledge_tree",
                        "example_question": _val[:80],
                        "match_type": "semantic",
                        "score": float(_sim),
                        "node_id": _nid,
                    })
            except Exception:
                pass

        # ---- 3. 合并排序 ----
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    # ------------------------------------------------------------------
    def get_stats(self) -> dict[str, Any]:
        return {
            "double_write_enabled": self._double_write_enabled,
            "records": self._records,
            "nodes_written": self._nodes_written,
        }


# ==================================================================
# 单例
# ==================================================================
def get_reasoning_experience_indexer() -> ReasoningExperienceIndexer:
    """获取双写索引器单例。"""
    global _indexer
    if _indexer is None:
        with _lock:
            if _indexer is None:
                _indexer = ReasoningExperienceIndexer()
    return _indexer


def shutdown_reasoning_experience_indexer() -> None:
    """复位单例（满足器官零状态，规则4）。"""
    global _indexer
    _indexer = None
