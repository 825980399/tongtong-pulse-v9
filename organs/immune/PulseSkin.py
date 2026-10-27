"""PulseSkin —— PulseSkin 相关实现

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

"""
PulseSkin —— 脉冲驱动皮肤（免疫系统第二器官 · v9.5 分层脉冲版）
版本: v9.5 PulseNet
设计: 路灯、小林、星轨
日期: 2026年6月9日
更新: 2026年6月13日（P0-2+P0-5: 五合一全面改造——事件枚举+补充get_stats+自测同步）
更新: 2026年6月14日（v9.5: 安全拦截标记layer=L0，安全通过标记layer=L1，适配分层异步调度）

职责:
    1. 补丁预检：接收 SkinEvent.REVIEW_PATCH 脉冲，检查补丁安全性
    2. 安全沙箱校验：验证代码是否包含危险操作
    3. 输入净化：过滤外部输入中的恶意内容
    4. 多重屏障：按危险等级分级处理
"""

from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import LogLevel, SecurityEvent, SkinEvent, SystemEvent


class PulseSkin(BasePulseOrgan):
    """
    脉冲驱动皮肤（v9.5 分层脉冲版）

    防御流程:
        SkinEvent.REVIEW_PATCH 脉冲到达
        → 检查补丁内容是否包含危险操作
        → 按危险等级分级处理（拒绝/警告/通过）
        → 发射 SkinEvent.REVIEW_RESULT 脉冲
        → 危险拦截：L0生命线层告警
        → 安全通过/警告：L1实时交互层
    """

    def __init__(self, organ_name: str = "皮肤"):
        super().__init__(organ_name)

        # 从config加载皮肤安全配置
        self._load_skin_config()

        # 统计
        self._review_count = 0
        self._blocked_count = 0
        self.sandbox_core = None
    def _load_skin_config(self):
        """从config加载皮肤安全配置，失败时使用兜底值"""
        try:
            import config
            cfg = getattr(config, 'SKIN_CONFIG', {})
            self._dangerous_patterns = cfg.get("dangerous_patterns", [
                "rm -rf", "del /f", "format c:", "DROP TABLE",
                "os.system", "subprocess.call", "eval(", "exec(",
                "__import__", "importlib", "compile(",
            ])
            self._suspicious_patterns = cfg.get("suspicious_patterns", [
                "open(", "file.write", "socket.", "requests.post",
                "shutil.rmtree", "os.remove", "os.unlink",
            ])
        except Exception as e:
            self._log(LogLevel.INFO, f"[WARNING] PulseSkin.py:71: {type(e).__name__}: {e}")
            self._dangerous_patterns = [
                "rm -rf", "del /f", "format c:", "DROP TABLE",
                "os.system", "subprocess.call", "eval(", "exec(",
                "__import__", "importlib", "compile(",
            ]
            self._suspicious_patterns = [
                "open(", "file.write", "socket.", "requests.post",
                "shutil.rmtree", "os.remove", "os.unlink",
            ]

    def set_sandbox_core(self, core):
        self.sandbox_core = core

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == SkinEvent.REVIEW_PATCH:
            return self._on_review_patch(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None

    # ========== 事件处理 ==========

    def _on_review_patch(self, payload: dict) -> dict[str, Any]:
        """审查补丁"""
        code = payload.get("code", "")
        patch_name = payload.get("patch_name", "unknown")

        if not code:
            return {"status": "skipped", "reason": "空代码"}

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._review_count += 1

        # 步骤1: 检查危险操作
        for pattern in self._dangerous_patterns:
            if pattern in code:
                # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                self._blocked_count += 1
                # v9.5: 安全拦截告警标记为L0生命线层
                self._emit(SecurityEvent.BLOCKED, {
                    "patch_name": patch_name,
                    "verdict": "blocked",
                    "reason": f"包含危险操作: {pattern}",
                    "level": "L1",
                }, priority=9, layer="L0")
                return {"status": "blocked", "reason": f"包含危险操作: {pattern}"}

        # 步骤2: 检查可疑操作
        warnings = []
        for pattern in self._suspicious_patterns:
            if pattern in code:
                warnings.append(pattern)

        # 步骤3: 发射审查结果（v9.5: L1实时交互层）
        # 警告不等于阻塞——使用PASSED+verdict="warning"区分，避免误导白细胞
        verdict = "warning" if warnings else "passed"  # noqa: F841
        if warnings:
            self._emit(SecurityEvent.PASSED, {
                "patch_name": patch_name,
                "verdict": "warning",
                "warnings": warnings,
                "level": "L1",
            }, priority=6, layer="L1")
        else:
            self._emit(SecurityEvent.PASSED, {
                "patch_name": patch_name,
                "verdict": "passed",
                "level": "L1",
            }, priority=5, layer="L1")
        return {
            "status": "passed" if not warnings else "warning",
            "warnings": warnings,
        }

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "review_count": self._review_count,
            "blocked_count": self._blocked_count,
            "dangerous_patterns": len(self._dangerous_patterns),
            "is_running": self.is_running,
        }

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    SkinEvent.REVIEW_PATCH,
                    SystemEvent.STATUS_REQUEST,
                ],
                "min_priority": 1,
            }
        ]

    # ========== 未来演化预留 ==========

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "皮肤",
    "class_name": "PulseSkin",
    "attr_name": "skin",
    "system": "immune",
    "always_online": False,
    "feature_flag": "enable_immune",
    "extra_deps": {},
    "post_wiring": [
        {"target": "sandbox_core", "setter": "set_sandbox_core"},
    ],
}
