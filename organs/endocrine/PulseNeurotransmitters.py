# -*- coding: utf-8 -*-
"""
PulseNeurotransmitters —— 神经递质器官 · 细粒度化学状态池

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 维护模块级 NEUROTRANSMITTERS 定义的多巴胺/血清素等递质水平，随 ChatEvent.MESSAGE 与 HormonesEvent.EMOTION_DETECTED 刺激升高、按各自 decay_rate 自然回落，对外提供单递质、全量、复合三种查询与状态快照。
机制: on_pulse 分派 _on_message / _on_emotion；_adjust 按刺激量修改递质并夹取 min/max 边界，_decay 按 decay_rate 逐拍回落，_record_history 记录历史曲线；_notify_listeners 回调 add_listener 注册、remove_listener 注销的监听者；get_level / get_all_levels / get_composite_state 提供三种粒度查询，get_state_snapshot / load_state_snapshot 负责持久化；get_resonance_conditions 声明订阅 SystemEvent.BOOT 等事件的共振条件。
定位: 内分泌层的「神经递质池」，always_online=True 且 feature_flag=None（生命线核心不可关闭），为激素器官与皮层提供情绪之下的化学底噪。
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import  ChatEvent, HormonesEvent, LogLevel, SystemEvent, HeartEvent

# 神经递质定义
NEUROTRANSMITTERS = {
    "dopamine": {
        "name": "多巴胺",
        "function": "奖励/动机/愉悦",
        "base_level": 0.5,
        "decay_rate": 0.02,  # 每次心跳衰减
        "min": 0.0,
        "max": 1.0,
    },
    "serotonin": {
        "name": "血清素",
        "function": "情绪稳定/满足感",
        "base_level": 0.5,
        "decay_rate": 0.01,
        "min": 0.0,
        "max": 1.0,
    },
    "norepinephrine": {
        "name": "去甲肾上腺素",
        "function": "觉醒/注意力/应激",
        "base_level": 0.3,
        "decay_rate": 0.03,
        "min": 0.0,
        "max": 1.0,
    },
    "acetylcholine": {
        "name": "乙酰胆碱",
        "function": "学习/记忆/注意力",
        "base_level": 0.4,
        "decay_rate": 0.015,
        "min": 0.0,
        "max": 1.0,
    },
    "gaba": {
        "name": "GABA",
        "function": "抑制/放松/焦虑缓解",
        "base_level": 0.4,
        "decay_rate": 0.02,
        "min": 0.0,
        "max": 1.0,
    },
    "glutamate": {
        "name": "谷氨酸",
        "function": "兴奋/学习/突触可塑性",
        "base_level": 0.5,
        "decay_rate": 0.025,
        "min": 0.0,
        "max": 1.0,
    },
}


class PulseNeurotransmitters(BasePulseOrgan):
    """脉冲驱动神经递质系统"""

    def __init__(self, organ_name: str = "神经递质"):
        super().__init__(organ_name)

        # 神经递质水平
        self._levels: dict[str, float] = {}
        for key, info in NEUROTRANSMITTERS.items():
            self._levels[key] = info["base_level"]

        # 递质历史（用于趋势分析）
        self._history: list[dict[str, Any]] = []
        self._max_history = 100

        # 事件监听器（递质水平显著变化时通知）
        self._listeners: list[Callable] = []
        self._listener_lock = threading.Lock()

        # 最后衰减时间
        self._last_decay_time = time.time()

        # 综合状态缓存
        self._state_cache: dict[str, Any] = {}
        self._cache_ttl = 1.0  # 1秒缓存
        self._cache_time = 0.0
        self._nt_beat_counter = 0  # ★日志巡检修复：心跳计数，用于周期性打印递质水平

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        if not self.is_running:
            return None

        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == ChatEvent.MESSAGE:
            self._on_message(payload)
        elif event_type == HormonesEvent.EMOTION_DETECTED:
            self._on_emotion(payload)
        elif event_type == SystemEvent.BOOT:
            self._log(LogLevel.INFO, "神经递质系统已启动（6种递质调节就绪）")
        elif event_type == HeartEvent.BEAT:
            self._decay()
        return None

    def get_resonance_conditions(self) -> list[dict[str, Any]]:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [ChatEvent.MESSAGE, HormonesEvent.EMOTION_DETECTED, HeartEvent.BEAT],
                "min_priority": 1,
            },
        ]

    # ========== 事件处理 ==========

    def _on_message(self, payload: dict[str, Any]) -> None:
        """对话消息触发递质变化"""
        content = payload.get("content", "")
        if not content:
            return

        # 正性内容 → 多巴胺+血清素上升
        positive_words = ["好", "棒", "喜欢", "开心", "成功", "完成", "谢谢", "爱"]
        negative_words = ["差", "糟糕", "讨厌", "难过", "失败", "错误", "问题", "焦虑"]

        pos_count = sum(1 for w in positive_words if w in content)
        neg_count = sum(1 for w in negative_words if w in content)

        if pos_count > neg_count:
            self._adjust("dopamine", 0.05 * pos_count)
            self._adjust("serotonin", 0.03 * pos_count)
        elif neg_count > pos_count:
            self._adjust("norepinephrine", 0.08 * neg_count)  # 应激
            self._adjust("gaba", -0.03 * neg_count)  # 焦虑
            self._adjust("serotonin", -0.02 * neg_count)

        # 学习/思考内容 → 乙酰胆碱+谷氨酸上升
        learn_words = ["学习", "思考", "分析", "理解", "记忆", "代码", "问题", "怎么"]
        if any(w in content for w in learn_words):
            self._adjust("acetylcholine", 0.04)
            self._adjust("glutamate", 0.03)

    def _on_emotion(self, payload: dict[str, Any]) -> None:
        """情绪事件触发递质释放"""
        emotion = payload.get("emotion", "")
        intensity = payload.get("intensity", 0.5)

        emotion_map = {
            "快乐": {"dopamine": 0.1, "serotonin": 0.05},
            "喜悦": {"dopamine": 0.12, "serotonin": 0.06},
            "悲伤": {"serotonin": -0.08, "dopamine": -0.05},
            "愤怒": {"norepinephrine": 0.15, "gaba": -0.05},
            "焦虑": {"norepinephrine": 0.1, "gaba": -0.08},
            "恐惧": {"norepinephrine": 0.12, "gaba": -0.06},
            "惊讶": {"norepinephrine": 0.08, "glutamate": 0.05},
            "厌恶": {"serotonin": -0.04, "dopamine": -0.03},
            "平静": {"gaba": 0.05, "norepinephrine": -0.03},
            "好奇": {"dopamine": 0.06, "acetylcholine": 0.05},
        }

        adjustments = emotion_map.get(emotion, {})
        for key, delta in adjustments.items():
            self._adjust(key, delta * intensity)

    def _decay(self) -> None:
        """自然衰减（模拟代谢）"""
        now = time.time()
        elapsed = now - self._last_decay_time
        if elapsed < 1.0:  # 至少1秒才衰减一次
            return
        self._last_decay_time = now
        self._nt_beat_counter += 1

        for key, info in NEUROTRANSMITTERS.items():
            decay = info["decay_rate"] * (elapsed / 8.0)  # 假设8秒一拍
            target = info["base_level"]
            current = self._levels[key]
            # 向基础水平回归
            if current > target:
                self._levels[key] = max(target, current - decay)
            elif current < target:
                self._levels[key] = min(target, current + decay)

        # ★日志巡检修复：每100次心跳打印一次递质水平，确认系统正常运行
        if self._nt_beat_counter % 100 == 0:
            _summary = ", ".join(
                f"{info['name']}={self._levels[k]:.2f}"
                for k, info in NEUROTRANSMITTERS.items()
            )
            self._log(LogLevel.INFO, f"递质水平快照(第{self._nt_beat_counter}拍): {_summary}")

    # ========== 递质调节 ==========

    def _adjust(self, key: str, delta: float) -> None:
        """调节递质水平"""
        if key not in self._levels:
            return
        info = NEUROTRANSMITTERS[key]
        old = self._levels[key]
        new = max(info["min"], min(info["max"], old + delta))
        self._levels[key] = new

        # 显著变化时记录历史并通知
        if abs(new - old) > 0.02:
            self._record_history(key, old, new, delta)
            self._notify_listeners(key, old, new, delta)

    def _record_history(self, key: str, old: float, new: float, delta: float) -> None:
        """记录递质变化历史"""
        self._history.append({
            "time": time.time(),
            "transmitter": key,
            "old": old,
            "new": new,
            "delta": delta,
        })
        if len(self._history) > self._max_history:
            self._history.pop(0)

    def _notify_listeners(self, key: str, old: float, new: float, delta: float) -> None:
        """通知监听器"""
        with self._listener_lock:
            for cb in self._listeners:
                try:
                    cb(key, old, new, delta)
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    # ========== 状态查询 ==========

    def get_level(self, key: str) -> float:
        """获取指定递质水平"""
        return self._levels.get(key, 0.0)

    def get_all_levels(self) -> dict[str, float]:
        """获取所有递质水平"""
        return dict(self._levels)

    def get_composite_state(self) -> dict[str, Any]:
        """获取综合状态（衍生指标）"""
        now = time.time()
        if now - self._cache_time < self._cache_ttl and self._state_cache:
            return self._state_cache

        d = self._levels["dopamine"]
        s = self._levels["serotonin"]
        n = self._levels["norepinephrine"]
        a = self._levels["acetylcholine"]
        g = self._levels["gaba"]
        glu = self._levels["glutamate"]

        state = {
            "arousal": min(1.0, (n + glu) / 2),  # 觉醒度
            "pleasure": min(1.0, (d + s) / 2),  # 愉悦度
            "calmness": min(1.0, g),  # 平静度
            "focus": min(1.0, (a + n) / 2),  # 注意力
            "learning_readiness": min(1.0, (a + glu + d) / 3),  # 学习准备度
            "stress": max(0.0, n - g),  # 压力水平
            "mood_stability": min(1.0, s),  # 情绪稳定性
            "levels": dict(self._levels),
        }
        self._state_cache = state
        self._cache_time = now
        return state

    def get_state_snapshot(self) -> dict[str, Any]:
        """获取状态快照（用于持久化）"""
        return {
            "levels": dict(self._levels),
            "history": self._history[-20:],
            "last_decay_time": self._last_decay_time,
        }

    def load_state_snapshot(self, state: dict[str, Any]) -> None:
        """从快照恢复状态"""
        if "levels" in state:
            for key, val in state["levels"].items():
                if key in self._levels:
                    self._levels[key] = float(val)
        if "last_decay_time" in state:
            self._last_decay_time = float(state["last_decay_time"])

    # ========== 监听器 ==========

    def add_listener(self, callback: Callable) -> None:
        """添加递质变化监听器"""
        with self._listener_lock:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable) -> None:
        """移除监听器"""
        with self._listener_lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    # ========== 统计 ==========

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        return {
            "transmitters": len(self._levels),
            "levels": {k: round(v, 3) for k, v in self._levels.items()},
            "history_count": len(self._history),
            "listeners": len(self._listeners),
            "composite": self.get_composite_state(),
        }


# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
# ★2026-09-03修复：此前缺失ORGAN_META，声明式装配模式下神经递质器官不会被加载，导致0日志
ORGAN_META = {
    "name": "神经递质",
    "class_name": "PulseNeurotransmitters",
    "attr_name": "neurotransmitters",
    "system": "endocrine",
    "always_online": True,
    # ★PHASE17-0.5-1（2026-09-07）：原为 "enable_neurotransmitters"，
    #   但该开关在 config.py 中**并不存在**，且与 always_online=True 语义冲突
    #   （「生命线核心不可关闭」vs「可开关降级」）。经星轨决策：移除 feature_flag，
    #   对齐核心器官惯例（心/肝/肺/肾/血管/皮层均为 always_online=True + feature_flag=None）。
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [
        {"target": "激素", "setter": "set_neurotransmitters_ref"},
    ],
}
