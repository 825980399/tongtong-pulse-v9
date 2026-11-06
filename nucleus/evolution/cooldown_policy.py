# -*- coding: utf-8 -*-
"""cooldown_policy.py —— 僵尸冷却判据分级（第463批）

## 修的是什么（实盘取证，不是推测）

10:54 第 1 轮：``[P1-3 冷却隔离] 候选15个 → 放行1个（冷却跳过14个／到期解冻0个）``
→ ``[修复蒸馏] 共1个问题`` → ``[自主进化] 修复完成: 0/15个，处理1个``。

根因在 ``SafeEvolutionExecutor._cooldown_classify()``：**只看 type 是否在 15 类
本地规则白名单里**，不在就一律判「不可修复·本地无规则转LLM」并冷却 21600s。
而 `periodic_task_no_reentry` / `non_atomic_write` 这类问题，446 刀1 的检测器
早已覆盖、462 的证据链能给出 8/8 —— 它们恰恰是**能产出补丁**的，却被关在门外。
更糟的是登记发生在循环结束后对**所有 processed 问题**（不管本轮是否真的尝试过
修复），每轮重登记都把 deadline 推到 ``now+TTL`` ⇒ 永不过期（实盘「解冻 0 个」）。

## 本模块的契约

* 判据从「type 在不在白名单」改成「**这条问题有没有可复现的修复路径**」：
  本地规则可修、446 检测器可复现的代码类 ⇒ `skip_cool`（继续参与生成）；
  高危安全拦截 / 无代码定位的日志类 ⇒ `cool`；其余代码类 ⇒ `cool_short`。
* 纯函数、无副作用，便于单测与 A/B；调用方仍负责不刷新已有冷却与强制复检。
* 未知类型不猜：默认 `cool_short`（短冷却），绝不 `skip_cool`。
"""
from __future__ import annotations

from typing import Any

from nucleus._silent_except import silent_exc

SCHEMA = "cooldown_policy/463.v1"

A_SKIP = "skip_cool"
A_COOL = "cool"
A_COOL_SHORT = "cool_short"

#: ★446 刀1 检测器注册表覆盖的六类（对**代码片段**可给出 true_pass / baseline>0）
REPRO_COVERED: frozenset[str] = frozenset({
    "silent_exception", "print_instead_of_log", "code_optimization",
    "periodic_task_no_reentry", "non_atomic_write",
    "cross_module_singleton_call",
})


def _t(issue: dict) -> str:
    return str(issue.get("type") or issue.get("issue_type") or "").strip()


def _is_code_issue(issue: dict) -> bool:
    return bool(str(issue.get("file") or "").strip()
                and str(issue.get("method") or "").strip())


def classify(issue: dict, *, local_fixable_types: Any = None,
             high_risk_types: Any = None) -> dict:
    """返回 ``{"action", "why", "is_code_issue", "type", "schema"}``。

    Args:
        local_fixable_types: 调用方的本地高置信度规则白名单（集合）。
        high_risk_types: 安全边界故意不修的类型集合（集合）。
    """
    _local = set(local_fixable_types or ())
    _high = set(high_risk_types or ())
    _type = _t(issue)
    _code = _is_code_issue(issue)
    _out = {"action": A_COOL_SHORT, "why": "本地无规则·转LLM",
            "is_code_issue": _code, "type": _type, "schema": SCHEMA}

    if _type in _high:
        _out["action"] = A_COOL
        _out["why"] = "高危·安全拦截"
        return _out
    if _type in _local:
        _out["action"] = A_SKIP
        _out["why"] = "本地规则可修"
        return _out
    if _code and _type in REPRO_COVERED:
        _out["action"] = A_SKIP
        _out["why"] = "446检测器可复现·应继续生成"
        return _out
    if not _code:
        _out["action"] = A_COOL
        _out["why"] = "日志类·无代码定位"
        return _out
    return _out


def should_force_recheck(hits: int, *, need: int = 3) -> bool:
    """★463 刀2：累计被冷却跳过达 ``need`` 次 ⇒ 强制放行一次复检。

    纯函数，供冷却扫描与单测共用**同一口径**（避免内联判据漂移）。
    """
    try:
        return int(hits) >= max(1, int(need))
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="cooldown_policy::should_force_recheck", level="debug")
        return False


def priority(issue: dict, *, local_fixable_types: Any = None) -> int:
    """排序键（越小越优先）：可修的代码类排在日志类之前。"""
    if not isinstance(issue, dict):
        return 99
    _local = set(local_fixable_types or ())
    _type = _t(issue)
    if _type in _local:
        return 0
    if _is_code_issue(issue) and _type in REPRO_COVERED:
        return 1
    if _is_code_issue(issue):
        return 2
    return 9
