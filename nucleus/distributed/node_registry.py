# -*- coding: utf-8 -*-
# ⚠️ @deprecated (157-D C-4 躯体/多机封存 / Q157-2 已裁):
#   分布式集群·node_registry：多机节点注册，单机现实下未启用；③封存（待多机专门批）。
#   复活须待 PHASE19（具身化/多机）专门批；禁止新代码 import 本模块（若仍在用请先接线）。
"""分布式节点注册表 —— 主线第71批 T3/P1（第一阶段：单机模拟）。

实现节点注册 / 注销 / 查询 / 状态更新 / 持久化。
第71批是分布式组件引入第一阶段，默认关闭（ENABLE_DISTRIBUTED=False），
单机模拟模式下预注册 3 个虚拟节点（localhost:5051/5052/5053）。

★ 零副作用原则：
  - 所有方法在开关关闭时仍可本地调用（注册表是纯本地结构，不依赖网络）；
  - 持久化失败只记 WARNING，不抛异常给调用方；
  - 线程安全（内部 RLock）。
"""
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from nucleus._silent_except import silent_exc

try:
    from nucleus.logger import get_module_logger as _get_module_logger
    _logger = _get_module_logger("pulse.module.node_registry")
except Exception:  # pragma: no cover - 仅为健壮性兜底
    import logging
    _logger = logging.getLogger("pulse.module.node_registry")


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


def _distributed_enabled() -> bool:
    try:
        import config
        return bool(getattr(config, "ENABLE_DISTRIBUTED", False))
    except Exception as e:
        silent_exc(e, where="nucleus.distributed.node_registry::_distributed_enabled L40")
        return False


# 单机模拟：3 个虚拟节点，指向 localhost 不同端口
_SIM_NODES = [
    {"node_id": "node-1", "host": "localhost", "port": 5051,
     "shard_ids": [0, 1, 2, 3, 4], "capacity": {"cpu": 8, "memory": 16, "disk": 500}},
    {"node_id": "node-2", "host": "localhost", "port": 5052,
     "shard_ids": [5, 6, 7, 8, 9], "capacity": {"cpu": 8, "memory": 16, "disk": 500}},
    {"node_id": "node-3", "host": "localhost", "port": 5053,
     "shard_ids": [10, 11, 12, 13, 14, 15], "capacity": {"cpu": 8, "memory": 16, "disk": 500}},
]


