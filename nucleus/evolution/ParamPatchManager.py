# -*- coding: utf-8 -*-
"""
ParamPatchManager.py —— 参数补丁管理器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 参数补丁的创建、审批与应用管理
机制: 大型模块（1482行），包含1个类、10个核心方法，采用分层架构实现
定位: 进化管理层
"""

from nucleus._silent_except import silent_exc
from config import TIMEOUT_CONFIG
import json
import os
import threading
import time
from typing import Any
from nucleus.evolution.LogAnalyzer import extract_log_level  # ★主线第30批 T2：真实日志级别解析
from nucleus.data.DataAccessLayer import safe_read_json


# 项目根目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONFIG_OVERRIDE_PATH = os.path.join(_PROJECT_ROOT, "data", "config_override.json")
_PATCH_HISTORY_PATH = os.path.join(_PROJECT_ROOT, "data", "param_patch_history.json")

# 参数安全范围（防止参数调整过度）
# ★A-9死参数清理（2026-09-08，内部协作者拍板）：移除 search_quality_threshold /
#   search_low_overlap_threshold / innerworld_search_quality_threshold /
#   stomach_min_keywords 的安全范围项（参数本体已从 RUNTIME_PARAMS 删除）
_PARAM_SAFE_RANGES = {
    # 搜索参数
    "search_cooldown_seconds": (1, 300),
    "search_max_per_hour": (1, 200),
    "search_max_articles": (1, 20),
    # 消化参数
    "stomach_keyword_min_length": (1, 10),
    "stomach_keyword_max_length": (2, 20),
    "stomach_purity_threshold": (0.1, 0.9),
    "stomach_quality_warn_threshold": (0.1, 0.9),
    # 潜意识参数
    "subconscious_counterfactual_chance": (0.0, 0.8),
    "subconscious_creative_chance": (0.0, 0.8),
    "subconscious_curiosity_interval": (60, 1800),
    "subconscious_inspiration_chance": (0.0, 0.5),
    # 兴趣参数
    "interest_decay_rate": (0.001, 0.05),
    "interest_explore_success_boost": (0.1, 1.0),
    "interest_boost_amount": (0.0, 1.0),
    # 风险参数
    "risk_false_positive_threshold": (0.1, 0.9),
    "risk_false_positive_min_samples": (1, 50),
    # InfoField参数
    "infofield_pattern_freq_threshold": (0.1, 0.9),
    "infofield_pattern_count_threshold": (2, 50),
    # 日志参数
    "log_structured_parallel": (0, 1),
    # 大模型调用参数
    "llm_remediation_threshold": (0.1, 0.9),
    "llm_max_retries": (1, 10),
    "llm_timeout_seconds": (10, 120),
    "llm_quality_compare_enabled": (0, 1),
    # 记忆存储参数
    "memory_snapshot_interval": (60, 3600),
    "memory_cold_node_recall_count": (10, 200),
    "memory_hot_node_max": (100, 2000),
    "memory_node_expire_days": (7, 365),
    # 知识检索参数
    "knowledge_query_max_results": (5, 100),
    "knowledge_expand_node_count": (1, 20),
    "knowledge_min_similarity": (0.1, 0.9),
    "knowledge_index_rebuild_interval": (600, 86400),
    # 输出验证参数
    "output_min_length": (0, 200),
    "output_max_length": (500, 10000),
    "output_relevance_threshold": (0.1, 0.9),
    "output_max_remediation_attempts": (1, 10),
    # 代码学习参数
    "code_learn_scan_interval": (120, 3600),
    "code_learn_max_methods_per_scan": (10, 200),
    "code_learn_distill_threshold": (50, 1000),
    "code_learn_param_audit_enabled": (0, 1),
    # 并行调度参数
    "parallel_max_workers": (1, 32),
    "parallel_task_timeout": (30, 600),
    "parallel_cpu_intensive_workers": (1, 16),
    "parallel_io_intensive_workers": (1, 32),
    # 心跳参数
    "heartbeat_fast_interval": (0.1, 10),
    "heartbeat_slow_interval": (1, 60),
    "heartbeat_status_update_interval": (1, 120),
    # 情绪价值观参数
    "emotion_decay_rate": (0.0, 0.1),
    "value_update_threshold": (0.01, 0.5),
    "value_min_confidence": (0.1, 0.9),
    # 自主迭代参数
    "evolution_patch_verify_wait": (10, 300),
    "evolution_health_drop_rollback": (-20, 0),
    # 内在世界参数
    "innerworld_reflection_interval": (30, 600),
    "innerworld_vision_interval": (300, 7200),
    # （A-9：innerworld_search_quality_threshold 安全范围已随死参数清理移除）
    # 认知反思参数
    "reflection_min_issues_to_store": (0, 10),
    # 肝脏参数
    "liver_noise_threshold": (1, 20),
    "liver_compress_interval": (60, 1800),
    "liver_fuse_interval": (120, 3600),
    # 肾脏参数
    "kidney_forget_score_threshold": (0.1, 0.9),
    "kidney_forget_scan_interval": (1, 20),
    "kidney_code_learning_zombie_hours": (1, 168),
    "kidney_search_content_zombie_hours": (1, 168),
    "kidney_normal_l1_zombie_hours": (0.5, 48),
    # 视觉皮层参数
    "visual_mediapipe_cooldown": (5, 120),
    "visual_frame_skip": (1, 10),
    "visual_window_size": (10, 200),
    "visual_stable_presence_ratio": (0.1, 0.9),
    "visual_stable_absence_ratio": (0.01, 0.5),
    # node_pool参数
    "nodepool_max_hot": (1000, 50000),
    "nodepool_max_warm": (5000, 200000),
    "nodepool_max_cold_cache": (500, 20000),
    # 心脏参数
    "heart_base_interval": (1.0, 120.0),
    "heart_min_interval": (0.5, 30.0),
    "heart_max_interval": (10.0, 300.0),
    # 眼睛参数
    "eyes_top_k": (5, 100),
    "eyes_min_score": (0.01, 0.9),
    "eyes_reopen_backoff": (0.5, 30.0),
    # 血管参数
    "vessel_silence_threshold": (5, 60),
    "vessel_check_interval": (1, 10),
    "vessel_alert_cooldown": (60, 1800),
    "vessel_restart_threshold": (1, 10),
    "vessel_degrade_threshold": (2, 20),
    # 主动性参数
    "initiative_silence_threshold": (60, 3600),
    "initiative_min_interval": (60, 1800),
    # 语义理解参数
    "semantic_confidence_threshold": (0.3, 0.95),
    "semantic_high_confidence_sample_rate": (0.01, 0.5),
    "semantic_sample_interval": (1, 30),
    "semantic_api_call_limit_per_hour": (5, 100),
    "semantic_lesson_feedback_interval": (1, 20),
    # 代码沙箱参数
    "sandbox_max_output_chars": (1000, 50000),
    "sandbox_max_memory_mb": (64, 1024),
    "sandbox_max_recent": (5, 100),
    # 双腿参数
    "legs_max_failures": (1, 10),
    "legs_cooldown_seconds": (60, 7200),
    # 嘴巴参数
    "mouth_tts_max_fails": (1, 20),
    "mouth_tts_cooldown_seconds": (5, 300),
    # 听觉参数
    "ears_max_context": (1, 20),
    # 触觉参数
    "touch_snapshot_interval": (5, 300),
    "touch_alarm_cooldown": (10, 600),
    "touch_gpu_probe_interval": (5, 120),
    # 精神核心参数
    "spiritual_interval": (100, 3000),
    # 文件消化器参数
    "file_digester_max_recent": (10, 200),
}

