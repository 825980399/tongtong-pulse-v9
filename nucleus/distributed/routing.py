# -*- coding: utf-8 -*-
"""分布式路由与负载均衡 —— 主线第70批 T3/P1（**只做设计与接口定义，不实施**）。

第70批不引入分布式运行时，本模块提供：
  - 请求路由（客户端路由 / 代理路由 / 混合路由）
  - 节点健康度管理
  - 负载均衡策略（轮询 / 最少连接 / 一致性哈希 / 动态负载）

★开关 ``ENABLE_DISTRIBUTED`` 默认 False；本模块纯计算，无 IO、无副作用。
"""
import threading
import time
from typing import Any, Dict, List, Optional

try:
    from nucleus.logger import get_module_logger as _get_module_logger
    _logger = _get_module_logger("pulse.module.routing")
except Exception:  # pragma: no cover
    import logging
    _logger = logging.getLogger("pulse.module.routing")


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
    except Exception:
        return False


class NodeRouter:
    """路由与负载均衡。

    推荐「混合路由」：客户端缓存路由表直接访问，路由表失效/未命中时由代理兜底。
    这样既避免每次请求都过代理（单点+延迟），又不会因客户端路由表过期而失败。
    """

    def __init__(self, nodes: Optional[List[str]] = None,
                 strategy: str = "least_conn") -> None:
        self._nodes: List[str] = list(nodes or [])
        self._strategy = strategy or "least_conn"
        self._lock = threading.RLock()
        # 健康分：1.0=健康，0.0=不可用；动态路由按此加权
        self._health: Dict[str, float] = {_n: 1.0 for _n in self._nodes}
        # 最少连接：当前连接数
        self._conns: Dict[str, int] = {_n: 0 for _n in self._nodes}
        self._rr_index = 0
        self._local_handlers: Dict[str, Any] = {}
        self._load_stats: Dict[str, Dict[str, float]] = {}

    # ---------- 节点管理 ----------
    def add_node(self, node_id: str) -> None:
        with self._lock:
            if node_id not in self._nodes:
                self._nodes.append(node_id)
                self._health[node_id] = 1.0
                self._conns[node_id] = 0

    def remove_node(self, node_id: str) -> None:
        with self._lock:
            if node_id in self._nodes:
                self._nodes.remove(node_id)
                self._health.pop(node_id, None)
                self._conns.pop(node_id, None)

    def update_node_health(self, node_id: str, health_score: float) -> None:
        """更新节点健康状态（0.0~1.0）。"""
        with self._lock:
            self._health[node_id] = max(0.0, min(1.0, float(health_score)))

    def get_healthy_nodes(self, min_health: float = 0.5) -> List[str]:
        """获取健康节点列表。"""
        with self._lock:
            return [_n for _n in self._nodes
                    if self._health.get(_n, 0.0) >= min_health]

    # ---------- 路由 ----------
    def route_request(self, request_type: str = "", node_id: str = "",
                      data: Any = None) -> Optional[str]:
        """路由请求，返回目标节点；无健康节点 → None。

        Args:
            request_type: 请求类型（read / write ...）
            node_id: 关联的节点 ID（一致性哈希策略下参与计算）
            data: 请求负载

        Returns:
            目标节点 ID；无可用节点返回 None
        """
        _candidates = self.get_healthy_nodes()
        if not _candidates:
            return None

        if self._strategy == "round_robin":
            with self._lock:
                _n = _candidates[self._rr_index % len(_candidates)]
                self._rr_index += 1
                return _n

        if self._strategy == "consistent_hash":
            if not node_id:
                return _candidates[0]
            _h = 0
            for _ch in str(node_id):
                _h = (_h * 31 + ord(_ch)) & 0xFFFFFFFF
            return _candidates[_h % len(_candidates)]

        if self._strategy == "dynamic":
            # 动态：健康分最高 + 连接数最少
            with self._lock:
                return min(_candidates,
                           key=lambda _n: (self._conns.get(_n, 0) /
                                           max(0.01, self._health.get(_n, 0.01))))

        # 默认 least_conn：连接数最少
        with self._lock:
            return min(_candidates, key=lambda _n: self._conns.get(_n, 0))

    def acquire(self, node_id: str) -> None:
        """记录一次连接获取（最少连接策略用）。"""
        with self._lock:
            self._conns[node_id] = self._conns.get(node_id, 0) + 1

    def release(self, node_id: str) -> None:
        """记录一次连接释放。"""
        with self._lock:
            self._conns[node_id] = max(0, self._conns.get(node_id, 0) - 1)

    # ---------- 请求转发与负载统计（主线第71批 T3）----------
    def register_local_handler(self, node_id: str, handler: Any) -> None:
        """注册本地处理器（单机模拟：forward_request 直接调用本地方法）。"""
        with self._lock:
            self._local_handlers[node_id] = handler

    def forward_request(self, target_node: str, request_type: str = "",
                       data: Any = None, timeout: float = 30.0,
                       retries: int = 2) -> Dict[str, Any]:
        """转发请求到目标节点（第一阶段：单机模拟本地调用）。

        - 开关关闭或目标节点不存在：返回对应状态（不抛异常）
        - 模拟模式：调用已注册本地处理器，失败按 retries 重试，仍失败回退本地
        - 记录每节点请求数 / 成功率 / 平均延迟（负载统计）
        """
        if not _distributed_enabled():
            return {"status": "disabled", "node": target_node}
        if target_node not in self._nodes:
            return {"status": "no_route", "node": target_node}
        _start = time.time()
        _ok = False
        _last_err = ""
        _attempts = 0
        for _i in range(int(retries) + 1):
            _attempts += 1
            try:
                with self._lock:
                    _h = self._local_handlers.get(target_node)
                if _h is not None:
                    _h(request_type, data)
                _ok = True
                break
            except Exception as _e:
                _last_err = "%s: %s" % (type(_e).__name__, _e)
                _logger.debug("[T3] 转发请求失败(重试 %d/%d): %s", _i, retries, _last_err)
        _latency = (time.time() - _start) * 1000.0
        with self._lock:
            _s = self._load_stats.setdefault(target_node,
                {"requests": 0, "success": 0, "fails": 0, "latency_sum": 0.0})
            _s["requests"] += 1
            if _ok:
                _s["success"] += 1
            else:
                _s["fails"] += 1
            _s["latency_sum"] += _latency
        if _ok:
            return {"status": "ok", "node": target_node,
                    "attempts": _attempts, "latency_ms": _latency}
        return {"status": "fallback", "node": target_node,
                "attempts": _attempts, "error": _last_err, "latency_ms": _latency}

    def get_load_stats(self) -> Dict[str, Any]:
        """各节点负载统计（请求数 / 成功率 / 平均延迟）。"""
        with self._lock:
            _out: Dict[str, Any] = {}
            for _n, _s in self._load_stats.items():
                _req = _s["requests"]
                _avg = (_s["latency_sum"] / _req) if _req else 0.0
                _out[_n] = {
                    "requests": _req,
                    "success": _s["success"],
                    "fails": _s["fails"],
                    "success_rate": (_s["success"] / _req) if _req else 0.0,
                    "avg_latency_ms": _avg,
                }
            return _out

    # [M71-ROUTING]
    def get_stats(self) -> Dict[str, Any]:
        """路由状态。"""
        with self._lock:
            return {
                "enabled": _distributed_enabled(),
                "strategy": self._strategy,
                "nodes": list(self._nodes),
                "health": dict(self._health),
                "connections": dict(self._conns),
            }



def build_simulation_router(simulation: bool = True) -> "NodeRouter":
    """构建单机模拟路由器（预注册 3 个虚拟节点 node-1/2/3）。"""
    _router = NodeRouter(strategy="least_conn")
    for _n in ("node-1", "node-2", "node-3"):
        _router.add_node(_n)
    return _router


def get_router(nodes: Optional[List[str]] = None) -> NodeRouter:
    """获取路由器实例。"""
    return NodeRouter(nodes=nodes)
