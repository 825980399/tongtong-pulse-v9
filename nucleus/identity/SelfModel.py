# -*- coding: utf-8 -*-
"""
SelfModel.py —— 自我模型

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 框架自我认知模型与身份表征
机制: 基于SelfModel类实现，包含10个核心方法
定位: 身份核心层

⚠️ @deprecated (157-D C-3 自我感知三件 / Q157-3 已裁 · 165批收口):
    本模块为「自我感知三件套」之一，与另两件并列封存（详见登记册 C-3 行，统一口径，不再逐一互引模块名）。
    全仓无任何外部 import / 实例化（仅模块内自引用单例）。
    封存待复活专批（登记册 C-3）：复活须走专门批（自我感知整体通电方案），禁止新代码 import 本模块。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底
from nucleus._silent_except import silent_exc



class SelfModel(SilentLogMixin):
    """统一自我模型。"""

    def __init__(self, cache_ttl: float = 5.0,
                 enabled: bool = True) -> None:
        """
        Args:
            cache_ttl: 缓存有效期秒数（压力均衡，避免高频查询）
            enabled: 开关
        """
        self._cache_ttl = max(0.5, float(cache_ttl))
        self._enabled = enabled

        # 各模块引用（依赖注入，未注入时为 None）
        self._self_awareness = None
        self._narrative_self = None
        self._hormones = None
        self._stress_axis = None
        self._runtime_metrics = None
        self._runtime_tempo = None
        self._trajectory = None
        self._feedback_loop = None
        self._strategy_selector = None

        # 自定义维度注册（可扩展）
        self._custom_dimensions: dict[str, Callable[[], dict[str, Any]]] = {}

        # 缓存
        self._cache: dict[str, tuple[float, Any]] = {}

        # 注入运行轨迹单例（严格受 ENABLE_RUNTIME_TRAJECTORY_PERSIST 灰度开关保护：
        # 未启用时保持 _trajectory=None，读取分支走 default，行为与现状完全一致，零回退风险）
        try:
            import config as _cfg
            if getattr(_cfg, "ENABLE_RUNTIME_TRAJECTORY_PERSIST", False):
                from nucleus.runtime.RuntimeTrajectory import get_runtime_trajectory
                self.set_trajectory(get_runtime_trajectory())
        except Exception as e:
            silent_exc(e, where="nucleus.identity.SelfModel::__init__ L62")

    # ========== 依赖注入 ==========

    def set_self_awareness(self, ref: Any) -> None:
        self._self_awareness = ref

    def set_narrative_self(self, ref: Any) -> None:
        self._narrative_self = ref

    def set_hormones(self, ref: Any) -> None:
        self._hormones = ref

    def set_stress_axis(self, ref: Any) -> None:
        self._stress_axis = ref

    def set_runtime_metrics(self, ref: Any) -> None:
        self._runtime_metrics = ref

    def set_runtime_tempo(self, ref: Any) -> None:
        self._runtime_tempo = ref

    def set_trajectory(self, ref: Any) -> None:
        self._trajectory = ref

    def set_feedback_loop(self, ref: Any) -> None:
        self._feedback_loop = ref


    def register_dimension(self, name: str,
                           provider: Callable[[], dict[str, Any]]) -> None:
        """注册自定义自我认知维度。"""
        self._custom_dimensions[name] = provider

    # ========== 核心：统一查询 ==========

    def get_self_snapshot(self) -> dict[str, Any]:
        """获取完整自我快照（我是谁/在做什么/感觉如何/健康吗/做过什么）。"""
        if not self._enabled:
            return {"enabled": False, "timestamp": time.time()}

        return {
            "timestamp": time.time(),
            "datetime": time.strftime('%Y-%m-%d %H:%M:%S'),
            "identity": self._get_identity(),
            "current_state": self._get_current_state(),
            "feelings": self._get_feelings(),
            "health": self._get_health(),
            "history": self._get_history(),
            "custom_dimensions": self._get_custom_dimensions(),
        }

    # ========== 维度：我是谁 ==========

    def _get_identity(self) -> dict[str, Any]:
        """身份认知：价值观/成长阶段/知识画像。"""
        _result: dict[str, Any] = {"source": "default", "values": [], "growth_stage": "unknown"}
        try:
            if self._narrative_self and hasattr(self._narrative_self, "get_stats"):
                _stats = self._narrative_self.get_stats() or {}
                _result["source"] = "PulseNarrativeSelf"
                _result["dynamic_values"] = _stats.get("dynamic_values", [])
                _result["life_stage"] = _stats.get("life_stage", "unknown")
            if self._self_awareness and hasattr(self._self_awareness, "get_knowledge_profile"):
                _profile = self._self_awareness.get_knowledge_profile() or {}
                _result["knowledge_profile"] = _profile
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return _result

    # ========== 维度：我在做什么 ==========

    def _get_current_state(self) -> dict[str, Any]:
        """当前状态：运行节律/最近活动/推理策略。"""
        _result: dict[str, Any] = {"source": "default", "tempo": "unknown", "recent_activity": []}
        try:
            if self._runtime_tempo and hasattr(self._runtime_tempo, "get_state"):
                _state = self._runtime_tempo.get_state() or {}
                _result["source"] = "RuntimeTempo"
                _result["tempo"] = _state.get("state", "unknown")
                _result["is_conversation"] = _state.get("is_conversation", False)
            if self._trajectory and hasattr(self._trajectory, "get_recent"):
                _recent = self._trajectory.get_recent(5) or []
                _result["recent_activity"] = [
                    {"time": r.get("datetime", ""),
                     "type": r.get("event_type", ""),
                     "source": r.get("source", ""),
                     "content": r.get("content", "")[:80]}
                    for r in _recent
                ]
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return _result

    # ========== 维度：我感觉如何 ==========

    def _get_feelings(self) -> dict[str, Any]:
        """情绪体感：情绪状态/激素水平/压力等级。"""
        _result: dict[str, Any] = {"source": "default", "emotion": "neutral", "stress": 0.0}
        try:
            if self._hormones and hasattr(self._hormones, "get_current_emotion"):
                _emo = self._hormones.get_current_emotion() or {}
                _result["source"] = "PulseHormones"
                _result["emotion"] = _emo.get("emotion", "neutral")
                _result["emotion_intensity"] = _emo.get("intensity", 0.0)
            if self._stress_axis and hasattr(self._stress_axis, "get_stress_level"):
                _stress = self._stress_axis.get_stress_level() or 0.0
                _result["stress_level"] = float(_stress)
            if self._feedback_loop and hasattr(self._feedback_loop, "get_stats"):
                _fb = self._feedback_loop.get_stats() or {}
                _result["reasoning_quality"] = {
                    "success_rate": _fb.get("success_rate", 0.0),
                    "recommended_params": _fb.get("recommended_params", {}),
                }
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return _result

    # ========== 维度：我健康吗 ==========

    def _get_health(self) -> dict[str, Any]:
        """系统健康度：运行指标/错误率/资源压力。"""
        _result: dict[str, Any] = {"source": "default", "health_score": 1.0, "errors": 0}
        try:
            if self._runtime_metrics and hasattr(self._runtime_metrics, "get_snapshot"):
                _snap = self._runtime_metrics.get_snapshot() or {}
                _result["source"] = "RuntimeMetrics"
                _result["pulse_count"] = _snap.get("pulse_count", 0)
                _result["error_count"] = _snap.get("error_count", 0)
                _result["reentry_count"] = _snap.get("reentry_count", 0)
                _result["uptime"] = _snap.get("uptime_seconds", 0)
                # 简单健康度计算（错误率越低越健康）
                _total = max(1, _snap.get("pulse_count", 1))
                _errors = _snap.get("error_count", 0)
                _result["health_score"] = round(max(0.0, 1.0 - _errors / _total), 3)
            if self._trajectory and hasattr(self._trajectory, "get_stats"):
                _trj = self._trajectory.get_stats() or {}
                _result["trajectory_records"] = _trj.get("total_records", 0)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return _result

    # ========== 维度：我做过什么 ==========

    def _get_history(self) -> dict[str, Any]:
        """运行历史：轨迹统计/事件分布。"""
        _result: dict[str, Any] = {"source": "default", "total_events": 0}
        try:
            if self._trajectory and hasattr(self._trajectory, "get_stats"):
                _stats = self._trajectory.get_stats() or {}
                _result["source"] = "RuntimeTrajectory"
                _result["total_events"] = _stats.get("total_records", 0)
                _result["event_distribution"] = _stats.get("event_type_counts", {})
                _result["uptime_seconds"] = _stats.get("uptime_seconds", 0)
                _result["records_per_minute"] = _stats.get("records_per_minute", 0)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return _result

    # ========== 自定义维度 ==========

    def _get_custom_dimensions(self) -> dict[str, Any]:
        """获取所有注册的自定义维度。"""
        _result: dict[str, Any] = {}
        for name, provider in self._custom_dimensions.items():
            try:
                _result[name] = provider()
            except Exception:
                _result[name] = {"error": "provider_failed"}
        return _result

    # ========== 便捷查询 ==========

    def who_am_i(self) -> str:
        """我是谁（文本摘要）。"""
        _id = self._get_identity()
        _values = _id.get("dynamic_values", []) or []
        _stage = _id.get("life_stage", "未知阶段")
        _val_str = "、".join([v.get("value", "") for v in _values[:3]]) if _values else "未定义"
        return f"我是曈曈，当前处于「{_stage}」，核心价值观：{_val_str}"

    def how_am_i(self) -> str:
        """我感觉如何（文本摘要）。"""
        _feel = self._get_feelings()
        _emo = _feel.get("emotion", "平静")
        _stress = _feel.get("stress_level", 0.0)
        _health = self._get_health().get("health_score", 1.0)
        return f"当前情绪「{_emo}」，压力等级{_stress:.2f}，系统健康度{_health:.1%}"

    def what_am_i_doing(self) -> str:
        """我在做什么（文本摘要）。"""
        _state = self._get_current_state()
        _tempo = _state.get("tempo", "未知")
        _recent = _state.get("recent_activity", [])
        _last = _recent[-1] if _recent else None
        if _last:
            return f"当前运行节律「{_tempo}」，最近活动：{_last.get('type', '')} - {_last.get('content', '')[:40]}"
        return f"当前运行节律「{_tempo}」，暂无近期活动记录"


# ========== 便捷函数 ==========

_self_model: SelfModel | None = None




if __name__ == "__main__":
    # 自测（无依赖注入，返回默认值）
    model = SelfModel(cache_ttl=0.1)
    snap = model.get_self_snapshot()
    print(f"身份: {snap['identity']}")
    print(f"状态: {snap['current_state']}")
    print(f"体感: {snap['feelings']}")
    print(f"健康: {snap['health']}")
    print(f"历史: {snap['history']}")
    print(f"\n我是谁: {model.who_am_i()}")
    print(f"我感觉: {model.how_am_i()}")
    print(f"我在做: {model.what_am_i_doing()}")
