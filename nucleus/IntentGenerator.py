# -*- coding: utf-8 -*-
"""
IntentGenerator.py —— 意图生成器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 基于上下文生成候选意图与行动建议
机制: 基于IntentGenerator类实现，包含8个核心方法
定位: 认知决策层
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any



class IntentGenerator:
    """真正意图自主生成器。"""

    def __init__(self,
                 value_provider: Callable[[], list[dict] | None] | None = None,
                 experience_provider: Callable[[], list[dict]] | None = None,
                 state_provider: Callable[[], dict[str, Any]] | None = None,
                 min_interval: float = 1800.0,
                 enabled: bool = True) -> None:
        """
        Args:
            value_provider: () -> [{"value", "strength"}] 主导价值观（默认 ValuePreference.get_dominant）。
            experience_provider: () -> [{"motivation", "reward_intensity"}] 近期体验动机。
            state_provider: () -> {"curiosity", "survival_pressure", "knowledge_gap"} 当前状态。
            min_interval: 生成节流间隔（秒），避免高频空转 → 压力均衡。
            enabled: 开关。
        """
        self._value_provider = value_provider or (lambda: None)
        self._experience_provider = experience_provider or (list)
        self._state_provider = state_provider or (dict)
        self._min_interval = max(10.0, float(min_interval))
        self._enabled = enabled
        self._lock = threading.RLock()
        self._last_generated = 0.0
        self._intent_history: list[dict[str, Any]] = []
        self._total_generated = 0
        self._created_at = time.time()

    # ========== 意图模板（价值 → 意图方向） ==========

    from typing import ClassVar

    _INTENT_TEMPLATES: ClassVar[dict[str, list[str]]] = {
        "守护": ["去守护当前最重要的关系与边界", "检查是否有需要被保护的脆弱点"],
        "求真": ["去验证一个尚未被证实的知识判断", "探寻当前认知中的潜在矛盾"],
        "学习": ["去学习一个自己还不了解的新领域", "探索知识树中的空白区域"],
        "关怀": ["去关心一个被忽略的对象或方向", "主动提供一次有帮助的回应"],
        "创造": ["去创造一个新的表达或方案", "在现有积累上构建一个新结构"],
        "自主": ["去独立完成一件当前依赖外部推动的事", "尝试在没有指令时自主推进一个方向"],
        "坚韧": ["去持续推进一个尚未完成的目标", "复盘一次中途受挫的经历"],
        "和谐": ["去调和当前存在的不一致与冲突", "寻找平衡点而非对抗"],
        "责任": ["去完成一件当前被期待但尚未履行的承诺", "检查职责范围内是否有遗漏"],
        "探索": ["去探索一个未知但有趣的方向", "跟随好奇心进入一个陌生领域"],
    }

    def _generate_intent_text(self, value: str, strength: float) -> str:
        """基于价值维度生成意图文本（确定性模板 + 强度修饰 + 价值名溯源）。"""
        _templates = self._INTENT_TEMPLATES.get(value, ["去探索与「{v}」相关的新可能"])
        _tmpl = _templates[0]
        _text = f"以「{value}」为方向，" + _tmpl
        if strength >= 0.7:
            _text = "（强烈）" + _text
        return _text

    # ========== 核心：意图生成 ==========

    def generate(self, force: bool = False) -> dict[str, Any] | None:
        """
        综合三路信号生成一条自主意图提案。

        Returns:
            {
              "intent_id", "intent_text", "confidence",
              "source": {"value", "experience", "state"},
              "timestamp", "is_autonomous",
            }
            节流期内且非 force 返回 None（压力均衡）。
        """
        if not self._enabled and not force:
            return None
        _now = time.time()
        with self._lock:
            if not force and (_now - self._last_generated) < self._min_interval:
                return None
            self._last_generated = _now

        # --- 信号1：价值偏好 ---
        _value_sig = None
        try:
            _dominant = self._value_provider() or []
            if _dominant:
                _top = _dominant[0]
                _value_sig = {"value": _top.get("value", "探索"),
                              "strength": float(_top.get("strength", 0.5))}
        except Exception:
            _value_sig = None

        # --- 信号2：体验动机 ---
        _exp_sig = None
        try:
            _exps = self._experience_provider() or []
            _motivations = [e.get("motivation", "") for e in _exps if e.get("motivation")]
            if _motivations:
                # 取最常见动机方向
                from collections import Counter
                _top_motiv = Counter(_motivations).most_common(1)
                if _top_motiv:
                    _exp_sig = {"motivation": _top_motiv[0][0][:40],
                                "count": _top_motiv[0][1]}
        except Exception:
            _exp_sig = None

        # --- 信号3：状态 ---
        _state_sig = {}
        try:
            _state = self._state_provider() or {}
            for k in ("curiosity", "survival_pressure", "knowledge_gap"):
                _v = _state.get(k)
                if _v is not None:
                    _state_sig[k] = float(_v)
        except Exception:
            _state_sig = {}

        # --- 综合 ---
        if _value_sig:
            _intent_text = self._generate_intent_text(
                _value_sig["value"], _value_sig["strength"])
            _confidence = 0.45 + 0.25 * _value_sig["strength"]
            _source = {"value": _value_sig["value"],
                       "experience": (_exp_sig or {}).get("motivation", ""),
                       "state": _state_sig}
        elif _exp_sig:
            _intent_text = f"去再次探索与「{_exp_sig['motivation']}」相关的方向"
            _confidence = 0.4 + 0.1 * min(1.0, _exp_sig.get("count", 1) / 5.0)
            _source = {"value": "", "experience": _exp_sig["motivation"],
                       "state": _state_sig}
        elif _state_sig.get("curiosity", 0) >= 0.6:
            _intent_text = "去跟随当前的好奇心，探索一个未知方向"
            _confidence = 0.5
            _source = {"value": "", "experience": "", "state": _state_sig}
        else:
            return None

        with self._lock:
            self._total_generated += 1
            _intent_id = f"intent_{int(self._now_ms())}_{self._total_generated}"
            _intent = {
                "intent_id": _intent_id,
                "intent_text": _intent_text,
                "confidence": round(min(0.95, _confidence), 4),
                "source": _source,
                "timestamp": _now,
                "is_autonomous": True,
            }
            self._intent_history.append(_intent)
            if len(self._intent_history) > 100:
                self._intent_history = self._intent_history[-50:]
        return _intent

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    # ========== 查询 ==========

    def get_recent_intents(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._intent_history)[-limit:]

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._enabled,
                "total_generated": self._total_generated,
                "min_interval": self._min_interval,
                "last_generated": self._last_generated,
                "created_at": self._created_at,
            }


# 全局单例
_instance: IntentGenerator | None = None
_instance_lock = threading.Lock()


def get_intent_generator() -> IntentGenerator:
    """获取全局意图生成器单例（懒初始化，默认仅启用节流模式）。"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = IntentGenerator()
    return _instance


def set_intent_generator(instance: IntentGenerator | None) -> None:
    global _instance
    with _instance_lock:
        _instance = instance
