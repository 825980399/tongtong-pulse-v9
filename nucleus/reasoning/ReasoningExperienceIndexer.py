# -*- coding: utf-8 -*-
"""
ReasoningExperienceIndexer.py —— 推理经验索引器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 推理经验的索引与检索
机制: 基于ReasoningExperienceIndexer类实现，包含10个核心方法
定位: 记忆推理层
"""

from __future__ import annotations

import threading
import time
import json
import os
import re
import io
from typing import Any

from nucleus.mnemosyne.ReasoningExperience import get_reasoning_experience
from nucleus._silent_except import silent_exc


_lock = threading.Lock()
_indexer: ReasoningExperienceIndexer | None = None

# 节点副本路径（星轨 Q2 决策的 /推理经验/ 路径，按规则类型分三类）
PATH_CAUSAL = "/推理经验/因果/"
PATH_CONTRADICTION = "/推理经验/矛盾/"
PATH_ANALOGY = "/推理经验/类比/"

# 来源器官标记（写入节点副本的 source_organ，便于溯源）
SOURCE_ORGAN = "reasoning_experience_indexer"

# ★第164批 刀A1：补救成功 / 失败模式 路径
PATH_REMEDIATION = "/推理经验/补救成功/"
PATH_FAILURE = "/推理经验/失败模式/"

# ★第165批 刀A5：计数持久化状态文件（git-ignored 运行时状态，data/ 下）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_STATE_PATH = os.path.join(_PROJECT_ROOT, "data", "reasoning_experience_indexer_state.json")

# ★第165批 刀A5：关键词切分（分词/去停用词），替代整句作关键词
_STOPWORDS = frozenset(
    "的 了 和 与 及 或 在 是 我 你 他 她 它 们 这 那 有 把 被 让 给 对 从 到 以 为 之 其 此 "
    "个 种 项 条 次 下 上 中 也 都 就 还 很 最 不 没 别 请 您 我们 你们 他们 这个 那个".split()
)
_TOKEN_RE = re.compile(r"[一-鿿]+|[a-zA-Z0-9_]+")

def _split_keywords(text):
    """将句子拆为关键词（CJK 整段 + 英文词，去停用词/单字噪声），最多 16 个。"""
    if not text:
        return []
    _out = []
    _seen = set()
    for _tok in _TOKEN_RE.findall(str(text)):
        _t = _tok.strip().lower()
        if not _t or _t in _STOPWORDS:
            continue
        if len(_t) == 1 and not _t.isalnum():
            continue
        if _t not in _seen:
            _seen.add(_t)
            _out.append(_t)
        if len(_out) >= 16:
            break
    return _out

