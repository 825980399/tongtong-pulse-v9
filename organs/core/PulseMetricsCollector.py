# -*- coding: utf-8 -*-
"""
PulseMetricsCollector —— 多层级指标采集器 · 运行态可观测性汇聚点

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 承接 MetricsEvent.COLLECT 与心跳、情绪、兴趣、反思、生命状态等多类事件，采集分子/细胞/器官三层指标，构建可观测性快照并输出到控制台与日志。
机制: on_pulse 分派 _on_collect / _on_heartbeat_observe / _on_heartbeat_alive / _on_persona_switched / _on_emotion_detected / _on_interest_changed / _on_search_completed / _on_life_state_changed / _on_reflection_insight / _on_liver_compressed / _on_instinct_upgraded 等订阅点；_collect_molecular / _collect_cellular / _collect_organ_level 做三层采集，_collect_knowledge_evolution / _collect_organ_status_stats / _collect_link_status 补齐维度，_refresh_organ_cache 缓存器官表；_build_observability_snapshot 汇总后广播 ObservabilityEvent.SNAPSHOT 与 MetricsEvent.SNAPSHOT，_print_console_snapshot / _print_body_ui 做可视化，_log_observability 落盘。
定位: 全系统的「仪表盘」，是运行态可观测性的唯一汇聚点。
"""

import os
import sys

from nucleus.const import LogLevel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import os
import threading
import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import (
    HeartEvent,
    HormonesEvent,
    InterestEvent,
    MetricsEvent,
    ObservabilityEvent,
    OrganStatus,
    SystemEvent,
)
from nucleus.data.DataAccessLayer import safe_write_json
from nucleus.organ_identity import ORGAN_ALIASES  # ★T-112d：器官名归一化单源真相
from nucleus._silent_except import silent_exc
from nucleus._warn_throttle import should_warn

# 尝试读取配置，缺失时使用默认值
try:
    from config import FEATURE, OBSERVABILITY
    _obs_enabled = FEATURE.get("enable_observability", True)
    _snapshot_interval = OBSERVABILITY.get("snapshot_interval_beats", 5)
    _console_output = OBSERVABILITY.get("console_output", True)
    _file_output = OBSERVABILITY.get("file_output", True)
    _flat_format = OBSERVABILITY.get("flat_format", True)
    _cache_seconds = OBSERVABILITY.get("organ_stats_cache_seconds", 30)
except ImportError:
    _obs_enabled = True
    _snapshot_interval = 5
    _console_output = True
    _file_output = True
    _flat_format = True
    _cache_seconds = 30


