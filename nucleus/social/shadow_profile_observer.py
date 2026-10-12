# -*- coding: utf-8 -*-
"""社会层 刀A：影子画像观测（内存态、零落盘、零LLM）。

以 ``nucleus.field.request_dedup_observe.get_current_user()`` 解析出的用户标识为键，
在对话/推理反思点聚合三维观测：
  · address   —— 当前以何种身份称呼（用户键本身；刀B 再细化昵称/尊称偏好）
  · summary   —— 最近一次交互摘要（截断后的 prompt）
  · freq      —— 累计观测次数（频次）

仅内存态，进程退出即弃；不写盘、不调 LLM，不新增任何伦理采集面积。
INFO 级探针用于可观测性（同 185卫生刀 / 187探针 法）。
"""
from __future__ import annotations

import time
from typing import Any, Dict

from nucleus.logger import get_module_logger

_log = get_module_logger("social.shadow_profile")

_PROFILES: Dict[str, Dict[str, Any]] = {}
_MAX_SUMMARY = 200


def observe(user: str | None = None, *, summary: str | None = None,
            address_pref: str | None = None) -> Dict[str, Any] | None:
    """以 user 为键聚合一次观测（内存态）。

    返回更新后的画像副本；user 解析为空（"?" / None）则忽略并返回 None。
    三维：address（称呼键）/ summary（交互摘要）/ freq（频次）。
    """
    _key = (user or "").strip() or "?"
    if _key == "?":
        return None
    _p = _PROFILES.get(_key)
    if _p is None:
        _p = {"address": _key, "summary": "", "freq": 0, "last_seen": 0.0}
        _PROFILES[_key] = _p
    _p["freq"] = int(_p.get("freq", 0)) + 1
    _p["last_seen"] = time.time()
    if address_pref:
        _p["address"] = address_pref
    if summary:
        _p["summary"] = summary[:_MAX_SUMMARY]
    # INFO 探针（可观测性；零副作用）
    _log.info("[影子画像] user=%s freq=%s summary_len=%s", _key, _p["freq"], len(_p["summary"]))
    return dict(_p)


def get_profile(user: str | None = None) -> Dict[str, Any] | None:
    """读取某用户的内存画像副本（无则返回 None）。"""
    _key = (user or "").strip() or "?"
    _p = _PROFILES.get(_key)
    return dict(_p) if _p else None


def snapshot() -> Dict[str, Dict[str, Any]]:
    """全量内存画像快照（仍内存态，不落盘）。"""
    return {_k: dict(_v) for _k, _v in _PROFILES.items()}


def clear_all() -> None:
    """会话级清空（测试 / 会话结束调用）。"""
    _PROFILES.clear()
