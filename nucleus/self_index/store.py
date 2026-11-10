# -*- coding: utf-8 -*-
"""493批：Self-Index 存储（TTL 缓存 + 落盘 data/self_index/*.json）。

设计（任务书 §纪律 + 493-2/3/4「每小时 / 每分钟刷新」诉求）：
  * 三个导出器各自独立 TTL（事件:1h / 运行态:1min / 意图:1h），**不注入任何定时器**、
    不影响现有链路；查询驱动刷新，满足「不要每个事件都实时查」；
  * 落盘路径相对 `data/self_index`（不写死绝对路径，纪律 §2），文件内含 generated_at
    时间戳（纪律 §2「加时间戳」）；固定文件名 + 内容时间戳，不累积历史文件；
  * 开关 ENABLE_M493_SELF_INDEX 关闭时，refresh 直接返回 None（不读源、不落盘）。
"""
from __future__ import annotations

import json
import os
import time

from nucleus._silent_except import silent_exc
from nucleus.self_index import exporters
from nucleus.self_index.schema import build_self_index

#: 各 kind 的刷新间隔（秒）
_TTL = {
    "event_subscribers": 3600,   # 493-2：每小时刷新
    "organs_runtime": 60,        # 493-3：每分钟刷新
    "intent_map": 3600,          # 493-4：每小时刷新
    "module_graph": 3600,        # 493.5-1：模块图每小时刷新（构建一次约 <10s，落盘缓存）
}


class SelfIndexStore:
    """Self-Index 进程内缓存 + 落盘。线程不安全（单线程查询场景足够）。"""

    def __init__(self, root: str = "data/self_index"):
        self._root = root
        self._cache: dict = {}
        self._cache_at: dict = {}

    # ---- 内部：刷新 + 落盘 ----
    def _refresh(self, kind: str):
        import config as _cfg
        # 493.5 各刀独立开关；493 三导出走总开关
        if kind == "module_graph":
            if not bool(getattr(_cfg, "ENABLE_M4935_MODULE_GRAPH", True)):
                return None
        elif kind == "organ_aliases":
            if not bool(getattr(_cfg, "ENABLE_M4935_ORGAN_ALIASES", True)):
                return None
        elif kind == "actions":
            if not bool(getattr(_cfg, "ENABLE_M4935_ACTION_LIBRARY", True)):
                return None
        else:
            if not exporters._self_index_on():
                return None
        _now = time.time()
        _at = self._cache_at.get(kind, 0)
        if kind in self._cache and (_now - _at) < _TTL.get(kind, 3600):
            return self._cache[kind]
        if kind == "event_subscribers":
            _data = exporters.export_event_subscribers()
        elif kind == "organs_runtime":
            _data = exporters.export_organs_runtime()
        elif kind == "intent_map":
            _data = exporters.export_intent_map()
        elif kind == "module_graph":
            from nucleus.self_index import module_graph as _mg
            _data = _mg.build_module_graph()
        elif kind == "organ_aliases":
            _data = exporters.export_organ_aliases()
        elif kind == "actions":
            _data = exporters.export_actions()
        else:
            return None
        self._cache[kind] = _data
        self._cache_at[kind] = _now
        self._dump(kind, _data)
        return _data

    def _dump(self, kind: str, data: dict) -> None:
        """落盘 data/self_index/{kind}.json（含 generated_at 时间戳，不累积历史文件名）。"""
        try:
            os.makedirs(self._root, exist_ok=True)
            _p = os.path.join(self._root, f"{kind}.json")
            with open(_p, "w", encoding="utf-8") as _f:
                json.dump(data, _f, ensure_ascii=False, indent=2)
        except (OSError, TypeError) as _e:
            silent_exc(_e, where="SelfIndexStore._dump", level="debug")

    # ---- 访问器 ----
    def events(self) -> dict | None:
        return self._refresh("event_subscribers")

    def organs_runtime(self) -> dict | None:
        return self._refresh("organs_runtime")

    def intent_map(self) -> dict | None:
        return self._refresh("intent_map")

    # ---- 493.5 新增访问器 ----
    def module_graph(self) -> dict | None:
        return self._refresh("module_graph")

    def organ_aliases(self) -> dict | None:
        return self._refresh("organ_aliases")

    def actions(self) -> dict | None:
        return self._refresh("actions")

    def build(self) -> dict:
        """组装顶层 Self-Index（含 body_relations 推导）。"""
        _ev = self.events() or {"events": {}}
        _or = self.organs_runtime() or {"organs": []}
        _im = self.intent_map() or {"intents": []}
        _br = exporters.derive_body_relations(_ev)
        return build_self_index(
            _or.get("organs", []),
            _ev.get("events", {}),
            _im.get("intents", []),
            _br,
        )