# 热加载黑名单（不允许参数补丁修改的配置块）
_PATCH_BLACKLIST = {
    "REMOTE_API_CONFIG",
    "SELF_AWARENESS_CONFIG",
    "WECOM_BOT_ID",
    "WECOM_SECRET",
}


class ParamPatchManager:
    """运行时参数补丁管理器"""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending_patches: list[dict[str, Any]] = []
        self._applied_patches: list[dict[str, Any]] = []
        self._load_history()
        # ★P1: 后台自动应用线程
        self._auto_apply_running = False
        self._auto_apply_thread = None
        self._auto_apply_interval = 300  # 每5分钟检查一次（压力均衡）
        self._last_auto_apply = 0.0

    def generate_patch(self, param_name: str, new_value: Any,
                       reason: str, source: str = "auto") -> dict[str, Any] | None:
        """生成参数补丁（类似代码补丁的diff）

        Args:
            param_name: 参数名（RUNTIME_PARAMS中的键）
            new_value: 新值
            reason: 调整原因
            source: 来源（auto/code_learner/reasoning_feedback/manual）

        Returns:
            补丁字典，或None（安全检查失败）
        """
        # 1. 安全范围检查
        if param_name in _PARAM_SAFE_RANGES:
            min_val, max_val = _PARAM_SAFE_RANGES[param_name]
            if isinstance(new_value, (int, float)):
                if new_value < min_val or new_value > max_val:
                    return {
                        "status": "rejected",
                        "reason": f"参数{param_name}={new_value}超出安全范围[{min_val},{max_val}]",
                        "param": param_name,
                    }

        # 2. 黑名单检查
        if param_name in _PATCH_BLACKLIST:
            return {
                "status": "rejected",
                "reason": f"参数{param_name}在黑名单中，不允许自动修改",
                "param": param_name,
            }

        # 3. 获取当前值
        current_value = self._get_current_value(param_name)

        # 4. 生成补丁
        patch = {
            "id": f"param_patch_{int(time.time() * 1000)}",
            "param": param_name,
            "old_value": current_value,
            "new_value": new_value,
            "delta": new_value - current_value if isinstance(new_value, (int, float)) and isinstance(current_value, (int, float)) else None,
            "reason": reason,
            "source": source,
            "status": "pending",
            "created_at": time.time(),
            "verified": False,
            "applied": False,
            "effect_verified": False,
        }

        with self._lock:
            self._pending_patches.append(patch)

        return patch

    def verify_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        """验证参数补丁（副本验证——不实际应用，只检查合理性）

        类似代码补丁的副本验证：
        1. 检查参数是否存在
        2. 检查类型是否匹配
        3. 检查调整幅度是否合理（单次不超过50%）
        4. 模拟应用后的预期效果
        """
        result = {
            "patch_id": patch.get("id"),
            "verified": False,
            "issues": [],
            "expected_effect": "",
        }

        param_name = patch.get("param")
        new_value = patch.get("new_value")
        old_value = patch.get("old_value")

        # 1. 检查参数是否存在于RUNTIME_PARAMS
        try:
            from config import RUNTIME_PARAMS
            if param_name not in RUNTIME_PARAMS:
                result["issues"].append(f"参数{param_name}不在RUNTIME_PARAMS中")
        except Exception as e:
            result["issues"].append(f"读取config失败: {e}")

        # 2. 检查类型匹配
        if old_value is not None and new_value is not None:
            if type(old_value) != type(new_value):
                # 允许int和float互转
                if not (isinstance(old_value, (int, float)) and isinstance(new_value, (int, float))):
                    result["issues"].append(
                        f"类型不匹配: old={type(old_value).__name__}, new={type(new_value).__name__}")

        # 3. 检查调整幅度（单次不超过50%）
        if (isinstance(old_value, (int, float)) and isinstance(new_value, (int, float))
                and old_value != 0):
            change_ratio = abs(new_value - old_value) / abs(old_value)
            if change_ratio > 0.5:
                result["issues"].append(
                    f"调整幅度过大: {change_ratio:.0%}（建议单次不超过50%）")

        # 4. 预期效果分析
        if not result["issues"]:
            patch["verified"] = True
            result["verified"] = True
            if patch.get("delta") is not None:
                direction = "增加" if patch["delta"] > 0 else "减少"
                result["expected_effect"] = (
                    f"参数{param_name}将{direction}{abs(patch['delta']):.3f}，"
                    f"预期效果: {patch.get('reason', '未知')}")
        else:
            result["verified"] = False

        return result

    def apply_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        """应用参数补丁（写入config_override.json，触发热加载）

        类似代码补丁的应用：
        1. 验证补丁
        2. 备份当前配置
        3. 写入config_override.json
        4. 等待热加载生效
        5. 记录应用历史
        """
        result = {
            "patch_id": patch.get("id"),
            "applied": False,
            "error": None,
        }

        # 1. 验证补丁
        if not patch.get("verified"):
            verify_result = self.verify_patch(patch)
            if not verify_result["verified"]:
                result["error"] = f"补丁验证失败: {verify_result['issues']}"
                patch["status"] = "rejected"
                return result

        # 2. 写入config_override.json
        try:
            # 读取当前override
            override_data = {}
            if os.path.exists(_CONFIG_OVERRIDE_PATH):
                override_data = safe_read_json(_CONFIG_OVERRIDE_PATH, default={})

            # 确保RUNTIME_PARAMS存在
            if "RUNTIME_PARAMS" not in override_data:
                override_data["RUNTIME_PARAMS"] = {}

            # 备份旧值
            old_override = override_data["RUNTIME_PARAMS"].get(patch["param"])
            patch["old_override_value"] = old_override

            # 写入新值
            override_data["RUNTIME_PARAMS"][patch["param"]] = patch["new_value"]

            # 原子写入
            temp_path = _CONFIG_OVERRIDE_PATH + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(override_data, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, _CONFIG_OVERRIDE_PATH)

            # 3. 等待热加载生效（配置监听器会自动检测）
            time.sleep(1)  # 给文件监听器一点时间

            patch["applied"] = True
            patch["applied_at"] = time.time()
            patch["status"] = "applied"
            result["applied"] = True

            # 4. 记录历史
            with self._lock:
                self._applied_patches.append(patch)
                if patch in self._pending_patches:
                    self._pending_patches.remove(patch)
            self._save_history()
            # ★P1: 记录参数变更日志
            try:
                import config as _cfg
                if hasattr(_cfg, 'log_config_change'):
                    _cfg.log_config_change(
                        param_name=patch["param"],
                        old_value=patch["old_value"],
                        new_value=patch["new_value"],
                        source=f"param_patch:{patch.get('source', 'unknown')}"
                    )
            except Exception as e:
                silent_exc(e, where="nucleus.evolution.ParamPatchManager::apply_patch L382")

        except Exception as e:
            silent_exc(e, where="nucleus.evolution.ParamPatchManager::apply_patch L385")
            result["error"] = str(e)
            patch["status"] = "failed"

        return result

    def verify_effect(self, patch: dict[str, Any],
                      baseline_metrics: dict[str, Any] | None = None,
                      wait_seconds: int = 30) -> dict[str, Any]:
        """验证补丁应用后的运行时效果

        类似代码补丁的运行时验证：
        1. 等待参数生效
        2. 收集应用后的指标
        3. 与基线对比
        4. 效果不佳建议回滚
        """
        result = {
            "patch_id": patch.get("id"),
            "effect_verified": False,
            "baseline": baseline_metrics,
            "post_metrics": None,
            "delta": None,
            "recommended": "keep",  # keep/rollback/observe
            "reason": "",
        }

        if not patch.get("applied"):
            result["reason"] = "补丁未应用，无法验证效果"
            return result

        # 等待参数生效并收集运行时指标
        time.sleep(min(wait_seconds, 5))  # 验证时最多等5秒，实际效果由后续监控完成

        # ★163批 刀7：灰度开关（PARAM_PATCH_EFFECT_VERIFY_ENABLED，默认 False）→
        #   开启时执行「真实效果验证」：复用既有 worker 验证链路（按名派发
        #   "verify_patch_effect_in_process"，不 import 封存模块），使 effect_verified
        #   反映真实结果而非硬编码 False；关闭时退化为原「待验证」桩（零行为变化）。
        import config as _cfg
        if getattr(_cfg, "PARAM_PATCH_EFFECT_VERIFY_ENABLED", False):
            from nucleus.reasoning.ReasoningWorkerPool import get_reasoning_pool
            try:
                _pool = get_reasoning_pool()
                _patch_info = {
                    "id": patch.get("id"),
                    "param": patch.get("param"),
                    "applied_at": patch.get("applied_at"),
                }
                _log_file = os.path.join(_PROJECT_ROOT, "logs", "pulse.log")
                _future = _pool.submit("verify_patch_effect_in_process", _patch_info, _log_file)
                _vres = _future.result(timeout=30)
                if _vres.get("error"):
                    result["effect_verified"] = False
                    patch["effect_verified"] = False
                    result["recommended"] = "observe"
                    result["reason"] = f"真实验证返回错误: {_vres.get('error')}"
                else:
                    _effect = _vres.get("effect", "unknown")
                    _rec = _vres.get("recommendation", "observe")
                    patch["effect_verified"] = True
                    patch["effect_result"] = _effect
                    patch["effect_verified_at"] = time.time()
                    patch["before_metrics"] = _vres.get("before_metrics", {})
                    patch["after_metrics"] = _vres.get("after_metrics", {})
                    patch["delta"] = _vres.get("delta", {})
                    result["effect_verified"] = True
                    result["recommended"] = _rec
                    result["post_metrics"] = patch["after_metrics"]
                    result["delta"] = patch["delta"]
                    result["reason"] = f"已执行真实效果验证(effect={_effect}, recommended={_rec})"
            except Exception as _ve:
                # 真实验证执行异常 → 维持待验证态（不谎报为已验证）
                silent_exc(_ve, where="ParamPatchManager.verify_effect:real")
                result["effect_verified"] = False
                patch["effect_verified"] = False
                result["recommended"] = "observe"
                result["reason"] = "真实验证执行异常，维持待验证态"
            return result

        # 标记为待验证（默认态：开关关闭，实际效果验证由框架的健康度比较完成）
        patch["effect_verified"] = False
        patch["effect_verify_scheduled_at"] = time.time()
        patch["baseline_metrics"] = baseline_metrics

        result["effect_verified"] = False
        result["recommended"] = "observe"
        result["reason"] = f"补丁已应用，将在{wait_seconds}秒后进行效果验证"

        return result

    def rollback_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        """回滚参数补丁（恢复旧值）"""
        result = {
            "patch_id": patch.get("id"),
            "rolled_back": False,
            "error": None,
        }

        try:
            # 读取当前override
            override_data = {}
            if os.path.exists(_CONFIG_OVERRIDE_PATH):
                override_data = safe_read_json(_CONFIG_OVERRIDE_PATH, default={})

            # 恢复旧值
            if "RUNTIME_PARAMS" in override_data:
                old_override = patch.get("old_override_value")
                if old_override is not None:
                    override_data["RUNTIME_PARAMS"][patch["param"]] = old_override
                else:
                    override_data["RUNTIME_PARAMS"].pop(patch["param"], None)

            # 原子写入
            temp_path = _CONFIG_OVERRIDE_PATH + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(override_data, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, _CONFIG_OVERRIDE_PATH)

            patch["status"] = "rolled_back"
            patch["rolled_back_at"] = time.time()
            result["rolled_back"] = True
            self._save_history()
            # ★P1: 记录参数回滚日志
            try:
                import config as _cfg
                if hasattr(_cfg, 'log_config_change'):
                    _cfg.log_config_change(
                        param_name=patch["param"],
                        old_value=patch["new_value"],
                        new_value=patch["old_value"],
                        source="param_patch_rollback"
                    )
            except Exception as e:
                silent_exc(e, where="nucleus.evolution.ParamPatchManager::rollback_patch L472")

        except Exception as e:
            silent_exc(e, where="nucleus.evolution.ParamPatchManager::rollback_patch L475")
            result["error"] = str(e)

        return result

    def _get_current_value(self, param_name: str) -> Any:
        """获取参数当前值"""
        try:
            from config import RUNTIME_PARAMS
            return RUNTIME_PARAMS.get(param_name)
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.ParamPatchManager::_get_current_value L485")
            return None

    def _load_history(self):
        """加载补丁历史"""
        try:
            if os.path.exists(_PATCH_HISTORY_PATH):
                history = safe_read_json(_PATCH_HISTORY_PATH, default={})
                self._applied_patches = history.get("applied", [])[-100:]  # 保留最近100条
        except Exception:
            self._applied_patches = []

    def _save_history(self):
        """保存补丁历史"""
        try:
            history = {
                "applied": self._applied_patches[-100:],
                "saved_at": time.time(),
            }
            temp_path = _PATCH_HISTORY_PATH + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, _PATCH_HISTORY_PATH)
        except Exception as e:
            silent_exc(e, "ParamPatchManager.py:507:_save_history", level="warning")

    def get_pending_patches(self) -> list[dict[str, Any]]:
        """获取待处理补丁"""
        with self._lock:
            return list(self._pending_patches)

    def get_applied_patches(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取已应用补丁"""
        with self._lock:
            return list(self._applied_patches[-limit:])

    def get_stats(self) -> dict[str, Any]:
        """获取补丁统计"""
        with self._lock:
            total = len(self._applied_patches)
            rolled_back = sum(1 for p in self._applied_patches if p.get("status") == "rolled_back")
            effective = sum(1 for p in self._applied_patches
                          if p.get("status") == "applied" and p.get("effect_verified"))
            return {
                "pending": len(self._pending_patches),
                "total_applied": total,
                "rolled_back": rolled_back,
                "effective": effective,
                "success_rate": round(effective / max(1, total - rolled_back), 2) if total > rolled_back else 0,
            }

    def auto_apply_pending(self, max_apply: int = 3, auto_verify: bool = True) -> dict[str, Any]:
        """★P1: 自动应用待处理的补丁（验证通过后自动应用）

        Args:
            max_apply: 本次最多应用的补丁数（压力均衡，避免一次应用过多）
            auto_verify: 是否自动进行效果验证调度

        Returns:
            应用结果统计
        """
        result = {
            "attempted": 0,
            "applied": 0,
            "rejected": 0,
            "failed": 0,
            "details": [],
        }

        with self._lock:
            pending = list(self._pending_patches)

        for patch in pending[:max_apply]:
            result["attempted"] += 1
            try:
                # 1. 验证补丁
                verify_result = self.verify_patch(patch)
                if not verify_result["verified"]:
                    patch["status"] = "rejected"
                    patch["reject_reason"] = "; ".join(verify_result["issues"])
                    result["rejected"] += 1
                    result["details"].append({
                        "patch_id": patch["id"],
                        "param": patch["param"],
                        "status": "rejected",
                        "reason": patch["reject_reason"],
                    })
                    # 从待处理列表移除
                    with self._lock:
                        if patch in self._pending_patches:
                            self._pending_patches.remove(patch)
                    continue

                # 2. 应用补丁
                apply_result = self.apply_patch(patch)
                if apply_result["applied"]:
                    result["applied"] += 1
                    result["details"].append({
                        "patch_id": patch["id"],
                        "param": patch["param"],
                        "old_value": patch["old_value"],
                        "new_value": patch["new_value"],
                        "status": "applied",
                    })

                    # 3. 调度效果验证
                    if auto_verify:
                        patch["effect_verify_scheduled"] = True
                        patch["effect_verify_scheduled_at"] = time.time()
                else:
                    result["failed"] += 1
                    result["details"].append({
                        "patch_id": patch["id"],
                        "param": patch["param"],
                        "status": "failed",
                        "error": apply_result.get("error"),
                    })

            except Exception as e:
                result["failed"] += 1
                result["details"].append({
                    "patch_id": patch.get("id"),
                    "param": patch.get("param"),
                    "status": "error",
                    "error": str(e),
                })

        return result

    def verify_applied_patches_effect(self, log_file: str | None = None,
                                       wait_after_apply: int = 60) -> dict[str, Any]:
        """★P1: 验证已应用补丁的运行时效果（分析日志对比前后指标）

        Args:
            log_file: 日志文件路径，默认logs/pulse.log
            wait_after_apply: 应用后等待多久才验证（秒）

        Returns:
            验证结果，含每个补丁的效果判定和回滚建议
        """
        import re as _re
        result = {
            "verified": 0,
            "effective": 0,
            "ineffective": 0,
            "rollback_recommended": 0,
            "details": [],
        }

        if log_file is None:
            log_file = os.path.join(_PROJECT_ROOT, "logs", "pulse.log")

        # 找出需要验证的补丁（已应用但未验证，且应用超过wait_after_apply秒）
        now = time.time()
        patches_to_verify = []
        with self._lock:
            for patch in self._applied_patches:
                if (patch.get("status") == "applied"
                        and not patch.get("effect_verified")
                        and patch.get("applied_at")
                        and (now - patch["applied_at"]) > wait_after_apply):
                    patches_to_verify.append(patch)

        if not patches_to_verify:
            result["note"] = "没有需要验证的补丁"
            return result

        # 读取日志，统计补丁应用前后的关键指标
        try:
            if not os.path.exists(log_file):
                result["note"] = f"日志文件不存在: {log_file}"
                return result

            with open(log_file, encoding="utf-8", errors="ignore") as f:
                log_lines = f.readlines()

            # 解析日志时间戳
            def _parse_time(line):
                m = _re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                if m:
                    from datetime import datetime
                    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
                return None

            for patch in patches_to_verify:
                apply_time = patch.get("applied_at", now)
                before_errors = 0
                after_errors = 0
                before_warnings = 0
                after_warnings = 0

                for line in log_lines:
                    t = _parse_time(line)
                    if t is None:
                        continue
                    # ★主线第30批 T2：按真实级别标记判定（无标记时回退原宽松判据）
                    _lv30 = extract_log_level(line)
                    if t < apply_time - 300 and t > apply_time - 600:  # 应用前5-10分钟
                        if (_lv30 in ("ERROR", "CRITICAL")) if _lv30 else ("ERROR" in line):
                            before_errors += 1
                        if (_lv30 == "WARNING") if _lv30 else ("WARNING" in line):
                            before_warnings += 1
                    elif t > apply_time + 30 and t < apply_time + 330:  # 应用后0.5-5.5分钟
                        if (_lv30 in ("ERROR", "CRITICAL")) if _lv30 else ("ERROR" in line):
                            after_errors += 1
                        if (_lv30 == "WARNING") if _lv30 else ("WARNING" in line):
                            after_warnings += 1

                # 效果判定
                error_delta = after_errors - before_errors
                warning_delta = after_warnings - before_warnings

                if error_delta < 0:
                    effect = "effective"
                    result["effective"] += 1
                    patch["effect_verified"] = True
                    patch["effect_result"] = "effective"
                    patch["error_reduction"] = abs(error_delta)
                elif error_delta == 0 and warning_delta <= 0:
                    effect = "neutral"
                    result["effective"] += 1  # 中性也算有效（没有恶化）
                    patch["effect_verified"] = True
                    patch["effect_result"] = "neutral"
                else:
                    effect = "ineffective"
                    result["ineffective"] += 1
                    patch["effect_verified"] = True
                    patch["effect_result"] = "ineffective"
                    patch["error_increase"] = error_delta

                    # 错误增加超过5条，建议回滚
                    if error_delta > 5:
                        patch["rollback_recommended"] = True
                        result["rollback_recommended"] += 1

                result["verified"] += 1
                result["details"].append({
                    "patch_id": patch["id"],
                    "param": patch["param"],
                    "effect": effect,
                    "before_errors": before_errors,
                    "after_errors": after_errors,
                    "before_warnings": before_warnings,
                    "after_warnings": after_warnings,
                    "rollback_recommended": patch.get("rollback_recommended", False),
                })

            self._save_history()

        except Exception as e:
            result["error"] = str(e)

        return result

    def auto_rollback_ineffective(self) -> dict[str, Any]:
        """★P1: 自动回滚效果不佳的补丁"""
        result = {
            "rolled_back": 0,
            "details": [],
        }
        with self._lock:
            for patch in self._applied_patches:
                if patch.get("rollback_recommended") and patch.get("status") == "applied":
                    rollback_result = self.rollback_patch(patch)
                    if rollback_result["rolled_back"]:
                        result["rolled_back"] += 1
                        result["details"].append({
                            "patch_id": patch["id"],
                            "param": patch["param"],
                            "reason": "效果不佳，自动回滚",
                        })
        return result

    def verify_effect_in_process(self, log_file: str | None = None,
                                  max_patches: int = 5) -> dict[str, Any]:
        """★P1: 使用独立进程验证补丁效果（不阻塞主进程）。

        利用ReasoningWorkerPool在独立进程中分析日志，
        对比补丁应用前后的关键指标，判定效果。

        Args:
            log_file: 日志文件路径
            max_patches: 最多验证的补丁数

        Returns:
            验证结果统计
        """
        result = {
            "submitted": 0,
            "completed": 0,
            "effective": 0,
            "degraded": 0,
            "rollback_recommended": 0,
            "details": [],
            "error": None,
        }

        try:
            # 找出需要验证的补丁
            now = time.time()
            patches_to_verify = []
            with self._lock:
                for patch in self._applied_patches:
                    if (patch.get("status") == "applied"
                            and not patch.get("effect_verified")
                            and patch.get("applied_at")
                            and (now - patch["applied_at"]) > 60):
                        patches_to_verify.append(patch)
                        if len(patches_to_verify) >= max_patches:
                            break

            if not patches_to_verify:
                result["note"] = "没有需要验证的补丁"
                return result

            result["submitted"] = len(patches_to_verify)

            # 尝试使用ReasoningWorkerPool（独立进程）
            try:
                from nucleus.reasoning.ReasoningWorkerPool import get_reasoning_pool
                pool = get_reasoning_pool()

                # 提交验证任务到独立进程
                futures = []
                for patch in patches_to_verify:
                    patch_info = {
                        "id": patch.get("id"),
                        "param": patch.get("param"),
                        "applied_at": patch.get("applied_at"),
                    }
                    future = pool.submit(
                        "verify_patch_effect_in_process",
                        patch_info,
                        log_file,
                    )
                    futures.append((patch, future))

                # 等待结果（带超时，避免阻塞）
                for patch, future in futures:
                    try:
                        verify_result = future.result(timeout=30)  # 30秒超时
                        result["completed"] += 1

                        # 更新补丁状态
                        effect = verify_result.get("effect", "unknown")
                        recommendation = verify_result.get("recommendation", "observe")
                        patch["effect_verified"] = True
                        patch["effect_result"] = effect
                        patch["effect_verified_at"] = time.time()
                        patch["before_metrics"] = verify_result.get("before_metrics", {})
                        patch["after_metrics"] = verify_result.get("after_metrics", {})
                        patch["delta"] = verify_result.get("delta", {})

                        if effect in ("significantly_improved", "improved", "neutral"):
                            result["effective"] += 1
                        elif effect in ("significantly_degraded", "degraded"):
                            result["degraded"] += 1
                            if recommendation == "rollback":
                                patch["rollback_recommended"] = True
                                result["rollback_recommended"] += 1

                        result["details"].append({
                            "patch_id": patch["id"],
                            "param": patch["param"],
                            "effect": effect,
                            "recommendation": recommendation,
                            "error_delta": verify_result.get("delta", {}).get("errors", 0),
                        })
                    except Exception as _fe:
                        result["details"].append({
                            "patch_id": patch.get("id"),
                            "param": patch.get("param"),
                            "effect": "timeout",
                            "error": str(_fe),
                        })

                self._save_history()

            except Exception as _pe:
                # ReasoningWorkerPool不可用，回退到主进程验证
                result["error"] = f"独立进程验证失败，回退主进程: {_pe}"
                fallback_result = self.verify_applied_patches_effect(log_file=log_file)
                result.update(fallback_result)

        except Exception as e:
            result["error"] = str(e)

        return result


    def start_auto_apply(self, interval: int = 300, initial_delay: float = 0.0):
        """★P1: 启动后台自动应用线程。

        ★主线第61批 T2/P1：新增 initial_delay（首跑延迟，秒）。
          根因：__init__ 中 _last_auto_apply = 0.0 → 首轮 `now - 0` 远超 interval
                → **首次立即触发**，恰与启动高峰期的第30次心跳（4 个任务重叠）撞车。
          修法：把「已等待时长」预置为 interval - initial_delay，使首跑恰好落在
                initial_delay 秒之后；initial_delay=0 时回到「立即首跑」（零回归）。
        """
        if self._auto_apply_running:
            return
        self._auto_apply_interval = interval
        self._auto_apply_running = True
        try:
            _delay = max(0.0, float(initial_delay or 0.0))
        except (TypeError, ValueError):
            _delay = 0.0
        try:
            self._last_auto_apply = time.time() - float(interval) + _delay
        except (TypeError, ValueError):
            self._last_auto_apply = 0.0
        self._auto_apply_thread = threading.Thread(
            target=self._auto_apply_loop, daemon=True, name="ParamPatchAutoApply")
        self._auto_apply_thread.start()
        print(f"[ParamPatchManager] 后台自动应用线程已启动（间隔{interval}秒，首跑延迟{_delay:.0f}秒）")

    def stop_auto_apply(self):
        """★P1: 停止后台自动应用线程。"""
        self._auto_apply_running = False
        if self._auto_apply_thread and self._auto_apply_thread.is_alive():
            self._auto_apply_thread.join(timeout=5)
        print("[ParamPatchManager] 后台自动应用线程已停止")

    def _auto_apply_loop(self):
        """★P1: 后台自动应用循环。"""
        while self._auto_apply_running:
            try:
                now = time.time()
                if now - self._last_auto_apply >= self._auto_apply_interval:
                    self._last_auto_apply = now
                    # 0. 先检测补丁冲突并自动解决
                    conflict_result = self.detect_patch_conflicts()
                    if conflict_result.get("total_conflicts", 0) > 0:
                        print(f"[ParamPatchManager] 检测到{conflict_result['total_conflicts']}个冲突，"
                              f"自动解决{conflict_result.get('auto_resolved', 0)}个")
                    # 1. 自动应用待处理补丁（按优先级排序）
                    apply_result = self.auto_apply_pending(max_apply=3, auto_verify=True)
                    if apply_result.get("applied", 0) > 0:
                        print(f"[ParamPatchManager] 自动应用了{apply_result['applied']}个参数补丁")
                    # 2. 验证已应用补丁的效果（优先使用独立进程，不阻塞主进程）
                    effect_result = self.verify_effect_in_process()
                    if effect_result.get("completed", 0) > 0:
                        print(f"[ParamPatchManager] 独立进程验证了{effect_result['completed']}个补丁效果，"
                              f"有效{effect_result['effective']}个，降级{effect_result['degraded']}个，"
                              f"建议回滚{effect_result['rollback_recommended']}个")
                    elif effect_result.get("submitted", 0) > 0:
                        print(f"[ParamPatchManager] 已提交{effect_result['submitted']}个补丁到独立进程验证")
                    # 3. 自动回滚效果不佳的补丁
                    rollback_result = self.auto_rollback_ineffective()
                    if rollback_result.get("rolled_back", 0) > 0:
                        print(f"[ParamPatchManager] 自动回滚了{rollback_result['rolled_back']}个无效补丁")

                    # 4. 每10次循环学习一次历史补丁效果（约50分钟）
                    # ★7-1/P1-13：懒初始化与自增同处一个临界区，
                    #   否则两个线程可能各自初始化一次，计数被重置。
                    with self._lock:
                        if not hasattr(self, '_learn_counter'):
                            self._learn_counter = 0
                        self._learn_counter += 1
                    if self._learn_counter % 10 == 0:
                        learn_result = self.learn_from_history()
                        if learn_result.get("recommendations"):
                            print(f"[ParamPatchManager] 历史学习: {len(learn_result['recommendations'])}条优化建议")
                            for rec in learn_result["recommendations"][:3]:
                                print(f"  - {rec}")
                        # 动态调整参数安全范围
                        range_result = self.adjust_safe_ranges_from_history()
                        if range_result.get("adjusted", 0) > 0:
                            print(f"[ParamPatchManager] 安全范围动态调整: "
                                  f"扩展{len(range_result.get('expanded', []))}个，"
                                  f"收缩{len(range_result.get('contracted', []))}个")
                        # ★P1: 生成参数分析报告（HTML可视化）
                        try:
                            from nucleus.evolution.ParamAnalysisReport import (
                                generate_report,
                            )
                            report_path = generate_report()
                            print(f"[ParamPatchManager] 参数分析报告已生成: {report_path}")
                        except Exception as report_e:
                            print(f"[ParamPatchManager] 报告生成失败: {report_e}")
            except Exception as e:
                print(f"[ParamPatchManager] 自动应用循环异常: {e}")
            # 睡眠（分段睡眠，便于快速停止）
            for _ in range(self._auto_apply_interval):
                if not self._auto_apply_running:
                    break
                time.sleep(1)

    def learn_from_history(self, min_samples: int = 5) -> dict[str, Any]:
        """★P1: 从历史补丁中学习最优参数组合。

        分析已应用补丁的效果，找出：
        1. 哪些参数调整最有效（错误减少最多）
        2. 哪些参数调整经常失败（需要回滚）
        3. 参数调整的最优方向和幅度
        4. 生成参数优化建议

        Args:
            min_samples: 最小样本数

        Returns:
            学习结果 {best_params, worst_params, recommendations, stats}
        """
        result = {
            "total_patches": 0,
            "effective_patches": 0,
            "rolled_back_patches": 0,
            "best_params": [],
            "worst_params": [],
            "recommendations": [],
            "stats": {},
        }

        try:
            with self._lock:
                applied = [p for p in self._applied_patches if p.get("effect_verified")]

            result["total_patches"] = len(applied)
            result["effective_patches"] = sum(1 for p in applied
                                              if p.get("effect_result") in
                                              ("significantly_improved", "improved", "neutral"))
            result["rolled_back_patches"] = sum(1 for p in applied
                                                 if p.get("status") == "rolled_back")

            if len(applied) < min_samples:
                result["note"] = f"样本不足（{len(applied)}<{min_samples}），暂不生成学习建议"
                return result

            # 按参数名分组统计
            param_stats = {}
            for patch in applied:
                param = patch.get("param", "unknown")
                if param not in param_stats:
                    param_stats[param] = {
                        "total": 0,
                        "effective": 0,
                        "rolled_back": 0,
                        "total_error_reduction": 0,
                        "deltas": [],
                    }
                param_stats[param]["total"] += 1
                if patch.get("effect_result") in ("significantly_improved", "improved", "neutral"):
                    param_stats[param]["effective"] += 1
                if patch.get("status") == "rolled_back":
                    param_stats[param]["rolled_back"] += 1
                delta = patch.get("delta", {}).get("errors", 0)
                if isinstance(delta, (int, float)):
                    param_stats[param]["total_error_reduction"] -= delta  # 负数表示错误减少
                    param_stats[param]["deltas"].append(delta)

            # 计算每个参数的成功率和平均效果
            param_effectiveness = []
            for param, stats in param_stats.items():
                if stats["total"] > 0:
                    success_rate = stats["effective"] / stats["total"]
                    avg_error_reduction = (stats["total_error_reduction"] / stats["total"]
                                          if stats["total"] > 0 else 0)
                    param_effectiveness.append({
                        "param": param,
                        "success_rate": round(success_rate, 2),
                        "avg_error_reduction": round(avg_error_reduction, 2),
                        "total": stats["total"],
                        "rolled_back": stats["rolled_back"],
                    })

            # 排序：成功率降序
            param_effectiveness.sort(key=lambda x: x["success_rate"], reverse=True)

            result["best_params"] = param_effectiveness[:5]  # 最有效的5个参数
            result["worst_params"] = param_effectiveness[-5:] if len(param_effectiveness) >= 5 else param_effectiveness
            result["stats"] = {
                "overall_success_rate": round(result["effective_patches"] / max(1, result["total_patches"]), 2),
                "rollback_rate": round(result["rolled_back_patches"] / max(1, result["total_patches"]), 2),
                "unique_params": len(param_stats),
            }

            # 生成优化建议
            for p in param_effectiveness[:3]:
                if p["success_rate"] >= 0.7:
                    result["recommendations"].append(
                        f"参数'{p['param']}'调整成功率{p['success_rate']:.0%}，"
                        f"平均减少错误{p['avg_error_reduction']:.1f}，建议继续使用此调整方向")
            for p in param_effectiveness[-3:]:
                if p["success_rate"] < 0.3 and p["total"] >= 3:
                    result["recommendations"].append(
                        f"参数'{p['param']}'调整成功率仅{p['success_rate']:.0%}，"
                        f"建议调整幅度或暂停此参数的自动调整")

        except Exception as e:
            result["error"] = str(e)

        return result

    def get_optimal_params(self) -> dict[str, Any]:
        """★P1: 获取当前最优参数配置（基于历史学习）。

        Returns:
            最优参数配置字典 {param: best_value}
        """
        optimal = {}
        try:
            with self._lock:
                applied = [p for p in self._applied_patches
                          if p.get("effect_verified")
                          and p.get("effect_result") in ("significantly_improved", "improved")]

            # 对每个参数，找到效果最好的调整值
            param_best = {}
            for patch in applied:
                param = patch.get("param")
                if not param:
                    continue
                new_value = patch.get("new_value")
                delta = patch.get("delta", {}).get("errors", 0)
                if param not in param_best or delta < param_best[param]["delta"]:
                    param_best[param] = {"value": new_value, "delta": delta}

            for param, info in param_best.items():
                optimal[param] = info["value"]

        except Exception as e:
            silent_exc(e, where="nucleus.evolution.ParamPatchManager::get_optimal_params L1105")

        return optimal

    def adjust_safe_ranges_from_history(self, min_samples: int = 10) -> dict[str, Any]:
        """★P1: 根据历史学习结果动态调整参数安全范围。

        分析历史补丁的效果，自动扩展或收缩参数的安全范围：
        - 如果某参数的调整总是有效且幅度较大，扩展安全范围
        - 如果某参数的调整经常失败，收缩安全范围

        Args:
            min_samples: 最小样本数

        Returns:
            调整结果 {adjusted_ranges, stats}
        """
        result = {
            "adjusted": 0,
            "expanded": [],
            "contracted": [],
            "stats": {},
        }

        try:
            with self._lock:
                applied = [p for p in self._applied_patches if p.get("effect_verified")]

            if len(applied) < min_samples:
                result["note"] = f"样本不足（{len(applied)}<{min_samples}）"
                return result

            # 按参数分组
            param_deltas = {}
            for patch in applied:
                param = patch.get("param")
                if not param or param not in _PARAM_SAFE_RANGES:
                    continue
                delta = patch.get("delta")
                if delta is None:
                    continue
                effective = patch.get("effect_result") in ("significantly_improved", "improved", "neutral")
                if param not in param_deltas:
                    param_deltas[param] = {"effective": [], "ineffective": []}
                if effective:
                    param_deltas[param]["effective"].append(abs(delta))
                else:
                    param_deltas[param]["ineffective"].append(abs(delta))

            # 动态调整安全范围
            for param, deltas in param_deltas.items():
                if len(deltas["effective"]) + len(deltas["ineffective"]) < 3:
                    continue

                current_min, current_max = _PARAM_SAFE_RANGES[param]
                current_range = current_max - current_min

                # 计算有效调整的平均幅度
                if deltas["effective"]:
                    avg_effective_delta = sum(deltas["effective"]) / len(deltas["effective"])
                else:
                    avg_effective_delta = 0

                # 计算无效调整的平均幅度
                if deltas["ineffective"]:
                    avg_ineffective_delta = sum(deltas["ineffective"]) / len(deltas["ineffective"])
                else:
                    avg_ineffective_delta = 0

                # 如果有效调整幅度大于当前范围的50%，扩展范围
                if avg_effective_delta > current_range * 0.5 and len(deltas["effective"]) >= 3:
                    expansion = avg_effective_delta * 0.5
                    if isinstance(current_min, int):
                        new_min = max(0, int(current_min - expansion))
                        new_max = int(current_max + expansion)
                    else:
                        new_min = max(0.0, current_min - expansion)
                        new_max = current_max + expansion
                    _PARAM_SAFE_RANGES[param] = (new_min, new_max)
                    result["expanded"].append({
                        "param": param,
                        "old_range": [current_min, current_max],
                        "new_range": [new_min, new_max],
                        "reason": f"有效调整平均幅度{avg_effective_delta:.2f}超过当前范围50%",
                    })
                    result["adjusted"] += 1

                # 如果无效调整幅度大于有效调整幅度，收缩范围
                elif (avg_ineffective_delta > avg_effective_delta * 1.5
                      and len(deltas["ineffective"]) >= 3):
                    contraction = avg_ineffective_delta * 0.3
                    if isinstance(current_min, int):
                        new_min = int(current_min + contraction)
                        new_max = max(new_min + 1, int(current_max - contraction))
                    else:
                        new_min = current_min + contraction
                        new_max = max(new_min + 0.01, current_max - contraction)
                    _PARAM_SAFE_RANGES[param] = (new_min, new_max)
                    result["contracted"].append({
                        "param": param,
                        "old_range": [current_min, current_max],
                        "new_range": [new_min, new_max],
                        "reason": f"无效调整平均幅度{avg_ineffective_delta:.2f}是有效调整的1.5倍以上",
                    })
                    result["adjusted"] += 1

            result["stats"] = {
                "total_params_analyzed": len(param_deltas),
                "expanded_count": len(result["expanded"]),
                "contracted_count": len(result["contracted"]),
            }

        except Exception as e:
            result["error"] = str(e)

        return result

    def prioritize_pending_patches(self) -> list[dict[str, Any]]:
        """★P1: 对待处理补丁进行优先级排序。

        排序规则：
        1. 高优先级参数（影响核心功能）优先
        2. 历史成功率高的参数调整优先
        3. 调整幅度小的优先（更安全）
        4. 新发现的问题优先

        Returns:
            排序后的待处理补丁列表
        """
        # 高优先级参数（影响核心功能）
        # ★A-9：search_quality_threshold 已移除（死参数清理）
        _high_priority_params = {
            "llm_remediation_threshold", "output_relevance_threshold",
            "stomach_purity_threshold",
            "knowledge_min_similarity", "parallel_max_workers",
        }

        def _priority_score(patch):
            score = 0
            param = patch.get("param", "")
            # 1. 高优先级参数 +50分
            if param in _high_priority_params:
                score += 50
            # 2. 历史成功率（从已应用补丁中统计）
            with self._lock:
                same_param_applied = [p for p in self._applied_patches
                                     if p.get("param") == param and p.get("effect_verified")]
            if same_param_applied:
                success_count = sum(1 for p in same_param_applied
                                   if p.get("effect_result") in
                                   ("significantly_improved", "improved", "neutral"))
                success_rate = success_count / len(same_param_applied)
                score += int(success_rate * 30)  # 最多30分
            # 3. 调整幅度小的优先（最多20分）
            old_val = patch.get("old_value", 0)
            new_val = patch.get("new_value", 0)
            if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)) and old_val != 0:
                change_ratio = abs(new_val - old_val) / abs(old_val)
                score += max(0, 20 - int(change_ratio * 40))  # 变化越小分数越高
            # 4. 新发现的问题优先（最多10分）
            if patch.get("is_new_issue", False):
                score += 10
            return score

        with self._lock:
            pending = list(self._pending_patches)

        pending.sort(key=_priority_score, reverse=True)
        return pending

    def detect_patch_conflicts(self) -> dict[str, Any]:
        """★P1: 检测待处理补丁之间的冲突。

        冲突类型：
        1. 同一参数有多个待处理补丁（方向相反或重复）
        2. 相关参数的调整方向矛盾（如同时增加和减少搜索频率）
        3. 参数调整超出安全范围

        Returns:
            冲突检测结果 {conflicts, resolved, details}
        """
        result = {
            "total_conflicts": 0,
            "same_param_conflicts": [],
            "related_param_conflicts": [],
            "out_of_range": [],
            "auto_resolved": 0,
            "details": [],
        }

        try:
            with self._lock:
                pending = list(self._pending_patches)

            # 1. 同一参数有多个待处理补丁
            param_patches = {}
            for patch in pending:
                param = patch.get("param")
                if param not in param_patches:
                    param_patches[param] = []
                param_patches[param].append(patch)

            for param, patches in param_patches.items():
                if len(patches) > 1:
                    # 检查方向是否相反
                    directions = set()
                    for p in patches:
                        old_val = p.get("old_value", 0)
                        new_val = p.get("new_value", 0)
                        if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)):
                            if new_val > old_val:
                                directions.add("increase")
                            elif new_val < old_val:
                                directions.add("decrease")
                            else:
                                directions.add("same")

                    if len(directions) > 1:
                        conflict = {
                            "param": param,
                            "patch_count": len(patches),
                            "directions": list(directions),
                            "patch_ids": [p.get("id") for p in patches],
                            "resolution": "保留最新的补丁，删除旧的",
                        }
                        result["same_param_conflicts"].append(conflict)
                        result["total_conflicts"] += 1
                        # 自动解决：保留最新的补丁
                        patches.sort(key=lambda x: x.get("created_at", 0), reverse=True)
                        for old_patch in patches[1:]:
                            if old_patch in self._pending_patches:
                                self._pending_patches.remove(old_patch)
                                result["auto_resolved"] += 1
                    else:
                        # 方向相同，合并为一个补丁（取最大调整幅度）
                        patches.sort(key=lambda x: abs(x.get("new_value", 0) - x.get("old_value", 0)),
                                    reverse=True)
                        for old_patch in patches[1:]:
                            if old_patch in self._pending_patches:
                                self._pending_patches.remove(old_patch)
                                result["auto_resolved"] += 1

            # 2. 相关参数的调整方向矛盾
            # ★A-9：stomach_min_keywords 已移除（死参数），相关组只剩纯度单参数，整组删除
            _related_param_groups = [
                {"search_cooldown_seconds", "search_max_per_hour"},  # 冷却增加应该减少频率
                {"parallel_max_workers", "parallel_task_timeout"},  # 工作线程增加应该减少超时
            ]
            for group in _related_param_groups:
                group_patches = [p for p in pending if p.get("param") in group]
                if len(group_patches) >= 2:
                    # 检查方向是否矛盾
                    directions = {}
                    for p in group_patches:
                        param = p.get("param")
                        old_val = p.get("old_value", 0)
                        new_val = p.get("new_value", 0)
                        if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)):
                            directions[param] = "increase" if new_val > old_val else "decrease"

                    # 简单的矛盾检测：如果冷却增加但频率也增加，可能矛盾
                    if ("search_cooldown_seconds" in directions and
                            "search_max_per_hour" in directions):
                        if (directions["search_cooldown_seconds"] == "increase" and
                                directions["search_max_per_hour"] == "increase"):
                            result["related_param_conflicts"].append({
                                "params": ["search_cooldown_seconds", "search_max_per_hour"],
                                "issue": "冷却时间和搜索频率同时增加，可能矛盾",
                                "resolution": "建议人工审核",
                            })
                            result["total_conflicts"] += 1

            # 3. 参数调整超出安全范围
            for patch in pending:
                param = patch.get("param")
                new_val = patch.get("new_value")
                if param in _PARAM_SAFE_RANGES and new_val is not None:
                    min_val, max_val = _PARAM_SAFE_RANGES[param]
                    if isinstance(new_val, (int, float)):
                        if new_val < min_val or new_val > max_val:
                            result["out_of_range"].append({
                                "patch_id": patch.get("id"),
                                "param": param,
                                "new_value": new_val,
                                "safe_range": [min_val, max_val],
                                "resolution": "自动裁剪到安全范围",
                            })
                            result["total_conflicts"] += 1
                            # 自动裁剪
                            patch["new_value"] = max(min_val, min(max_val, new_val))
                            result["auto_resolved"] += 1

            self._save_history()

        except Exception as e:
            result["error"] = str(e)

        return result

    def run_ab_test(self, param_name: str, control_value: Any = None,
                    experiment_value: Any = None,
                    log_file: str | None = None) -> dict[str, Any]:
        """★P1: 运行参数AB测试（利用独立进程对比不同参数配置的效果）。"""
        result = {
            "test_id": f"ab_{int(time.time())}",
            "param_name": param_name,
            "status": "pending",
            "result": None,
            "error": None,
        }
        try:
            if control_value is None:
                try:
                    import config as _cfg
                    _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
                    control_value = _rp.get(param_name)
                except Exception:
                    control_value = None
            if control_value is None:
                result["error"] = f"参数'{param_name}'不存在或无法获取当前值"
                return result
            if experiment_value is None:
                if isinstance(control_value, (int, float)):
                    experiment_value = control_value * 1.2
                    if isinstance(control_value, int):
                        experiment_value = int(experiment_value)
                elif isinstance(control_value, bool):
                    experiment_value = not control_value
                else:
                    result["error"] = f"参数'{param_name}'类型不支持自动生成实验组"
                    return result
            try:
                from nucleus.reasoning.ReasoningWorkerPool import get_reasoning_pool
                pool = get_reasoning_pool()
                test_config = {
                    "test_id": result["test_id"],
                    "param_name": param_name,
                    "control_value": control_value,
                    "experiment_value": experiment_value,
                    "log_file": log_file,
                }
                future = pool.submit("run_ab_test_in_process", test_config)
                ab_result = future.result(timeout=TIMEOUT_CONFIG['llm_call'])
                result["status"] = "completed"
                result["result"] = ab_result
                result["control_value"] = control_value
                result["experiment_value"] = experiment_value
                if ab_result.get("winner") == "experiment" and ab_result.get("statistically_significant"):
                    patch = self.generate_patch(
                        param_name=param_name,
                        new_value=experiment_value,
                        reason=f"AB测试验证：提升{ab_result.get('improvement', 0):.1f}%",
                        source="ab_test",
                    )
                    if patch:
                        result["auto_patch_generated"] = True
                        result["patch_id"] = patch.get("id")
            except Exception as _pe:
                result["error"] = f"独立进程AB测试失败，回退主进程: {_pe}"
                from nucleus.evolution.ParamABTestEngine import run_ab_test_in_process
                test_config = {
                    "test_id": result["test_id"],
                    "param_name": param_name,
                    "control_value": control_value,
                    "experiment_value": experiment_value,
                    "log_file": log_file,
                }
                ab_result = run_ab_test_in_process(test_config)
                result["status"] = "completed"
                result["result"] = ab_result
        except Exception as e:
            result["error"] = str(e)
        return result

    def run_batch_ab_tests(self, param_names: list[str],
                            log_file: str | None = None) -> dict[str, Any]:
        """★P1: 批量运行多个参数的AB测试。"""
        results = {}
        for param_name in param_names:
            try:
                result = self.run_ab_test(param_name, log_file=log_file)
                results[param_name] = result
            except Exception as e:
                results[param_name] = {"error": str(e)}
        return {
            "total_tests": len(param_names),
            "completed": sum(1 for r in results.values() if r.get("status") == "completed"),
            "winners": {k: v.get("result", {}).get("winner") for k, v in results.items()
                       if v.get("status") == "completed"},
            "details": results,
        }

# 单例
_param_patch_manager = None
_param_patch_lock = threading.Lock()


def get_param_patch_manager() -> ParamPatchManager:
    """获取参数补丁管理器单例"""
    global _param_patch_manager
    with _param_patch_lock:
        if _param_patch_manager is None:
            _param_patch_manager = ParamPatchManager()
        return _param_patch_manager
