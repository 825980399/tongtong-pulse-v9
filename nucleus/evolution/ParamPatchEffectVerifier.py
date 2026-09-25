# -*- coding: utf-8 -*-
"""
ParamPatchEffectVerifier.py —— 参数补丁效果验证器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 验证参数补丁的效果与副作用
机制: 函数式模块，包含2个工具函数
定位: 进化验证层
"""

from nucleus._silent_except import silent_exc
import os
import re
import time
from datetime import datetime
from typing import Any
from nucleus.evolution.LogAnalyzer import extract_log_level  # ★主线第30批 T2：真实日志级别解析


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def verify_patch_effect_in_process(patch_info: dict, log_file: str | None = None,
                                    before_window: int = 300,
                                    after_window: int = 300) -> dict[str, Any]:
    """
    在独立进程中执行的参数补丁效果验证函数。

    这个函数会被ReasoningWorkerPool调用，在独立进程中执行，
    避免大量日志分析阻塞主进程。

    Args:
        patch_info: 补丁信息 {id, param, applied_at, ...}
        log_file: 日志文件路径
        before_window: 应用前分析窗口（秒）
        after_window: 应用后分析窗口（秒）

    Returns:
        验证结果 {patch_id, param, before_metrics, after_metrics, delta, effect, recommendation}
    """
    if log_file is None:
        log_file = os.path.join(_PROJECT_ROOT, "logs", "pulse.log")

    result = {
        "patch_id": patch_info.get("id"),
        "param": patch_info.get("param"),
        "applied_at": patch_info.get("applied_at"),
        "before_metrics": {},
        "after_metrics": {},
        "delta": {},
        "effect": "unknown",
        "recommendation": "observe",
        "error": None,
    }

    try:
        if not os.path.exists(log_file):
            result["error"] = f"日志文件不存在: {log_file}"
            return result

        apply_time = patch_info.get("applied_at", time.time())
        before_start = apply_time - before_window
        before_end = apply_time - 10  # 应用前10秒到应用前before_window秒
        after_start = apply_time + 10  # 应用后10秒开始
        after_end = apply_time + after_window

        # 解析日志
        before_errors = 0
        before_warnings = 0
        before_info = 0
        before_debug = 0
        after_errors = 0
        after_warnings = 0
        after_info = 0
        after_debug = 0

        # 关键事件统计
        before_key_events = {}
        after_key_events = {}
        key_event_patterns = [
            (r"大模型调用", "llm_call"),
            (r"补救触发", "remediation"),
            (r"搜索.*成功", "search_success"),
            (r"消化完成", "digest_complete"),
            (r"知识.*存储", "knowledge_store"),
            (r"蒸馏完成", "distill_complete"),
            (r"ERROR", "error"),
            (r"WARNING", "warning"),
        ]

        with open(log_file, encoding="utf-8", errors="ignore") as f:
            for line in f:
                # 解析时间戳
                m = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                if not m:
                    continue
                try:
                    t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
                except Exception as e:
                    silent_exc(e, "ParamPatchEffectVerifier.py:101:verify_patch_effect_in_process", level="warning")
                    continue

                # 确定时间段
                if before_start <= t <= before_end:
                    # ★主线第30批 T2：按真实级别标记判定（无标记时回退原宽松判据）
                    _lv30 = extract_log_level(line)
                    _is_err30 = (_lv30 in ("ERROR", "CRITICAL")) if _lv30 else ("ERROR" in line)
                    _is_warn30 = (_lv30 == "WARNING") if _lv30 else ("WARNING" in line)
                    if _is_err30:
                        before_errors += 1
                    elif _is_warn30:
                        before_warnings += 1
                    elif "INFO" in line:
                        before_info += 1
                    elif "DEBUG" in line:
                        before_debug += 1
                    for pattern, key in key_event_patterns:
                        if re.search(pattern, line):
                            before_key_events[key] = before_key_events.get(key, 0) + 1
                elif after_start <= t <= after_end:
                    # ★主线第30批 T2：同上，按真实级别标记判定
                    _lv30a = extract_log_level(line)
                    _is_err30a = (_lv30a in ("ERROR", "CRITICAL")) if _lv30a else ("ERROR" in line)
                    _is_warn30a = (_lv30a == "WARNING") if _lv30a else ("WARNING" in line)
                    if _is_err30a:
                        after_errors += 1
                    elif _is_warn30a:
                        after_warnings += 1
                    elif "INFO" in line:
                        after_info += 1
                    elif "DEBUG" in line:
                        after_debug += 1
                    for pattern, key in key_event_patterns:
                        if re.search(pattern, line):
                            after_key_events[key] = after_key_events.get(key, 0) + 1

        # 计算指标
        result["before_metrics"] = {
            "errors": before_errors,
            "warnings": before_warnings,
            "info": before_info,
            "debug": before_debug,
            "key_events": before_key_events,
        }
        result["after_metrics"] = {
            "errors": after_errors,
            "warnings": after_warnings,
            "info": after_info,
            "debug": after_debug,
            "key_events": after_key_events,
        }

        # 计算变化
        error_delta = after_errors - before_errors
        warning_delta = after_warnings - before_warnings
        result["delta"] = {
            "errors": error_delta,
            "warnings": warning_delta,
            "info": after_info - before_info,
            "debug": after_debug - before_debug,
        }

        # 效果判定
        if error_delta < -2:
            result["effect"] = "significantly_improved"
            result["recommendation"] = "keep"
        elif error_delta < 0:
            result["effect"] = "improved"
            result["recommendation"] = "keep"
        elif error_delta == 0 and warning_delta <= 0:
            result["effect"] = "neutral"
            result["recommendation"] = "keep"
        elif error_delta > 5:
            result["effect"] = "significantly_degraded"
            result["recommendation"] = "rollback"
        elif error_delta > 0:
            result["effect"] = "degraded"
            result["recommendation"] = "observe"
        else:
            result["effect"] = "uncertain"
            result["recommendation"] = "observe"

    except Exception as e:
        result["error"] = str(e)

    return result


def verify_multiple_patches(patches: list[dict], log_file: str | None = None) -> list[dict]:
    """
    批量验证多个补丁的效果（在独立进程中执行）。

    Args:
        patches: 补丁信息列表
        log_file: 日志文件路径

    Returns:
        验证结果列表
    """
    results = []
    for patch in patches:
        result = verify_patch_effect_in_process(patch, log_file)
        results.append(result)
    return results


# 导出可被ReasoningWorkerPool调用的函数名
EXPORTED_FUNCTIONS = [
    "verify_patch_effect_in_process",
    "verify_multiple_patches",
]
