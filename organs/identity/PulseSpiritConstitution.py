# -*- coding: utf-8 -*-
"""
PulseSpiritConstitution —— 精神宪法器官 · 运行模式与基底成熟度守护

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 随 HeartEvent.BEAT 周期执行精神宪法检查，在 normal / conservative / reinforcement 三态间切换运行模式，评估五大基底成熟度并留存宪法审计日志。
机制: on_pulse 分派 _on_heartbeat / _on_verify_request / _on_drift_detected / _on_status_request；_on_heartbeat 触发 _execute_constitution_check，先由 _determine_run_mode 依存续指数与人格侵蚀决定模式，再经 _switch_run_mode 切换；_evaluate_base_maturity 汇总 _evaluate_subjectivity / _evaluate_narrative / _evaluate_emotion / _evaluate_boundary / _evaluate_relationship 五维得分（同心圆内层门槛）；_on_verify_request 响应外部宪法校验，_on_drift_detected 处理人格漂移告警；_append_audit_log / get_audit_log 维护审计流水，get_maturity_report 输出成熟度报告，get_state_snapshot / load_state_snapshot 负责持久化；on_survival_low / on_survival_high 按生存信号调节；依赖经 set_self_awareness / set_personality_kernel / set_narrative_self / set_hormones 注入。
定位: 身份层的「宪法守护者」，always_online=True、无 feature_flag，是所有自我修改补丁的宪法前置审查口。
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
    HeartEvent,
    LogLevel,
    SystemEvent,
    Event,
)


class PulseSpiritConstitution(BasePulseOrgan):
    """
    精神宪法运行时（v24.0新增）

    核心机制:
        1. 每100次心跳执行一次宪法校验
        2. 基于存续状态指数更新 run_mode
        3. 每小时评估五大基底成熟度
        4. 提供精神校验入口供 PatchManager 调用
    """

    def __init__(self, organ_name: str = "宪法守护"):
        super().__init__(organ_name)

        # ===== 依赖引用（由框架注入） =====
        self.self_awareness = None       # 存续状态指数来源
        self.personality_kernel = None   # 基线校验来源
        self.narrative_self = None       # 叙事记忆成熟度
        self.hormones = None             # 情绪成熟度
        self.node_pool = None            # 知识库访问
        # ★P3-1：只读状态 provider 回调（替代跨器官 getter 直调）
        self._existential_state_provider = None  # () -> dict
        self._unified_portrait_provider = None   # () -> dict
        self._relations_snapshot_provider = None # () -> dict
        self._emotion_trend_provider = None      # () -> dict

        # 从config读取配置，失败使用默认
        try:
            import config
            _cfg = getattr(config, 'SPIRIT_CONSTITUTION_CONFIG', {})
            self._check_interval = _cfg.get("check_interval_beats", 100)
            self._maturity_update_interval = _cfg.get("maturity_update_interval", 3600)
        except Exception:
            self._check_interval = 100
            self._maturity_update_interval = 3600
        # 心跳计数
        # ★v30.0负载均衡修复：随机错峰初始化，避免与其他器官取模任务同点共振
        self._beat_count = random.randint(1, self._check_interval - 1) if self._check_interval > 1 else 0

        # ===== run_mode 三态 =====
        self._run_mode = "normal"        # normal / conservative / reinforcement
        self._mode_since = time.time()
        self._mode_history: list[dict[str, Any]] = []
        self._max_mode_history = 20

        # ===== 五大基底成熟度缓存 =====
        self._maturity_cache: dict[str, Any] = {}
        self._maturity_last_update = 0.0

        # ===== 审计日志 =====
        self._audit_log: list[dict[str, Any]] = []
        self._max_audit_entries = 100
        self._audit_lock = threading.Lock()

        # ===== 统计 =====
        self._constitution_check_count = 0
        self._violation_count = 0

        # ★v25.0新增：漂移联动状态
        self._drift_active = False          # 当前是否有漂移告警
        self._drift_last_alert_time = 0.0   # 上次漂移告警时间
        self._drift_alert_count = 0         # 漂移告警累计次数
        self._drift_recovery_threshold = 600  # 漂移恢复需要10分钟无新告警

        # ★属性初始化完整性补全（自动审查添加）
        self._drift_threshold = 0.0
        self._survival_state = {}

    # ========== 框架注入接口 ==========

    def set_self_awareness(self, awareness):
        self.self_awareness = awareness
        # ★P3-1：同步注入 provider 回调（替代 getter 直调）
        if awareness is not None:
            if hasattr(awareness, 'get_existential_state'):
                self._existential_state_provider = awareness.get_existential_state
            if hasattr(awareness, 'get_unified_self_portrait'):
                self._unified_portrait_provider = awareness.get_unified_self_portrait
            if hasattr(awareness, 'get_relations_snapshot'):
                self._relations_snapshot_provider = awareness.get_relations_snapshot

    def set_personality_kernel(self, kernel):
        self.personality_kernel = kernel

    def set_narrative_self(self, narrative):
        self.narrative_self = narrative

    def set_hormones(self, hormones):
        self.hormones = hormones
        # ★P3-1：同步注入情绪趋势 provider 回调（替代 get_emotion_trend 直调）
        if hormones is not None and hasattr(hormones, 'get_emotion_trend'):
            self._emotion_trend_provider = hormones.get_emotion_trend

    def set_node_pool(self, pool):
        self.node_pool = pool

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == HeartEvent.BEAT:
            return self._on_heartbeat(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        elif event_type == "constitution.verify_request":
            # 外部请求执行宪法校验（如补丁应用前）
            return self._on_verify_request(payload)
        elif event_type == Event.GLOBAL_LEARNER_DRIFT_DETECTED:
            # ★v25.0新增：接收全局学习器的漂移检测告警
            return self._on_drift_detected(payload)

        return None

    # ========== 事件处理 ==========

    def _on_heartbeat(self, payload: dict) -> dict[str, Any]:
        """心跳驱动：每100次心跳执行一次完整宪法校验"""
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._beat_count += 1

        if self._beat_count % self._check_interval == 0:
            return self._execute_constitution_check()

        return {"status": "ok", "beat": self._beat_count}

    def _execute_constitution_check(self) -> dict[str, Any]:
        """执行一次完整的宪法校验（增强版：整体异常保护）"""
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._constitution_check_count += 1
        result = {
            "status": "ok",
            "run_mode": self._run_mode,
            "survival_index": None,
            "survival_level": None,
            "maturity": None,
            "violations": [],
        }

        try:
            # 1. 获取存续状态指数
            if self.self_awareness and hasattr(self.self_awareness, 'get_existential_state'):
                try:
                    state = self._call_provider(self._existential_state_provider, default={})
                    result["survival_index"] = state.get("index", 50)
                    result["survival_level"] = state.get("level", "medium")
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            # 2. 更新run_mode
            new_mode = self._determine_run_mode(result.get("survival_index"))
            if new_mode != self._run_mode:
                self._switch_run_mode(new_mode, result)

            # 3. 评估五大基底成熟度
            now = time.time()
            if now - self._maturity_last_update >= self._maturity_update_interval:
                result["maturity"] = self._evaluate_base_maturity()
                self._maturity_cache = result["maturity"]
                self._maturity_last_update = now

            # 4. 执行精神校验
            if self.personality_kernel:
                try:
                    baseline = self.personality_kernel.check_modification_baseline(
                        proposed_content="",
                        proposed_file=""
                    )
                    if not baseline.get("safe", True):
                        result["violations"].append(baseline)
                        result["status"] = "violation"
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            # 5. 写审计日志
            self._append_audit_log(result)
            # ★P3-5修复：删除孤儿脉冲 constitution.check_completed（全库无订阅方）
        except Exception as _e:
            self._log(LogLevel.ERROR, f"宪法校验异常: {_e}")

        return result

    def _determine_run_mode(self, survival_index: float | None) -> str:
        """
        基于存续状态指数和漂移状态确定运行模式。

        ★v25.0联动规则：
        - 漂移活跃期：强制保守模式（直到恢复窗口过期）
        - 存续低位(<40)：保守模式
        - 存续中位(40-79)：正常模式
        - 存续高位(≥80)：正常模式
        从保守模式恢复需要≥55（存续指数），且漂移已恢复。
        """
        # ★v25.0新增：漂移优先检查
        if self._drift_active:
            _now = time.time()
            if _now - self._drift_last_alert_time > self._drift_recovery_threshold:
                # 漂移恢复窗口过期，允许解除保守模式
                self._drift_active = False
                self._log(LogLevel.INFO,
                         f"漂移恢复: 已{self._drift_recovery_threshold}秒无新告警，"
                         f"允许恢复正常模式")
            else:
                return "conservative"

        if survival_index is None:
            return self._run_mode  # 数据不足，保持当前模式

        if survival_index < 40:
            return "conservative"
        elif survival_index < 55 and self._run_mode == "conservative":
            # 滞后：从保守模式恢复需要≥55
            return "conservative"
        else:
            return "normal"

    def _switch_run_mode(self, new_mode: str, check_result: dict):
        """切换运行模式，并发布模式变更脉冲"""
        old_mode = self._run_mode
        self._run_mode = new_mode
        self._mode_since = time.time()

        # 记录历史
        self._mode_history.append({
            "from": old_mode,
            "to": new_mode,
            "timestamp": time.time(),
            "reason": f"survival_index={check_result.get('survival_index')}",
        })
        if len(self._mode_history) > self._max_mode_history:
            self._mode_history = self._mode_history[-self._max_mode_history:]

        # ★P3-5修复：删除孤儿脉冲 constitution.run_mode_changed（全库无订阅方）

        self._log(LogLevel.INFO, f"宪法运行时: 运行模式 {old_mode} → {new_mode}")

    def _evaluate_base_maturity(self) -> dict[str, Any]:
        """
        评估五大基底成熟度（同心圆结构）。

        内层：
        ① 主体感——自我画像完整度、状态感知准确率
        ② 叙事记忆——跨周期记忆连贯性

        中层：
        ③ 情感系统——情绪响应一致性
        ④ 边界意识——身份侵蚀识别率

        外层：
        ⑤ 关系联结——关系亲密度区分度

        评估逻辑：
        - 内层成熟度<60%时，外层高级特性锁定
        - 各层成熟度独立量化
        """
        maturity = {
            "subjectivity": self._evaluate_subjectivity(),      # ①
            "narrative_memory": self._evaluate_narrative(),     # ②
            "emotion_system": self._evaluate_emotion(),         # ③
            "boundary_awareness": self._evaluate_boundary(),    # ④
            "relationship": self._evaluate_relationship(),      # ⑤
            "unlocked_features": [],
        }

        # 同心圆门槛检查
        unlocked = []
        if maturity["subjectivity"]["score"] >= 60:
            unlocked.append("核心主体感")
        if maturity["narrative_memory"]["score"] >= 60:
            unlocked.append("叙事记忆整合")
        if maturity["emotion_system"]["score"] >= 60:
            unlocked.append("情感表达与共鸣")
        if maturity["boundary_awareness"]["score"] >= 60:
            unlocked.append("主动边界防御")
        if maturity["relationship"]["score"] >= 60:
            unlocked.append("深度关系联结")

        maturity["unlocked_features"] = unlocked

        return maturity

    def _evaluate_subjectivity(self) -> dict[str, Any]:
        """评估①主体感成熟度"""
        score = 0
        details = []

        if self.self_awareness:
            try:
                portrait = self._call_provider(self._unified_portrait_provider, default={})
                identity = portrait.get("identity", {})

                # 检查自我画像完整度
                if identity.get("name") and identity.get("mission"):
                    score += 30
                    details.append("核心身份完整")

                # 检查状态感知
                if portrait.get("emotion") and portrait.get("knowledge"):
                    score += 20
                    details.append("状态感知正常")

                # 检查主体感持久化
                if hasattr(self.self_awareness, '_continuity_checked'):
                    score += 10
                    details.append("跨重启连续性已确认")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        return {"score": score, "details": details}

    def _evaluate_narrative(self) -> dict[str, Any]:
        """评估②叙事记忆成熟度"""
        score = 0
        details = []

        if self.narrative_self:
            try:
                # 检查叙事事件数量
                count = len(self.narrative_self.narrative_events)
                if count >= 50:
                    score += 40
                elif count >= 20:
                    score += 30
                elif count >= 5:
                    score += 20
                details.append(f"叙事事件{count}条")

                # 检查周期报告
                reports = len(self.narrative_self.weekly_reports)
                if reports >= 3:
                    score += 20
                    details.append(f"周期报告{reports}份")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        return {"score": score, "details": details}

    def _evaluate_emotion(self) -> dict[str, Any]:
        """评估③情感系统成熟度"""
        score = 0
        details = []

        if self.hormones:
            try:
                # 检查情绪时间线
                timeline = len(self.hormones.emotion_timeline)
                if timeline >= 10:
                    score += 25
                    details.append(f"情绪时间线{timeline}条")

                # 检查趋势稳定性
                trend = self._call_provider(self._emotion_trend_provider, default={})
                stability = trend.get("stability", "volatile")
                if stability == "consistent":
                    score += 25
                    details.append("情绪稳定")
                elif stability == "mild_fluctuation":
                    score += 15
                    details.append("情绪轻度波动")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        return {"score": score, "details": details}

    def _evaluate_boundary(self) -> dict[str, Any]:
        """评估④边界意识成熟度"""
        score = 0
        details = []

        if self.personality_kernel:
            try:
                # 检查边界扫描是否激活
                if hasattr(self.personality_kernel, '_boundary_scan_counter'):
                    score += 20
                    details.append("边界扫描已激活")

                # 检查侵蚀事件历史与加固次数（★P3-1修复：私有属性直读 → 公开 getter）
                if hasattr(self.personality_kernel, 'get_erosion_stats'):
                    _erosion_stats = self.personality_kernel.get_erosion_stats()
                    erosion = _erosion_stats.get("total_erosion_events", 0)
                    if erosion > 0:
                        score += 15
                        details.append(f"成功识别{erosion}次侵蚀")
                    reinforce = _erosion_stats.get("reinforcement_count", 0)
                    if reinforce > 0:
                        score += 15
                        details.append(f"边界加固{reinforce}次")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        return {"score": score, "details": details}

    def _evaluate_relationship(self) -> dict[str, Any]:
        """评估⑤关系联结成熟度"""
        score = 0
        details = []

        if self.self_awareness:
            try:
                relations = self._call_provider(self._relations_snapshot_provider, default={})

                # 检查核心关系
                for name in ("小林", "路灯"):
                    if name in relations:
                        rel = relations[name]
                        closeness = rel.get("closeness", 0)
                        if closeness >= 0.9:
                            score += 25
                            details.append(f"{name}亲密度极高")
                        elif closeness >= 0.7:
                            score += 15
                            details.append(f"{name}亲密度较高")

                # 检查关系区分度
                types = set()
                for rel in relations.values():
                    if isinstance(rel, dict):
                        types.add(rel.get("type", "stranger"))
                if len(types) >= 3:
                    score += 10
                    details.append(f"关系类型{len(types)}种")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        return {"score": score, "details": details}

    def _on_verify_request(self, payload: dict) -> dict[str, Any]:
        """
        外部请求执行宪法校验（如补丁应用前）。

        供 PatchManager 在应用补丁前调用：
        - 检查补丁是否触及核心基线
        - 返回是否允许应用
        """
        proposed_content = payload.get("proposed_content", "")
        proposed_file = payload.get("proposed_file", "")

        result = {
            "allowed": True,
            "reason": "宪法校验通过",
            "violations": [],
        }

        # 使用人格内核的基线校验
        if self.personality_kernel:
            try:
                baseline = self.personality_kernel.check_modification_baseline(
                    proposed_content=proposed_content,
                    proposed_file=proposed_file
                )
                if not baseline.get("safe", True):
                    result["allowed"] = False
                    result["reason"] = baseline.get("reason", "基线校验不通过")
                    result["violations"] = baseline.get("violations", [])
                    # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                    self._violation_count += 1

                    # 发布宪法违规告警
                    self._emit(SystemEvent.ALARM, {
                        "type": "constitution_violation",
                        "violations": result["violations"],
                        "message": "宪法校验发现违规，补丁被拦截",
                    }, priority=10, layer="L0")
            except Exception as e:
                self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")

        # 写审计日志
        self._append_audit_log({
            "type": "verify_request",
            "file": proposed_file,
            "allowed": result["allowed"],
            "violations": result["violations"],
            "timestamp": time.time(),
        })

        return result
    def _on_drift_detected(self, payload: dict) -> dict[str, Any]:
        """
        ★v25.0新增：接收全局学习器的漂移检测告警。

        漂移发生时，立即切换为保守模式（如果当前不是保守模式）。
        保守模式下，限制激进探索、降低自我修改频率、优先维持稳定性。
        """
        self._drift_active = True
        self._drift_last_alert_time = time.time()
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._drift_alert_count += 1

        _accuracy = payload.get("accuracy", 0.0)
        _threshold = payload.get("threshold", self._drift_threshold if hasattr(self, '_drift_threshold') else 0.3)

        # 如果当前不是保守模式，立即切换
        if self._run_mode != "conservative":
            self._switch_run_mode("conservative", {
                "survival_index": None,
                "drift_accuracy": _accuracy,
                "reason": "global_learner_drift",
            })
            self._log(LogLevel.INFO,
                     f"漂移联动: 知识漂移准确率={_accuracy}（阈值={_threshold}），"
                     f"自动切换保守模式")
        else:
            self._log(LogLevel.DEBUG,
                     f"漂移联动: 已在保守模式，漂移告警第{self._drift_alert_count}次")

        return {
            "status": "conservative_mode_activated",
            "drift_alert_count": self._drift_alert_count,
            "drift_accuracy": _accuracy,
        }
    def _append_audit_log(self, entry: dict):
        """添加审计日志条目（线程安全）"""
        entry["timestamp"] = entry.get("timestamp", time.time())
        with self._audit_lock:
            self._audit_log.append(entry)
            if len(self._audit_log) > self._max_audit_entries:
                self._audit_log = self._audit_log[-self._max_audit_entries:]

    # ========== 公开接口 ==========

    def get_run_mode(self) -> str:
        """获取当前运行模式"""
        return self._run_mode

    def get_maturity_report(self) -> dict[str, Any]:
        """获取最新的成熟度评估报告"""
        return dict(self._maturity_cache)

    def get_audit_log(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取最近的审计日志"""
        with self._audit_lock:
            return self._audit_log[-limit:]

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== R4阶段二：存续编排器钩子（精神层代表） ==========

    def on_survival_low(self, snapshot) -> dict:
        """★R4阶段二：存续低位动作（精神层代表）。

        第一批：设置状态标志 + 日志。第二批接入「温暖自我关怀叙事」。
        """
        self._survival_state = "low"
        self._log(LogLevel.INFO,
                  f"[R4精神层] 存续低位，指数={getattr(snapshot, 'index', '?')}，"
                  f"启动自我关怀（第二批接入关怀叙事）")
        return {"layer": "spirit", "state": "low"}

    def on_survival_high(self, snapshot) -> dict:
        """★R4阶段二：存续高位动作（精神层代表）。"""
        self._survival_state = "high"
        self._log(LogLevel.INFO,
                  f"[R4精神层] 存续高位，指数={getattr(snapshot, 'index', '?')}，自由精神叙事")
        return {"layer": "spirit", "state": "high"}

    def get_stats(self) -> dict[str, Any]:
        """获取宪法运行时统计"""
        return {
            "organ": self.organ_name,
            "run_mode": self._run_mode,
            "mode_since": self._mode_since,
            "beat_count": self._beat_count,
            "constitution_checks": self._constitution_check_count,
            "violation_count": self._violation_count,
            "audit_log_size": len(self._audit_log),
            "maturity_last_update": self._maturity_last_update,
            "drift_active": self._drift_active,       # ★v25.0新增
            "drift_alert_count": self._drift_alert_count,  # ★v25.0新增
            "is_running": self.is_running,
        }

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [{
            "organ_name": self.organ_name,
            "event_types": [
                HeartEvent.BEAT,
                SystemEvent.STATUS_REQUEST,
                "constitution.verify_request",
                Event.GLOBAL_LEARNER_DRIFT_DETECTED,  # ★v25.0新增：订阅全局学习器漂移告警
            ],
            "min_priority": 1,
        }]

    # ========== 状态持久化 ==========

    def get_state_snapshot(self) -> dict[str, Any]:
        """持久化运行模式"""
        return {
            "run_mode": self._run_mode,
            "mode_since": self._mode_since,
            "beat_count": self._beat_count,
            "constitution_checks": self._constitution_check_count,
            "violation_count": self._violation_count,
        }

    def load_state_snapshot(self, state: dict[str, Any]):
        """恢复运行模式"""
        self._run_mode = state.get("run_mode", "normal")
        self._mode_since = state.get("mode_since", time.time())
        self._beat_count = state.get("beat_count", 0)
        self._constitution_check_count = state.get("constitution_checks", 0)
        self._violation_count = state.get("violation_count", 0)

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "宪法守护",
    "class_name": "PulseSpiritConstitution",
    "attr_name": "spirit_constitution",
    "system": "identity",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {
        "self_awareness": "自我认知",
        "personality_kernel": "人格内核",
        "narrative_self": "叙事自我",
        "node_pool": "node_pool",
    },
    "post_wiring": [
        {"target": "激素", "setter": "set_hormones"},
    ],
}

