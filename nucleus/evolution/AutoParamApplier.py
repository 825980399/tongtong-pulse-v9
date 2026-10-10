# -*- coding: utf-8 -*-
"""
AutoParamApplier.py —— 参数自动应用器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 自动应用进化产生的参数优化
机制: 基于AutoParamApplier类实现，包含9个核心方法
定位: 进化执行层
"""

import threading
import time
from collections.abc import Callable
from typing import Any

from nucleus._silent_except import silent_exc

_PROJECT_ROOT = None  # 占位（如需持久化再启用；当前审计走 ParamPatchManager 历史文件）

# 结构性高风险参数黑名单（即使未来变成数值型也禁止自动应用，须人工审批）
_STRUCTURAL_RISKY = {
    "ENABLE_RUNTIME_TRAJECTORY_PERSIST", "ENABLE_TIME_CORE",
    "ENABLE_TIMECORE_ACTIVE_SCHEDULE", "ENABLE_KNOWLEDGE_TIMELINESS",
    "ENABLE_GLOBAL_LEARNER", "ENABLE_SEARCH_QUALITY_CLOSED_LOOP",
    "ENABLE_DIGESTION_QUALITY_CLOSED_LOOP", "ENABLE_RSS_COLLECTOR",
    "ENABLE_ENCYCLOPEDIA_QUERY", "ENABLE_KNOWLEDGE_ACQUISITION_ROUTER",
    "ENABLE_STOMACH_PATH_OPTIMIZE", "ENABLE_WIKI_TRIGGER_STRICT",
    "ENABLE_LOOP_MULTI_PARAM", "ENABLE_NARRATIVE_CONSUMPTION",
    "ENABLE_AUTO_PARAM_APPLY", "ENABLE_KNOWLEDGE_GRAPH_CONSUMPTION",
    "log_structured_parallel", "llm_quality_compare_enabled",
    "code_learn_param_audit_enabled",
}


