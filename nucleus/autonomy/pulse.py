# -*- coding: utf-8 -*-
"""474 批第三期：**自主性观测节拍接线**（把第二期的旁路接进主循环）。

接线的是什么
------------
第二期交付了 `ReasoningObserver.observe()`，但它只是「可调用的旁路」——没人调就永远不会跑。
本模块提供 `autonomy_observe_tick()`，由 `main.py` 主循环每拍调用，内部按墙钟
300s 自节流（与 422 周期 apply 同口径），做且只做三件事：

  1. 从 `verification_learning_hub` 取**只读快照**（既有记录，不新增、不修改）；
  2. 交给 `ReasoningObserver` 归一成结构化经验，写入自有台账
     `data/autonomy/experience.jsonl`（该目录已被 `/data/` ignore）；
  3. 产出 `reasoning` 域的「推理自给率 / 外部依赖率」。

为什么这样接是安全的
--------------------
* **零生产副作用**：全程只读 hub、只写自有台账；不触碰八闸、审批、补丁应用、
  自重启、日限额、冷却等任何既有判定 ⇒ 不需要任务书改安全边界。
* **绝不外联**（铁律 304）：本模块不 import 任何 HTTP / 模型客户端，
  覆盖不了的对象也只是"没观测到"，不会去问外部 LLM。
* **异常绝不外溢**：`autonomy_observe_tick()` 自己吞掉全部异常；
  `main.py` 调用点再包一层 try ⇒ 观测坏了最多没数据，主循环照跑。
* **日志不决定返回值**（铁律 40）：所有留痕都在返回 dict 算完之后。
* **灰度可逐字回滚**：`ENABLE_M474_AUTONOMY_PULSE = False` ⇒ 不装配、不采样、
  不写台账、不打印，与第三期之前的行为完全一致。

★`_cfg_bool` 复用 kernel 的单一实现：`None`=没配（用缺省），`0`/`False`=显式关，
  绝不能写成 `getattr(cfg, X, True) or True` —— 那会把 0 吞成 True（铁律：getattr 0 陷阱）。
"""
from __future__ import annotations

import copy
import logging
import time
from typing import Any

from nucleus._silent_except import silent_exc

from .kernel import _cfg as _cfg_bool
from .reasoning import ReasoningObserver

_LOGGER = logging.getLogger("pulse")

_DEFAULT_INTERVAL_SEC = 300
_DEFAULT_MAX_BATCH = 200

# ★模块级单例：跨节拍保留 `_seen` 去重与累计计数。绝不能每拍重建 observer，
#   否则同一条 hub 记录会被反复记成"新经验"（铁律 55：状态位反复入队必须配上限）。
_STATE: dict = {"last_ts": 0.0, "runs": 0}
_OBSERVER: ReasoningObserver | None = None


def _cfg_int(name: str, default: int, lo: int, hi: int) -> int:
    """数字配置；**只有 None 才算缺省**，非法值夹到 [lo, hi] 而不是崩掉节拍。"""
    try:
        import config as _c
        _v = getattr(_c, name, None)
        if _v is None:
            return default
        return max(lo, min(hi, int(_v)))
    except Exception as _e:
        silent_exc(_e, where="autonomy.pulse::_cfg_int", level="debug")
        return default


def read_hub_entries(hub: Any, max_batch: int = 0) -> list:
    """取 hub 记录的**只读深拷贝快照**。绝不修改 hub、绝不返回内部对象引用。

    优先走公开口 `entries_snapshot()`（若 hub 未来提供）；没有则退回既有的
    `_entries` + `_lock`。后者与 hub 自己的 `get_stats()` 读的是同一份数据，
    所以基准是可靠的；加锁是 3.14t free-threading 下的硬要求（无 GIL）。
    """
    _snap = getattr(hub, "entries_snapshot", None)
    if callable(_snap):
        _rows = _snap()
    else:
        _lock = getattr(hub, "_lock", None)
        if _lock is not None and hasattr(_lock, "__enter__"):
            with _lock:
                _rows = list(getattr(hub, "_entries", ()) or ())
        else:
            _rows = list(getattr(hub, "_entries", ()) or ())
    _out = [copy.deepcopy(_r) for _r in _rows if isinstance(_r, dict)]
    if max_batch > 0 and len(_out) > max_batch:
        _out = _out[-max_batch:]
    return _out


