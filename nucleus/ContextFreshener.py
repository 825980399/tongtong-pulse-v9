# -*- coding: utf-8 -*-
"""
ContextFreshener.py —— 上下文刷新器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 动态刷新对话上下文，保持相关性与时效性
机制: 基于ContextFreshener类实现，包含10个核心方法
定位: 对话管理层
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any
from nucleus.data.DataAccessLayer import safe_read_json



class ContextFreshener:
    """上下文保鲜引擎（分析型，纯函数式，零写入）。"""

    # 保鲜判定阈值
    REFRESH_HEALTH_MIN = 0.35   # 健康度≥此值才值得保鲜
    REFRESH_AGE_DAYS = 3.0      # 记忆年龄≥此天数触发保鲜候选
    COMPRESS_HEALTH_MAX = 0.35  # 健康度<此值才考虑压缩
    COMPRESS_KEYWORDS_MIN = 3   # 关键词≥此数量才值得压缩（有精华可保）

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_analysis: dict[str, Any] | None = None
        self._total_analyzed = 0
        self._created_at = time.time()

    # ========== 保鲜判定 ==========

    @staticmethod
    def _memory_age_days(memory: dict[str, Any]) -> float:
        ts = memory.get("timestamp", 0) or 0
        try:
            ts = float(ts)  # 兼容字符串时间戳（真实数据为 str）
        except (TypeError, ValueError):
            ts = 0.0
        if not ts:
            return 999.0
        return (time.time() - ts) / 86400.0

    @staticmethod
    def _memory_health(memory: dict[str, Any]) -> float:
        """复刻 ContextSnapshot._assess_memory_health 的评分（激活/关键词/情感/时间），
        保持口径一致，避免保鲜与淘汰判断冲突。"""
        score = 0.0
        qc = memory.get("query_count", 0) or 0
        if qc >= 3:
            score += 0.3
        elif qc >= 1:
            score += 0.15
        kws = memory.get("keywords", []) or []
        if len(kws) >= 4:
            score += 0.2
        elif len(kws) >= 2:
            score += 0.1
        tone = memory.get("emotional_tone", "neutral")
        score += {"positive": 0.3, "concerned": 0.25, "negative": 0.2, "neutral": 0.1}.get(tone, 0.1)
        ts = memory.get("timestamp", 0) or 0
        try:
            ts = float(ts)  # 兼容字符串时间戳（真实数据为 str）
        except (TypeError, ValueError):
            ts = 0.0
        if ts:
            age_days = (time.time() - ts) / 86400.0
            if age_days < 1:
                score += 0.2
            elif age_days < 7:
                score += 0.15
            elif age_days < 30:
                score += 0.1
            elif age_days < 90:
                score += 0.05
        return min(1.0, score)

    def classify_memory(self, memory: dict[str, Any]) -> str:
        """单条记忆分类：refresh / compress / keep。

        决策优先级：
          1. refresh：健康度≥阈值 且 年龄≥保鲜天数 → 时间衰减导致边缘化，值得保鲜。
          2. compress：健康度<压缩阈值 且 关键词丰富（有精华可保）→ 智能压缩。
          3. keep：其余保持不动。
        """
        _health = self._memory_health(memory)
        _age = self._memory_age_days(memory)
        _kw = len(memory.get("keywords", []) or [])

        if _health >= self.REFRESH_HEALTH_MIN and _age >= self.REFRESH_AGE_DAYS:
            return "refresh"
        if _health < self.COMPRESS_HEALTH_MAX and _kw >= self.COMPRESS_KEYWORDS_MIN:
            return "compress"
        return "keep"

    # ========== 数据加载 ==========

    def load_conversation_data(self, base_dir: str = "data/context") -> dict[str, Any]:
        """
        直接加载对话记忆数据（与 ContextSnapshot 同源）。

        说明：ContextSnapshot.load_conversation_memory(user_name=None) 只返回用户摘要
        （不含 memories），故此处直接读 conversation_memory.json 拿到完整记忆，
        保证保鲜分析与淘汰逻辑使用同一份数据源（口径一致、零冲突）。

        Returns:
            {"users": {user: {"memories": [...]}}} 或空结构。
        """
        try:
            _path = os.path.join(base_dir, "conversation_memory.json")
            if not os.path.exists(_path):
                return {"users": {}}
            _data = safe_read_json(_path, default={})
            if not isinstance(_data, dict):
                return {"users": {}}
            return _data
        except Exception as e:
            print(f"[WARNING] ContextFreshener.py:118: {type(e).__name__}: {e}")
            return {"users": {}}

    # ========== 分析 ==========

    def analyze(self, conversation_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        分析对话记忆，生成保鲜建议清单。

        Args:
            conversation_data: ContextSnapshot 的 conversation_memory.json 内容
                （结构 {"users": {user: {"memories": [...]}}}）。
                为空时用内存中的最近数据（若存在）。

        Returns:
            {
              "refresh_candidates": [{"user_name", "question", "health", "age_days"}],
              "compress_candidates": [{"user_name", "question", "health", "keywords"}],
              "total_analyzed": int,
              "summary": str,
            }
        """
        _data = conversation_data or (self._last_analysis or {}).get("_data", {})
        refresh_list: list[dict[str, Any]] = []
        compress_list: list[dict[str, Any]] = []
        total = 0

        users = (_data or {}).get("users", {}) or {}
        for uname, partition in users.items():
            for mem in (partition or {}).get("memories", []) or []:
                total += 1
                _cls = self.classify_memory(mem)
                if _cls == "refresh":
                    refresh_list.append({
                        "user_name": uname,
                        "question": (mem.get("question") or "")[:60],
                        "health": round(self._memory_health(mem), 2),
                        "age_days": round(self._memory_age_days(mem), 1),
                    })
                elif _cls == "compress":
                    compress_list.append({
                        "user_name": uname,
                        "question": (mem.get("question") or "")[:60],
                        "health": round(self._memory_health(mem), 2),
                        "keywords": (mem.get("keywords") or [])[:8],
                    })

        with self._lock:
            self._total_analyzed += total
            self._last_analysis = {
                "refresh_candidates": refresh_list,
                "compress_candidates": compress_list,
                "total_analyzed": total,
                "summary": (
                    f"上下文保鲜分析: {total}条记忆，"
                    f"{len(refresh_list)}条建议保鲜(时间衰减边缘)，"
                    f"{len(compress_list)}条建议压缩(保留精华)"
                ),
                "_data": _data,
            }
        return {k: v for k, v in self._last_analysis.items() if k != "_data"}

    # ========== 查询 ==========

    def get_refresh_report(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._last_analysis:
                return None
            return {k: v for k, v in self._last_analysis.items() if k != "_data"}

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total_analyzed": self._total_analyzed,
                "created_at": self._created_at,
                "last_summary": (self._last_analysis or {}).get("summary", ""),
            }


# 全局单例
_instance: ContextFreshener | None = None
_instance_lock = threading.Lock()


def get_context_freshener() -> ContextFreshener:
    """获取全局上下文保鲜引擎单例（懒初始化）。"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = ContextFreshener()
    return _instance


