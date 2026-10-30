# -*- coding: utf-8 -*-
"""★403 刀2：可观测快照分频（热 5s / 温 60s / 冷 300s）。

问题
----
`health_snapshot.json` 每 5s 重建一次，而里面混着三类成本完全不同的指标：
内存/心跳这类**本来就该 5s**；器官状态/链路统计要遍历器官与订阅条件；
知识库大小/备份数/补丁积压这类**几分钟才变一次**，却每 5s 去 glob 目录、
读一次 `pending_patches.json`。热路径被冷指标拖着跑。

分频口径
--------
* **热**（留在 `health_snapshot.json`，5s）：内存、GC、线程/句柄、心跳、脉冲/信息场计数、
  梦境/反思/肝脏/情绪/生命状态等内存缓存读数（本来就零 IO）。
* **温**（`health_snapshot_hourly.json`，60s）：器官状态、节点池、链路订阅、
  控制器历史统计、请求去重、兴趣 TOP。
* **冷**（同文件，300s）：知识演化、快照备份数、待重修补丁积压。

为什么热文件里**不保留**温/冷键
--------------------------------
保留就等于"拆了但没拆"：读方仍会读到 5s 一刷的旧值，且体积不变。
所以热文件只留 `tiers` 元信息（温/冷的刷新时刻与年龄、hourly 文件路径），
读方统一走 `read_merged()` 合并——读接口不变，文件结构变。

为什么合并逻辑放在这里而不是各读方自己写
----------------------------------------
读方有三处（health_ui 服务端出口、runtime_state_writer、expectation_ledger），
各写一遍必然出现"某一处忘了合并"的半盲区。统一入口 + 缺失时降级返回热文件。
"""
from __future__ import annotations

import json
import os

from nucleus._silent_except import silent_exc

SCHEMA = "snapshot_tiers/403.v1"

#: 温指标（60s 刷新）
WARM_KEYS = ("nodes", "organs", "link_status", "controller",
             "request_dedup", "top_interests")
#: 冷指标（300s 刷新）
COLD_KEYS = ("knowledge_evolution", "snapshot_backups", "patch_needs_repair")

WARM_INTERVAL_S = 60.0
COLD_INTERVAL_S = 300.0

HOURLY_NAME = "health_snapshot_hourly.json"


def valid_keys() -> tuple:
    """温/冷键的并集（读方用它判断"这个键是否被搬走了"）。"""
    return tuple(WARM_KEYS) + tuple(COLD_KEYS)


def hourly_path(health_path: str, explicit: str | None = None) -> str:
    """温冷快照路径：与热文件同目录，固定名 `health_snapshot_hourly.json`。"""
    if explicit:
        return explicit
    return os.path.join(os.path.dirname(os.path.abspath(health_path)), HOURLY_NAME)


def split(snapshot: dict) -> tuple:
    """把一份完整快照切成 ``(hot, warm, cold)`` 三份（都是新 dict，不改原对象）。"""
    _snap = snapshot or {}
    _warm = {k: _snap[k] for k in WARM_KEYS if k in _snap}
    _cold = {k: _snap[k] for k in COLD_KEYS if k in _snap}
    _hot = {k: v for k, v in _snap.items() if k not in _warm and k not in _cold}
    return _hot, _warm, _cold


def build_hot(snapshot: dict, warm_at: float, cold_at: float,
              now: float | None = None,
              warm_path: str | None = None) -> dict:
    """生成热文件内容：热键 + `tiers` 元信息（温/冷只留年龄，不留值）。"""
    import time as _t
    _now = float(now if now is not None else _t.time())
    _hot, _warm, _cold = split(snapshot)
    _hot["tiers"] = {
        "schema": SCHEMA,
        "hot_interval_s": 5.0,
        "warm_interval_s": WARM_INTERVAL_S,
        "cold_interval_s": COLD_INTERVAL_S,
        "warm_age_s": round(max(0.0, _now - float(warm_at or 0.0)), 1),
        "cold_age_s": round(max(0.0, _now - float(cold_at or 0.0)), 1),
        "warm_keys": list(WARM_KEYS),
        "cold_keys": list(COLD_KEYS),
        "hourly_file": warm_path or HOURLY_NAME,
        # 元信息也要控体积：热文件 5s 写一次，每多 100 字节就是每天多写 1.7MB
        "note": "温冷已移至 hourly；读方走 read_merged()，不得把缺失当 0",
    }
    return _hot


def build_hourly(warm: dict, cold: dict, warm_at: float, cold_at: float,
                 now: float | None = None) -> dict:
    """生成温冷文件内容。"""
    import time as _t
    _now = float(now if now is not None else _t.time())
    return {
        "schema": SCHEMA,
        "timestamp": round(_now, 3),
        "warm_at": round(float(warm_at or 0.0), 3),
        "cold_at": round(float(cold_at or 0.0), 3),
        "warm_age_s": round(max(0.0, _now - float(warm_at or 0.0)), 1),
        "cold_age_s": round(max(0.0, _now - float(cold_at or 0.0)), 1),
        "warm": dict(warm or {}),
        "cold": dict(cold or {}),
    }


def read_json(path: str, default=None):
    """读 JSON（不存在/坏文件都返回 default，不抛）。"""
    if default is None:
        default = {}
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as _f:
            return json.load(_f)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="utils.snapshot_tiers.read_json", level="warning")
        return default


def read_merged(health_path: str, hourly: str | None = None) -> dict:
    """读热文件并合并温/冷块，返回**与拆分前同构**的快照。

    降级：hourly 缺失/坏掉 ⇒ 只返回热文件（`tiers.merge_source="hot_only"`），
    绝不把缺失的温/冷键填 0（没测到 ≠ 真 0）。
    """
    _hot = read_json(health_path, {})
    if not isinstance(_hot, dict) or not _hot:
        return {}
    _tiers = _hot.get("tiers") if isinstance(_hot.get("tiers"), dict) else None
    if not _tiers:
        return _hot  # 旧格式（未分频），原样返回
    _hp = hourly
    if not _hp:
        _name = str(_tiers.get("hourly_file") or HOURLY_NAME)
        _hp = _name if os.path.isabs(_name) else os.path.join(
            os.path.dirname(os.path.abspath(health_path)), os.path.basename(_name))
    _h = read_json(_hp, {})
    if not isinstance(_h, dict) or not _h:
        _hot.setdefault("tiers", {})["merge_source"] = "hot_only"
        return _hot
    _merged = dict(_hot)
    for _k, _v in (_h.get("warm") or {}).items():
        _merged.setdefault(_k, _v)
    for _k, _v in (_h.get("cold") or {}).items():
        _merged.setdefault(_k, _v)
    _merged["tiers"] = dict(_tiers)
    _merged["tiers"]["merge_source"] = "hot+hourly"
    _merged["tiers"]["hourly_warm_age_s"] = _h.get("warm_age_s")
    _merged["tiers"]["hourly_cold_age_s"] = _h.get("cold_age_s")
    return _merged
