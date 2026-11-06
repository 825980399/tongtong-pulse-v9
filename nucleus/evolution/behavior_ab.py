# -*- coding: utf-8 -*-
"""behavior_ab.py —— 行为 A/B 开关回退（第462批）

## 与现有 A/B 的区别（别混）

`perf_delta_probe.py`（428 刀3）的 A/B 是**性能**沙箱：同一文件改前/改后在
子进程里各测一次导入耗时 / P95 / 内存。它回答「改完变慢了吗」。
本模块回答的是另一件事：**「这个开关到底有没有造成行为差异」** —— 同一 case
在开关关/开各跑一次**同一探针**，比对输出，锁定唯一变量。

路灯改码时每批都这么做：458 批把 `ENABLE_M458_EXPERIENCE_NO_DELEGATE` 关/开
各跑一次 t146，用「同样 4 red / 10 passed」锁定唯一变量，才敢改规格断言。
闭环内此前没有这个能力。

## 为什么必须要求「判别力」

如果关/开两次输出**相同**，说明这个开关对该 case 没有判别力 —— 此时拿
开关后的结果当证据就是自欺（铁律35：判据要挑有判别力的量，在旧代码上也
成立的自欺）。所以 `ab_compare()` 返回 `discriminative`，`collect_evidence()`
只在它为 True 时才允许据此提权。

## 安全边界

* `try/finally` 必须恢复开关原值；原本不存在的属性用 delattr 清掉。
* 不写源码、不改 config 文件、不起子进程（探针是纯静态 AST，只读）。
* 任一侧异常 ⇒ `discriminative=False` 并留原因，绝不把异常伪装成通过。
"""
from __future__ import annotations

import time
from typing import Any

import config
from nucleus._silent_except import silent_exc

SCHEMA = "behavior_ab/462.v1"


def _cfg(name: str, default: Any) -> Any:
    _v = getattr(config, name, None)
    return default if _v is None else _v


def ab_compare(probe_fn: Any, *, switch_obj: Any, switch_name: str,
               off_value: Any = False, on_value: Any = True,
               case: Any = None, now: float | None = None) -> dict:
    """同一 case 在开关关/开各跑一次同一探针，比对输出。

    Returns:
        ``{"ok", "discriminative", "off", "on", "restored", "why", "switch", "at"}``
    """
    _now = float(now if now is not None else time.time())
    _out: dict = {"ok": False, "discriminative": False,
                  "off": None, "on": None, "restored": False,
                  "why": "", "switch": str(switch_name), "at": _now,
                  "schema": SCHEMA}
    if not callable(probe_fn):
        _out["why"] = "probe_fn 不可调用"
        return _out
    _had = hasattr(switch_obj, switch_name)
    _orig = getattr(switch_obj, switch_name, None)
    try:
        try:
            setattr(switch_obj, switch_name, off_value)
            _out["off"] = probe_fn(case)
            setattr(switch_obj, switch_name, on_value)
            _out["on"] = probe_fn(case)
        finally:
            if _had:
                setattr(switch_obj, switch_name, _orig)
            else:
                try:
                    delattr(switch_obj, switch_name)
                except AttributeError as _e:
                    silent_exc(_e, where="behavior_ab::ab_compare delattr",
                               level="debug")
            _out["restored"] = True
        _out["ok"] = True
        _out["discriminative"] = (_out["off"] != _out["on"])
        _out["why"] = ("两侧输出不同 ⇒ 开关对该 case 有判别力"
                       if _out["discriminative"] else
                       "两侧输出相同 ⇒ 无判别力，不许据此提权")
        return _out
    except Exception as _e:
        silent_exc(_e, where="behavior_ab::ab_compare", level="debug")
        _out["why"] = f"A/B 执行异常: {type(_e).__name__}: {_e}"
        return _out


def _snippet_probe(case: Any) -> dict:
    """与 self_verdict.collect_evidence 同口径的单次静态探针输出。

    ★函数内惰性导入 self_verdict，避免 self_verdict → behavior_ab 的循环导入。
    """
    from nucleus.evolution.patch_active_reprobe import active_reprobe
    from nucleus.evolution.self_verdict import normalize_snippet

    _p = dict(case or {})
    if bool(_cfg("ENABLE_M461_SNIPPET_NORMALIZE", True)):
        _o, _ = normalize_snippet(_p.get("original_code"))
        _m, _ = normalize_snippet(_p.get("modified_code"))
        _p["original_code"] = _o
        _p["modified_code"] = _m
    _r = active_reprobe(_p)
    return {
        "verdict": str(_r.get("reprobe_verdict") or ""),
        "baseline_hits": _r.get("reprobe_baseline_hits"),
        "after_hits": _r.get("reprobe_after_hits"),
    }


def probe_snippet_switch(patch: dict, *, now: float | None = None) -> dict:
    """专用于 461 片段归一化的 A/B：关/开各跑一次，验证开关确有判别力。

    off 侧预期 `false_pass`（片段解析不到完整函数），on 侧预期 `true_pass`。
    """
    return ab_compare(_snippet_probe, switch_obj=config,
                      switch_name="ENABLE_M461_SNIPPET_NORMALIZE",
                      off_value=False, on_value=True,
                      case=patch, now=now)


def require_discriminative() -> bool:
    return bool(_cfg("M462_AB_REQUIRE_DISCRIMINATIVE", True))