class NodeRegistry:
    """分布式节点注册表（第一阶段：单机模拟，持久化到本地 JSON）。"""

    def __init__(self, path: str = "", simulation_mode: bool = True,
                 auto_persist: bool = True) -> None:
        self._path = path or str(_cfg("NODE_REGISTRY_PATH",
                                      "data/distributed/node_registry.json"))
        self._simulation = bool(simulation_mode)
        self._auto_persist = bool(auto_persist)
        self._lock = threading.RLock()
        self._nodes: Dict[str, dict] = {}
        self._load()
        if self._simulation and not self._nodes:
            self._setup_simulation()

    # ---------- 持久化 ----------
    def _load(self) -> None:
        with self._lock:
            if not self._path or not os.path.isfile(self._path):
                return
            try:
                with open(self._path, "r", encoding="utf-8") as _f:
                    _data = json.load(_f)
                _nodes = _data.get("nodes", {}) if isinstance(_data, dict) else {}
                if isinstance(_nodes, dict):
                    self._nodes = {str(k): dict(v) for k, v in _nodes.items()}
            except Exception as _e:
                _logger.warning("[T3] 节点注册表加载失败(已忽略): %s: %s",
                                type(_e).__name__, _e)

    def persist(self) -> bool:
        """持久化到本地文件。失败返回 False（不抛异常）。"""
        if not self._path:
            return False
        with self._lock:
            _dir = os.path.dirname(self._path)
            try:
                if _dir and not os.path.isdir(_dir):
                    os.makedirs(_dir, exist_ok=True)
                _payload = {
                    "saved_at": time.time(),
                    "simulation": self._simulation,
                    "nodes": self._nodes,
                }
                _tmp = self._path + ".tmp"
                with open(_tmp, "w", encoding="utf-8") as _f:
                    json.dump(_payload, _f, ensure_ascii=False, indent=2)
                os.replace(_tmp, self._path)
                return True
            except Exception as _e:
                _logger.warning("[T3] 节点注册表持久化失败(已忽略): %s: %s",
                                type(_e).__name__, _e)
                return False

    def _maybe_persist(self) -> None:
        if self._auto_persist:
            try:
                self.persist()
            except Exception as e:
                silent_exc(e, where="nucleus.distributed.node_registry::_maybe_persist L113")

    def _setup_simulation(self) -> None:
        _now = time.time()
        for _n in _SIM_NODES:
            _node = dict(_n)
            _node["status"] = "active"
            _node["health_score"] = 1.0
            _node["last_heartbeat"] = _now
            _node["registered_at"] = _now
            self._nodes[_node["node_id"]] = _node
        self._maybe_persist()

    # ---------- 节点管理 ----------
    def register(self, node_id: str, host: str = "localhost", port: int = 0,
                 shard_ids: Optional[List[int]] = None,
                 capacity: Optional[Dict[str, Any]] = None) -> bool:
        """注册节点。已存在则更新。"""
        if not node_id:
            return False
        with self._lock:
            _now = time.time()
            _node = self._nodes.get(node_id, {})
            _node.update({
                "node_id": node_id,
                "host": host,
                "port": int(port or 0),
                "shard_ids": list(shard_ids if shard_ids is not None
                                  else _node.get("shard_ids", [])),
                "capacity": dict(capacity if capacity is not None
                                 else _node.get("capacity", {})),
                "status": _node.get("status", "active"),
                "health_score": float(_node.get("health_score", 1.0)),
                "last_heartbeat": _node.get("last_heartbeat", _now),
                "registered_at": _node.get("registered_at", _now),
            })
            self._nodes[node_id] = _node
            self._maybe_persist()
            return True

    def unregister(self, node_id: str) -> bool:
        """注销节点（保留注册信息仅标记 inactive）。"""
        if not node_id:
            return False
        with self._lock:
            if node_id in self._nodes:
                self._nodes[node_id]["status"] = "inactive"
                self._maybe_persist()
                return True
            return False

    def get_node(self, node_id: str) -> Optional[dict]:
        with self._lock:
            return dict(self._nodes[node_id]) if node_id in self._nodes else None

    def get_all_nodes(self) -> List[dict]:
        with self._lock:
            return [dict(v) for v in self._nodes.values()]

    def get_nodes_by_shard(self, shard_id: int) -> List[dict]:
        with self._lock:
            return [dict(v) for v in self._nodes.values()
                    if int(shard_id) in (v.get("shard_ids") or [])]

    def get_active_nodes(self) -> List[dict]:
        with self._lock:
            return [dict(v) for v in self._nodes.values()
                    if v.get("status") == "active"]

    def update_node_status(self, node_id: str, status: str = "",
                           health_score: float = 0.0) -> bool:
        """更新节点状态 / 健康分。"""
        if not node_id:
            return False
        with self._lock:
            _node = self._nodes.get(node_id)
            if _node is None:
                return False
            if status:
                _node["status"] = status
            if health_score is not None:
                try:
                    _node["health_score"] = max(0.0, min(1.0, float(health_score)))
                except (TypeError, ValueError) as e:
                    silent_exc(e, where="nucleus.distributed.node_registry::update_node_status L197")
            _node["last_heartbeat"] = time.time()
            self._maybe_persist()
            return True

    def mark_heartbeat(self, node_id: str, health_score: float = 1.0) -> bool:
        """心跳上报：刷新 last_heartbeat 与健康分。"""
        if not node_id:
            return False
        with self._lock:
            _node = self._nodes.get(node_id)
            if _node is None:
                return False
            _node["last_heartbeat"] = time.time()
            try:
                _node["health_score"] = max(0.0, min(1.0, float(health_score)))
            except (TypeError, ValueError) as e:
                silent_exc(e, "node_registry.py:213:mark_heartbeat", level="warning")
            if _node.get("status") == "inactive":
                _node["status"] = "active"
            self._maybe_persist()
            return True

    def get_load_stats(self) -> Dict[str, Any]:
        """注册表负载统计（节点数 / 健康分布 / 分片覆盖）。"""
        with self._lock:
            _total = len(self._nodes)
            _active = sum(1 for v in self._nodes.values()
                          if v.get("status") == "active")
            _healthy = sum(1 for v in self._nodes.values()
                           if float(v.get("health_score", 0.0)) >= 0.5)
            _shards: Dict[int, int] = {}
            for _v in self._nodes.values():
                for _s in (_v.get("shard_ids") or []):
                    _shards[int(_s)] = _shards.get(int(_s), 0) + 1
            return {
                "enabled": _distributed_enabled(),
                "simulation": self._simulation,
                "total": _total,
                "active": _active,
                "healthy": _healthy,
                "shards_covered": len(_shards),
                "nodes": list(self._nodes.keys()),
            }

    def clear(self) -> None:
        """清空注册表（测试用，谨慎调用）。"""
        with self._lock:
            self._nodes = {}
            self._maybe_persist()