def _get_observer() -> ReasoningObserver:
    """惰性装配：kernel 默认影子模式（enforce=False），只借它的台账与指标。"""
    global _OBSERVER
    if _OBSERVER is None:
        from .kernel import CapabilityKernel
        _OBSERVER = ReasoningObserver(CapabilityKernel())
    return _OBSERVER


def autonomy_observe_tick(now: float | None = None, hub: Any = None,
                          force: bool = False) -> dict:
    """主循环每拍调用；内部按墙钟自节流。**任何异常都不外溢**。

    Args:
        now:   注入墙钟（单测用）；生产不传。
        hub:   注入 hub（单测用）；生产不传 ⇒ 取框架既有单例。
        force: 绕过节流（单测用）；生产恒 False。

    Returns:
        恒为 dict，`status` ∈ {disabled, skipped_throttle, ok, error}。
        `status` 只由观测结果决定，**不由日志决定**（铁律 40）。
    """
    if not _cfg_bool("ENABLE_M474_AUTONOMY_PULSE", True):
        return {"status": "disabled"}

    _now = float(time.time() if now is None else now)
    _interval = float(_cfg_int("M474_AUTONOMY_PULSE_INTERVAL_SEC",
                               _DEFAULT_INTERVAL_SEC, 30, 86400))
    if not force and (_now - float(_STATE["last_ts"])) < _interval:
        return {"status": "skipped_throttle",
                "remain_sec": round(_interval - (_now - float(_STATE["last_ts"])), 1)}
    _STATE["last_ts"] = _now
    _STATE["runs"] += 1

    try:
        if hub is None:
            from nucleus.mnemosyne.verification_learning_hub import (
                get_verification_learning_hub,
            )
            hub = get_verification_learning_hub()
        _max_batch = _cfg_int("M474_AUTONOMY_PULSE_MAX_BATCH",
                              _DEFAULT_MAX_BATCH, 1, 2000)
        _entries = read_hub_entries(hub, max_batch=_max_batch)
        _res = _get_observer().observe(_entries)
        _snap = _res.get("kernel") or {}
        _reason_domain = (_snap.get("by_domain") or {}).get("reasoning") or {}
        _out = {
            "status": "ok",
            "new": int(_res.get("new") or 0),
            "scanned": len(_entries),
            "total": int(_res.get("total") or 0),
            "reasoning_self_sufficiency_rate": _res.get(
                "reasoning_self_sufficiency_rate"),
            "external_dependency_rate": _res.get("external_dependency_rate"),
            "kernel_runs": _snap.get("runs"),
            "domain_external_rate": _reason_domain.get("external_dependency_rate"),
            "runs": _STATE["runs"],
        }
    except Exception as _e:
        silent_exc(_e, where="autonomy.pulse::autonomy_observe_tick", level="debug")
        return {"status": "error", "error_type": type(_e).__name__,
                "runs": _STATE["runs"]}

    # ★留痕一律在返回值算完之后；日志异常绝不能影响 status / new（铁律 40）。
    try:
        _log = (_LOGGER.info if (_out["new"] > 0 or _STATE["runs"] == 1)
                else _LOGGER.debug)
        _log("[474刀3][自主性观测] 新增%d条｜扫描%d｜推理自给率=%s 外部依赖率=%s"
             "｜累计%d次观测｜域=reasoning",
             _out["new"], _out["scanned"],
             _out["reasoning_self_sufficiency_rate"],
             _out["external_dependency_rate"], _out["total"])
    except Exception as _le:
        silent_exc(_le, where="autonomy.pulse::autonomy_observe_tick log", level="debug")
    return _out


def pulse_snapshot() -> dict:
    """供观测/排障读取：节拍状态 + 累计指标（不触发采样）。"""
    _snap = _get_observer().snapshot()
    # ★`**_snap` 必须放前面：它自带 `schema=autonomy_reasoning/474.v2`，
    #   放后面会把本层的 pulse schema 覆盖掉（第三期单测实测踩到）。
    return {**_snap,
            "schema": "autonomy_pulse/474.v3",
            "reasoning_schema": _snap.get("schema"),
            "runs": _STATE["runs"],
            "last_ts": _STATE["last_ts"]}
