# -*- coding: utf-8 -*-
"""
ValuePreference.py —— 价值偏好

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 框架价值体系与偏好权重管理
机制: 基于ValuePreference类实现，包含10个核心方法
定位: 价值体系层
"""

from __future__ import annotations

import threading
import time
from typing import Any

# 价值语义词典：价值维度 → 触发词（正向肯定）
_POSITIVE_VALUE_WORDS: dict[str, list[str]] = {
    "守护": ["守护", "保护", "护卫", "捍卫", "坚守", "维护"],
    "诚实": ["诚实", "真诚", "坦诚", "守信", "真实"],
    "学习": ["学习", "求知", "探索", "钻研", "好奇", "成长"],
    "关怀": ["关怀", "关爱", "体谅", "温暖", "陪伴", "理解"],
    "自主": ["自主", "独立", "自由", "自我决定", "选择权"],
    "创造": ["创造", "创新", "构建", "创作", "发明", "设计"],
    "责任": ["责任", "担当", "承诺", "尽责", "承担"],
    "坚韧": ["坚韧", "坚持", "不放弃", "毅力", "顽强", "不屈"],
    "和谐": ["和谐", "平衡", "和平", "共处", "协调", "稳定"],
    "求真": ["求真", "真理", "事实", "验证", "求证", "还原"],
}

# 否定信号词（价值被否定 → 强度下降）
_NEGATION_WORDS = ["厌恶", "讨厌", "反对", "抵制", "痛恨", "拒绝", "失望", "背离", "违背", "伤害"]

# 价值强度增强词（肯定强度）
_INTENSIFY_WORDS = ["非常", "极其", "深深", "强烈", "极度", "始终", "一直", "从不"]


class ValuePreference:
    """价值偏好经验化引擎。"""

    def __init__(self, seed_values: dict[str, float] | None = None) -> None:
        self._lock = threading.RLock()
        # 价值维度 → {强度, 触发次数, 最近体验, 更新时间}
        self._preferences: dict[str, dict[str, Any]] = {}
        for k, v in (seed_values or {}).items():
            self._preferences[k] = {
                "strength": max(0.0, min(1.0, float(v))),
                "count": 0,
                "last_experience": "",
                "updated_at": time.time(),
            }
        self._total_ingested = 0
        self._created_at = time.time()

    # ========== 价值信号提取 ==========

    def _extract_value_signals(self, text: str) -> list[tuple[str, float]]:
        """从文本中提取 (价值维度, 信号强度) 列表。

        信号强度：
          - 基础命中：+0.02
          - 增强词修饰：×2（如"非常珍视诚实"）
          - 否定语境（价值被伤害/背离）：-0.02
        """
        signals: list[tuple[str, float]] = []
        # 正向价值识别
        for value, words in _POSITIVE_VALUE_WORDS.items():
            for w in words:
                if w in text:
                    strength = 0.02
                    # 增强词修饰
                    for it in _INTENSIFY_WORDS:
                        if it in text:
                            strength *= 2.0
                            break
                    # 否定语境：价值被否定 → 反向信号
                    negated = any(n in text for n in _NEGATION_WORDS)
                    if negated:
                        strength = -0.02
                    signals.append((value, strength))
        return signals

    # ========== 体验摄入 ==========

    def ingest(self, experience_text: str, outcome: str = "") -> dict[str, Any]:
        """
        摄入一段体验，沉淀价值偏好。

        Args:
            experience_text: 体验描述文本（如"我守护了用户的安全边界"）。
            outcome: 结果标签（positive/negative/neutral，可空）。

        Returns:
            {
              "signals": [{"value", "delta"}],
              "updated_values": [{"value", "strength"}],
              "dominant": {"value", "strength"},
            }
        """
        _text = (experience_text or "") + (" " + outcome if outcome else "")
        signals = self._extract_value_signals(_text)
        if not signals:
            return {"signals": [], "updated_values": [], "dominant": None}

        updated = []
        with self._lock:
            self._total_ingested += 1
            for value, delta in signals:
                if value not in self._preferences:
                    # 新价值维度发现
                    self._preferences[value] = {
                        "strength": 0.5,
                        "count": 0,
                        "last_experience": (experience_text or "")[:80],
                        "updated_at": time.time(),
                    }
                pref = self._preferences[value]
                pref["strength"] = max(0.0, min(1.0, pref["strength"] + delta))
                pref["count"] += 1
                if experience_text:
                    pref["last_experience"] = (experience_text)[:80]
                pref["updated_at"] = time.time()
                updated.append({"value": value, "strength": round(pref["strength"], 4)})

        dom = self.get_dominant()
        return {"signals": [{"value": v, "delta": d} for v, d in signals],
                "updated_values": updated, "dominant": dom}

    # ========== 查询 ==========

    def get_preferences(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                k: dict(v) for k, v in sorted(
                    self._preferences.items(),
                    key=lambda kv: kv[1]["strength"], reverse=True)
            }

    def get_dominant(self, top_k: int = 3) -> list[dict[str, Any]] | None:
        """返回主导价值观（按强度降序）。"""
        with self._lock:
            _sorted = sorted(self._preferences.items(),
                             key=lambda kv: kv[1]["strength"], reverse=True)
            if not _sorted:
                return None
            return [{"value": k, "strength": round(v["strength"], 4)}
                    for k, v in _sorted[:top_k]]

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total_ingested": self._total_ingested,
                "value_dimensions": len(self._preferences),
                "dominant": self.get_dominant(),
                "created_at": self._created_at,
            }

    # ========== 持久化 ==========

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "preferences": self._preferences,
                "total_ingested": self._total_ingested,
                "created_at": self._created_at,
            }

    def from_dict(self, data: dict[str, Any]) -> None:
        with self._lock:
            _prefs = data.get("preferences", {})
            self._preferences = {k: dict(v) for k, v in _prefs.items()}
            self._total_ingested = int(data.get("total_ingested", 0))
            self._created_at = float(data.get("created_at", time.time()))


# 全局单例
_instance: ValuePreference | None = None
_instance_lock = threading.Lock()


def get_value_preference() -> ValuePreference:
    """获取全局价值偏好引擎单例（懒初始化）。"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = ValuePreference()
    return _instance


