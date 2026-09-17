# -*- coding: utf-8 -*-
"""
SearchIntentClassifier.py —— 搜索意图分类器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 分类用户搜索意图，路由到对应搜索策略
机制: 基于SearchIntentClassifier类实现，包含10个核心方法
定位: 搜索管理层
"""

import json
import os
import threading
import time
from collections.abc import Callable
from typing import Any


# ===== 内置基础规则（不可被进化覆盖） =====
BUILTIN_RULES: dict[str, dict[str, Any]] = {
    "fact_query": {
        "keywords": ["是什么", "什么是", "多少", "是谁", "什么时候", "定义", "含义", "哪个", "哪一年", "是多少", "何为", "有哪些", "为什么"],
        "confidence": 0.7,
    },
    "concept_learning": {
        "keywords": ["原理", "机制", "教程", "入门", "详解", "怎么学", "如何理解", "概念", "基础", "流程"],
        "confidence": 0.7,
    },
    "error_diagnosis": {
        "keywords": ["报错", "失败", "异常", "错误", "bug", "解决", "排查", "崩溃", "修复", "问题", "error", "exception"],
        "confidence": 0.75,
    },
    "trend_watching": {
        "keywords": ["最新", "热点", "新闻", "动态", "趋势", "前沿", "2026", "近日", "最近", "更新"],
        "confidence": 0.7,
    },
    "deep_research": {
        "keywords": ["综述", "对比", "跨领域", "分析", "研究", "深入", "探索", "全景", "体系", "架构"],
        "confidence": 0.65,
    },
}

# ===== 意图 → 搜索策略映射 =====
INTENT_STRATEGY: dict[str, dict[str, Any]] = {
    "fact_query": {"deep_search": False, "priority": "normal"},
    "concept_learning": {"deep_search": True, "priority": "normal"},
    "error_diagnosis": {"deep_search": True, "priority": "high"},
    "trend_watching": {"deep_search": False, "priority": "normal"},
    "deep_research": {"deep_search": True, "priority": "high"},
}