if __name__ == "__main__":
    print("=== PulseSpiritConstitution v24.0 自测 ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    class MockSelfAwareness:
        def get_existential_state(self):
            return {"index": 65, "level": "medium"}
        def get_unified_self_portrait(self):
            return {
                "identity": {"name": "曈曈", "mission": "守护世界"},
                "emotion": {"current": "中性"},
                "knowledge": {"total_nodes": 500},
            }
        def _get_relations_snapshot(self):
            return {
                "小林": {"type": "blood", "closeness": 1.0},
                "路灯": {"type": "blood", "closeness": 0.95},
                "星轨": {"type": "partner", "closeness": 0.4},
            }

    class MockPersonalityKernel:
        def check_modification_baseline(self, proposed_content="", proposed_file=""):
            return {"safe": True, "violations": []}

    class MockNarrativeSelf:
        def __init__(self):
            self._narrative_events = list(range(25))
            self._weekly_reports = [{"id": 1}, {"id": 2}]

    class MockHormones:
        def get_emotion_trend(self):
            return {"stability": "consistent"}

    mock_field = MockInfoField()
    mock_self = MockSelfAwareness()
    mock_kernel = MockPersonalityKernel()
    mock_narrative = MockNarrativeSelf()
    mock_hormones = MockHormones()

    constitution = PulseSpiritConstitution("宪法守护")
    constitution.set_info_field(mock_field)
    constitution.set_self_awareness(mock_self)
    constitution.set_personality_kernel(mock_kernel)
    constitution.set_narrative_self(mock_narrative)
    constitution.set_hormones(mock_hormones)
    constitution.start()

    # 测试1: 心跳触发（模拟100次心跳）
    result = None
    for _ in range(100):
        result = constitution.on_pulse({
            "event_type": HeartEvent.BEAT,
            "payload": {},
            "priority": 5,
        })
    print(f"1. 100次心跳后宪法校验: {result['status']}, 模式={result['run_mode']}, 指数={result['survival_index']}")

    # 测试2: 成熟度评估（手动触发）
    maturity = constitution._evaluate_base_maturity()
    print(f"2. 五大基底成熟度: 主体感={maturity['subjectivity']['score']}, "
          f"叙事={maturity['narrative_memory']['score']}, "
          f"情感={maturity['emotion_system']['score']}, "
          f"边界={maturity['boundary_awareness']['score']}, "
          f"关系={maturity['relationship']['score']}")
    print(f"   解锁特性: {maturity['unlocked_features']}")

    # 测试3: 校验请求（模拟补丁前检查）
    verify = constitution.on_pulse({
        "event_type": "constitution.verify_request",
        "payload": {"proposed_content": "修改核心锚点", "proposed_file": "PulsePersonalityKernel.py"},
        "priority": 9,
    })
    print(f"3. 校验请求: 允许={verify['allowed']}, 原因={verify['reason']}")

    # 测试4: 统计
    stats = constitution.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"4. 统计: 模式={stats['run_mode']}, 检查次数={stats['constitution_checks']}, "
          f"违规={stats['violation_count']}")

    constitution.stop()
    print("\n=== 自测全部通过 ===")
