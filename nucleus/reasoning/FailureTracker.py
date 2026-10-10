# -*- coding: utf-8 -*-
"""
FailureTracker.py —— 失败追踪器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 推理与执行失败的追踪与分析
机制: 基于FailureTracker类实现，包含10个核心方法
定位: 推理治理层
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any

from nucleus.data.DataAccessLayer import safe_read_json, safe_write_json
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("失败计数器")

_DEFAULT_ESCALATE_THRESHOLD = 3  # 同一问题失败 3 次即升级
# ★158-α-2：自生成代码失败率阈值（失败次数 / 总尝试次数）。
#   与 SafeEvolutionExecutor._dynamic_llm_min_trust 的 rule_bad 判定（规则失败率>=67%）
#   口径一致，避免双标准。失败率超阈值且失败>=2 次时 WARNING 告警。
_FAILURE_RATE_ALERT_THRESHOLD = 0.67  # 失败率告警阈值（0.67 = 67%）


def get_failure_tracker(project_root: str | None = None) -> FailureTracker:
    """模块级单例（与框架其他 get_xxx 模式一致）"""
    global _failure_tracker
    if _failure_tracker is None:
        _failure_tracker = FailureTracker(project_root)
    return _failure_tracker


def shutdown_failure_tracker():
    """复位单例（退出时调用，避免重启复用旧状态）"""
    global _failure_tracker
    _failure_tracker = None


_failure_tracker: FailureTracker | None = None


class FailureTracker:
    """失败计数器 + 升级策略"""

    def __init__(self, project_root: str | None = None):
        self._project_root = project_root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self._data_dir = os.path.join(self._project_root, "data", "patches")
        self._state_file = os.path.join(self._data_dir, "failure_tracker.json")
        self._escalate_threshold = _DEFAULT_ESCALATE_THRESHOLD
        self._failure_rate_threshold = _FAILURE_RATE_ALERT_THRESHOLD
        self._state: dict[str, Any] = {"records": {}}
        self._load()

    # ========== 持久化 ==========
    def _load(self):
        try:
            if os.path.exists(self._state_file):
                _raw = safe_read_json(self._state_file, default={})
                if isinstance(_raw, dict) and isinstance(_raw.get("records"), dict):
                    self._state = _raw
        except Exception as _e:
            _module_logger.debug(f"失败计数器加载失败（使用空状态）: {_e}")

    def _save(self):
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            safe_write_json(self._state_file, self._state, indent=2)
        except Exception as _e:
            _module_logger.debug(f"失败计数器保存失败: {_e}")

    # ========== 签名 ==========
    @staticmethod
    def build_signature(file: str, issue_type: str) -> str:
        """按「文件 + 问题类型」构建归一化签名（同类问题归并计数）"""
        _key = f"{os.path.basename(file)}::{issue_type}"
        return hashlib.md5(_key.encode("utf-8")).hexdigest()[:16]

    # ========== 记录接口 ==========
    def record_failure(self, signature: str, detail: str = "") -> int:
        """记录一次失败，返回累计失败次数。达到阈值自动标记升级。"""
        _now = time.time()
        _rec = self._state["records"].get(signature, {
            "failures": 0,
            "successes": 0,
            "first_failure_ts": _now,
            "last_failure_ts": _now,
            "last_detail": "",
            "escalated": False,
            "escalated_ts": 0,
        })
        _rec["failures"] += 1
        _rec["last_failure_ts"] = _now
        if detail:
            _rec["last_detail"] = detail[:300]
        if _rec["failures"] >= self._escalate_threshold and not _rec["escalated"]:
            _rec["escalated"] = True
            _rec["escalated_ts"] = _now
            _module_logger.warning(
                f"问题已连续失败 {_rec['failures']} 次达到升级阈值，标记需升级（换策略或人工介入）: {detail[:120]}"
            )
        # ★158-α-2：自生成代码失败率阈值告警——在「连续失败次数」升级之外，
        #   新增「失败率」维度（失败次数 / 总尝试次数）。失败率超阈值且失败>=2 次时
        #   WARNING，提示换策略或人工介入。叠加而非替换既有升级逻辑，计数器异常时
        #   不影响主流程（告警失败仅 debug）。
        try:
            _att = _rec["failures"] + _rec["successes"]
            _rate = _rec["failures"] / max(1, _att)
            if _rec["failures"] >= 2 and _rate >= self._failure_rate_threshold:
                _module_logger.warning(
                    f"[失败率告警] 自生成代码失败率 {_rate:.0%}（{_rec['failures']}/{_att}）"
                    f"达阈值 {self._failure_rate_threshold:.0%}，建议换策略或人工介入: {detail[:120]}")
        except Exception as _rate_e:
            _module_logger.debug(f"失败率告警计算异常（忽略）: {_rate_e}")
        self._state["records"][signature] = _rec
        self._save()
        return _rec["failures"]

    def record_success(self, signature: str):
        """问题解决后清零失败计数（避免历史失败永久阻塞后续修复尝试）"""
        _rec = self._state["records"].get(signature)
        if not _rec:
            return
        _rec["failures"] = 0
        _rec["escalated"] = False
        _rec["successes"] = _rec.get("successes", 0) + 1
        self._save()

    # ========== 查询接口 ==========
    def get_failure_count(self, signature: str) -> int:
        _rec = self._state["records"].get(signature)
        return _rec.get("failures", 0) if _rec else 0

    def should_escalate(self, signature: str) -> bool:
        _rec = self._state["records"].get(signature)
        return bool(_rec and _rec.get("escalated"))

    def get_failure_rate(self, signature: str) -> float:
        """某签名的自生成代码失败率 = 失败次数 / 总尝试次数（无记录为 0.0）。"""
        _rec = self._state["records"].get(signature)
        if not _rec:
            return 0.0
        return _rec["failures"] / max(1, _rec["failures"] + _rec["successes"])

    def get_global_failure_rate(self) -> float:
        """全局自生成代码失败率 = 总失败 / 总尝试（无记录为 0.0）。"""
        _records = self._state.get("records", {})
        _f = sum(r.get("failures", 0) for r in _records.values())
        _s = sum(r.get("successes", 0) for r in _records.values())
        if _f + _s == 0:
            return 0.0
        return _f / (_f + _s)

    def get_escalated_issues(self) -> list[dict[str, Any]]:
        """待升级问题清单（供创造者/进化驱动消费）"""
        _result = []
        for _sig, _rec in self._state["records"].items():
            if _rec.get("escalated"):
                _result.append({
                    "signature": _sig,
                    "failures": _rec.get("failures", 0),
                    "first_failure_ts": _rec.get("first_failure_ts", 0),
                    "last_failure_ts": _rec.get("last_failure_ts", 0),
                    "last_detail": _rec.get("last_detail", ""),
                })
        _result.sort(key=lambda x: x["failures"], reverse=True)
        return _result

    def get_stats(self) -> dict[str, Any]:
        """运行统计（供仪表盘/日志）"""
        _records = self._state.get("records", {})
        return {
            "total_issues": len(_records),
            "escalated_issues": sum(1 for r in _records.values() if r.get("escalated")),
            "threshold": self._escalate_threshold,
            "global_failure_rate": self.get_global_failure_rate(),
        }


if __name__ == "__main__":
    _ft = FailureTracker()
    _sig = FailureTracker.build_signature("test.py", "test_type")
    print("初始失败数:", _ft.get_failure_count(_sig))
    for _i in range(1, 4):
        _n = _ft.record_failure(_sig, f"第{_i}次失败")
        print(f"  第{_i}次 → 累计{_n}, 需升级={_ft.should_escalate(_sig)}")
    _ft.record_success(_sig)
    print("成功后 → 累计", _ft.get_failure_count(_sig), ", 需升级=", _ft.should_escalate(_sig))
