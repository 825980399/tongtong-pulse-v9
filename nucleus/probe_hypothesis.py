# -*- coding: utf-8 -*-
"""
probe_hypothesis.py —— 探测假设

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 硬件探测的假设生成与验证框架
机制: 函数式模块，包含2个工具函数
定位: 硬件抽象层
"""

from __future__ import annotations

from typing import Any

# ========================================================================
# 规则表：问题类型 → 关联假设
#
# 每条规则：
#   "same_organ": [相关类型列表]  —— 同 organ 出现本类型时，假设同 organ 其他方法
#                                   可能也有这些「相关类型」的问题
#
# 语义分组（异常处理 / 并发锁 / 资源 / 安全 / 健壮性）：
# ========================================================================

# 相关类型：某 type 出现时，同 organ 其他方法可能连带出现的 type
_RELATED_TYPES: dict[str, list[str]] = {
    # 异常处理反模式（成片出现）
    "silent_exception": ["bare_except", "bare_return_none_in_except"],
    "bare_except": ["silent_exception", "bare_return_none_in_except"],
    "bare_return_none_in_except": ["silent_exception", "bare_except"],
    # 并发/锁反模式
    "lock_with_emit": ["thread_no_daemon", "unjoined_thread"],
    "thread_no_daemon": ["unjoined_thread", "lock_with_emit"],
    "unjoined_thread": ["thread_no_daemon", "lock_with_emit"],
    # 资源管理反模式
    "resource_no_close": ["no_timeout_http", "unbounded_deque"],
    "no_timeout_http": ["resource_no_close", "subprocess_shell"],
    # 安全反模式
    "sql_injection": ["subprocess_shell", "unsafe_eval"],
    "unsafe_eval": ["sql_injection", "subprocess_shell"],
    "subprocess_shell": ["unsafe_eval", "sql_injection"],
    # 健壮性反模式
    "busy_loop_no_exit": ["periodic_task_no_reentry"],
    "periodic_task_no_reentry": ["busy_loop_no_exit"],
}

# 同型扩散：默认所有已知反模式类型，只要在某 organ 出现，就假设同 organ 其他方法可能也有同型
_SAME_TYPE_CANDIDATES: set[str] = {
    "silent_exception", "bare_except", "bare_return_none_in_except",
    "lock_with_emit", "thread_no_daemon", "unjoined_thread",
    "resource_no_close", "no_timeout_http", "unbounded_deque",
    "sql_injection", "unsafe_eval", "subprocess_shell",
    "busy_loop_no_exit", "periodic_task_no_reentry",
    "long_method", "status_request_duplicate", "non_atomic_write",
    "cross_module_singleton_call",
}


def _dedupe(items: list[str]) -> list[str]:
    """保序去重。"""
    _seen: set[str] = set()
    _out: list[str] = []
    for _i in items:
        if _i not in _seen:
            _seen.add(_i)
            _out.append(_i)
    return _out


def generate_hypotheses(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从已发现的 issue 列表生成「定向验证假设」。

    每条假设：
        {
            "organ": str,            # 假设要定向探查的 organ（类/文件所属器官）
            "file": str,             # 假设要定向探查的文件
            "target_types": [str],   # 假设该 organ 可能存在的关联问题类型
            "triggered_by": str,     # 触发该假设的源 issue 类型
            "trigger_file": str,     # 触发该假设的源 issue 文件
            "rule": str,             # 命中的规则名（same_organ / related_type）
        }

    规则逻辑：
        - 同型扩散：某 organ 出现 type X → 假设同 organ 其他方法也可能有 X。
        - 相关类型：某 organ 出现 type X → 假设同 organ 可能有 _RELATED_TYPES[X]。
    输出按 organ 去重合并。
    """
    if not isinstance(issues, list):
        return []

    # organ -> 触发的源类型集合（用于同型扩散 + 相关类型）
    _organ_types: dict[str, set[str]] = {}
    # organ -> (file, 触发详情) 用于溯源
    _organ_meta: dict[str, dict[str, str]] = {}

    for _issue in issues:
        if not isinstance(_issue, dict):
            continue
        _organ = str(_issue.get("organ", "") or "")
        _file = str(_issue.get("file", "") or "")
        _type = str(_issue.get("type", "unknown") or "unknown")
        if not _organ and not _file:
            continue
        # 用 organ 或 file 作为分组键（issue 可能缺 organ）
        _key = _organ or _file
        _organ_types.setdefault(_key, set()).add(_type)
        if _key not in _organ_meta:
            _organ_meta[_key] = {"organ": _organ, "file": _file, "trigger": _type}

    _hypotheses: dict[str, dict[str, Any]] = {}

    for _key, _types in _organ_types.items():
        _meta = _organ_meta[_key]
        _target_types: list[str] = []
        for _t in _types:
            # 同型扩散：已知反模式类型，假设同 organ 其他方法也有同型
            if _t in _SAME_TYPE_CANDIDATES:
                _target_types.append(_t)
            # 相关类型：语义关联的类型
            for _rel in _RELATED_TYPES.get(_t, []):
                _target_types.append(_rel)
        _target_types = _dedupe(_target_types)

        if not _target_types:
            continue

        _hypotheses[_key] = {
            "organ": _meta.get("organ", ""),
            "file": _meta.get("file", ""),
            "target_types": _target_types,
            "triggered_by": _meta.get("trigger", ""),
            "trigger_file": _meta.get("file", ""),
            "rule": "same_organ_related",
        }

    return list(_hypotheses.values())


__all__ = [
    "_RELATED_TYPES",
    "_SAME_TYPE_CANDIDATES",
    "generate_hypotheses",
]
