# -*- coding: utf-8 -*-
"""Standing Orders 四字段（179批 刀2：O-B2 实装）。

设计稿：177批《O-B2 Standing Orders 设计稿》。
四字段：grant(扩授权) / trigger(触发) / gate(闸，引用 O-B1 键) / escalate(升级)。
闸字段**引用既有 O-B1 键名**（OB1_SANDBOX_LEVEL 等，178刀3 d01c30e 已落），不重造。

纪律：
  - 总开关 ENABLE_STANDING_ORDERS 默认 False → 不加载/不生效，零行为变化。
  - 仅落地四字段数据结构 + 闸引用校验，不实装执行引擎侧自动生效逻辑（推后续批）。
  - 无静默 except（cw2 红线）。
"""
from __future__ import annotations

from dataclasses import dataclass

import config as _cfg


@dataclass(frozen=True)
class StandingOrder:
    grant: str       # 扩授权：允许自主执行的动作域
    trigger: str     # 触发：自动生效的条件
    gate: str        # 闸：执行前必过的安全闸（引用 O-B1 键名）
    escalate: str    # 升级：触发人工审批的条件


def _enabled() -> bool:
    return bool(getattr(_cfg, "ENABLE_STANDING_ORDERS", False))


def _ob1_identifiers() -> set[str]:
    """收集既有 O-B1 全部标识符（facet 名 / level_key / 子键），统一小写便于引用比对。"""
    _facets = getattr(_cfg, "OB1_CONFIG_FACETS", {}) or {}
    _ids: set[str] = set()
    for _name, _facet in _facets.items():
        _ids.add(str(_name).lower())
        _ids.add(str(_facet.get("level_key", "")).lower())
        for _k in (_facet.get("keys", []) or []):
            _ids.add(str(_k).lower())
    _ids.discard("")
    return _ids


def gate_references_ob1(gate: str) -> bool:
    """闸字段是否引用了既有 O-B1 标识符（facet 名如 sandbox，或键名如 OB1_SANDBOX_LEVEL）。

    设计稿闸示例为 ``o_b1_sandbox=strict``，引用的是 O-B1 的 sandbox facet；
    大小写不敏感比对，兼容 ``o_b1_sandbox`` 与 ``OB1_SANDBOX_LEVEL`` 两种写法。
    """
    _g = (gate or "").lower()
    return any(_id and _id in _g for _id in _ob1_identifiers())


def load_orders() -> list[StandingOrder]:
    """从 config.STANDING_ORDERS 加载四字段结构；开关关闭返回空列表。"""
    if not _enabled():
        return []
    _raw = getattr(_cfg, "STANDING_ORDERS", []) or []
    _orders: list[StandingOrder] = []
    for _o in _raw:
        _orders.append(StandingOrder(
            grant=str(_o.get("grant", "")),
            trigger=str(_o.get("trigger", "")),
            gate=str(_o.get("gate", "")),
            escalate=str(_o.get("escalate", "")),
        ))
    return _orders