class ReasoningExperienceIndexer:
    """推理经验双写索引器（规则通道层，不改 ReasoningExperience.py）。"""

    def __init__(self, reasoning_exp=None, node_pool=None,
                 vector_store=None, encode_queue=None):
        self._exp = reasoning_exp if reasoning_exp is not None else get_reasoning_experience()
        self._node_pool = node_pool
        self._store = vector_store
        self._queue = encode_queue
        self._knowledge_tree = None
        self._double_write_enabled = False
        self._records = 0          # 双写次数（验收用）
        self._nodes_written = 0    # 节点副本写入次数（本会话增量）
        self._state_lock = threading.RLock()
        # ★第164批 刀A1：补救/失败模式计数与内存索引
        self._remediation_count = 0
        self._remediation_log: list[dict[str, Any]] = []
        self._failure_modes: list[dict[str, Any]] = []
        self._failure_mode_total = 0   # 失败模式累计（本会话增量）
        # ★第165批 刀A5：累计计数基线（自 data/ 状态文件载入，重启不归零）
        self._base_remediation = 0
        self._base_nodes = 0
        self._base_failure = 0
        self._load_state()

    # ------------------------------------------------------------------
    # 依赖注入（main.py 装配时调用）
    # ------------------------------------------------------------------
    def set_dependencies(self, node_pool=None, vector_store=None,
                         encode_queue=None, knowledge_tree=None):
        """注入知识树 / 向量库 / 编码队列 / 知识树依赖（可多次调用，逐步装配）。"""
        if node_pool is not None:
            self._node_pool = node_pool
        if vector_store is not None:
            self._store = vector_store
        if encode_queue is not None:
            self._queue = encode_queue
        if knowledge_tree is not None:
            self._knowledge_tree = knowledge_tree

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
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.ReasoningExperienceIndexer::search_with_semantic L200")

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
            except Exception as e:
                silent_exc(e, where="nucleus.reasoning.ReasoningExperienceIndexer::search_with_semantic L230")

        # ---- 3. 合并排序 ----
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # ★第164批 刀A1：补救成功蒸馏 + 失败模式索引
    # ------------------------------------------------------------------
    def record_remediation_success(self, question: str, correct_answer: str,
                                   rule_candidate: str | None = None,
                                   confidence: float = 0.8) -> None:
        """★第164批 刀A1：LLM 补救成功路径——(问题, 正确答案) 蒸馏为 L2 节点 + 候选规则。

        仅当双写开启且 node_pool 就绪时写 L2 节点副本；JSON 经验始终记录。
        无 try/except：异常上抛给调用方既有异常边界（不新增静默 except 处理）。
        """
        _q = str(question or "").strip()
        _a = str(correct_answer or "").strip()
        if not _q or not _a:
            return
        self._exp.record(_q, "remediation_success", source="remediation", confidence=float(confidence))
        with self._state_lock:
            self._remediation_count += 1
            self._persist_state()
        self._remediation_log.append({
            "question": _q, "answer": _a, "rule": rule_candidate, "ts": time.time()})
        if len(self._remediation_log) > 500:
            self._remediation_log = self._remediation_log[-300:]
        if self._double_write_enabled and self._node_pool is not None:
            _value = (f"补救成功｜问题：{_q}｜正确答案：{_a}"
                      + (f"｜候选规则：{rule_candidate}" if rule_candidate else ""))
            from nucleus.mnemosyne.PulseNode import PulseNode
            _node = PulseNode(
                value=_value,
                keywords=_split_keywords(_q) if _q else [],
                source_organ=SOURCE_ORGAN,
                evol_level=PulseNode.EVOL_L2,
                importance=PulseNode.IMPORTANCE_B,
                space_path=PATH_REMEDIATION,
            )
            if self._knowledge_tree is not None:
                self._knowledge_tree.register_path(PATH_REMEDIATION)
            _nid = self._node_pool.add(_node)
            with self._state_lock:
                self._nodes_written += 1
                self._persist_state()
            if self._queue is not None:
                self._queue.submit(_nid, _value)
            # ★第164批 刀A2：蒸馏沉淀埋点（L2 节点已写入知识树）
            from nucleus.LLMDependencyMetrics import record_remediation_distilled
            record_remediation_distilled(1)

    def record_failure_mode(self, pattern: str, question: str | None = None,
                            context: str | None = None, confidence: float = 0.3) -> None:
        """★第164批 刀A1：记录推理失败模式，供检测器认领。

        写入 JSON 经验 + 内存索引（get_failure_modes 可查询）；双写开启时
        额外写 L1 节点副本（/推理经验/失败模式/）。无 try/except（异常上抛）。
        """
        _p = str(pattern or "").strip()
        _q = str(question or "").strip()
        if not _p and not _q:
            return
        self._exp.record(_q or _p, "failure_mode", source="remediation", confidence=float(confidence))
        self._failure_modes.append({
            "pattern": _p, "question": _q, "context": context,
            "confidence": float(confidence), "ts": time.time()})
        if len(self._failure_modes) > 500:
            self._failure_modes = self._failure_modes[-300:]
        if self._double_write_enabled and self._node_pool is not None:
            _value = f"推理失败模式｜{_p}｜问题：{_q}｜上下文：{context or ''}"
            from nucleus.mnemosyne.PulseNode import PulseNode
            _node = PulseNode(
                value=_value,
                keywords=_split_keywords(_q) if _q else _split_keywords(_p),
                source_organ=SOURCE_ORGAN,
                evol_level=PulseNode.EVOL_L1,
                importance=PulseNode.IMPORTANCE_B,
                space_path=PATH_FAILURE,
            )
            if self._knowledge_tree is not None:
                self._knowledge_tree.register_path(PATH_FAILURE)
            _nid = self._node_pool.add(_node)
            with self._state_lock:
                self._nodes_written += 1
                self._failure_mode_total += 1
                self._persist_state()
            if self._queue is not None:
                self._queue.submit(_nid, _value)

    def get_failure_modes(self, limit: int = 20) -> list[dict[str, Any]]:
        """★第164批 刀A1：返回近期失败模式（供检测器认领）。"""
        _lim = max(1, int(limit))
        return list(self._failure_modes[-_lim:])

    def get_remediation_successes(self, limit: int = 20) -> list[dict[str, Any]]:
        """★第164批 刀A1：返回近期补救成功记录（验收用）。"""
        _lim = max(1, int(limit))
        return list(self._remediation_log[-_lim:])

    def get_stats(self) -> dict[str, Any]:
        return {
            "double_write_enabled": self._double_write_enabled,
            "records": self._records,
            "nodes_written": self._base_nodes + self._nodes_written,
            "remediation_count": self._base_remediation + self._remediation_count,
            "failure_mode_count": len(self._failure_modes),
            "failure_mode_total": self._base_failure + self._failure_mode_total,
        }

    # ------------------------------------------------------------------
    # ★第165批 刀A5：计数落盘持久化（重启不归零）
    # ------------------------------------------------------------------
    def _load_state(self) -> None:
        """启动时从 data/ 状态文件载入累计计数基线（git-ignored 运行时状态）。"""
        try:
            if os.path.exists(_STATE_PATH):
                with io.open(_STATE_PATH, encoding='utf-8') as _f:
                    _s = json.load(_f)
                self._base_remediation = int(_s.get('remediation_count', 0) or 0)
                self._base_nodes = int(_s.get('nodes_written', 0) or 0)
                self._base_failure = int(_s.get('failure_mode_total', 0) or 0)
        except Exception as _e:
            silent_exc(_e, "nucleus.reasoning.ReasoningExperienceIndexer::_load_state")

    def _persist_state(self) -> None:
        """原子写回累计计数（基线 + 本会话增量）。"""
        try:
            _d = os.path.dirname(_STATE_PATH)
            if _d:
                os.makedirs(_d, exist_ok=True)
            _payload = {
                "remediation_count": self._base_remediation + self._remediation_count,
                "nodes_written": self._base_nodes + self._nodes_written,
                "failure_mode_total": self._base_failure + self._failure_mode_total,
            }
            _tmp = _STATE_PATH + ".tmp"
            with io.open(_tmp, 'w', encoding='utf-8') as _f:
                json.dump(_payload, _f, ensure_ascii=False)
            os.replace(_tmp, _STATE_PATH)
        except Exception as _e:
            silent_exc(_e, "nucleus.reasoning.ReasoningExperienceIndexer::_persist_state")

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