class SearchIntentClassifier:
    """搜索意图分类器（单例）。"""

    def __init__(self, rules_path: str = "data/knowledge/search_intent_rules.json",
                 max_evolution_samples: int = 200):
        self._rules_path = rules_path
        self._max_evolution_samples = max_evolution_samples

        # 用户层规则（进化生成，可清空回退内置）
        self._user_rules: dict[str, dict[str, Any]] = {}
        # 进化素材缓存（有上限）
        self._evolution_samples: list[dict[str, Any]] = []
        self._lock = threading.Lock()

        # LLM 兜底回调（由外部注入，避免模块间强耦合）
        self._llm_callback: Callable[[str], str | None] | None = None

        # 统计
        self._total_classified = 0
        self._llm_fallback_count = 0
        self._mismatch_count = 0

        self._load_user_rules()

    # ========== 规则加载/持久化 ==========

    def _load_user_rules(self):
        """从 JSON 加载用户层规则（进化生成），失败则空字典。"""
        try:
            if os.path.exists(self._rules_path):
                with open(self._rules_path, encoding="utf-8") as f:
                    data = json.load(f)
                    self._user_rules = data.get("user_rules", {})
        except Exception as e:
            print(f"[WARNING] SearchIntentClassifier.py:82: {type(e).__name__}: {e}")
            self._user_rules = {}

    def _save_user_rules(self):
        """持久化用户层规则到 JSON。"""
        try:
            os.makedirs(os.path.dirname(self._rules_path), exist_ok=True)
            with open(self._rules_path, "w", encoding="utf-8") as f:
                json.dump({"user_rules": self._user_rules, "updated_at": time.time()},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # 持久化失败不影响分类

    def reset_user_rules(self):
        """一键清空用户层规则，回退到出厂内置规则。"""
        with self._lock:
            self._user_rules = {}
            self._save_user_rules()

    # ========== 分类 ==========

    def classify(self, text: str) -> dict[str, Any]:
        """分类搜索意图。

        Returns:
            {
                "intent": str,          # 5 类意图之一
                "confidence": float,    # 0-1
                "source": "rule"/"llm", # 分类来源
                "strategy": {...},      # 搜索策略
            }
        """
        self._total_classified += 1
        _rule_result = self._classify_by_rule(text)

        # 高置信度：直接返回规则结果，不调 LLM
        if _rule_result["confidence"] >= 0.65:
            _rule_result["source"] = "rule"
            _rule_result["strategy"] = INTENT_STRATEGY.get(_rule_result["intent"], INTENT_STRATEGY["fact_query"])
            return _rule_result

        # 低置信度：尝试 LLM 兜底
        if self._llm_callback:
            try:
                _llm_intent = self._call_llm_fallback(text)
                if _llm_intent:
                    with self._lock:
                        self._llm_fallback_count += 1
                    # 记录不一致案例（进化素材）
                    if _llm_intent != _rule_result["intent"]:
                        self._record_mismatch(text, _rule_result["intent"], _llm_intent)
                    return {
                        "intent": _llm_intent,
                        "confidence": 0.85,
                        "source": "llm",
                        "strategy": INTENT_STRATEGY.get(_llm_intent, INTENT_STRATEGY["fact_query"]),
                    }
            except Exception:
                pass  # LLM 失败，降级用规则结果

        # LLM 不可用或失败：降级用规则结果（置信度偏低）
        _rule_result["source"] = "rule"
        _rule_result["strategy"] = INTENT_STRATEGY.get(_rule_result["intent"], INTENT_STRATEGY["fact_query"])
        return _rule_result

    def _classify_by_rule(self, text: str) -> dict[str, Any]:
        """本地规则分类（内置规则 + 用户层规则）。"""
        if not text:
            return {"intent": "fact_query", "confidence": 0.0}

        _text = text.lower()
        _best_intent = "fact_query"
        _best_score = 0.0

        # 合并内置 + 用户层规则（用户层同名规则的关键词做增量补充，不覆盖）
        _merged: dict[str, dict[str, Any]] = {}
        for intent, rule in BUILTIN_RULES.items():
            _merged[intent] = {
                "keywords": list(rule.get("keywords", [])),
                "confidence": rule.get("confidence", 0.7),
            }
        for intent, rule in self._user_rules.items():
            if intent not in _merged:
                _merged[intent] = {"keywords": [], "confidence": rule.get("confidence", 0.7)}
            _merged[intent]["keywords"].extend(rule.get("keywords", []))

        # 关键词命中打分
        for intent, rule in _merged.items():
            _hits = sum(1 for kw in rule.get("keywords", []) if kw.lower() in _text)
            if _hits > 0:
                # 命中数越多置信度越高，基于规则基础置信度
                _score = min(0.95, rule.get("confidence", 0.7) + (_hits - 1) * 0.05)
                if _score > _best_score:
                    _best_score = _score
                    _best_intent = intent

        return {"intent": _best_intent, "confidence": _best_score}

    def _call_llm_fallback(self, text: str) -> str | None:
        """通过注入的 LLM 回调做语义兜底分类。"""
        if not self._llm_callback:
            return None
        _prompt = (
            f"请将以下搜索意图分类为以下5类之一（只返回类别名，不要解释）：\n"
            f"fact_query(事实查询) / concept_learning(概念学习) / "
            f"error_diagnosis(错误排查) / trend_watching(趋势关注) / deep_research(深度研究)\n\n"
            f"搜索内容: {text}\n\n类别:"
        )
        _reply = self._llm_callback(_prompt)
        if not _reply:
            return None
        _reply = _reply.strip().lower()
        for intent in ("fact_query", "concept_learning", "error_diagnosis", "trend_watching", "deep_research"):
            if intent in _reply:
                return intent
        return None

    # ========== 规则自进化 ==========

    def set_llm_callback(self, callback: Callable[[str], str | None] | None):
        """注入 LLM 兜底回调（由外部传入，如 PulseLung._call_remote_api 的封装）。"""
        self._llm_callback = callback

    def _record_mismatch(self, text: str, rule_intent: str, llm_intent: str):
        """记录规则分类与 LLM 分类不一致的案例（进化素材）。"""
        with self._lock:
            # ★7-1: 外层 with self._lock 已持有该锁；
            # threading.Lock 不可重入，此处不得再次 acquire（否则死锁）。
            self._mismatch_count += 1
            self._evolution_samples.append({
                "text": text[:100],
                "rule_intent": rule_intent,
                "llm_intent": llm_intent,
                "timestamp": time.time(),
            })
            # 素材上限保护
            if len(self._evolution_samples) > self._max_evolution_samples:
                self._evolution_samples = self._evolution_samples[-self._max_evolution_samples:]

    def get_evolution_suggestions(self) -> list[dict[str, Any]]:
        """生成规则进化建议（供 PatchManager 审批链使用）。

        分析进化素材，找出高频「规则误分类」模式，生成规则补丁建议。
        建议以「用户层规则增量」形式呈现，不覆盖内置规则。
        """
        with self._lock:
            if not self._evolution_samples:
                return []

            # 统计每个 (rule_intent -> llm_intent) 的频次
            from collections import Counter
            _pattern_counter = Counter(
                (s["rule_intent"], s["llm_intent"]) for s in self._evolution_samples
            )

            _suggestions = []
            for (rule_intent, llm_intent), count in _pattern_counter.most_common(10):
                if count < 3:  # 阈值：至少出现3次才建议
                    continue
                # 提取该模式下的样本关键词
                _samples = [s["text"] for s in self._evolution_samples
                            if s["rule_intent"] == rule_intent and s["llm_intent"] == llm_intent]
                _suggestions.append({
                    "pattern": f"{rule_intent} -> {llm_intent}",
                    "count": count,
                    "samples": _samples[:5],
                    "suggested_action": "add_keywords_to_intent",
                    "target_intent": llm_intent,
                    "note": f"规则将「{rule_intent}」误分类为「{llm_intent}」{count}次，建议为「{llm_intent}」补充相关关键词",
                })
            return _suggestions

    def apply_evolution(self, intent: str, keywords: list[str], confidence: float = 0.7):
        """应用进化规则（用户层规则增量，不覆盖内置规则）。"""
        with self._lock:
            if intent not in self._user_rules:
                self._user_rules[intent] = {"keywords": [], "confidence": confidence}
            _existing = self._user_rules[intent].get("keywords", [])
            _new = [kw for kw in keywords if kw not in _existing]
            self._user_rules[intent]["keywords"].extend(_new)
            self._save_user_rules()

    # ========== 统计 ==========

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total_classified": self._total_classified,
                "llm_fallback_count": self._llm_fallback_count,
                "mismatch_count": self._mismatch_count,
                "evolution_samples": len(self._evolution_samples),
                "user_rules": len(self._user_rules),
            }


# ===== 模块级单例 =====
_classifier: SearchIntentClassifier | None = None
_classifier_lock = threading.Lock()


def get_intent_classifier() -> SearchIntentClassifier:
    """获取搜索意图分类器单例。"""
    global _classifier
    if _classifier is None:
        with _classifier_lock:
            if _classifier is None:
                _classifier = SearchIntentClassifier()
    return _classifier


def shutdown_intent_classifier() -> None:
    """复位单例。"""
    global _classifier
    _classifier = None
