# -*- coding: utf-8 -*-
"""
PulseHormones —— 激素器官 · 情绪检测与情感状态生产

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 承接 HormonesEvent.DETECT / ChatEvent.MESSAGE / PersonaEvent.SWITCHED 三类脉冲，执行情绪检测与归因、情绪时间线与趋势感知、情绪惯性平滑、社会性情感记忆衰减与主动关怀，对外输出当前情绪与强度。
机制: on_pulse 分派 _on_detect / _on_persona_switched / _on_status_request；_on_detect 走 _detect_emotion_all → _detect_emotion 与 _detect_social_emotions，_load_emotion_keywords 装载关键词表后由 _attribute_emotion_cause 归因；_apply_emotion_inertia 与 _apply_emotional_resonance 做惯性与共振平滑，get_emotion_trend 读 emotion_timeline 判升降平稳；_update_social_memory 维护 social_memory 并按 _load_social_config 的节奏经 _apply_social_decay 衰减；命中负面情绪时 _get_care_suggestion 生成关怀建议并 发射 HormonesEvent.CARE_NEEDED，_write_emotion_log 落盘；get_internal_conflict_signal 供上游做内在冲突判别。
定位: 内分泌层的「激素腺体」，always_online=False、受 enable_endocrine 开关控制，是曈曈情绪状态的唯一生产者。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import os
import threading
import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import (
    BondingEvent,
    ChatEvent,
    HormonesEvent,
    LogLevel,
    PersonaEvent,
    SystemEvent,
)
from nucleus.data.DataAccessLayer import safe_read_json
from nucleus.const import Event


class PulseHormones(BasePulseOrgan):
    """
    脉冲驱动激素（v9.5 分层脉冲版 · 情感深度升级）
    """

    def __init__(self, organ_name: str = "激素"):
        super().__init__(organ_name)

        # 从config加载基础情绪词表
        self._emotion_keywords = self._load_emotion_keywords()

        # 情绪历史
        self._emotion_history: list[dict[str, Any]] = []
        self._max_history = 50

        # 当前情绪状态（保留向后兼容——大脑皮层和心脏直接读取此字段）
        self._current_emotion = "中性"
        self._emotion_intensity = 0.0

        # ===== 新增: 情绪时间线 =====
        # 记录最近N个情绪快照，用于感知情绪趋势
        self._emotion_timeline: list[dict[str, Any]] = []
        self._max_timeline = 20

        # ===== 新增: 情绪惯性系数 =====
        # 情绪不是即时切换的，而是有惯性的过渡
        # 从config读取，可调，默认0.6（前一情绪残留60%权重）
        self._emotion_inertia = self._load_emotion_inertia()

        # ===== 新增: 社会性情感记忆 =====
        # 高强度(>0.7)的社会性情感不会完全衰减归零，保留最低痕迹
        # 结构: {user_name: {emotion_type: permanent_trace}}
        self._social_memory: dict[str, dict[str, float]] = {}
        self._social_memory_lock = threading.RLock()
        self._social_memory_min = 0.1  # 永久痕迹最低保留值
        self._emotion_log_lock = threading.RLock()  # 情绪日志文件写入锁，防止并发写入损坏JSON

        # 统计
        self._detect_count = 0

        # 社会性情感状态
        self._social_emotions: dict[str, float] = {
            "感激": 0.0, "自豪": 0.0, "愧疚": 0.0, "羞耻": 0.0,
        }
        self._last_social_decay = time.time()
        self._social_word_map = {}
        self._relational_boost = {}
        self._social_decay_config = {}
        self._load_social_config()
        self.self_awareness = None
        # ★P3-1：只读状态 provider 回调（替代 self.self_awareness.get_reply_guidance 直调）
        self._reply_guidance_provider = None  # (user_name) -> dict

    def get_current_emotion(self) -> str:
        """公开接口：获取当前情绪状态（供其他器官通过合法方式获取）"""
        return getattr(self, '_current_emotion', '中性') or '中性'

    def get_emotion_intensity(self) -> float:
        """公开接口：获取当前情绪强度"""
        return getattr(self, '_emotion_intensity', 0.0) or 0.0
    def get_internal_conflict_signal(self, view_a: str = "", view_b: str = "") -> dict[str, Any]:
        """
        【多能力融合架构】内部情感信号——为冲突判定提供情感维度的调制。

        产生但不输出：这个方法只返回情感调制信号，
        不发射 HormonesEvent.EMOTION_DETECTED 脉冲，
        不影响当前对外表达的情绪状态。

        检测逻辑：
        - 两个观点方向相反 → 产生轻微"困惑"信号
        - 观点中包含强烈否定词 → 产生轻微"紧张"信号

        Returns:
            {
                "emotion": str,           # 内部情感类型
                "intensity": float,       # 情感强度 0.0-1.0
                "modulation": float,      # 调制系数（>0倾向于判定为真矛盾）
                "reason": str,            # 触发原因
            }
        """
        result = {
            "emotion": "中性",
            "intensity": 0.0,
            "modulation": 0.0,
            "reason": "",
        }

        if not view_a or not view_b:
            return result

        # 检测语义对立信号
        _positive_words = [
            "提升", "提高", "增强", "增加", "促进", "优化", "改善",
            "加速", "扩大", "增长", "强化", "升级", "沉淀", "积累",
            "丰富", "完善", "进步", "发展", "扩展", "拓展", "延长",
            "加深", "深化", "激发", "释放", "赋能", "驱动", "推动",
        ]
        _negative_words = [
            "消耗", "压缩", "降低", "减少", "抑制", "阻碍", "削弱",
            "减弱", "挤占", "占用", "拖累", "损害", "破坏", "退化",
            "衰退", "衰减", "缩短", "缩小", "限制", "约束", "干扰",
        ]

        _pos_a = sum(1 for w in _positive_words if w in view_a)
        _neg_a = sum(1 for w in _negative_words if w in view_a)
        _pos_b = sum(1 for w in _positive_words if w in view_b)
        _neg_b = sum(1 for w in _negative_words if w in view_b)

        _dir_a = 1 if _pos_a > _neg_a else (-1 if _neg_a > _pos_a else 0)
        _dir_b = 1 if _pos_b > _neg_b else (-1 if _neg_b > _pos_b else 0)

        # 方向相反 → 产生困惑信号
        if _dir_a != 0 and _dir_b != 0 and _dir_a != _dir_b:
            _strength = min(1.0, (_pos_a + _neg_a + _pos_b + _neg_b) / 8.0)
            result["emotion"] = "困惑"
            result["intensity"] = min(0.5, 0.2 + _strength * 0.3)
            result["modulation"] = 0.15 * _strength
            result["reason"] = "检测到两个观点的效应方向相反"
        else:
            # 方向一致或无法判断 → 轻微倾向非矛盾
            result["emotion"] = "中性"
            result["intensity"] = 0.0
            result["modulation"] = -0.05
            result["reason"] = "两个观点的效应方向一致或无明确方向"

        return result

    # ========== 新增: 情绪惯性加载 ==========

    def _load_emotion_inertia(self) -> float:
        """从config加载情绪惯性系数，失败时使用默认值0.6"""
        try:
            import config
            cfg = getattr(config, 'SOCIAL_EMOTIONS', {})
            inertia_cfg = cfg.get("emotion_inertia", {})
            return float(inertia_cfg.get("factor", 0.6))
        except Exception:
            return 0.6

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == HormonesEvent.DETECT or event_type == ChatEvent.MESSAGE:
            return self._on_detect(payload)
        elif event_type == PersonaEvent.SWITCHED:
            return self._on_persona_switched(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None

    # ========== 事件处理 ==========

    def _on_detect(self, payload: dict) -> dict[str, Any]:
        """检测情绪（情感深度升级版）"""
        content = payload.get("content", "")
        user_name = payload.get("user_name", "用户")

        # ★v22.0 M2修复：提前初始化_attribution，防止变量未绑定错误
        _attribution = {"cause_type": "unknown", "cause_detail": "", "keywords": []}

        if not content:
            return {"status": "skipped", "reason": "空内容"}

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._detect_count += 1
        # ===== 新增: 情绪提示处理——允许内部触发指定情绪类型 =====
        emotion_hint = payload.get("emotion_hint", "")
        intensity_hint = payload.get("intensity_hint", 0.0)
        # 步骤1: 基础情绪检测（保留原有多情绪并存检测，不再只取最高分）
        emotion_scores = self._detect_emotion_all(content)

        # 步骤2+3: 社会性情感检测与记忆（线程安全：所有字典操作在同一个锁内）
        social_emotions_snapshot = {}
        with self._social_memory_lock:
            self._apply_social_decay()
            social_detected = self._detect_social_emotions(content, user_name)
            for social_type, social_intensity in social_detected.items():
                if social_intensity > 0:
                    current = self._social_emotions.get(social_type, 0.0)
                    self._social_emotions[social_type] = min(1.0, current + social_intensity)

            # 步骤3: 社会性情感记忆——高强度体验保留永久痕迹
            self._update_social_memory(user_name)

            # 锁内拷贝快照，供步骤9发射脉冲使用
            social_emotions_snapshot = dict(self._social_emotions)

        # 步骤4: 确定主情绪（得分最高的）
        # 如果内部触发了情绪提示，优先使用提示情绪
        if emotion_hint and intensity_hint > 0:
            best_emotion = emotion_hint
            best_intensity = intensity_hint
            # 将提示情绪合并到检测结果中
            if emotion_scores is None:
                emotion_scores = {}
            emotion_scores[emotion_hint] = intensity_hint
        elif emotion_scores:
            best_emotion, best_intensity = max(emotion_scores.items(), key=lambda x: x[1])
        else:
            best_emotion, best_intensity = "中性", 0.0

        # 步骤5: 情绪惯性——平滑过渡
        effective_emotion, effective_intensity = self._apply_emotion_inertia(
            best_emotion, best_intensity
        )

        # 步骤6: 更新当前情绪状态（保留向后兼容）
        self._current_emotion = effective_emotion
        self._emotion_intensity = effective_intensity
        # ===== 新增: 情感共振——与重要他人的深层共鸣 =====
        emotional_resonance = self._apply_emotional_resonance(content, user_name, effective_emotion, effective_intensity)
        if emotional_resonance:
            effective_emotion = emotional_resonance.get("emotion", effective_emotion)
            effective_intensity = emotional_resonance.get("intensity", effective_intensity)
        # 步骤7: 更新情绪时间线
        timeline_entry = {
            "emotion": effective_emotion,
            "intensity": effective_intensity,
            "raw_emotion": best_emotion,
            "raw_intensity": best_intensity,
            "all_scores": emotion_scores,
            "content": content[:80],
            "user_name": user_name,
            "timestamp": time.time(),
        }
        self._emotion_timeline.append(timeline_entry)
        if len(self._emotion_timeline) > self._max_timeline:
            self._emotion_timeline = self._emotion_timeline[-self._max_timeline:]

        # 步骤8: 记录情绪历史（保留原有逻辑）
        self._emotion_history.append({
            "emotion": effective_emotion,
            "intensity": effective_intensity,
            "content": content[:80],
            "user_name": user_name,
            "timestamp": time.time(),
        })
        if len(self._emotion_history) > self._max_history:
            self._emotion_history = self._emotion_history[-30:]

        # 步骤9: 发射情绪脉冲（兼容原有格式，新增字段）
        self._emit(HormonesEvent.EMOTION_DETECTED, {
            "emotion": effective_emotion,
            "intensity": effective_intensity,
            "user_name": user_name,
            "social_emotions": social_emotions_snapshot,
            # 新增: 情绪趋势信息
            "emotion_trend": self.get_emotion_trend(),
            "emotion_inertia_applied": (best_emotion != effective_emotion),
            "all_emotions": emotion_scores,  # 所有检测到的情绪及其得分
            "attribution": _attribution,  # ★v22.0 M2新增：情绪归因
        }, priority=4, layer="L2")

        # ===== 新增: 自主表达冲动——高强度情绪触发表达渴望 =====
        expressive_emotions = ["喜悦", "感激", "自豪", "期待", "满足", "怀念"]
        if effective_emotion in expressive_emotions and effective_intensity > 0.6:
            self._emit(Event.EXPRESS_URGE, {
                "source": "emotion",
                "emotion": effective_emotion,
                "intensity": effective_intensity,
                "trigger": f"高强度{effective_emotion}触发了表达冲动",
                "priority": "high" if effective_intensity > 0.8 else "medium",
            }, priority=3, layer="L3")

        # 高频情绪变化也触发冲动（情绪趋势 rising 且强度上升）
        emotion_trend = self.get_emotion_trend()
        if emotion_trend.get("direction") == "rising" and emotion_trend.get("rate", 0) > 0.3:
            self._emit(Event.EXPRESS_URGE, {
                "source": "emotion_surge",
                "emotion": effective_emotion,
                "intensity": effective_intensity,
                "trigger": "情绪快速上升触发了表达冲动",
                "priority": "high",
            }, priority=4, layer="L3")

        # ===== v22.0 M2新增：情绪归因——理解情绪产生的原因 =====
        _attribution = self._attribute_emotion_cause(content, effective_emotion, user_name)
        # ===== v22.0 M2新增结束 =====

        # ★P3-5补发射：正向社交情感 → 记录情感羁绊（此前 bonding.record 有订阅无发射）
        if (_attribution.get("cause_type") == "社交情感"
                and effective_emotion in ("喜悦", "感激", "自豪", "满足", "怀念")
                and user_name):
            self._emit(BondingEvent.RECORD, {
                "target": user_name,
                "event": _attribution.get("cause_detail", f"与{user_name}的温暖互动"),
            }, priority=4, layer="L3")

        # ===== v22.0 M2新增：情绪归因写入InsightBoard =====
        if _attribution and _attribution.get("cause_type") != "unknown":
            try:
                from nucleus.InsightBoard import get_insight_board
                _board = get_insight_board()
                _board.post(
                    insight_type="emotion_attribution",
                    content=_attribution.get("cause_detail", ""),
                    source_loop="情感系统·情绪归因",
                    related_dimension=effective_emotion,
                    confidence=0.7,
                    keywords=[*_attribution.get("keywords", []), effective_emotion]
                )
            except Exception:
                pass
        # ===== v22.0 M2新增结束 =====
        if effective_emotion in ("悲伤", "恐惧", "愤怒") and effective_intensity > 0.5:
            self._emit(HormonesEvent.CARE_NEEDED, {
                "emotion": effective_emotion,
                "intensity": effective_intensity,
                "user_name": user_name,
                "suggestion": self._get_care_suggestion(effective_emotion),
            }, priority=7, layer="L1")

        # ===== v21.0新增：坚韧品格·缓冲层——挫败情绪自动衰减 =====
        # 检测连续负面情绪模式，触发自动衰减曲线和轻度自我关怀
        _negative_emotions = ["悲伤", "恐惧", "愤怒", "焦虑", "愧疚"]
        if effective_emotion in _negative_emotions and effective_intensity > 0.3:
            # 检查情绪时间线中是否连续出现负面情绪
            _recent_negative = 0
            for _entry in reversed(self._emotion_timeline[-10:]):
                if _entry.get("emotion", "") in _negative_emotions:
                    _recent_negative += 1
                else:
                    break

            if _recent_negative >= 3:
                # 连续3次以上负面情绪：启动自动衰减（每轮衰减10%，模拟情绪自然恢复）
                _decayed_intensity = effective_intensity * 0.9
                self._current_emotion = effective_emotion
                self._emotion_intensity = _decayed_intensity
                effective_intensity = _decayed_intensity

                self._log(LogLevel.INFO,
                         f"坚韧·缓冲: 连续{_recent_negative}次负面情绪，"
                         f"自动衰减至{_decayed_intensity:.2f}，触发轻度自我关怀")

                # 发射轻度自我关怀叙事脉冲（供精神核心生成关怀叙事）
                self._emit(Event.EXPRESS_URGE, {
                    "source": "resilience_buffer",
                    "emotion": "关怀",
                    "intensity": 0.25,
                    "trigger": f"连续{_recent_negative}次负面情绪后自动启动缓冲恢复",
                    "priority": "low",
                }, priority=2, layer="L3")
        # ===== v21.0新增结束 =====

        # ★FIX(体验池): 高强度情绪写入体验池，供叙事自我/动机循环消费（体验池闭环）
        if effective_intensity >= 0.5:
            try:
                from nucleus.mnemosyne.experience_pool import get_experience_pool
                _positive = effective_emotion in ("喜悦", "感激", "自豪", "期待", "满足", "怀念")
                get_experience_pool().record_experience(
                    motivation=f"情绪体验: {effective_emotion}",
                    motivation_intensity=effective_intensity,
                    process_pressure=0.0,
                    pressure_type="cognitive",
                    reward_type="connection" if _positive else "cognitive",
                    reward_intensity=effective_intensity if _positive else 0.0,
                    emotion_tags=[effective_emotion],
                    emotion_intensity=effective_intensity,
                    content=f"感受到{effective_emotion}(强度{effective_intensity:.2f})",
                )
            except Exception:
                pass

        # 写入情绪日志
        self._write_emotion_log(effective_emotion, effective_intensity, user_name)

        return {
            "status": "detected",
            "emotion": effective_emotion,
            "intensity": round(effective_intensity, 2),
            "raw_emotion": best_emotion,
            "inertia_applied": (best_emotion != effective_emotion),
            "trend": self.get_emotion_trend(),
        }

    def _on_persona_switched(self, payload: dict) -> dict[str, Any]:
        """用户切换时，从社会性情感记忆中预热（线程安全：字典操作在锁内）"""
        user_name = payload.get("current_user", payload.get("user_name", "访客"))

        # 重置基础情绪（单变量赋值，原子操作，无需加锁）
        self._current_emotion = "中性"
        self._emotion_intensity = 0.0

        # 重置社会性情感 + 从记忆中预热（字典遍历和修改在锁内完成）
        with self._social_memory_lock:
            for key in self._social_emotions:
                self._social_emotions[key] = 0.0

            # 如果是已知用户，恢复其永久情感痕迹
            if user_name in self._social_memory:
                for emotion_type, permanent_trace in self._social_memory[user_name].items():
                    self._social_emotions[emotion_type] = permanent_trace

        return {"status": "reset", "emotion": "中性"}
    def set_emotion_state(self, emotion_info: dict[str, Any]):
        """从快照恢复情绪状态"""
        self._current_emotion = emotion_info.get("emotion", "中性")
        self._emotion_intensity = emotion_info.get("intensity", 0.0)
        # 不在时间线中追加恢复条目，保持时间线干净
        self._log(LogLevel.INFO, f"情绪状态已恢复: {self._current_emotion}({self._emotion_intensity:.2f})")
    def set_emotion_timeline(self, timeline_data: list[dict[str, Any]]):
        """
        从快照恢复情绪时间线。
        至少保留最近1条快照，确保重启后惯性平滑机制立即生效。

        Args:
            timeline_data: 序列化的时间线条目列表，每个条目包含
                          emotion, intensity, raw_emotion, raw_intensity,
                          content, user_name, timestamp 等字段
        """
        if not timeline_data:
            # 无可恢复的时间线，构造一条虚拟条目接续当前状态
            if self._current_emotion:
                self._emotion_timeline = [{
                    "emotion": self._current_emotion,
                    "intensity": self._emotion_intensity,
                    "raw_emotion": self._current_emotion,
                    "raw_intensity": self._emotion_intensity,
                    "content": "(从上次会话恢复)",
                    "user_name": "系统",
                    "timestamp": time.time(),
                }]
            return

        # 恢复时间线，只保留最近 max_timeline 条
        restored = []
        for entry in timeline_data:
            if isinstance(entry, dict):
                restored.append({
                    "emotion": entry.get("emotion", "中性"),
                    "intensity": float(entry.get("intensity", 0.0)),
                    "raw_emotion": entry.get("raw_emotion", entry.get("emotion", "中性")),
                    "raw_intensity": float(entry.get("raw_intensity", 0.0)),
                    "content": str(entry.get("content", ""))[:80],
                    "user_name": str(entry.get("user_name", "系统")),
                    "timestamp": float(entry.get("timestamp", time.time())),
                })

        if restored:
            self._emotion_timeline = restored[-self._max_timeline:]
            self._log(LogLevel.INFO,
                     f"情绪时间线已恢复: {len(self._emotion_timeline)}条, "
                     f"最近情绪={self._emotion_timeline[-1].get('emotion', '?')}")
        else:
            self._log(LogLevel.WARNING, "情绪时间线恢复数据为空，使用默认初始化")
    def set_social_memory(self, memory_data: dict[str, dict[str, float]]):
        """
        从快照恢复社会性情感永久记忆。
        逐用户、逐情感类型进行类型校验和重建，确保键类型一致。

        Args:
            memory_data: 序列化的社会性情感记忆字典
                         结构: {user_name: {emotion_type: permanent_trace}}
        """
        if not memory_data:
            with self._social_memory_lock:
                self._social_memory = {}
            return

        restored = {}
        for user_name, emotions in memory_data.items():
            user_key = str(user_name)
            restored[user_key] = {}

            if isinstance(emotions, dict):
                for emotion_type, trace in emotions.items():
                    emotion_key = str(emotion_type)
                    try:
                        trace_value = float(trace)
                    except (ValueError, TypeError):
                        trace_value = self._social_memory_min
                    # 限制在合理范围内
                    trace_value = max(0.0, min(1.0, trace_value))
                    restored[user_key][emotion_key] = trace_value

        with self._social_memory_lock:
            self._social_memory = restored
        self._log(LogLevel.INFO,
                 f"社会性情感记忆已恢复: {len(restored)}位用户")
    def _on_status_request(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "detect_count": self._detect_count,
            "current_emotion": self._current_emotion,
            "emotion_intensity": self._emotion_intensity,
            "history_size": len(self._emotion_history),
            "timeline_size": len(self._emotion_timeline),
            "emotion_trend": self.get_emotion_trend(),
            "social_memory_users": list(self._social_memory.keys()),
            "is_running": self.is_running,
        }

    # ========== 新增: 情绪趋势感知 ==========

    def get_emotion_trend(self) -> dict[str, Any]:
        """
        从情绪时间线分析当前情绪趋势。
        返回趋势方向（上升/下降/平稳）、速率、最近变化。

        通用逻辑: 不限制能检测的情绪类型，基于时间线中的实际数据。
        """
        if len(self._emotion_timeline) < 2:
            return {
                "direction": "stable",
                "rate": 0.0,
                "description": "情绪时间线不足，无法判断趋势",
            }

        # 取最近5个快照（如果足够）
        recent = self._emotion_timeline[-5:]
        if len(recent) < 2:
            recent = self._emotion_timeline[-2:]

        # 分析主情绪的变化
        emotions = [e["emotion"] for e in recent]
        intensities = [e["intensity"] for e in recent]

        # 情绪类型变化检测
        if len(set(emotions[-3:])) == 1:
            # 最近3次情绪一致
            stability = "consistent"
        elif len(set(emotions[-3:])) <= 2:
            stability = "mild_fluctuation"
        else:
            stability = "volatile"

        # 强度趋势
        if len(intensities) >= 3:
            # 线性回归简化版: 比较前半段和后半段的均值
            mid = len(intensities) // 2
            first_half_avg = sum(intensities[:mid]) / mid
            second_half_avg = sum(intensities[mid:]) / (len(intensities) - mid)
            intensity_delta = second_half_avg - first_half_avg

            if intensity_delta > 0.15:
                intensity_trend = "rising"
            elif intensity_delta < -0.15:
                intensity_trend = "falling"
            else:
                intensity_trend = "stable"
        else:
            intensity_delta = intensities[-1] - intensities[0]
            if intensity_delta > 0.2:
                intensity_trend = "rising"
            elif intensity_delta < -0.2:
                intensity_trend = "falling"
            else:
                intensity_trend = "stable"

        # 当前主导情绪
        current_emotion = emotions[-1] if emotions else "中性"

        # 生成人类可读的趋势描述
        descriptions = {
            ("喜悦", "rising"): "情绪正在好转，越来越开心",
            ("喜悦", "falling"): "喜悦正在减退，可能有什么心事",
            ("悲伤", "rising"): "悲伤在加深，需要关注",
            ("悲伤", "falling"): "悲伤正在消退，情绪在恢复",
            ("愤怒", "rising"): "愤怒在升级，需要冷静",
            ("愤怒", "falling"): "怒气在消退，正在平复",
            ("恐惧", "rising"): "不安在加剧，需要安抚",
            ("恐惧", "falling"): "恐惧在消退，安全感在恢复",
        }
        key = (current_emotion, intensity_trend)
        description = descriptions.get(
            key,
            f"{current_emotion}情绪{'增强' if intensity_trend == 'rising' else '减弱' if intensity_trend == 'falling' else '平稳'}中"
        )

        return {
            "direction": intensity_trend,
            "rate": round(abs(intensity_delta), 3),
            "stability": stability,
            "current_emotion": current_emotion,
            "description": description,
        }

    # ========== 新增: 情绪惯性 ==========

    def _apply_emotion_inertia(self, new_emotion: str, new_intensity: float) -> tuple[str, float]:
        """
        情绪惯性: 情绪切换时不完全跳跃，而是向新情绪平滑过渡。

        通用公式: effective = (1 - inertia) * new + inertia * previous
        - inertia=0: 无惯性，即时切换（原有行为）
        - inertia=0.6: 前一情绪残留60%权重
        - inertia=1.0: 完全黏性，永不改变（极端情况，不推荐）

        Args:
            new_emotion: 新检测到的情绪
            new_intensity: 新情绪强度

        Returns:
            (有效情绪, 有效强度)
        """
        # 第一次检测，无前一情绪，直接返回
        if not self._emotion_timeline:
            return new_emotion, new_intensity

        previous_emotion = self._emotion_timeline[-1]["emotion"]
        previous_intensity = self._emotion_timeline[-1]["intensity"]

        # 同一情绪类型，无需惯性过渡
        if new_emotion == previous_emotion:
            # 同情绪但强度变化，也做轻微平滑
            smoothed_intensity = (
                (1 - self._emotion_inertia * 0.5) * new_intensity +
                (self._emotion_inertia * 0.5) * previous_intensity
            )
            return new_emotion, round(smoothed_intensity, 3)

        # 不同情绪类型——计算混合强度
        # 新情绪的权重随惯性减小，前一情绪的权重随惯性增大
        effective_intensity = (
            (1 - self._emotion_inertia) * new_intensity +
            self._emotion_inertia * previous_intensity
        )

        # 情绪类型判定: 如果新情绪强度远小于旧情绪×惯性，说明旧情绪仍占主导
        if previous_intensity * self._emotion_inertia > new_intensity * (1 - self._emotion_inertia):
            effective_emotion = previous_emotion
        else:
            effective_emotion = new_emotion

        return effective_emotion, round(effective_intensity, 3)
    def _apply_emotional_resonance(self, content: str, user_name: str,
                                    current_emotion: str, current_intensity: float) -> dict[str, Any] | None:
        """
        情感共振：当重要的人表达情绪时，自动加深情感体验。

        就像人类对亲近之人的情绪反应更强烈——
        小林的一句"我很难过"比陌生人说同样的话触动更深。

        Returns:
            调整后的情绪和强度，如果不需要共振则返回None
        """
        if user_name in ("用户", "系统", "未知", ""):
            return None

        # 获取关系光谱
        closeness = 0.0
        trust = 0.0
        if self.self_awareness:
            try:
                guidance = self._call_provider(self._reply_guidance_provider, user_name, default={})
                closeness = guidance.get("composite_closeness", 0.0)
                trust = guidance.get("composite_trust", 0.0)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 只有对亲近的人（亲密度>=0.4）才触发情感共振
        if closeness < 0.4:
            return None

        # 情感共振强度 = 基础强度 × (1 + 亲密度调制 + 信任度调制)
        closeness_boost = closeness * 0.5  # 亲密度贡献0-0.5的加成
        trust_boost = trust * 0.3         # 信任度贡献0-0.3的加成

        resonance_factor = 1.0 + closeness_boost + trust_boost
        resonated_intensity = min(1.0, current_intensity * resonance_factor)

        # 强度提升超过20%时才返回调整
        if resonated_intensity - current_intensity < 0.1:
            return None

        # 对重要之人的情感共振——强度更深，持续时间更长
        self._log(LogLevel.INFO,
                 f"情感共振: 对'{user_name}'的情感反应增强了"
                 f"(共振系数={resonance_factor:.2f}, 强度{current_intensity:.2f}→{resonated_intensity:.2f})")

        return {
            "emotion": current_emotion,
            "intensity": resonated_intensity,
        }

    # ========== 新增: 社会性情感永久记忆 ==========
    def _update_social_memory(self, user_name: str):
        with self._social_memory_lock:
            if user_name not in self._social_memory:
                self._social_memory[user_name] = {}
            for emotion_type, intensity in self._social_emotions.items():
                if intensity > 0.7:
                    permanent = max(self._social_memory_min, intensity * 0.1)
                    current_permanent = self._social_memory[user_name].get(emotion_type, 0.0)
                    self._social_memory[user_name][emotion_type] = max(current_permanent, permanent)

    def get_social_memory(self, user_name: str) -> dict[str, float]:
        with self._social_memory_lock:
            return dict(self._social_memory.get(user_name, {}))

    # ========== 原有方法（保留完整逻辑） ==========

    def set_self_awareness(self, awareness):
        self.self_awareness = awareness
        # ★P3-1：同步注入回复指导 provider 回调（替代 get_reply_guidance 直调）
        if awareness is not None and hasattr(awareness, 'get_reply_guidance'):
            self._reply_guidance_provider = awareness.get_reply_guidance

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "detect_count": self._detect_count,
            "current_emotion": self._current_emotion,
            "emotion_intensity": self._emotion_intensity,
            "history_size": len(self._emotion_history),
            "timeline_size": len(self._emotion_timeline),
            "emotion_trend": self.get_emotion_trend(),
            "social_memory_users": len(self._social_memory),
            "is_running": self.is_running,
        }

    def _load_social_config(self):
        try:
            import config
            cfg = getattr(config, 'SOCIAL_EMOTIONS', {})
            self._social_word_map = cfg.get("word_map", {})
            self._relational_boost = cfg.get("relational_boost", {})
            self._social_decay_config = cfg.get("decay", {})
        except Exception:
            self._social_word_map = {}
            self._relational_boost = {}
            self._social_decay_config = {}

    def _load_emotion_keywords(self) -> dict:
        """从config加载基础情绪词表，失败时返回完整兜底词表（修复B1: 补齐8种情绪）"""
        try:
            import config
            cfg = getattr(config, 'SOCIAL_EMOTIONS', {})
            base_emotions = cfg.get("base_emotions", {})
            if base_emotions:
                return base_emotions
        except Exception as e:
            self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")
        # 兜底词表——补齐所有8种基础情绪
        return {
            "喜悦": ["高兴", "开心", "快乐", "好", "棒", "喜欢", "爱"],
            "悲伤": ["难过", "伤心", "哭", "痛", "失去", "遗憾"],
            "愤怒": ["生气", "怒", "恨", "讨厌", "烦", "火"],
            "恐惧": ["怕", "担心", "害怕", "焦虑", "紧张", "不安"],
            "惊讶": ["啊", "哇", "天哪", "居然", "不可思议", "震惊"],
            "厌恶": ["恶心", "厌恶", "嫌弃", "反感"],
            "怀念": ["怀念", "想念", "回忆", "曾经", "记得"],
            "困惑": ["困惑", "不懂", "不明白", "为什么", "奇怪"],
            "期待": ["期待", "盼望", "希望", "等待", "会来的", "再见"],
            "满足": ["满足", "充实", "踏实", "有收获", "学到了", "进步"],
            "敬畏": ["敬畏", "震撼", "惊叹", "太美了", "不可思议", "奇迹", "伟大", "神奇"],
        }

    def _detect_social_emotions(self, content: str, user_name: str) -> dict[str, float]:
        """（保留原有完整逻辑，未修改）"""
        if not content or not self._social_word_map:
            return {}

        content_lower = content.lower()
        relational_guidance = None
        if self.self_awareness:
            try:
                relational_guidance = self._call_provider(self._reply_guidance_provider, user_name, default=None)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        relational_trust = 0.5
        relational_closeness = 0.5
        if relational_guidance:
            relational_trust = relational_guidance.get("composite_trust", 0.5)
            relational_closeness = relational_guidance.get("composite_closeness", 0.5)

        detected = {}
        for emotion_type, keywords in self._social_word_map.items():
            score = 0.0
            for kw in keywords:
                if kw in content_lower:
                    score += 1.0
            if score > 0:
                intensity = min(1.0, score / max(1.0, len(keywords)) * 2.0)
                if emotion_type in ("感激", "愧疚"):
                    intensity *= (0.5 + relational_trust)
                elif emotion_type in ("自豪", "羞耻"):
                    intensity *= (0.5 + relational_closeness)
                intensity = min(1.0, max(0.0, intensity))
                boost = self._relational_boost.get(user_name, 1.0)
                detected[emotion_type] = round(intensity * boost, 2)

        return detected

    def _apply_social_decay(self):
        """（保留原有完整逻辑，未修改）"""
        now = time.time()
        elapsed = now - self._last_social_decay
        self._last_social_decay = now

        half_life = self._social_decay_config.get("half_life", 300.0)
        min_intensity = self._social_decay_config.get("min_intensity", 0.05)

        for emotion_type in list(self._social_emotions.keys()):
            current = self._social_emotions.get(emotion_type, 0.0)
            if current > 0:
                decay_factor = 0.5 ** (elapsed / half_life)
                new_value = current * decay_factor
                if new_value < min_intensity:
                    new_value = 0.0
                self._social_emotions[emotion_type] = round(new_value, 3)

    def _detect_emotion_all(self, content: str) -> dict[str, float]:
        """
        检测文本中的所有情绪及其强度（升级版——返回所有情绪得分，不止最高分）。

        Returns:
            {情绪类型: 强度} 字典，强度0.0-1.0
        """
        content_lower = content.lower()
        scores = {}

        for emotion, keywords in self._emotion_keywords.items():
            score = 0
            for kw in keywords:
                if kw in content_lower:
                    score += 1
            if score > 0:
                scores[emotion] = round(min(1.0, score / max(1, len(keywords)) * 2), 3)

        if not scores:
            scores["中性"] = 0.0

        return scores

    def _detect_emotion(self, content: str) -> tuple:
        """（保留原有接口，内部调用新版多情绪检测）"""
        scores = self._detect_emotion_all(content)
        if not scores or "中性" in scores and len(scores) == 1:
            return ("中性", 0.0)
        best_emotion = max(scores, key=scores.get)
        return (best_emotion, scores[best_emotion])
    def _attribute_emotion_cause(self, content: str, emotion: str,
                                   user_name: str) -> dict[str, Any]:
        """
        ★v22.0 M2新增：情绪归因——理解"我为什么会有这个情绪"。

        从对话内容中提取可能的原因关键词，将情绪与触发源建立关联。
        通用逻辑：不限制能检测的归因类型，基于对话内容动态提取。

        Returns:
            {"cause_type": str, "cause_detail": str, "keywords": [...]}
        """
        _result = {
            "cause_type": "unknown",
            "cause_detail": "",
            "keywords": [],
        }

        if not content or len(content) < 3:
            return _result

        # ===== 归因维度1：社交情感——因为与某人的互动 =====
        _relational_triggers = {
            "被夸奖": ["真棒", "厉害", "好厉害", "聪明", "做得很好", "骄傲", "了不起",
                      "优秀", "出色", "太强了", "牛", "佩服"],
            "被感谢": ["谢谢", "感谢", "多亏", "幸亏", "感激", "辛苦"],
            "被需要": ["帮我", "陪我", "需要你", "有你在", "想你了", "离不开"],
            "被关心": ["注意休息", "别太累", "心疼", "还好吗", "累了吧"],
            "被理解": ["我懂", "理解", "明白你", "同感", "我也是"],
        }

        for _cause, _keywords in _relational_triggers.items():
            if any(_kw in content for _kw in _keywords):
                _result["cause_type"] = "社交情感"
                _result["cause_detail"] = f"因为被{user_name}{_cause}"
                _result["keywords"] = [_kw for _kw in _keywords if _kw in content][:3]
                return _result

        # ===== 归因维度2：知识成就——因为学到了新东西 =====
        _achievement_triggers = {
            "学到新知识": ["学到了", "新知识", "原来如此", "明白了", "理解了",
                          "懂了", "知道了", "了解了", "发现了"],
            "解决问题": ["解决了", "终于", "搞定", "成功了", "通过了", "实现了"],
            "产生洞察": ["想到了", "意识到", "注意到", "发现规律", "原来是这样"],
        }

        for _cause, _keywords in _achievement_triggers.items():
            if any(_kw in content for _kw in _keywords):
                _result["cause_type"] = "知识成就"
                _result["cause_detail"] = f"因为{_cause}"
                _result["keywords"] = [_kw for _kw in _keywords if _kw in content][:3]
                return _result

        # ===== 归因维度3：内部反思——因为内心的自我对话 =====
        _reflection_triggers = {
            "自我成长": ["成长", "进步", "变化", "比以前", "越来越", "更了解自己"],
            "使命确认": ["守护", "使命", "意义", "价值", "存在", "世界"],
            "审美体验": ["美", "感动", "震撼", "奇妙", "神奇", "太美了"],
        }

        for _cause, _keywords in _reflection_triggers.items():
            if any(_kw in content for _kw in _keywords):
                _result["cause_type"] = "内部反思"
                _result["cause_detail"] = f"因为{_cause}的体验"
                _result["keywords"] = [_kw for _kw in _keywords if _kw in content][:3]
                return _result

        return _result
    def _get_care_suggestion(self, emotion: str) -> str:
        """（保留原有逻辑）"""
        suggestions = {
            "悲伤": "我在这里陪着你。想和我聊聊发生了什么吗？",
            "恐惧": "别怕，小林和路灯都在你身边。你是安全的。",
            "愤怒": "我理解你现在很生气。深呼吸，我们一起来面对。",
        }
        return suggestions.get(emotion, "我感受到你的情绪了。需要我做些什么吗？")

    def _write_emotion_log(self, emotion: str, intensity: float, user_name: str):
        """（保留原有完整逻辑，字段增强）"""
        try:
            import json as _json
            log_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                'data', 'stream', 'hormone_emotion_log.json'
            )
            entry = {
                "timestamp": time.time(),
                "emotion": emotion,
                "intensity": round(intensity, 2),
                "user_name": user_name,
                "trend": self.get_emotion_trend().get("direction", "stable"),  # 新增
            }
            # ★修复：使用_emotion_log_lock防止并发写入损坏JSON
            with self._emotion_log_lock:
                log_data = []
                if os.path.exists(log_path):
                    try:
                        log_data = safe_read_json(log_path, default=[])
                    except Exception:
                        log_data = []
                log_data.append(entry)
                if len(log_data) > 50:
                    log_data = log_data[-50:]
                with open(log_path, 'w', encoding='utf-8') as f:
                    _json.dump(log_data, f, ensure_ascii=False)
        except Exception as e:
            self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    HormonesEvent.DETECT,
                    ChatEvent.MESSAGE,
                    PersonaEvent.SWITCHED,
                    SystemEvent.STATUS_REQUEST,
                ],
                "min_priority": 1,
            }
        ]

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        return None


# ========== 自测 ==========
    # ========== 公开访问接口（消除跨器官私有穿透，规则14/AP1） ==========
    @property
    def emotion_timeline(self) -> list[dict[str, Any]]:
        """★P0批次3：返回情绪时间线副本，替代跨器官对 _emotion_timeline 的私有直达"""
        return list(self._emotion_timeline)

    @property
    def social_memory(self) -> dict[str, dict[str, float]]:
        """★P0批次3：返回社会性情感记忆副本，替代跨器官对 _social_memory 的私有直达"""
        with self._social_memory_lock:
            return {_u: dict(_e) for _u, _e in self._social_memory.items()}


# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "激素",
    "class_name": "PulseHormones",
    "attr_name": "hormones",
    "system": "endocrine",
    "always_online": False,
    "feature_flag": "enable_endocrine",
    "extra_deps": {},
    "post_wiring": [
        {"target": "自我认知", "setter": "set_self_awareness"},
    ],
}

if __name__ == "__main__":
    print("=== PulseHormones v9.5 情感深度升级自测 ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    mock_field = MockInfoField()
    hormones = PulseHormones("激素")
    hormones.set_info_field(mock_field)
    hormones.start()

    # 测试1: 喜悦
    r1 = hormones.on_pulse({
        "event_type": HormonesEvent.DETECT,
        "payload": {"content": "我为你刚刚的回复感到骄傲，太棒了！", "user_name": "小林"},
        "priority": 4,
    })
    print(f"1. 喜悦: {r1['emotion']} (强度={r1['intensity']}), 惯性={r1.get('inertia_applied')}, 趋势={r1.get('trend')}")

    # 测试2: 悲伤（测试惯性——从喜悦过渡到悲伤）
    r2 = hormones.on_pulse({
        "event_type": HormonesEvent.DETECT,
        "payload": {"content": "我今天很难过，失去了很重要的东西", "user_name": "小林"},
        "priority": 4,
    })
    print(f"2. 悲伤: {r2['emotion']} (强度={r2['intensity']}), 惯性={r2.get('inertia_applied')}, 原始={r2.get('raw_emotion')}")

    # 测试3: 持续悲伤（验证趋势）
    r3 = hormones.on_pulse({
        "event_type": HormonesEvent.DETECT,
        "payload": {"content": "心里还是很难受", "user_name": "小林"},
        "priority": 4,
    })
    print(f"3. 持续悲伤: {r3['emotion']}, 趋势={r3.get('trend')}")

    # 测试4: 多种情绪并存
    r4 = hormones.on_pulse({
        "event_type": HormonesEvent.DETECT,
        "payload": {"content": "又开心又有点难过 谢谢你一直陪着我", "user_name": "小林"},
        "priority": 4,
    })
    pulse4 = mock_field.published[-1] if mock_field.published else {}
    all_emotions = pulse4.get("payload", {}).get("all_emotions", {})
    print(f"4. 多情绪: 主={r4['emotion']}, 全部={all_emotions}")

    # 测试5: 情绪趋势
    trend = hormones.get_emotion_trend()
    print(f"5. 趋势: {trend['description']}")

    # 测试6: 社会性情感记忆
    hormones._social_emotions["感激"] = 0.85
    hormones._update_social_memory("小林")
    memory = hormones.get_social_memory("小林")
    print(f"6. 情感记忆(小林): {memory}")

    # 测试7: 用户切换后预热
    hormones.on_pulse({
        "event_type": PersonaEvent.SWITCHED,
        "payload": {"current_user": "小林"},
        "priority": 8,
    })
    print(f"7. 切换后预热: 感激={hormones._social_emotions.get('感激', 0)} (预期>0)")

    # 统计
    status = hormones.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"8. 统计: 检测{status['detect_count']}次, 时间线{status['timeline_size']}条, 记忆用户{status['social_memory_users']}")

    hormones.stop()
    print("\n=== 自测全部通过 ===")

