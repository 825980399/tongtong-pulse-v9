# -*- coding: utf-8 -*-
"""
PulseMotivationCycle —— 动机循环器官（v24.0新增）· 内驱力引擎

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 承接心跳、StressAxisEvent.LEVEL_CHANGED 与 InferenceEvent.RESULT，维护动机队列与压力水位，计算优先级并向外发射动机信号与压力约束。
机制: _on_heartbeat → _run_cycle，_update_pressure 更新压力水位，_update_motivations 按 _calculate_priority 计算优先级，_add_motivation 入队、_cleanup_expired_motivations 清理过期项；_evaluate_and_record_feedback 记录反馈并回写经验池；_emit_motivation_signals 与 _emit_pressure_constraints 对外广播，_send_to_field 注入振荡场；_on_inference_result 吸收推理结果修正优先级。
定位: v24.0 引入的「内驱力引擎」，把压力转化为有序的行为动机。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import random
import threading
import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import (
    Event,
    HeartEvent,
    InferenceEvent,
    LogLevel,
    StressAxisEvent,
    SystemEvent,
)


class PulseMotivationCycle(BasePulseOrgan):
    """
    动机循环器官（v24.0新增）
    """

    def __init__(self, organ_name: str = "动机循环"):
        super().__init__(organ_name)

        # 依赖引用
        self.experience_pool = None     # ExperiencePool 实例
        self.stress_axis = None         # PulseStressAxis 实例
        self.self_awareness = None      # PulseSelfAwareness 实例
        self.oscillon_field = None      # OscillonField 实例
        self.node_pool = None           # ★骨架优化：节点池（替代穿透 self_awareness 读 node_pool）

        # 活跃动机列表
        self._active_motivations: list[dict[str, Any]] = []
        self._max_motivations = 10

        # 压力状态
        self._pressure_state: dict[str, float] = {
            "resource": 0.0,        # 资源压力（CPU/内存）
            "frustration": 0.0,     # 挫败压力（近期失败）
            "cognitive": 0.0,       # 认知压力（推理复杂度）
            "retrieval": 0.0,       # 记忆检索压力
        }

        # 循环控制
        self._last_cycle_time = 0.0
        self._cycle_interval = 60.0  # 每60秒运行一次循环
        self._heartbeat_count = 0

        # 统计
        self._cycle_count = 0
        self._motivation_generated_count = 0
        self._reward_issued_count = 0

        # 线程安全
        self._lock = threading.Lock()

        # 从配置加载间隔
        self._load_config()

        # ★v25.0新增：对话推理体验记录冷却（防止同一轮对话多次记录）
        self._last_inference_record_time = 0.0
        self._inference_record_cooldown = 10.0  # 10秒内只记录一次

        # ★v25.1修复：同动机发射冷却（消除「回答你是谁」等固定冲动空转）
        # 同一动机在冷却窗口内不重复发射，避免每 cycle 固定重复；其他高强动机可打断。
        self._last_urge_desc = ""
        self._last_urge_time = 0.0
        self._urge_cooldown = 300.0  # 默认同一动机 5 分钟内不重复发射
        # ★v25.2优化：动机强度衰减（P2 观察项落地，消除「你是谁」恒强空转）
        # 背景：用户问过「你是谁」后该动机以高强度(0.90)驻留，_add_motivation 用 max 合并
        #   只增不减 → 每冷却窗口(5min)重复发射相同冲动，形成「恒强空转」。
        # 方案：①发射后强度减半（冲动被表达即消退，符合动机动力学「满足后衰减」）；
        #       ②活跃动机强度低于下限(0.15)即移除（防低强度驻留死循环）。
        # 兼容：真实强化（用户再次提问触发推理）仍会 max 顶回，不误伤有真实依据的动机；
        #   资源告警等由 _update_motivations 每循环刷新，不受影响。
        self._urge_decay_after_emit = 0.5  # 发射后强度乘以此系数
        self._min_urge_intensity = 0.15  # 低于此强度移除动机

    def _load_config(self):
        """从config加载配置，失败使用默认"""
        try:
            import config
            cfg = getattr(config, 'MOTIVATION_CONFIG', {})
            self._cycle_interval = cfg.get("cycle_interval_seconds", 60.0)
            self._max_motivations = cfg.get("max_motivations", 10)
            self._urge_cooldown = cfg.get("urge_cooldown_seconds", 300.0)
            self._urge_decay_after_emit = cfg.get("urge_decay_after_emit", 0.5)
            self._min_urge_intensity = cfg.get("min_urge_intensity", 0.15)
        except Exception as e:
            self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")

    # ========== 框架注入接口 ==========

    def set_experience_pool(self, pool):
        self.experience_pool = pool

    def set_stress_axis(self, axis):
        self.stress_axis = axis

    def set_self_awareness(self, awareness):
        self.self_awareness = awareness

    def set_oscillon_field(self, field):
        self.oscillon_field = field

    def set_node_pool(self, pool):
        """★骨架优化：注入节点池（替代穿透 self_awareness 读 node_pool）"""
        self.node_pool = pool

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == HeartEvent.BEAT:
            return self._on_heartbeat(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        elif event_type == StressAxisEvent.LEVEL_CHANGED:
            return self._on_stress_level_changed(payload)
        elif event_type == InferenceEvent.RESULT:
            return self._on_inference_result(payload)
        return None

    def _on_heartbeat(self, payload: dict) -> dict[str, Any]:
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._heartbeat_count += 1
        now = time.time()
        # ★v29/14.45：动态间隔——无对话+硬件充裕时加速动机循环，有对话时减速
        _cycle_interval = self._cycle_interval
        try:
            from nucleus.runtime_tempo import get_runtime_tempo
            _tempo = get_runtime_tempo().get_background_tempo()
            _cycle_interval = max(5.0, self._cycle_interval * _tempo)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        if now - self._last_cycle_time >= _cycle_interval:
            self._last_cycle_time = now
            return self._run_cycle()
        return {"status": "waiting", "next_cycle_in": round(_cycle_interval - (now - self._last_cycle_time), 1)}

    def _on_stress_level_changed(self, payload: dict) -> dict[str, Any]:
        """应激轴水平变化时更新资源压力"""
        stress_level = payload.get("stress_level", 0.0)
        with self._lock:
            self._pressure_state["resource"] = max(self._pressure_state["resource"], stress_level * 0.8)
        return {"status": "updated"}
    def _on_inference_result(self, payload: dict) -> dict[str, Any] | None:
        """
        ★v25.0新增：接收推理结果，记录为体验池中的对话体验。
        
        根据推理置信度和方法判断体验质量：
        - 高置信度（≥0.8）→ 正向体验（认知/成就奖赏）
        - 低置信度（<0.4）→ 负向体验（挫败压力）
        - 中等置信度 → 中性体验（仅记录，低奖赏）
        """
        if not self.experience_pool:
            return {"status": "skipped", "reason": "体验池不可用"}

        # 节流：10秒内只记录一次
        _now = time.time()
        if _now - self._last_inference_record_time < self._inference_record_cooldown:
            return {"status": "throttled"}
        self._last_inference_record_time = _now

        _question = payload.get("question", "")
        _answer = payload.get("answer", "")
        _method = payload.get("method", "unknown")
        _confidence = payload.get("confidence", 0.5)
        _user_name = payload.get("user_name", "")

        # 跳过系统内部推理（无用户参与）
        if not _user_name or _user_name == "系统":
            return {"status": "skipped", "reason": "无用户参与"}

        # 根据置信度评估体验
        if _confidence >= 0.8:
            # 高置信度 → 正向体验
            _reward_type = "achievement" if _method == "rule" else "cognitive"
            _reward_intensity = min(0.9, 0.5 + _confidence * 0.4)
            _emotion_tags = ["满足", "自信"]
            _emotion_intensity = 0.5 + _confidence * 0.3
            _process_pressure = 0.1
            _pressure_type = "cognitive"
            _motivation_desc = f"回答用户关于「{_question[:30]}」的问题"
        elif _confidence < 0.4:
            # 低置信度 → 负向体验
            _reward_type = "cognitive"
            _reward_intensity = 0.1
            _emotion_tags = ["挫败", "不确定"]
            _emotion_intensity = 0.5
            _process_pressure = 0.7
            _pressure_type = "frustration"
            _motivation_desc = f"不确定如何回答「{_question[:30]}」"
        else:
            # 中等置信度 → 中性体验
            _reward_type = "cognitive"
            _reward_intensity = 0.3
            _emotion_tags = ["平静"]
            _emotion_intensity = 0.3
            _process_pressure = 0.3
            _pressure_type = "cognitive"
            _motivation_desc = f"尝试回答「{_question[:30]}」"

        self.experience_pool.record_experience(
            motivation=_motivation_desc,
            motivation_intensity=0.5,
            process_pressure=_process_pressure,
            pressure_type=_pressure_type,
            reward_type=_reward_type,
            reward_intensity=_reward_intensity,
            emotion_tags=_emotion_tags,
            emotion_intensity=_emotion_intensity,
            content=f"用户{_user_name}提问：{_question[:50]}，回答方法：{_method}，置信度：{_confidence}"
        )

        self._log(LogLevel.DEBUG,
                 f"对话体验记录: confidence={_confidence}, reward={_reward_intensity}, "
                 f"emotion={_emotion_intensity}")

        return {"status": "recorded", "confidence": _confidence}
    def _run_cycle(self) -> dict[str, Any]:
        """执行一次动机闭环迭代（增强版：整体异常保护）"""
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._cycle_count += 1
        try:
            self._update_pressure()
            self._update_motivations()
            self._evaluate_and_record_feedback()
            self._emit_motivation_signals()
            self._emit_pressure_constraints()
            self._send_to_field()
            self._cleanup_expired_motivations()
            self._log(LogLevel.INFO,
                 f"动机循环#{self._cycle_count}: 压力={self._pressure_state}, "
                 f"活跃动机={len(self._active_motivations)}")
        except Exception as _e:
            self._log(LogLevel.ERROR, f"动机循环异常: {_e}")
        return {
            "status": "cycle_completed",
            "cycle": self._cycle_count,
            "pressure": dict(self._pressure_state),
            "active_motivations": len(self._active_motivations),
        }

    def _update_pressure(self):
        """计算当前多维压力（0-1）"""
        with self._lock:
            # 1. 资源压力
            resource = 0.0
            try:
                if self.info_field:
                    snap = self.info_field.get_current("touch.hardware_snapshot")
                    if snap and isinstance(snap, dict):
                        payload = snap.get("payload", {})
                        cpu = payload.get("cpu", {}).get("usage_percent", 0)
                        mem = payload.get("memory", {}).get("usage_percent", 0)
                        resource = max(cpu, mem) / 100.0
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            if self.stress_axis:
                try:
                    stress_level = self.stress_axis.get_stats().get("stress_level", 0.0)
                    resource = max(resource, stress_level * 0.8)
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            self._pressure_state["resource"] = round(max(0.0, min(1.0, resource)), 2)

            # 2. 挫败压力（★v9.5修复：时间加权衰减，避免短时间几条负面便爆表卡死）
            # 原问题：recent_negative/5 过于敏感，6条近期负面即 frustration=1.0，
            # 导致自我修改被长期禁止（日志连续56次触发《自我修改约束》）。
            # 修复：按时间衰减加权（越新权重越高），并降低分母敏感度。
            frustration = 0.0
            if self.experience_pool:
                try:
                    negative = self.experience_pool.get_negative_experiences(limit=20)
                    if negative:
                        now = time.time()
                        # 时间衰减加权：1h内权重1.0，2h内 0.6，4h内 0.3，更旧 0.1
                        _weighted_neg = 0.0
                        for e in negative:
                            _age = now - e.get("timestamp", 0)
                            if _age < 3600:
                                _w = 1.0
                            elif _age < 7200:
                                _w = 0.6
                            elif _age < 14400:
                                _w = 0.3
                            else:
                                _w = 0.1
                            _weighted_neg += _w
                        # 分母从 5 放宽到 8，避免几条负面就顶满
                        frustration = min(1.0, _weighted_neg / 8.0)
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            self._pressure_state["frustration"] = round(frustration, 2)

            # 3. 认知压力
            cognitive = 0.0
            if self.experience_pool:
                try:
                    recent = self.experience_pool.query_experiences(
                        pressure_type="cognitive", limit=20
                    )
                    if recent:
                        avg_pressure = sum(e.get("process_pressure", 0.0) for e in recent) / len(recent)
                        cognitive = avg_pressure
                except Exception as e:
                    self._log(LogLevel.ERROR, f'异常: {e}')
            self._pressure_state["cognitive"] = round(cognitive, 2)

            # 4. 记忆检索压力（★骨架优化：直接读注入的 node_pool，不再穿透 self_awareness）
            retrieval = 0.0
            try:
                if self.node_pool:
                    total = self.node_pool.count()
                    retrieval = min(1.0, total / 50000.0)
            except Exception as e:
                self._log(LogLevel.ERROR, f'异常: {e}')
            self._pressure_state["retrieval"] = round(retrieval, 2)

    def _update_motivations(self):
        """从体验池生成/更新内生动机"""
        if not self.experience_pool:
            return

        positive = self.experience_pool.get_positive_experiences(limit=10)
        negative = self.experience_pool.get_negative_experiences(limit=5)

        # 基于正向体验生成趋近动机
        for exp in positive[:5]:
            motivation_desc = exp.get("motivation", "")
            if not motivation_desc or len(motivation_desc) < 2:
                motivation_desc = "探索新的可能"
            reward_intensity = exp.get("result_reward", {}).get("intensity", 0.5)
            resource_pressure = self._pressure_state.get("resource", 0.0)
            intensity = reward_intensity * (1 - resource_pressure * 0.5)
            if intensity < 0.1:
                continue
            motivation = {
                "id": f"mot_{int(time.time()*1000)}_{self._motivation_generated_count}",
                "description": motivation_desc,
                "intensity": round(intensity, 2),
                "priority": self._calculate_priority(intensity, exp.get("result_reward", {}).get("type", "cognitive")),
                "expires_at": time.time() + 3600,
                "source": "experience_pool",
                "type": "approach",
            }
            self._add_motivation(motivation)
            self._motivation_generated_count += 1

        # 基于负向体验生成回避动机
        for exp in negative[:3]:
            pressure_type = exp.get("pressure_type", "frustration")
            avoidance_desc = f"避免高{pressure_type}的情境"
            intensity = 0.5 * (1 - self._pressure_state.get("frustration", 0.0) * 0.3)
            if intensity < 0.1:
                continue
            motivation = {
                "id": f"mot_{int(time.time()*1000)}_{self._motivation_generated_count}",
                "description": avoidance_desc,
                "intensity": round(intensity, 2),
                "priority": self._calculate_priority(intensity, "avoidance"),
                "expires_at": time.time() + 3600,
                "source": "experience_pool",
                "type": "avoidance",
            }
            self._add_motivation(motivation)
            self._motivation_generated_count += 1

    def _calculate_priority(self, intensity: float, reward_type: str) -> int:
        """根据强度和奖赏类型计算优先级（1-10）"""
        base = int(intensity * 8) + 1
        if reward_type == "achievement":
            base += 1
        elif reward_type == "connection":
            base += 2
        return min(10, max(1, base))

    def _add_motivation(self, motivation: dict):
        """添加动机到活跃列表，去重"""
        with self._lock:
            for m in self._active_motivations:
                if m["description"] == motivation["description"]:
                    m["intensity"] = max(m["intensity"], motivation["intensity"])
                    m["expires_at"] = motivation["expires_at"]
                    return
            self._active_motivations.append(motivation)
            if len(self._active_motivations) > self._max_motivations:
                self._active_motivations.sort(key=lambda x: x["priority"], reverse=True)
                self._active_motivations = self._active_motivations[:self._max_motivations]

    def _cleanup_expired_motivations(self):
        """清理过期动机（★v25.2：+低强度移除，防恒强空转/低强度驻留死循环）"""
        now = time.time()
        _min_i = getattr(self, "_min_urge_intensity", 0.15)
        with self._lock:
            self._active_motivations = [
                m for m in self._active_motivations
                if m["expires_at"] > now and m["intensity"] >= _min_i
            ]

    def _evaluate_and_record_feedback(self):
        """评估当前状态并产生奖赏/挫败信号，记录为体验"""
        with self._lock:
            resource = self._pressure_state.get("resource", 0.0)
            frustration = self._pressure_state.get("frustration", 0.0)

        if frustration < 0.3 and resource < 0.7:
            reward_type = "cognitive"
            intensity = 0.3 + random.random() * 0.2
        elif frustration > 0.5:
            reward_type = "cognitive"
            intensity = 0.0  # 挫败，无奖赏
        else:
            reward_type = "connection" if resource < 0.4 else "cognitive"
            intensity = 0.2

        if self.experience_pool:
            self.experience_pool.record_experience(
                motivation="维持系统平衡",
                motivation_intensity=0.5,
                process_pressure=self._pressure_state.get("cognitive", 0.0),
                pressure_type="cognitive",
                reward_type=reward_type,
                reward_intensity=intensity,
                emotion_tags=["平静"] if intensity > 0 else ["挫败"],
                emotion_intensity=0.3 if intensity > 0 else 0.5,
                content="动机循环内部评估"
            )
            self._reward_issued_count += 1

    def _emit_motivation_signals(self):
        """发射动机信号，供其他器官使用"""
        with self._lock:
            if not self._active_motivations:
                return
            top = max(self._active_motivations, key=lambda x: x["intensity"])
            if top["intensity"] < 0.2:
                return
            # ★v25.1修复：同动机发射冷却——同一 description 在冷却窗口内不重复发射，
            # 消除「回答你是谁」等固定冲动每分钟空转；冷却窗口过后可再次发射，
            # 其他更高强度动机仍可随时打断（保持动机系统的多样性与响应性）。
            _now = time.time()
            if (top["description"] == self._last_urge_desc
                    and (_now - self._last_urge_time) < self._urge_cooldown):
                return
            self._last_urge_desc = top["description"]
            self._last_urge_time = _now
            # ★v25.2：发射后强度衰减——冲动被表达即消退（满足后衰减），
            #   避免「你是谁」等一次性动机以恒强重复发射；真实强化会再次顶回。
            _decay = getattr(self, "_urge_decay_after_emit", 0.5)
            top["intensity"] = round(top["intensity"] * _decay, 4)
            self._emit(Event.MOTIVATION_URGE, {
                "motivation_id": top["id"],
                "description": top["description"],
                "intensity": top["intensity"],
                "priority": top["priority"],
                "type": top["type"],
            }, priority=4, layer="L3")

    def _emit_pressure_constraints(self):
        """根据压力分类发射约束信号"""
        with self._lock:
            resource = self._pressure_state.get("resource", 0.0)
            frustration = self._pressure_state.get("frustration", 0.0)

        if resource > 0.8:
            self._emit(Event.CONSTRAINT_SELF_MODIFY_FORBIDDEN, {
                "reason": "资源压力过高，禁止自我修改",
                "pressure": resource,
            }, priority=8, layer="L0")
        if frustration > 0.7:
            self._emit(Event.CONSTRAINT_AGGRESSIVE_RESTRUCTURE_FORBIDDEN, {
                "reason": "挫败压力过高，禁止激进重构",
                "pressure": frustration,
            }, priority=8, layer="L0")

    def _send_to_field(self):
        """将动机和压力信号送入振荡场"""
        if not self.oscillon_field:
            return
        try:
            if hasattr(self.oscillon_field, 'update_signal'):
                self.oscillon_field.update_signal(
                    source=self.organ_name,
                    pressure=dict(self._pressure_state),
                    motivation_count=len(self._active_motivations)
                )
            elif hasattr(self.oscillon_field, 'propagate'):
                signal = {
                    "source": self.organ_name,
                    "pressure": dict(self._pressure_state),
                }
                self.oscillon_field.propagate(signal)
        except Exception as e:
            self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "organ": self.organ_name,
                "cycle_count": self._cycle_count,
                "active_motivations": len(self._active_motivations),
                "pressure": dict(self._pressure_state),
                "motivation_generated": self._motivation_generated_count,
                "reward_issued": self._reward_issued_count,
                "is_running": self.is_running,
            }

    def get_active_motivations(self) -> list[dict[str, Any]]:
        """★FIX: 暴露活跃动机列表，供其他器官检索/注入决策权重（问题3部分闭环）"""
        with self._lock:
            return list(self._active_motivations)

    def get_pressure_state(self) -> dict[str, Any]:
        """★FIX: 暴露压力状态，供其他模块感知资源/挫败压力"""
        with self._lock:
            return dict(self._pressure_state)

    def get_resonance_conditions(self) -> list:
        return [{
            "organ_name": self.organ_name,
            "event_types": [
                HeartEvent.BEAT,
                StressAxisEvent.LEVEL_CHANGED,
                SystemEvent.STATUS_REQUEST,
                InferenceEvent.RESULT,  # ★v25.0新增：订阅推理结果，记录对话体验
            ],
            "min_priority": 1,
        }]

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "动机循环",
    "class_name": "PulseMotivationCycle",
    "attr_name": "motivation_cycle",
    "system": "core",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [
        {"target": "experience_pool", "setter": "set_experience_pool"},
        {"target": "自我认知", "setter": "set_self_awareness"},
        {"target": "oscillon_monitor", "setter": "set_oscillon_field"},
        {"target": "node_pool", "setter": "set_node_pool"},
        {"target": "应激轴", "setter": "set_stress_axis"},
    ],
}

if __name__ == "__main__":
    print("=== PulseMotivationCycle v24.0 自测 ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)
        def get_current(self, key):
            return None

    class MockPulseCore:
        def emit(self, source_organ, event_type, payload, priority, layer="L1"):
            return {"event_type": event_type, "payload": payload}

    class MockStressAxis:
        def get_stats(self):
            return {"stress_level": 0.1}

    class MockExperiencePool:
        def __init__(self):
            self._experiences = []
        def get_positive_experiences(self, limit=10):
            return [{"motivation": "帮助小林", "result_reward": {"type": "achievement", "intensity": 0.8}, "timestamp": time.time()}]
        def get_negative_experiences(self, limit=5):
            return []
        def query_experiences(self, **kwargs):
            return []
        def record_experience(self, **kwargs):
            self._experiences.append(kwargs)

    class MockOscillonField:
        def propagate(self, signal):
            pass

    mock_field = MockInfoField()
    mock_stress = MockStressAxis()
    mock_pool = MockExperiencePool()
    mock_field_osc = MockOscillonField()

    mc = PulseMotivationCycle("动机循环")
    mc.set_info_field(mock_field)
    mc.set_pulse_core(MockPulseCore())
    mc.set_experience_pool(mock_pool)
    mc.set_stress_axis(mock_stress)
    mc.set_oscillon_field(mock_field_osc)
    mc.start()

    for i in range(3):
        mc._last_cycle_time = 0
        result = mc.on_pulse({"event_type": HeartEvent.BEAT, "payload": {}, "priority": 5})
        print(f"循环{i+1}: {result['status']}, 压力={result['pressure']}, 动机数={result['active_motivations']}")

    stats = mc.get_stats()
    print(f"统计: 循环{stats['cycle_count']}次, 生成动机{stats['motivation_generated']}, 奖赏{stats['reward_issued']}")
    mc.stop()
    print("\n=== 自测完成 ===")