class AutoParamApplier:
    """参数优化建议自动应用器（线程安全；一个后台巡检线程负责验证/回滚）"""

    def __init__(self, log_fn: Callable[[str], None] | None = None):
        self._log_fn = log_fn or (lambda _m: None)
        self._lock = threading.Lock()
        self._last_round_ts = 0.0          # 上一轮自动应用时间（每小时≤1轮）
        self._max_per_round = 3            # 单轮上限
        self._round_interval_sec = 3600.0  # 每小时≤1轮
        self._stats = {"submitted": 0, "applied": 0, "skipped_risky": 0,
                       "skipped_limit": 0, "rounds": 0}
        # 验证/回滚巡检
        self._timer = None
        self._running = False
        self._verify_interval_sec = 600.0  # 每10分钟巡检一次（压力均衡）

    # ========== 对外接口 ==========

    def submit_patches(self, patches: list[dict[str, Any]],
                       source: str = "code_learner") -> int:
        """提交参数补丁批（代码学习器建议转化而来）。返回实际自动应用数。

        灰度关闭 → 返回 0（补丁留在 ParamPatchManager pending 队列，人工审批，零回退）。
        """
        try:
            import config as _cfg
            if not getattr(_cfg, 'ENABLE_AUTO_PARAM_APPLY', False):
                return 0
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.AutoParamApplier::submit_patches L65")
            return 0
        if not patches:
            return 0

        with self._lock:
            _now = time.time()
            if _now - self._last_round_ts < self._round_interval_sec:
                self._stats["skipped_limit"] += len(patches)
                self._log(f"限流: 距上一轮自动应用不足1小时，本批{len(patches)}个补丁"
                          f"留在pending队列待下轮/人工")
                return 0
            self._last_round_ts = _now
            self._stats["rounds"] += 1

        _applied = 0
        for _p in patches[:self._max_per_round]:
            if self._apply_one(_p, source):
                _applied += 1
        if len(patches) > self._max_per_round:
            self._log(f"单轮上限{self._max_per_round}："
                      f"{len(patches) - self._max_per_round}个补丁留在pending队列")
        self._log(f"本轮自动应用完成: 提交{len(patches)}，应用{_applied}，"
                  f"其余留pending（人工可随时审批）")
        self._ensure_inspector()
        return _applied

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {**self._stats, "running": self._running}

    # ========== 内部实现 ==========

    def _apply_one(self, patch: dict[str, Any], source: str) -> bool:
        """应用单个补丁（风险分级 + PPM 安全检查 + 应用 + 日志）。"""
        _param = str(patch.get("param", ""))
        _new = patch.get("new_value")
        # 1) 风险分级：布尔/字符串 = 结构性参数 → 一律人工
        if not isinstance(_new, (int, float)) or isinstance(_new, bool):
            self._stats["skipped_risky"] += 1
            self._log(f"风险拦截: {_param} 新值非数值型"
                      f"（结构性参数）→ 留pending人工审批")
            return False
        if _param in _STRUCTURAL_RISKY:
            self._stats["skipped_risky"] += 1
            self._log(f"风险拦截: {_param} 在结构性高风险清单 → 留pending人工审批")
            return False

        # 2) PPM 全套安全检查 + 应用（generate→verify→apply）
        try:
            from nucleus.evolution.ParamPatchManager import get_param_patch_manager
            _ppm = get_param_patch_manager()
            _fresh = _ppm.generate_patch(
                _param, _new,
                reason=f"A-4自动应用:{patch.get('reason', '')[:60]}",
                source=f"auto_apply:{source}")
            if not _fresh or _fresh.get("status") == "rejected":
                self._log(f"安全拒绝: {_param} → "
                          f"{_fresh.get('reason') if _fresh else 'None'}（留pending人工）")
                return False
            _v = _ppm.verify_patch(_fresh)
            if not _v.get("verified"):
                self._log(f"副本验证失败: {_param} {_v.get('issues')}（留pending人工）")
                return False
            _r = _ppm.apply_patch(_fresh)
            if not _r.get("applied"):
                self._log(f"应用失败: {_param} {_r.get('error')}")
                return False
            self._stats["applied"] += 1
            self._log(f"自动应用: {_param} "
                      f"{_fresh.get('old_value')}→{_fresh.get('new_value')}"
                      f"（patch_id={_fresh.get('id')}，已进入验证/回滚巡检）")
            return True
        except Exception as _e:
            self._log(f"自动应用异常({_param})(已忽略): {_e}")
            return False

    def _ensure_inspector(self):
        """启动验证/回滚巡检线程（懒启动，daemon）。"""
        if self._running:
            return
        self._running = True
        self._timer = threading.Thread(target=self._inspect_loop, daemon=True,
                                       name="AutoParamApplierInspector")
        self._timer.start()
        self._log("验证/回滚巡检线程已启动（每10分钟，复用PPM日志对比基建）")

    def _inspect_loop(self):
        while self._running:
            time.sleep(self._verify_interval_sec)
            try:
                self._inspect_once()
            except Exception as _e:
                self._log(f"巡检异常(已忽略): {_e}")

    def _inspect_once(self):
        """复用 PPM 的日志对比验证 + 自动回滚（与闭环控制器同语义）。"""
        try:
            import config as _cfg
            if not getattr(_cfg, 'ENABLE_AUTO_PARAM_APPLY', False):
                return
            from nucleus.evolution.ParamPatchManager import get_param_patch_manager
            _ppm = get_param_patch_manager()
            _v = _ppm.verify_applied_patches_effect(wait_after_apply=300)
            _rb = _ppm.auto_rollback_ineffective()
            if _v.get("verified") or _v.get("evaluated"):
                self._log(f"巡检: 效果验证 {_v}")
            if _rb.get("rolled_back"):
                self._log(f"巡检: 自动回滚 {_rb.get('rolled_back')}个无效补丁 "
                          f"{_rb.get('details', [])[:3]}")
        except Exception as _e:
            self._log(f"巡检失败(已忽略): {_e}")

    def _log(self, msg: str):
        try:
            self._log_fn(f"[参数自动应用] {msg}")
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.AutoParamApplier::_log L181")


# ========== 模块级单例 ==========

_shared: AutoParamApplier | None = None
_shared_lock = threading.Lock()


def get_auto_param_applier(log_fn=None) -> AutoParamApplier:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = AutoParamApplier(log_fn=log_fn)
        return _shared
