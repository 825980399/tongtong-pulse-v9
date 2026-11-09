# -*- coding: utf-8 -*-
"""★485刀2（T-断网判定）：LLM 降级窗口状态文件 —— cortex 写、指标采集器读。

## 为什么经文件而不直连

`PulseMetricsCollector`（health_snapshot.json 的唯一构建方）**不持有 cortex 引用**
（构造器只装配 info_field / node_pool 等，实测 organs/core/PulseMetricsCollector.py
`__init__`），跨器官直取引用属于装配面改造，超出 485 批范围。本仓已有「生产者写
data/monitor 状态文件、快照构建方按需读」的先例（`_m421_patch_chain_snapshot` 读
data/patches、`hit_rate` 读 health_snapshot 自身），本模块沿用该模式。

## 语义

* ``degraded=True`` ⇒ 当前处于 LLM 降级窗口（`llm_unavailable_until` 未到），
  期间 cortex 对话兜底直接走本地链 + canned 话术，不再委派外脑（485-2）。
* 文件不存在 / 解析失败 ⇒ ``None``（**没测到 ≠ 真 0**，铁律 102：读不到不填假值）。
* 只在状态**变化**时写盘（cortex 侧控制调用频率），5s 快照读文件成本可忽略。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc

_STATE_REL = os.path.join("data", "monitor", "llm_degraded_state.json")


def _state_path(root: str | None = None) -> str:
    if root:
        return os.path.join(str(root), _STATE_REL)
    return os.path.join("data", "monitor", "llm_degraded_state.json")


def write_llm_degraded_state(
    degraded: bool,
    until: float | None = None,
    reason: str = "",
    root: str | None = None,
) -> bool:
    """写入/清除降级状态（**原子写**：tmp + os.replace，失败返回 False 不抛）。

    Args:
        degraded: 是否处于降级窗口。
        until: 窗口截止时间戳（``time.time()`` 口径）；``degraded=False`` 时忽略。
        reason: 进入降级的原因摘要（如 "3 failures in 300s"）。
        root: 项目根（测试注入用；缺省为 cwd 相对，与仓内其他 data/ 写点同口径）。
    """
    try:
        _p = _state_path(root)
        _dir = os.path.dirname(_p)
        if _dir and not os.path.isdir(_dir):
            os.makedirs(_dir, exist_ok=True)
        _doc: dict[str, Any] = {
            "degraded": bool(degraded),
            "until": float(until) if (degraded and until is not None) else None,
            "reason": str(reason or ""),
            "updated_at": round(time.time(), 3),
        }
        _tmp = _p + ".m485.tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            json.dump(_doc, _f, ensure_ascii=False)
        os.replace(_tmp, _p)
        return True
    except (OSError, TypeError, ValueError) as _e:
        silent_exc(_e, where="nucleus.offline_state::write_llm_degraded_state",
                   level="debug")
        return False


def read_llm_degraded_state(root: str | None = None) -> dict[str, Any] | None:
    """读降级状态。文件缺失 / 损坏 / 字段非法 ⇒ ``None``（不填假 0）。"""
    _p = _state_path(root)
    if not os.path.isfile(_p):
        return None
    try:
        with open(_p, encoding="utf-8") as _f:
            _d = json.load(_f)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="nucleus.offline_state::read_llm_degraded_state",
                   level="debug")
        return None
    if not isinstance(_d, dict) or not isinstance(_d.get("degraded"), bool):
        return None
    return _d
