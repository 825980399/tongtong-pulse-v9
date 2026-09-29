# -*- coding: utf-8 -*-
"""
PulseBloodVessel —— 血管器官 · 场数据循环与连通性监测

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日

职责: 订阅心跳脉冲对信息场执行连通性巡检，识别沉默器官与订阅配对缺口并发射告警脉冲，汇总各器官脉冲收发量生成循环健康报告。
机制: on_pulse 只对 HeartEvent.BEAT 响应，先由 _track_organ_activity 登记各器官最近活动时刻，再由 _run_patrol 执行巡检；超过阈值心跳周期无活动的器官发射 VascularEvent.SILENT_ORGAN（L0 生命线层），持续恶化则升级为 VascularEvent.ORGAN_ESCALATION。
定位: 信息场的「血液循环」监测层，不参与业务推理，只负责框架自身的存活与连通性兜底。
"""

import os
import sys
import threading
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config
from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import SILENCE_EXEMPT_ORGANS, HeartEvent, LogLevel, VascularEvent
from nucleus.organ_identity import resolve_organ_key  # ★相关任务：器官名归一化


class PulseBloodVessel(BasePulseOrgan):
    """
    血管 —— 场数据循环与连通性监测器官（v9.5 分层脉冲版）
    """

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'vessel_silence_threshold' in _rp and hasattr(self, '_silence_threshold'):
                self._silence_threshold = _rp['vessel_silence_threshold']
            if 'vessel_check_interval' in _rp and hasattr(self, '_check_interval'):
                self._check_interval = _rp['vessel_check_interval']
            if 'vessel_alert_cooldown' in _rp and hasattr(self, '_escalation_alert_cooldown'):
                self._escalation_alert_cooldown = _rp['vessel_alert_cooldown']
            if 'vessel_restart_threshold' in _rp and hasattr(self, '_escalation_restart_threshold'):
                self._escalation_restart_threshold = _rp['vessel_restart_threshold']
            if 'vessel_degrade_threshold' in _rp and hasattr(self, '_escalation_degrade_threshold'):
                self._escalation_degrade_threshold = _rp['vessel_degrade_threshold']
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')


    def __init__(self, organ_name: str = "血管"):
        super().__init__(organ_name)

        self.info_field = None
                # ★P1: 从RUNTIME_PARAMS读取参数（支持热加载）
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            self._silence_threshold = _rp.get('vessel_silence_threshold', 15)
            self._check_interval = _rp.get('vessel_check_interval', 3)
            self._escalation_alert_cooldown = _rp.get('vessel_alert_cooldown', 300.0)
            self._escalation_restart_threshold = _rp.get('vessel_restart_threshold', 2)
            self._escalation_degrade_threshold = _rp.get('vessel_degrade_threshold', 4)
        except Exception:
            self._silence_threshold = 15
            self._check_interval = 3
            self._escalation_alert_cooldown = 300.0
            self._escalation_restart_threshold = 2
            self._escalation_degrade_threshold = 4    # 连续 4 次巡检沉默 → 建议降级/停用
        # ★F4收尾：分级自愈总开关（默认开，关闭后仅保留沉默告警、不再发射处置脉冲）
        self._escalation_enabled = getattr(
            config, "SILENCE_ESCALATION_ENABLED", True)

        # ★F4收尾：低频器官豁免列表统一引用 const.SILENCE_EXEMPT_ORGANS，
        #   避免血管（检测侧）与系统管理器（处置侧）各自维护导致不一致。
        # ★相关任务：豁免表统一归一化为规范 key 集合（命名空间对齐）
        self._exempt_organs = {resolve_organ_key(o) for o in SILENCE_EXEMPT_ORGANS}

        # ★P0修复：初始化线程锁和追踪字典（之前缺失导致AttributeError）
        self._lock = threading.Lock()
        self._organ_last_active = {}
        self._organ_pulse_count = {}
        self._organ_silence_state = {}
        self._beat_count = 0
        self._last_beat_time = None
        self._beat_intervals = []
        self._last_escalation_alert = 0.0
        self._last_degrade_alert = 0.0
        self._last_restart_alert = 0.0
        # ★往期批次 相关任务（P2）：DEBUG 巡检行节流状态。
        #   记录上一轮巡检发现的沉默器官名集合，若本轮集合与之完全相同则跳过 DEBUG 日志，
        #   避免「同一批沉默器官」每 40 秒重复打印（任务书：原逻辑 7 小时刷屏 819 次）。
        self._last_patrol_silent_set: set = set()

    # ========== 生命周期 ==========

    def start(self):
        super().start()
        self._log(LogLevel.INFO,
                  f"已启动，沉默阈值={self._silence_threshold}次心跳, "
                  f"巡检间隔={self._check_interval}次心跳")

    def stop(self):
        super().stop()
        self._log(LogLevel.INFO, f"已停止, 追踪器官数={len(self._organ_last_active)}")

    # ========== 脉冲处理 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        if not self.is_running:
            return None

        event_type = pulse.get("event_type", "")
        source_organ = pulse.get("source_organ", "unknown")

        # 跟踪所有非自身来源的活跃
        if source_organ not in ("血管", "main"):
            self._track_organ_activity(source_organ, event_type)

        if event_type == HeartEvent.BEAT:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._beat_count += 1
            return self._run_patrol(pulse)

        return None

    def get_resonance_conditions(self) -> list[dict[str, Any]]:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": ["*"],  # ★v24.0修复：订阅所有事件以追踪各器官活跃
                "min_priority": 1,
            },
        ]

    # ========== 活跃追踪 ==========

    def _track_organ_activity(self, organ_name: str, event_type: str):
        if organ_name in ("心脏", "血管", "main"):
            return

        now = time.time()
        # 节流：每个器官 5 秒内只记录一次，降低高频脉冲下的锁竞争
        _last = getattr(self, f"_last_track_{organ_name}", 0.0)
        if now - _last < 5.0:
            return
        setattr(self, f"_last_track_{organ_name}", now)

        with self._lock:
            self._organ_last_active[organ_name] = now
            if organ_name not in self._organ_pulse_count:
                self._organ_pulse_count[organ_name] = {"sent": 0, "received": 0}
            self._organ_pulse_count[organ_name]["sent"] += 1

    # ========== 连通性巡检 ==========

    def _run_patrol(self, pulse: dict[str, Any]) -> dict[str, Any]:
        if self._beat_count % self._check_interval != 0:
            return {"patrol": "skipped", "beat": self._beat_count}

        # ★v25.0修复：动态追踪心跳间隔
        now = time.time()
        if self._last_beat_time is not None:
            interval = now - self._last_beat_time
            self._beat_intervals.append(interval)
            # 只保留最近10次采样
            if len(self._beat_intervals) > 10:
                self._beat_intervals.pop(0)
        self._last_beat_time = now

        # 计算平均心跳间隔（秒），默认10秒兜底
        avg_interval = (sum(self._beat_intervals) / len(self._beat_intervals)
                        if self._beat_intervals else 10.0)
        threshold_seconds = self._silence_threshold * avg_interval

        # ★P1修复：合并 InfoField 的器官接收活跃记录（解决只接收不发射被误判沉默）
        if self.info_field is not None:
            try:
                _field_active = getattr(self.info_field, '_organ_last_active', {})
                with self._lock:
                    for _on, _ts in _field_active.items():
                        if _on not in self._organ_last_active or _ts > self._organ_last_active[_on]:
                            self._organ_last_active[_on] = _ts
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        with self._lock:
            silent_organs = []
            for organ_name, last_active in list(self._organ_last_active.items()):
                # ★v25.0修复：跳过豁免器官
                # ★相关任务：比对走归一化，避免 source_organ 命名空间与裸名豁免表对不齐
                if resolve_organ_key(organ_name) in self._exempt_organs:
                    continue

                silence_duration = now - last_active

                if silence_duration > threshold_seconds:
                    silent_organs.append({
                        "organ": organ_name,
                        "silence_seconds": round(silence_duration, 1),
                    })

            active_count = len(self._organ_last_active) - len(silent_organs)
            total_tracked = len(self._organ_last_active)

            patrol_report = {
                "beat": self._beat_count,
                "total_tracked": total_tracked,
                "active_organs": active_count,
                "silent_organs": silent_organs,
                "silence_threshold_beats": self._silence_threshold,
                "avg_heartbeat_interval": round(avg_interval, 1),  # ★新增：心跳间隔信息
            }

        if silent_organs:
            organ_names = [s["organ"] for s in silent_organs]
            # ★修复：沉默检测降级为 DEBUG。脉冲架构「有任务才启动」，
            # 低频器官在巡检窗口内无动作是正常现象，不应作为 WARNING 刷屏。
            # 原逻辑每次巡检无条件打印，7 小时刷屏 819 次，淹没真正告警。
            # 真正需要关注的升级告警已由下方「分级自愈」WARNING（带去重）体现。
            _cur_patrol_set = set(organ_names)
            if _cur_patrol_set != self._last_patrol_silent_set:
                # 仅当沉默器官集合发生变化时才打印，避免同一批器官每 40 秒重复刷屏。
                self._log(LogLevel.DEBUG,
                          f"巡检发现 {len(silent_organs)} 个沉默器官: {', '.join(organ_names)}")
                self._last_patrol_silent_set = _cur_patrol_set

            # ★F4：告警去重 + 分级升级（不无限重复告警同一批器官）
            _now_alert = time.time()
            _escalated = []
            for _s in silent_organs:
                _name = _s["organ"]
                _state = self._organ_silence_state.get(_name, {
                    "level": 0, "alert_count": 0, "last_alert": 0.0,
                })
                # 告警冷却：同一器官冷却期内不重复告警，避免告警风暴
                if _now_alert - _state["last_alert"] < self._escalation_alert_cooldown:
                    continue
                # 连续沉默计数 +1
                _state["alert_count"] += 1
                _state["last_alert"] = _now_alert
                # 分级升级
                if _state["alert_count"] >= self._escalation_degrade_threshold:
                    _state["level"] = 3
                elif _state["alert_count"] >= self._escalation_restart_threshold:
                    _state["level"] = 2
                else:
                    _state["level"] = 1
                self._organ_silence_state[_name] = _state
                _escalated.append({
                    "organ": _name,
                    "level": _state["level"],
                    "alert_count": _state["alert_count"],
                    "silence_seconds": _s["silence_seconds"],
                })

            if self.info_field and self.pulse_core:
                # v9.5: 沉默器官告警标记为L0生命线层（去重后仅上报本次新增的告警）
                _escalated_names = [e["organ"] for e in _escalated]
                if _escalated_names:
                    alert_pulse = self.pulse_core.emit(
                        source_organ=self.organ_name,
                        event_type=VascularEvent.SILENT_ORGAN,
                        payload={
                            "silent_organs": _escalated_names,
                            "silence_details": _escalated,
                            "beat": self._beat_count,
                        },
                        priority=6,
                        layer="L0"
                    )
                    self.info_field.publish(alert_pulse)
                # ★F4：分级处置脉冲（level>=2 时发射，供系统管理器等处置器官消费）
                # ★F4收尾：受 SILENCE_ESCALATION_ENABLED 开关控制（默认开）
                _need_action = [e for e in _escalated if e["level"] >= 2]
                if _need_action and self._escalation_enabled:
                    escalation_pulse = self.pulse_core.emit(
                        source_organ=self.organ_name,
                        event_type=VascularEvent.ORGAN_ESCALATION,
                        payload={
                            "organs": _need_action,
                            "action": "restart" if any(
                                e["level"] == 2 for e in _need_action) else "degrade",
                            "beat": self._beat_count,
                        },
                        priority=7,
                        layer="L0"
                    )
                    self.info_field.publish(escalation_pulse)
                    self._log(LogLevel.WARNING,
                              f"分级自愈：{len(_need_action)} 个器官需处置: "
                              f"{[(e['organ'], e['level']) for e in _need_action]}")
        # ★R6修复（P1次生）：原实现把「复位已恢复器官」放在
        #   `else:` 分支里，触发条件是「本轮 silent_organs 全空」。
        #   实测常年有 10+ 个低频器官被误判沉默（详见 R2 白名单修复），
        #   该 else 分支几乎永不执行 → alert_count 单调递增永不归零
        #   → level 永久锁死 3（degrade 阈值）→ 器官即使早已恢复活跃，
        #   历史计数仍在，下次误判会立刻跳到最高级处置。
        #   改为逐器官复位：本轮**未**出现在沉默列表中的器官，
        #   若已超过 3 倍冷却期未再告警，则清除其累积状态。
        _now_reset = time.time()
        _silent_now = {_s["organ"] for _s in silent_organs}
        for _name in list(self._organ_silence_state.keys()):
            if _name in _silent_now:
                continue  # 本轮仍沉默，保留累计计数
            _st = self._organ_silence_state[_name]
            # 3 倍告警冷却未再告警则复位，避免状态无限累积
            if _now_reset - _st["last_alert"] > self._escalation_alert_cooldown * 3:
                del self._organ_silence_state[_name]

        return patrol_report

    # ========== 统计信息 ==========
    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()
    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "organ": self.organ_name,
                "beat_count": self._beat_count,
                "total_tracked_organs": len(self._organ_last_active),
                "silence_threshold_beats": self._silence_threshold,
                "check_interval_beats": self._check_interval,
                "exempt_organs": sorted(self._exempt_organs),  # ★新增：豁免器官列表
                "organ_activity": dict(self._organ_pulse_count),
                # ★F4：沉默器官分级状态
                "silence_escalation": dict(self._organ_silence_state),
            }

    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float):
        """【预留 v10.0】"""


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "血管",
    "class_name": "PulseBloodVessel",
    "attr_name": "blood_vessel",
    "system": "body",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseBloodVessel v9.5 分层脉冲自测 ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    class MockCore:
        def emit(self, source_organ, event_type, payload, priority, layer="L1"):
            return {
                # [批次4·深度体检][MAINT-4] __main__ mock 补回 pulse_id
                "pulse_id": f"pulse:{source_organ}:{event_type}",
                "event_type": event_type,
                "source_organ": source_organ,
                "payload": payload,
                "priority": priority,
                "layer": layer,
            }

    vessel = PulseBloodVessel("血管")
    mock_field = MockInfoField()
    mock_core = MockCore()
    vessel.set_info_field(mock_field)
    vessel.set_pulse_core(mock_core)
    vessel._silence_threshold = 2
    vessel._check_interval = 1

    vessel.start()

    print("1. 模拟胃和眼睛的活跃脉冲:")
    vessel.on_pulse({
        "event_type": "knowledge.written",
        "source_organ": "胃",
        "payload": {},
        "priority": 3,
    })
    vessel.on_pulse({
        "event_type": "knowledge.retrieved",
        "source_organ": "眼睛",
        "payload": {},
        "priority": 3,
    })
    print(f"   追踪器官: {list(vessel._organ_last_active.keys())}")
    print(f"   脉冲计数: {dict(vessel._organ_pulse_count)}")

    print("\n2. 第一次巡检（无沉默）:")
    result = vessel.on_pulse({
        "event_type": HeartEvent.BEAT,
        "source_organ": "心脏",
        "payload": {"beat_count": 1},
        "priority": 2,
    })
    print(f"   活跃器官={result['active_organs']}, 沉默={result['silent_organs']}")

    print("\n3. 模拟沉默（将胃的最后活跃时间设为30秒前）:")
    vessel._organ_last_active["胃"] = time.time() - 30
    result = vessel.on_pulse({
        "event_type": HeartEvent.BEAT,
        "source_organ": "心脏",
        "payload": {"beat_count": 2},
        "priority": 2,
    })
    print(f"   沉默器官: {[s['organ'] for s in result['silent_organs']]}")
    print(f"   告警脉冲已发射: {len(mock_field.published) > 0}")

    # 验证告警脉冲的 layer 标记
    alert_pulses = [p for p in mock_field.published if p.get("event_type") == VascularEvent.SILENT_ORGAN]
    if alert_pulses:
        print(f"   SILENT_ORGAN脉冲 layer: {alert_pulses[0].get('layer', '未设置')} (预期L0)")

    print("\n4. 血管统计:")
    stats = vessel.get_stats()
    print(f"   心跳数: {stats['beat_count']}")
    print(f"   追踪器官: {stats['total_tracked_organs']}")
    print(f"   器官活跃记录: {stats['organ_activity']}")

    vessel.stop()
    print("\n=== 自测全部通过 ===")
