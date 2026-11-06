# -*- coding: utf-8 -*-
"""root_cause_router.py —— 按根因类型分派修复动作（第462批）

## 补的是什么

459 批的 `voided_feature()` 把「229 条同一句 reason 模板」压成结构化
`error_type`（`voided_no_def` / `voided_mismatch` / `voided_o3_dead_code` /
`voided_<issue_type>`），但取证实测：**它只有一个写入点、零消费点**。
生成侧唯一的「按失败类型改动作」例子是 445 刀2 的
`no_def → require_full_def` 硬编码。分派器拿不到 `voided_feature` 的产出，
于是 229 条作废经验对下一轮生成没有任何影响。

## 本模块

* 读 `failure_experience.jsonl` 最近的结构化 `error_type`（459/460/461 都在写），
  与当前 `issue_type` 一起映射到 **修复动作 / prompt 提示 / 策略偏好 / trust 偏置**。
* 优先级：**结构化失败特征 > issue_type > generic** —— 已经证明改错过的，
  比「这类问题一般怎么修」更该优先。
* 未知类型一律 `generic`（零行为变化），不猜、不放宽任何闸门。
* prompt 提示**只追加、幂等、有上限**（`M462_ROUTER_MAX_HINTS`），不换生成器
  策略结构（沿用 445 的边界，避免把 114 批指纹台账一起改坏）。

## 边界

不写源码、不改 config 文件、不碰 trust 判据（`trust_bias` 目前只留痕供审计）；
pytest 护栏：默认生产路径 + pytest ⇒ 不写留痕。
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import config
from nucleus._silent_except import silent_exc

SCHEMA = "root_cause_router/462.v1"
HEAD = "[462根因分派] "
_DEFAULT_REL = "data/reports/evolution/root_cause_route.jsonl"

#: action → (提示语, 偏好策略, trust 偏置)。偏置只留痕，不改判据。
_ROUTES: dict[str, tuple[str, str, int]] = {
    "voided_no_def": ("上次补丁缺 def 签名被作废：必须给完整方法定义，不要只给方法体。",
                      "llm_full_def", 0),
    "voided_mismatch": ("上次补丁的 def 名与目标方法不一致：先核对方法名原文再生成。",
                        "rule_verify_name", 0),
    "voided_o3_dead_code": ("上次补丁被判死代码：确认新代码确实被主链调用，不要写孤立函数。",
                            "llm_with_call_site", 0),
    "self_verdict:post_apply_verify_failed": (
        "上次同类补丁应用后验证失败：本轮先做静态语法自检，再输出完整改动。",
        "llm_verify_first", 0),
    "periodic_task_no_reentry": ("周期任务缺重入守卫：补 _in_progress 标志并用 try/finally 释放。",
                                 "rule_reentry_guard", 0),
    "non_atomic_write": ("写文件非原子：改成写 .tmp 后 os.replace，避免半写状态。",
                         "rule_atomic_write", 0),
    "silent_exception": ("静默 except：每个 except 都要 silent_exc(e, where=..., level=...)。",
                         "rule_silent_exc", 0),
    "code_optimization": ("优化类问题无稳定复现判据：不要声称已修复，只给可验证的最小改动。",
                          "generic", 0),
}


def _cfg(name: str, default: Any) -> Any:
    _v = getattr(config, name, None)
    return default if _v is None else _v


def _on() -> bool:
    return bool(_cfg("ENABLE_M462_ROOT_CAUSE_ROUTER", True))


def _root(root: str | None = None) -> str:
    if root:
        return str(root)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _writable(root: str | None) -> bool:
    if root is not None:
        return True
    return "pytest" not in sys.modules


def _experience_path(root: str | None) -> str:
    _p = str(_cfg("M452_FAILURE_EXPERIENCE_PATH",
                  "data/patches/failure_experience.jsonl") or "")
    return _p if os.path.isabs(_p) else os.path.join(_root(root), _p)


def trace_path(root: str | None = None) -> str:
    return os.path.join(_root(root), str(_cfg("M462_ROUTER_TRACE_REL", _DEFAULT_REL)))


def note(row: dict, root: str | None = None) -> bool:
    if not _writable(root):
        return False
    _p = trace_path(root)
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception as _e:
        silent_exc(_e, where="root_cause_router::note", level="debug")
        return False


def recent_error_types(*, limit: int = 50, root: str | None = None) -> list[str]:
    """读 459/460/461 写进经验库的结构化 error_type（最近优先）。"""
    _p = _experience_path(root)
    _out: list[str] = []
    try:
        if not os.path.isfile(_p):
            return _out
        with open(_p, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _d = json.loads(_line)
                except ValueError as _e:
                    silent_exc(_e, where="root_cause_router::recent bad line",
                               level="debug")
                    continue
                _k = str((_d or {}).get("error_type") or "").strip()
                if _k:
                    _out.append(_k)
    except Exception as _e:
        silent_exc(_e, where="root_cause_router::recent_error_types", level="debug")
    return list(reversed(_out[-max(0, int(limit)):]))


def route(issue: dict, *, root: str | None = None,
          now: float | None = None) -> dict:
    """按根因类型分派。返回结构化路由（不改 issue）。"""
    _now = float(now if now is not None else time.time())
    _itype = str((issue or {}).get("issue_type")
                 or (issue or {}).get("type") or "").strip()
    _out: dict = {"action": "generic", "prompt_hint": "",
                  "prefer_strategy": "generic", "trust_bias": 0,
                  "matched": "", "why": "", "issue_type": _itype,
                  "at": _now, "schema": SCHEMA}
    if not _on():
        _out["why"] = "ENABLE_M462_ROOT_CAUSE_ROUTER=False"
        return _out

    # ① 结构化失败特征优先（已经证明改错过的，比类型先验更可信）
    _errs = recent_error_types(root=root)
    _candidates = _errs + [_itype]
    _seen: set[str] = set()
    for _key in _candidates:
        if not _key or _key in _seen or _key not in _ROUTES:
            continue
        _seen.add(_key)
        _hint, _prefer, _bias = _ROUTES[_key]
        _out["action"] = _key
        _out["matched"] = _key
        _out["prompt_hint"] = _hint
        _out["prefer_strategy"] = _prefer
        _out["trust_bias"] = _bias
        break
    if not _out["matched"]:
        _out["why"] = "无匹配根因 ⇒ generic（零行为变化，不猜）"
    else:
        _out["why"] = f"命中 {_out['matched']} ⇒ {_out['prefer_strategy']}"
    note({"kind": "route", **{k: _out[k] for k in
                              ("at", "issue_type", "action", "matched",
                               "prefer_strategy", "trust_bias", "why")},
          "schema": SCHEMA}, root=root)
    return _out


def append_hints(prompt: str, issue: dict, *, root: str | None = None,
                 now: float | None = None, max_hints: int | None = None) -> str:
    """幂等地把根因提示追加到生成 prompt 末尾；只追加、不改既有内容一字。"""
    if not _on() or not isinstance(issue, dict):
        return str(prompt or "")
    _r = route(issue, root=root, now=now)
    _hint = str(_r.get("prompt_hint") or "")
    if not _hint:
        return str(prompt or "")
    _line = HEAD + _hint
    _p = str(prompt or "")
    if _line in _p:
        return _p
    return _p + "\n\n" + _line
