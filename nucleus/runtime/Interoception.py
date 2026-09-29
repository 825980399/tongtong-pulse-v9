# -*- coding: utf-8 -*-
"""
Interoception.py —— 内感受

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 框架内部状态感知与内省
机制: 基于Interoception类实现，包含10个核心方法
定位: 自省感知层
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底



class Interoception(SilentLogMixin):
    """内在体感引擎。"""

    # 体感维度权重（综合体感计算用）
    _DIMENSION_WEIGHTS: ClassVar[dict[str, float]] = {
        "reasoning": 0.25,   # 推理质量
        "organ": 0.20,       # 器官健康
        "resource": 0.25,    # 资源压力
        "emotion": 0.20,     # 情绪状态
        "trajectory": 0.10,  # 运行轨迹
    }

    def __init__(self, cache_ttl: float = 5.0,
                 enabled: bool = True) -> None:
        """
        Args:
            cache_ttl: 缓存有效期秒数（压力均衡）
            enabled: 开关
        """
        self._cache_ttl = max(0.5, float(cache_ttl))
        self._enabled = enabled

        # 各模块引用（依赖注入）
        self._feedback_loop = None
        self._runtime_metrics = None
        self._runtime_tempo = None
        self._hormones = None
        self._stress_axis = None
        self._trajectory = None
        self._self_awareness = None

        # 缓存
        self._cache: dict[str, tuple[float, Any]] = {}
        self._anomaly_history: list[dict[str, Any]] = []

        # 注入运行轨迹单例（严格受 ENABLE_RUNTIME_TRAJECTORY_PERSIST 灰度开关保护：
        # 未启用时保持 _trajectory=None，读取分支走 default，行为与现状完全一致，零回退风险）
        try:
            import config as _cfg
            if getattr(_cfg, "ENABLE_RUNTIME_TRAJECTORY_PERSIST", False):
                from nucleus.runtime.RuntimeTrajectory import get_runtime_trajectory
                self.set_trajectory(get_runtime_trajectory())
        except Exception:
            pass

    # ========== 依赖注入 ==========

    def set_feedback_loop(self, ref: Any) -> None:
        self._feedback_loop = ref

    def set_runtime_metrics(self, ref: Any) -> None:
        self._runtime_metrics = ref

    def set_runtime_tempo(self, ref: Any) -> None:
        self._runtime_tempo = ref

    def set_hormones(self, ref: Any) -> None:
        self._hormones = ref

    def set_stress_axis(self, ref: Any) -> None:
        self._stress_axis = ref

    def set_trajectory(self, ref: Any) -> None:
        self._trajectory = ref

    def set_self_awareness(self, ref: Any) -> None:
        self._self_awareness = ref

    # ========== 核心：体感快照 ==========

    def get_body_snapshot(self) -> dict[str, Any]:
        """获取完整内在体感快照。"""
        if not self._enabled:
            return {"enabled": False, "timestamp": time.time()}

        _reasoning = self._get_reasoning_interoception()
        _organ = self._get_organ_interoception()
        _resource = self._get_resource_interoception()
        _emotion = self._get_emotion_interoception()
        _trajectory = self._get_trajectory_interoception()

        # 综合体感计算
        _comfort = self._calc_comfort(_reasoning, _organ, _resource, _emotion)
        _fatigue = self._calc_fatigue(_resource, _trajectory)
        _alertness = self._calc_alertness(_emotion, _stress_axis_level=_emotion.get("stress_level", 0))

        # 异常检测
        _anomalies = self._detect_anomalies(_reasoning, _organ, _resource, _emotion)

        return {
            "timestamp": time.time(),
            "datetime": time.strftime('%Y-%m-%d %H:%M:%S'),
            "reasoning": _reasoning,
            "organ": _organ,
            "resource": _resource,
            "emotion": _emotion,
            "trajectory": _trajectory,
            "overall": {
                "comfort": round(_comfort, 3),       # 舒适度 0-1
                "fatigue": round(_fatigue, 3),       # 疲劳度 0-1
                "alertness": round(_alertness, 3),   # 警觉度 0-1
                "anomalies": _anomalies,
            },
        }

    # ========== 维度：推理体感 ==========

    def _get_reasoning_interoception(self) -> dict[str, Any]:
        """推理体感：质量/成功率/延迟/策略偏好。"""
        _result = {"source": "default", "success_rate": 0.7, "avg_latency_ms": 0,
                   "recommended_params": {}, "status": "normal"}
        try:
            if self._feedback_loop and hasattr(self._feedback_loop, "get_stats"):
                _stats = self._feedback_loop.get_stats() or {}
                _result["source"] = "ReasoningFeedbackLoop"
                _result["success_rate"] = _stats.get("success_rate", 0.7)
                _result["total_records"] = _stats.get("total_records", 0)
                _result["recommended_params"] = _stats.get("recommended_params", {})
                # 推理状态判断
                _sr = _result["success_rate"]
                if _sr < 0.4:
                    _result["status"] = "struggling"  # 推理困难
                elif _sr < 0.6:
                    _result["status"] = "effortful"   # 需要努力
                else:
                    _result["status"] = "fluent"      # 流畅
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return _result

    # ========== 维度：器官体感 ==========

    def _get_organ_interoception(self) -> dict[str, Any]:
        """器官体感：健康度/活跃度/弱区。"""
        _result = {"source": "default", "health_score": 1.0, "weak_areas": [], "status": "healthy"}
        try:
            if self._runtime_metrics and hasattr(self._runtime_metrics, "get_snapshot"):
                _snap = self._runtime_metrics.get_snapshot() or {}
                _result["source"] = "RuntimeMetrics"
                _total = max(1, _snap.get("pulse_count", 1))
                _errors = _snap.get("error_count", 0)
                _result["health_score"] = round(max(0.0, 1.0 - _errors / _total), 3)
                _result["error_count"] = _errors
                _result["pulse_count"] = _snap.get("pulse_count", 0)
                if _result["health_score"] < 0.8:
                    _result["status"] = "unhealthy"
                elif _result["health_score"] < 0.95:
                    _result["status"] = "minor_issues"
            if self._self_awareness and hasattr(self._self_awareness, "is_in_weak_area"):
                # 弱区检测（如果有当前上下文）
                pass
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return _result

    # ========== 维度：资源体感 ==========

    def _get_resource_interoception(self) -> dict[str, Any]:
        """资源体感：运行节律/并行度/压力。"""
        _result = {"source": "default", "tempo": "unknown", "pressure_level": 0.0,
                   "is_conversation": False, "status": "relaxed"}
        try:
            if self._runtime_tempo and hasattr(self._runtime_tempo, "get_state"):
                _state = self._runtime_tempo.get_state() or {}
                _result["source"] = "RuntimeTempo"
                _result["tempo"] = _state.get("state", "unknown")
                _result["is_conversation"] = _state.get("is_conversation", False)
                # 资源压力判断
                if _result["tempo"] == "accelerated":
                    _result["pressure_level"] = 0.7
                    _result["status"] = "busy"
                elif _result["tempo"] == "conversation":
                    _result["pressure_level"] = 0.5
                    _result["status"] = "active"
                else:
                    _result["pressure_level"] = 0.2
                    _result["status"] = "relaxed"
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return _result

    # ========== 维度：情绪体感 ==========

    def _get_emotion_interoception(self) -> dict[str, Any]:
        """情绪体感：情绪/激素/压力。"""
        _result = {"source": "default", "emotion": "neutral", "intensity": 0.0,
                   "stress_level": 0.0, "status": "calm"}
        try:
            if self._hormones and hasattr(self._hormones, "get_current_emotion"):
                _emo = self._hormones.get_current_emotion() or {}
                _result["source"] = "PulseHormones"
                _result["emotion"] = _emo.get("emotion", "neutral")
                _result["intensity"] = _emo.get("intensity", 0.0)
            if self._stress_axis and hasattr(self._stress_axis, "get_stress_level"):
                _stress = self._stress_axis.get_stress_level() or 0.0
                _result["stress_level"] = float(_stress)
                if _result["stress_level"] > 0.7:
                    _result["status"] = "stressed"
                elif _result["stress_level"] > 0.4:
                    _result["status"] = "alert"
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return _result

    # ========== 维度：运行轨迹体感 ==========

    def _get_trajectory_interoception(self) -> dict[str, Any]:
        """运行轨迹体感：事件密度/活动类型/节奏。"""
        _result = {"source": "default", "total_events": 0, "event_rate": 0.0,
                   "dominant_type": "none", "status": "idle"}
        try:
            if self._trajectory and hasattr(self._trajectory, "get_stats"):
                _stats = self._trajectory.get_stats() or {}
                _result["source"] = "RuntimeTrajectory"
                _result["total_events"] = _stats.get("total_records", 0)
                _result["event_rate"] = _stats.get("records_per_minute", 0)
                _dist = _stats.get("event_type_counts", {})
                if _dist:
                    _result["dominant_type"] = max(_dist, key=_dist.get)
                if _result["event_rate"] > 10:
                    _result["status"] = "high_activity"
                elif _result["event_rate"] > 2:
                    _result["status"] = "active"
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return _result

    # ========== 综合体感计算 ==========

    def _calc_comfort(self, reasoning: dict, organ: dict,
                      resource: dict, emotion: dict) -> float:
        """计算舒适度（0-1，越高越舒适）。"""
        _score = 0.0
        _score += self._DIMENSION_WEIGHTS["reasoning"] * reasoning.get("success_rate", 0.7)
        _score += self._DIMENSION_WEIGHTS["organ"] * organ.get("health_score", 1.0)
        _score += self._DIMENSION_WEIGHTS["resource"] * (1.0 - resource.get("pressure_level", 0))
        _score += self._DIMENSION_WEIGHTS["emotion"] * (1.0 - emotion.get("stress_level", 0))
        _score += self._DIMENSION_WEIGHTS["trajectory"] * 0.8  # 轨迹默认中等
        return max(0.0, min(1.0, _score))

    def _calc_fatigue(self, resource: dict, trajectory: dict) -> float:
        """计算疲劳度（0-1，越高越疲劳）。"""
        _pressure = resource.get("pressure_level", 0)
        _rate = trajectory.get("event_rate", 0)
        _rate_fatigue = min(1.0, _rate / 20.0)  # 20事件/分钟为满疲劳
        return max(0.0, min(1.0, 0.6 * _pressure + 0.4 * _rate_fatigue))

    def _calc_alertness(self, emotion: dict, _stress_axis_level: float = 0) -> float:
        """计算警觉度（0-1，越高越警觉）。"""
        _stress = emotion.get("stress_level", 0)
        _intensity = emotion.get("intensity", 0)
        return max(0.0, min(1.0, 0.5 * _stress + 0.3 * _intensity + 0.2))

    # ========== 异常检测 ==========

    def _detect_anomalies(self, reasoning: dict, organ: dict,
                          resource: dict, emotion: dict) -> list[dict[str, Any]]:
        """检测异常状态。"""
        _anomalies = []
        if reasoning.get("success_rate", 1.0) < 0.4:
            _anomalies.append({"dimension": "reasoning", "level": "warning",
                               "message": f"推理成功率低({reasoning['success_rate']:.0%})"})
        if organ.get("health_score", 1.0) < 0.8:
            _anomalies.append({"dimension": "organ", "level": "critical",
                               "message": f"器官健康度低({organ['health_score']:.0%})"})
        if resource.get("pressure_level", 0) > 0.8:
            _anomalies.append({"dimension": "resource", "level": "warning",
                               "message": f"资源压力高({resource['pressure_level']:.0%})"})
        if emotion.get("stress_level", 0) > 0.7:
            _anomalies.append({"dimension": "emotion", "level": "warning",
                               "message": f"压力等级高({emotion['stress_level']:.0%})"})
        if _anomalies:
            self._anomaly_history.append({
                "timestamp": time.time(),
                "anomalies": _anomalies,
            })
            if len(self._anomaly_history) > 100:
                self._anomaly_history = self._anomaly_history[-50:]
        return _anomalies

    # ========== 便捷查询 ==========

    def get_comfort_level(self) -> float:
        """获取当前舒适度（0-1）。"""
        return self.get_body_snapshot()["overall"]["comfort"]

    def get_anomalies(self) -> list[dict[str, Any]]:
        """获取当前异常列表。"""
        return self.get_body_snapshot()["overall"]["anomalies"]

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息。"""
        return {
            "enabled": self._enabled,
            "cache_ttl": self._cache_ttl,
            "anomaly_history_size": len(self._anomaly_history),
        }


# ========== 便捷函数 ==========

_interoception: Interoception | None = None


def get_interoception() -> Interoception:
    """获取 Interoception 单例。"""
    global _interoception
    if _interoception is None:
        _interoception = Interoception()
    return _interoception


def set_interoception(interoception: Interoception) -> None:
    """设置 Interoception 单例（用于依赖注入/测试）。"""
    global _interoception
    _interoception = interoception


if __name__ == "__main__":
    # 自测（无依赖注入，返回默认值）
    intero = Interoception(cache_ttl=0.1)
    snap = intero.get_body_snapshot()
    print(f"推理体感: {snap['reasoning']}")
    print(f"器官体感: {snap['organ']}")
    print(f"资源体感: {snap['resource']}")
    print(f"情绪体感: {snap['emotion']}")
    print(f"轨迹体感: {snap['trajectory']}")
    print(f"综合体感: {snap['overall']}")