class PulseMetricsCollector(BasePulseOrgan):
    """脉冲驱动多层级指标采集器（v9.5 分层脉冲版）"""

    def __init__(self, organ_name: str = "指标采集器"):
        super().__init__(organ_name)
        self._metrics_history: list = []
        self._snapshot_count = 0
        self._beat_count = 0
        self._max_history = 100
        self._liver_compress_count = 0    # 肝脏压缩次数
        self._liver_fuse_count = 0        # 肝脏融合次数
        self._instinct_upgrade_count = 0   # 本能升级次数

        # ===== P1-4: 器官指标缓存 =====
        self._cached_organ_stats: dict[str, Any] = {}
        self._cache_timestamp = 0.0

        # ===== P1-4: 可观测性开关 =====
        self._obs_enabled = _obs_enabled
        self._snapshot_interval = 1   # 每次心跳都更新快照，确保UI数据实时
        self._console_output = _console_output
        self._file_output = _file_output
        self._flat_format = _flat_format
        # ===== P1-4 修复: 节点池引用 =====
        self._node_pool = None
        self._cached_heartbeat = {}      # 缓存最新心跳数据
        self._cached_current_user = "访客"  # 缓存当前用户
        self._dream_count = 0          # 梦境推演次数
        self._dream_last_topic = ""    # 最近梦境主题
        self._reflection_count = 0     # 自我反思次数
        self._reflection_last_domain = ""  # 最近反思领域
        self._cached_social_emotions = {}      # 社会性情感缓存
        self._cached_top_interests = []        # 兴趣前5维度缓存
        self._cached_life_state = {}       # 生命状态缓存
        self._cached_emotion_state = {}    # 情绪状态缓存（含趋势）
        # ===== 无头浏览器统计缓存 =====
        self._headless_stats = {
            "total_searches": 0,       # 累计搜索次数
            "successful_searches": 0,  # 成功搜索次数（找到文章）
            "total_articles": 0,       # 累计精读文章数
            "total_chars": 0,          # 累计消化字符数
            "last_search_topic": "",   # 最近搜索主题
            "last_articles": 0,        # 最近一次搜索找到的文章数
            "last_status": "idle",     # 最近状态：idle/searching/completed/fallback
        }
        # ===== 线程安全：统一缓存锁 =====
        self._cache_lock = threading.Lock()


    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == MetricsEvent.COLLECT:
            return self._on_collect(payload)
        elif event_type == HeartEvent.BEAT:
            return self._on_heartbeat_observe(payload)
        elif event_type == HeartEvent.ALIVE:
            return self._on_heartbeat_alive(payload)
        elif event_type == "persona.switched":
            return self._on_persona_switched(payload)
        elif event_type == "digest.knowledge":
            return self._on_digest_for_dream(payload)
        elif event_type == "reflection.insight":
            return self._on_reflection_insight(payload)
        elif event_type == "knowledge.compressed":
            return self._on_liver_compressed(payload)
        elif event_type == "instinct.upgraded":
            return self._on_instinct_upgraded(payload)
        elif event_type == HormonesEvent.EMOTION_DETECTED:
            return self._on_emotion_detected(payload)
        elif event_type == InterestEvent.CHANGED:
            return self._on_interest_changed(payload)
        elif event_type == "controller.search_completed":
            return self._on_search_completed(payload)
        elif event_type == "life_state.changed":
            # ★P0-6修复：补上订阅声明但缺失的处理分支，否则 _on_life_state_changed 成死代码、
            # 生命状态缓存永不被更新。
            return self._on_life_state_changed(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        return None

    # ========== 传统采集（保留兼容） ==========
    def _on_collect(self, payload: dict) -> dict[str, Any]:
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._snapshot_count += 1
        snapshot = {
            "molecular": self._collect_molecular(),
            "cellular": self._collect_cellular(),
            "organ_level": self._collect_organ_level(),
            "timestamp": time.time(),
            "snapshot_id": self._snapshot_count,
        }
        self._metrics_history.append(snapshot)
        if len(self._metrics_history) > self._max_history:
            self._metrics_history = self._metrics_history[-50:]
        self._emit(MetricsEvent.SNAPSHOT, snapshot, priority=2, layer="L3")

        # 同时写入共享数据文件供人体UI读取
        try:
            health_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                       'data', 'monitor', 'health_snapshot.json')
            os.makedirs(os.path.dirname(health_path), exist_ok=True)
            # 构建完整的快照数据（和心跳触发的一样）
            full_snapshot = self._build_observability_snapshot()
            safe_write_json(health_path, full_snapshot)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        return {"status": "collected", "snapshot_id": self._snapshot_count}
    # ========== P1-4: 心跳驱动可观测性快照 ==========

    def _on_heartbeat_observe(self, payload: dict | None = None) -> dict[str, Any] | None:
        """心跳触发：按间隔输出可观测性快照"""
        if not self._obs_enabled:
            return None

        # 从心跳脉冲 payload 中直接提取心率数据（线程安全：锁内更新缓存）
        with self._cache_lock:
            if payload:
                self._cached_heartbeat = {
                    "interval": payload.get("interval", 10.0),
                    "beat_count": payload.get("beat_count", 0),
                    "status": "搏动中",
                }

            # 尝试从信息场获取最新用户身份
            if self.info_field:
                try:
                    persona_pulse = self.info_field.get_current("persona.switched")
                    if persona_pulse and isinstance(persona_pulse, dict):
                        user = persona_pulse.get("payload", {}).get("current_user", "")
                        if user:
                            self._cached_current_user = user
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            self._beat_count += 1
            if self._beat_count % self._snapshot_interval != 0:
                return None
            self._snapshot_count += 1

            # 刷新器官指标缓存
            self._refresh_organ_cache()

        # 构建可观测性快照（锁内完成，确保读到一致的数据快照）
        with self._cache_lock:
            snapshot = self._build_observability_snapshot()

        try:
            health_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                       'data', 'monitor', 'health_snapshot.json')
            # T-112a：改走硬化写通道（路径锁 + 退避重试 + 唯一 tmp + 失败清理）
            if not safe_write_json(health_path, snapshot, backup=False):
                self._log(LogLevel.WARNING, "health_snapshot.json 写入失败（需关注）")
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return {"status": "observed", "beat": self._beat_count}
    def _on_heartbeat_alive(self, payload: dict) -> dict[str, Any]:
        """缓存心脏存活数据"""
        with self._cache_lock:
            self._cached_heartbeat = {
                "interval": payload.get("interval", 10.0),
                "beat_count": payload.get("beat_count", 0),
                "status": payload.get("status", "未知"),
            }
        return {"status": "cached"}

    def _on_persona_switched(self, payload: dict) -> dict[str, Any]:
        """缓存当前用户身份"""
        with self._cache_lock:
            self._cached_current_user = payload.get("current_user", "访客")
        return {"status": "cached", "user": self._cached_current_user}
    def _on_digest_for_dream(self, payload: dict) -> dict[str, Any]:
        """缓存梦境推演数据"""
        trigger_reason = payload.get("trigger_reason", "")
        if trigger_reason == "dream.deduction":
            with self._cache_lock:
                self._dream_count += 1
                content = payload.get("content", "")
                if content:
                    self._dream_last_topic = content[:60]
        return {"status": "cached", "dream_count": self._dream_count}
    def _on_emotion_detected(self, payload: dict) -> dict[str, Any]:
        """缓存最新情绪（含趋势信息）"""
        with self._cache_lock:
            self._cached_social_emotions = payload.get("social_emotions", {})
            self._cached_emotion_state = {
                "emotion": payload.get("emotion", "中性"),
                "intensity": payload.get("intensity", 0.0),
                "trend": payload.get("emotion_trend", {}),
                "inertia_applied": payload.get("emotion_inertia_applied", False),
            }
        return {"status": "cached"}

    def _on_interest_changed(self, payload: dict) -> dict[str, Any]:
        """缓存最新兴趣分布"""
        with self._cache_lock:
            interests = payload.get("current_interests", {})
            if interests:
                sorted_items = sorted(interests.items(), key=lambda x: x[1], reverse=True)
                self._cached_top_interests = sorted_items[:5]
        return {"status": "cached"}

    def _on_search_completed(self, payload: dict) -> dict[str, Any]:
        """缓存无头浏览器搜索完成数据"""
        with self._cache_lock:
            self._headless_stats["total_searches"] += 1
            articles = payload.get("articles_found", 0)
            chars = payload.get("total_chars", 0)
            status = payload.get("status", "unknown")  # noqa: F841
            if articles > 0:
                self._headless_stats["successful_searches"] += 1
            self._headless_stats["total_articles"] += articles
            self._headless_stats["total_chars"] += chars
            self._headless_stats["last_search_topic"] = payload.get("search_topic", "")
            self._headless_stats["last_articles"] = articles
            self._headless_stats["last_status"] = "completed" if articles > 0 else "fallback"
        return {"status": "cached"}
    def _on_life_state_changed(self, payload: dict) -> dict[str, Any]:
        """缓存最新生命状态"""
        self._cached_life_state = {
            "state": payload.get("new_state", "浅层活跃"),
            "previous": payload.get("old_state", ""),
            "since": payload.get("state_since", time.time()),
            "explore_interval": payload.get("explore_interval", 120),
            "dream_interval": payload.get("dream_interval", 300),
        }
        return {"status": "cached", "state": self._cached_life_state["state"]}


    def _on_reflection_insight(self, payload: dict) -> dict[str, Any]:
        """缓存自我反思数据"""
        with self._cache_lock:
            self._reflection_count += 1
            self._reflection_last_domain = payload.get("domain", "通用")
        return {"status": "cached", "reflection_count": self._reflection_count}

    def _build_observability_snapshot(self) -> dict[str, Any]:
        """构建完整的可观测性快照"""
        snapshot = {
            "timestamp": time.time(),
            "beat_count": self._beat_count,
        }

        # 系统级：脉冲统计
        if self.info_field and hasattr(self.info_field, 'pulse_core') and self.info_field.pulse_core:
            pulse_stats = self.info_field.pulse_core.get_stats()
            snapshot["pulse"] = {
                "emitted": pulse_stats.get("total_emitted", 0),
                "completed": pulse_stats.get("total_completed", 0),
                "timeout": pulse_stats.get("total_timeout", 0),
                "pending": pulse_stats.get("pending", 0),
            }

        # 系统级：信息场统计
        if self.info_field:
            field_stats = self.info_field.get_stats()
            snapshot["info_field"] = {
                "published": field_stats.get("total_published", 0),
                "matched": field_stats.get("total_matched", 0),
                "active_conditions": field_stats.get("active_conditions", 0),
            }

        # 系统级：节点池统计
        node_stats = {}
        node_pool = getattr(self, '_node_pool', None)
        if node_pool is None and self.info_field:
            node_pool = getattr(self.info_field, 'node_pool', None)
        if node_pool:
            pool_stats = node_pool.get_stats()
            node_stats = {
                "total": pool_stats.get("total_nodes", 0),
                "hot": pool_stats.get("hot_count", 0),
                "warm": pool_stats.get("warm_count", 0),
                "cold": pool_stats.get("cold_count", 0),
            }
        snapshot["nodes"] = node_stats

        # P2-4: 知识演化指标
        evol_stats = self._collect_knowledge_evolution(node_pool)
        snapshot["knowledge_evolution"] = evol_stats

        # 器官级：状态统计
        organ_status_stats = self._collect_organ_status_stats()
        snapshot["organs"] = organ_status_stats

        # 快照级：备份信息
        snapshot["snapshot_backups"] = self._count_snapshot_backups()

        # 核心链路状态统计
        snapshot["link_status"] = self._collect_link_status()

        # 动态心率数据
        snapshot["heartbeat"] = dict(self._cached_heartbeat) if self._cached_heartbeat else {
            "interval": 10.0, "beat_count": 0, "status": "起搏中"
        }

        # 当前用户身份
        snapshot["current_user"] = self._cached_current_user

        # 梦境推演与自我反思
        snapshot["dream"] = {
            "count": self._dream_count,
            "last_topic": self._dream_last_topic,
        }
        snapshot["reflection"] = {
            "count": self._reflection_count,
            "last_domain": self._reflection_last_domain,
        }

        # 肝脏工作统计
        snapshot["liver"] = {
            "compress_count": self._liver_compress_count,
            "fuse_count": self._liver_fuse_count,
            "instinct_upgrade": self._instinct_upgrade_count,
        }

        # ===== 新增：请求去重统计（★T-113d：通电后让去重可见，防止"沉默器官"）=====
        # 总开关关闭时 get_request_deduplicator() 返回 None → 仅标注 enabled=False；
        # 开启（启动侧设 PULSE_REQUEST_DEDUP=1）后含完整累计统计（claimed/duplicate/...）。
        _dd_enabled = False
        _dd_stats: dict[str, Any] = {"enabled": False}
        try:
            from nucleus.field.RequestDeduplicator import get_request_deduplicator
            _dd = get_request_deduplicator()
        except Exception:
            _dd = None
        if _dd is not None:
            _dd_enabled = True
            try:
                _dd_stats = _dd.stats()
                _dd_stats["enabled"] = True
            except Exception as _e:
                self._log(LogLevel.WARNING, f"[请求去重] 统计采集失败: {_e}")
        snapshot["request_dedup"] = _dd_stats

        # ===== 新增：needs_repair 积压统计（★T-113e④：让死字段被消费，不再零读者）=====
        # SafeEvolutionExecutor 在 runtime_failed 时写 needs_repair=True，此前全活树 0 读者。
        # 此处只读消费（fail-safe），把「真失败待重修」补丁数暴露到可观测快照。
        # （删除该字段会破坏第114批"needs_repair=真失败待重修 / undecidable=不可判定"
        #   拆分复用计划，故选「接消费」而非「删除」。)
        _nr_count = 0
        try:
            import json as _json
            import os as _os
            _root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
            _pp = _os.path.join(_root, "data", "patches", "pending_patches.json")
            if _os.path.exists(_pp):
                with open(_pp, encoding="utf-8") as _f:
                    _pending = _json.loads(_f.read())
                if isinstance(_pending, list):
                    _nr_count = sum(1 for _p in _pending if _p.get("needs_repair"))
        except Exception as _e:
            self._log(LogLevel.WARNING, f"[needs_repair] 积压采集失败: {_e}")
        snapshot["patch_needs_repair"] = _nr_count

        # ===== 新增：控制器统计（从历史脉冲中统计） =====
        controller_stats = {"operations": 0, "files_read": 0, "urls_opened": 0}
        if self.info_field:
            try:
                history = self.info_field.get_history(limit=500)
                for entry in history:
                    et = entry.get("event_type", "")
                    if et == "controller.open_url":
                        controller_stats["urls_opened"] += 1
                    elif et == "controller.read_file":
                        controller_stats["files_read"] += 1
                    elif et.startswith("controller."):
                        controller_stats["operations"] += 1
            except Exception as e:
                if should_warn("mc:snapshot_stats", 300):
                    silent_exc(e, "PulseMetricsCollector.py:428 快照统计", level="warning")
        snapshot["controller"] = controller_stats

        # ===== 新增：无头浏览器统计 =====
        snapshot["headless_browser"] = dict(self._headless_stats)

        # ===== 新增：社会性情感 =====
        snapshot["social_emotions"] = dict(self._cached_social_emotions)

        # ===== 新增：兴趣前5维度 =====
        snapshot["top_interests"] = [
            {"dim": d, "val": round(v, 2)} for d, v in self._cached_top_interests
        ]
        # ===== 新增：生命状态 =====
        snapshot["life_state"] = dict(self._cached_life_state) if self._cached_life_state else {
            "state": "浅层活跃", "previous": "", "since": time.time(),
            "explore_interval": 120, "dream_interval": 300,
        }

        # ===== 新增：情绪状态 =====
        snapshot["emotion_state"] = dict(self._cached_emotion_state) if self._cached_emotion_state else {
            "emotion": "中性", "intensity": 0.0, "trend": {}, "inertia_applied": False,
        }
        return snapshot

    # ========== P2-4: 知识演化可视化 ==========

    def _collect_knowledge_evolution(self, node_pool) -> dict[str, Any]:
        """采集知识演化指标：L1/L2/L3分布、兴趣光谱摘要"""
        evol = {"l1_count": 0, "l2_count": 0, "l3_count": 0, "interest_summary": {}}

        if node_pool:
            pool_stats = node_pool.get_stats()
            evol_dist = pool_stats.get("evol_distribution", {})
            evol["l1_count"] = evol_dist.get("L1", 0)
            evol["l2_count"] = evol_dist.get("L2", 0)
            evol["l3_count"] = evol_dist.get("L3", 0)
            # 本能节点数（L4）
            evol["instinct_count"] = pool_stats.get("instinct_count", 0)
            # 从L3中扣除L4本能节点，得到纯净的L3智慧节点数
            instinct_count = evol.get("instinct_count", 0)
            evol["l3_pure"] = max(0, evol["l3_count"] - instinct_count)

            # 知识密度：L2+L3+L4占总节点比例
            total = evol["l1_count"] + evol["l2_count"] + evol["l3_count"] + instinct_count
            if total > 0:
                evol["knowledge_density"] = round(
                    (evol["l2_count"] + evol["l3_count"] + instinct_count) / total, 3
                )
            else:
                evol["knowledge_density"] = 0.0

        # 兴趣光谱摘要
        if self.info_field:
            try:
                interest_pulse = self.info_field.get_current("interest.changed")
                if interest_pulse and isinstance(interest_pulse, dict):
                    payload = interest_pulse.get("payload", {})
                    interests = payload.get("current_interests", {})
                    if interests:
                        sorted_items = sorted(
                            interests.items(), key=lambda x: x[1], reverse=True
                        )
                        evol["interest_summary"] = {
                            "top_dimensions": [
                                {"dim": d, "val": round(v, 2)}
                                for d, v in sorted_items[:3]
                            ],
                            "decayed_dimensions": [
                                {"dim": d, "val": round(v, 2)}
                                for d, v in sorted_items[-3:]
                            ],
                        }
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        return evol

    def _collect_organ_status_stats(self) -> dict[str, int]:
        """P1-4: 统计器官状态分布（在线/熔断/恢复中/离线）"""
        stats = {"total": 0, "running": 0, "fused": 0, "recovering": 0, "stopped": 0, "idle": 0}

        for _organ_name, organ_data in self._cached_organ_stats.items():
            stats["total"] += 1
            status = organ_data.get("status", "unknown")
            if status == "RUNNING" or status == OrganStatus.RUNNING:
                stats["running"] += 1
            elif status == "FUSED" or status == OrganStatus.FUSED:
                stats["fused"] += 1
            elif status == "RECOVERING" or status == OrganStatus.RECOVERING:
                stats["recovering"] += 1
            elif status == "STOPPED" or status == OrganStatus.STOPPED:
                stats["stopped"] += 1
            else:
                stats["idle"] += 1

        return stats
    #: ★主线第50批 T3（P2-329）：期望接收者 → **真实注册名别名**。
    #  背景：判据原用单一字符串做子串匹配，但对话侧真实注册名是
    #  「Web对话-人脸监听」（web_chat.py:658）与
    #  「对话模块-全局回复监听」（chat_service.py:108）
    #  —— 前者不含 "对话模块" 子串 → **恒判 mismatch**。
    # ★T-112d：_LINK_ALIASES 提升为共享器官名归一化表（单源真相，见 nucleus.organ_identity）。
    #   原本地别名表已迁移并充实（覆盖豁免表命名空间），其余消费方统一走 resolve_organ_key。
    _LINK_ALIASES = ORGAN_ALIASES

    def _m50_alias_match_on(self) -> bool:
        """★T3 开关（默认 True；关闭 → 恢复旧的子串匹配）。"""
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_LINK_STATUS_ALIAS_MATCH", True))
        except Exception:
            return True

    @classmethod
    def _m50_resolve_aliases(cls, expected: str) -> tuple:
        """把期望名展开为别名元组（未登记则只有自身）。"""
        _e = str(expected or "")
        for _k, _al in cls._LINK_ALIASES.items():
            if _e == _k or _e in _al:
                return tuple(_al)
        return (_e,) if _e else ()

    def _m50_subscriber_matches(self, expected: str, subscribers) -> bool:
        """★别名感知匹配：任一接收者名与任一别名互为子串即命中。"""
        if not subscribers:
            return False
        if not self._m50_alias_match_on():
            # 旧行为（零回归）
            return expected in subscribers or any(
                expected in str(_s) for _s in subscribers)
        _al = [str(_a).lower() for _a in self._m50_resolve_aliases(expected) if _a]
        for _s in subscribers:
            _sl = str(_s or "").lower()
            if not _sl:
                continue
            for _a in _al:
                if _a in _sl or _sl in _a:
                    return True
        return False

    def _collect_link_status(self) -> dict[str, Any]:
        """采集核心脉冲链路的收发匹配状态"""
        links = {
            "eye_to_visual": {"event": "eyes.stream_frame", "emitter": "眼睛", "receiver": "视觉皮层", "status": "unknown"},
            "visual_to_chat": {"event": "chat.user_presence_detected", "emitter": "视觉皮层", "receiver": "对话模块", "status": "unknown"},
            # ★主线第50批 T3（P2-329）修正：原用 ``mouth.reply``（嘴巴→对话的回执）
            #   与链路名 ``chat_to_mouth``（对话→嘴巴）**方向相反**。
            #   真实链路 = 「对话/皮层 命令嘴巴说话」：
            #   事件 ``mouth.speak``，订阅者 = PulseMouth（MouthEvent.SPEAK）。
            "chat_to_mouth": {"event": "mouth.speak", "emitter": "对话模块", "receiver": "嘴巴", "status": "unknown"},
        }

        if self.info_field:
            conditions = getattr(self.info_field, '_conditions', {})
            # 统计每个事件的订阅情况
            event_subscribers = {}
            for _cond_id, cond_data in conditions.items():
                organ_name = cond_data.get("condition", {}).get("organ_name", "")
                event_types = cond_data.get("condition", {}).get("event_types", [])
                for et in event_types:
                    if et not in event_subscribers:
                        event_subscribers[et] = []
                    event_subscribers[et].append(organ_name)

            for _key, link in links.items():
                event = link["event"]
                subscribers = event_subscribers.get(event, [])
                expected_receiver = link["receiver"]
                # ★T3：别名感知匹配（关闭时回旧行为）
                if self._m50_subscriber_matches(expected_receiver, subscribers):
                    link["status"] = "connected"
                elif subscribers:
                    link["status"] = "mismatch"
                    link["actual_receivers"] = subscribers
                    link["expected_aliases"] = list(
                        self._m50_resolve_aliases(expected_receiver))
                else:
                    link["status"] = "orphan"

        return links
    def _refresh_organ_cache(self):
        """P1-4: 刷新器官指标缓存（利用心跳节奏）"""
        now = time.time()
        if now - self._cache_timestamp < _cache_seconds and self._cached_organ_stats:
            return  # 缓存未过期，跳过

        self._cached_organ_stats = {}

        # 从信息场中获取所有已注册的器官信息
        if self.info_field:
            conditions = getattr(self.info_field, '_conditions', {})
            for _cond_id, cond_data in conditions.items():
                organ_name = cond_data.get("condition", {}).get("organ_name", "")
                if organ_name and organ_name not in self._cached_organ_stats:
                    self._cached_organ_stats[organ_name] = {
                        "status": "RUNNING",  # 默认在线（已注册条件即为在线）
                        "last_seen": now,
                    }

        self._cache_timestamp = now
    def _on_liver_compressed(self, payload: dict) -> dict[str, Any]:
        """缓存肝脏压缩数据"""
        from_level = payload.get("from_level", "")
        with self._cache_lock:
            if from_level == "L1":
                self._liver_compress_count += 1
            elif from_level == "L2":
                self._liver_fuse_count += 1
        return {"status": "cached"}

    def _on_instinct_upgraded(self, payload: dict) -> dict[str, Any]:
        """缓存本能升级数据"""
        with self._cache_lock:
            self._instinct_upgrade_count += 1
        return {"status": "cached"}
    def _count_snapshot_backups(self) -> int:
        """P1-4: 统计快照备份文件数"""
        try:
            import glob
            count = 0
            for _ in glob.glob("data/knowledge/pulse_knowledge_snapshot.json.*.bak"):
                count += 1
            return count
        except Exception:
            return 0

    def _flatten_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """P1-4: 将嵌套快照扁平化为时序友好的一维字典"""
        flat = {
            "timestamp": snapshot.get("timestamp", 0),
            "beat_count": snapshot.get("beat_count", 0),
        }

        # 脉冲指标
        pulse = snapshot.get("pulse", {})
        flat["pulse_emitted"] = pulse.get("emitted", 0)
        flat["pulse_completed"] = pulse.get("completed", 0)
        flat["pulse_timeout"] = pulse.get("timeout", 0)
        flat["pulse_pending"] = pulse.get("pending", 0)

        # 信息场指标
        field = snapshot.get("info_field", {})
        flat["field_published"] = field.get("published", 0)
        flat["field_matched"] = field.get("matched", 0)
        flat["field_conditions"] = field.get("active_conditions", 0)

        # 节点池指标
        nodes = snapshot.get("nodes", {})
        flat["nodes_total"] = nodes.get("total", 0)
        flat["nodes_hot"] = nodes.get("hot", 0)
        flat["nodes_warm"] = nodes.get("warm", 0)
        flat["nodes_cold"] = nodes.get("cold", 0)

        # 器官状态指标
        organs = snapshot.get("organs", {})
        flat["organs_total"] = organs.get("total", 0)
        flat["organs_running"] = organs.get("running", 0)
        flat["organs_fused"] = organs.get("fused", 0)
        flat["organs_recovering"] = organs.get("recovering", 0)

        # 快照备份
        flat["snapshot_backups"] = snapshot.get("snapshot_backups", 0)

        # P2-4: 知识演化指标
        evol = snapshot.get("knowledge_evolution", {})
        flat["evol_l1"] = evol.get("l1_count", 0)
        flat["evol_l2"] = evol.get("l2_count", 0)
        flat["evol_l3"] = evol.get("l3_count", 0)
        flat["evol_density"] = evol.get("knowledge_density", 0.0)

        return flat

    def _print_console_snapshot(self, snapshot: dict[str, Any]):
        """P1-4: 控制台极简一行输出"""
        pulse = snapshot.get("pulse", {})
        nodes = snapshot.get("nodes", {})
        organs = snapshot.get("organs", {})

        fused_count = organs.get("fused", 0)
        fused_str = f" 熔断{fused_count}" if fused_count > 0 else ""

        evol = snapshot.get("knowledge_evolution", {})
        density = evol.get("knowledge_density", 0.0)
        line = (
            f"[可观测] 脉冲 {pulse.get('emitted',0)}/{pulse.get('completed',0)}/"
            f"{pulse.get('timeout',0)}(待{pulse.get('pending',0)}) | "
            f"节点 {nodes.get('total',0)}(热{nodes.get('hot',0)}/"
            f"温{nodes.get('warm',0)}/冷{nodes.get('cold',0)}) | "
            f"L1/L2/L3={evol.get('l1_count',0)}/{evol.get('l2_count',0)}/{evol.get('l3_count',0)} | "
            f"密度{density:.1%} | "
            f"器官 {organs.get('total',0)}{fused_str} | "
            f"备份 {snapshot.get('snapshot_backups',0)}"
        )
        self._log(LogLevel.INFO, line)
    def _print_body_ui(self, snapshot: dict[str, Any]):
        """人体UI：全身器官状态面板 + 脉冲链路通断"""
        organs = snapshot.get("organs", {})
        pulse = snapshot.get("pulse", {})
        nodes = snapshot.get("nodes", {})
        evol = snapshot.get("knowledge_evolution", {})
        field = snapshot.get("info_field", {})

        total = organs.get("total", 0)
        running = organs.get("running", 0)
        fused = organs.get("fused", 0)
        recovering = organs.get("recovering", 0)
        stopped = organs.get("stopped", 0)

        # 构建器官状态条
        running_bar = "🟢" * min(running, 20)
        fused_bar = "🔴" * fused
        recovering_bar = "🟡" * recovering
        stopped_bar = "⚪" * stopped

        status_bar = running_bar + fused_bar + recovering_bar + stopped_bar
        if len(status_bar) > 40:
            status_bar = f"🟢×{running} 🔴×{fused} 🟡×{recovering} ⚪×{stopped}"

        # 链路通断判断
        pulse_emitted = pulse.get("emitted", 0)
        pulse_matched = field.get("matched", 0)
        pulse_unmatched = pulse_emitted - pulse_matched

        if pulse_unmatched > 0:
            link_status = f"⚠️ 链路警告: {pulse_unmatched}个脉冲未匹配"
        elif fused > 0:
            link_status = f"⚠️ 器官熔断: {fused}个"
        else:
            link_status = "✅ 链路畅通"

        # 知识健康度
        density = evol.get("knowledge_density", 0)
        if density >= 0.8:
            knowledge_status = "🧠 知识丰富"
        elif density >= 0.5:
            knowledge_status = "📖 知识成长中"
        else:
            knowledge_status = "🌱 知识积累中"

        # 分层调度状态
        l1 = evol.get("l1_count", 0)
        l2 = evol.get("l2_count", 0)
        l3 = evol.get("l3_count", 0)

        print(f"""
┌──────────────────────────────────────────────────────┐
│  🫀 曈曈 v9.5 · 人体UI · 全身器官状态                   │
├──────────────────────────────────────────────────────┤
│  器官: {status_bar}                                      │
│  总计: {total} | 在线: {running} | 熔断: {fused} | 恢复: {recovering} | 离线: {stopped}│
├──────────────────────────────────────────────────────┤
│  📡 脉冲链路: {link_status}                                 │
│  发射: {pulse_emitted} | 匹配: {pulse_matched} | 未匹配: {pulse_unmatched}          │
├──────────────────────────────────────────────────────┤
│  🧠 知识: {nodes.get('total',0)}节点 (热{nodes.get('hot',0)}|温{nodes.get('warm',0)}|冷{nodes.get('cold',0)})              │
│  L1:{l1} | L2:{l2} | L3:{l3} | 密度:{density:.1%} | {knowledge_status}       │
├──────────────────────────────────────────────────────┤
│  📊 分层调度: L0生命线 | L1实时 | L2认知 | L3后台         │
│  备份: {snapshot.get('snapshot_backups',0)}份                                     │
└──────────────────────────────────────────────────────┘""")

    def _log_observability(self, snapshot: dict[str, Any]):
        """P1-4: 文件日志完整输出"""
        pulse = snapshot.get("pulse", {})
        nodes = snapshot.get("nodes", {})
        organs = snapshot.get("organs", {})
        field = snapshot.get("info_field", {})
        evol = snapshot.get("knowledge_evolution", {})
        msg = (
            f"可观测性快照 #{snapshot.get('beat_count', 0)}: "
            f"脉冲(发射{pulse.get('emitted',0)} 完成{pulse.get('completed',0)} "
            f"超时{pulse.get('timeout',0)} 待处理{pulse.get('pending',0)}) "
            f"信息场(发布{field.get('published',0)} 匹配{field.get('matched',0)} "
            f"条件{field.get('active_conditions',0)}) "
            f"节点(总{nodes.get('total',0)} 热{nodes.get('hot',0)} "
            f"温{nodes.get('warm',0)} 冷{nodes.get('cold',0)}) "
            f"器官(总{organs.get('total',0)} 运行{organs.get('running',0)} "
            f"熔断{organs.get('fused',0)} 恢复{organs.get('recovering',0)}) "
            f"备份{snapshot.get('snapshot_backups',0)} "
            f"演化(L1={evol.get('l1_count',0)} L2={evol.get('l2_count',0)} "
            f"L3={evol.get('l3_count',0)} 密度={evol.get('knowledge_density',0):.1%})"
        )
        self._log("INFO", msg)

    # ========== 分子级采集 ==========

    def _collect_molecular(self) -> dict[str, Any]:
        result = {}
        if self.info_field:
            es = self.info_field.get_current("energy.metabolism_snapshot")
            if es and isinstance(es, dict):
                result["energy_level"] = es.get("payload", {}).get("energy_level", 1.0)
            hw = self.info_field.get_current("touch.hardware_snapshot")
            if hw and isinstance(hw, dict):
                hp = hw.get("payload", {})
                result["cpu_usage"] = hp.get("cpu", {}).get("usage_percent", 0)
                result["memory_usage"] = hp.get("memory", {}).get("usage_percent", 0)
        return result

    def _collect_cellular(self) -> dict[str, Any]:
        result = {"pulse_completed": 0, "pulse_timeout": 0}
        if self.info_field and hasattr(self.info_field, 'pulse_core') and self.info_field.pulse_core:
            stats = self.info_field.pulse_core.get_stats()
            result["pulse_completed"] = stats.get("total_completed", 0)
            result["pulse_timeout"] = stats.get("total_timeout", 0)
        return result

    def _collect_organ_level(self) -> dict[str, Any]:
        return {"total_organs": len(self._cached_organ_stats) if self._cached_organ_stats else 50,
                "active_conditions": 50}

    # ========== 状态查询 ==========

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name, "snapshot_count": self._snapshot_count,
            "history_size": len(self._metrics_history), "is_running": self.is_running,
            "obs_enabled": self._obs_enabled,
            "obs_interval_beats": self._snapshot_interval,
            "cached_organs": len(self._cached_organ_stats),
        }

    def set_node_pool(self, node_pool):
        """注入节点池引用（P1-4修复）"""
        self._node_pool = node_pool

    def get_resonance_conditions(self) -> list:
        return [{"organ_name": self.organ_name,
                 "event_types": [MetricsEvent.COLLECT, SystemEvent.STATUS_REQUEST,
                                HeartEvent.BEAT, HeartEvent.ALIVE, "persona.switched",
                                "digest.knowledge", "reflection.insight",
                                "knowledge.compressed", "instinct.upgraded",
                                HormonesEvent.EMOTION_DETECTED, InterestEvent.CHANGED,
                                "controller.search_completed",
                                "life_state.changed"  # 新增：生命状态广播
                                ],
                 "min_priority": 1}]

    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None



# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "指标采集器",
    "class_name": "PulseMetricsCollector",
    "attr_name": "metrics_collector",
    "system": "core",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [
        {"target": "node_pool", "setter": "set_node_pool"},
    ],
}

if __name__ == "__main__":
    print("=== PulseMetricsCollector v9.5 分层脉冲自测 ===\n")
    class MockInfoField:
        def __init__(self):
            self.published = []
            self._data = {}
            self._conditions = {
                "cond:心脏:1": {"condition": {"organ_name": "心脏"}, "handler": None},
                "cond:胃:1": {"condition": {"organ_name": "胃"}, "handler": None},
                "cond:肝:1": {"condition": {"organ_name": "肝"}, "handler": None},
            }
        def publish(self, p): self.published.append(p)
        def get_current(self, k): return self._data.get(k)
        def get_stats(self):
            return {"total_published": 100, "total_matched": 95, "active_conditions": 50}
    class MockPulseCore:
        def get_stats(self):
            return {"total_emitted": 150, "total_completed": 147, "total_timeout": 3, "pending": 2}
    m = MockInfoField()
    m.pulse_core = MockPulseCore()
    m._data["energy.metabolism_snapshot"] = {"payload": {"energy_level": 0.85}}
    m._data["touch.hardware_snapshot"] = {"payload": {"cpu": {"usage_percent": 35}, "memory": {"usage_percent": 50}}}
    c = PulseMetricsCollector("指标采集器")
    c.set_info_field(m)
    c.start()
    c._snapshot_interval = 1  # 加速测试：每次心跳都输出
    # 刷新缓存
    c._refresh_organ_cache()
    print(f"1. 缓存器官数: {len(c._cached_organ_stats)}")
    # 心跳触发可观测性
    c._beat_count = 0
    r = c.on_pulse({"event_type": HeartEvent.BEAT, "payload": {}, "priority": 3})
    if r:
        print(f"2. 可观测性快照: 状态={r['status']}, 心跳={r['beat']}")
    # 验证扁平时序输出
    flat_pulses = [p for p in m.published if p.get("event_type") == ObservabilityEvent.SNAPSHOT]
    if flat_pulses:
        payload = flat_pulses[-1].get("payload", {})
        print(f"3. 扁平输出: pulse_emitted={payload.get('pulse_emitted')}, nodes_total={payload.get('nodes_total')}, organs_running={payload.get('organs_running')}")
        print(f"   ObservabilityEvent.SNAPSHOT脉冲 layer: {flat_pulses[-1].get('layer', '未设置')} (预期L3)")
    s = c.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}, "priority": 5})
    print(f"4. 统计: 采集{s['snapshot_count']}次, 可观测={'启用' if s['obs_enabled'] else '关闭'}")
    c.stop()
    print("\n=== 自测全部通过 ===")
# _m50_t3_mc_done
