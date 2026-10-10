# -*- coding: utf-8 -*-
# ⚠️ @deprecated (157-D C-4 躯体/多机封存 / Q157-2 已裁):
#   分布式集群·heartbeat：多机心跳，单机现实下未启用；③封存（待多机专门批）。
#   复活须待 PHASE19（具身化/多机）专门批；禁止新代码 import 本模块（若仍在用请先接线）。
"""心跳与健康检查 —— 主线第71批 T3/P1（第一阶段：单机模拟）。

实现心跳发送（后台线程）/ 健康检查 / 健康分计算 / 心跳超时处理。
第71批默认关闭（ENABLE_DISTRIBUTED=False），心跳线程仅在显式 start 后运行。

★ 零副作用原则：
  - 所有计算纯本地，不依赖网络（单机模拟）；
  - 线程异常被守护，绝不让后台线程退出主流程；
  - 开关关闭时仍可本地计算健康分（用于测试与诊断）。
"""
import threading
import time
from typing import Any, Callable, Dict, Optional

from nucleus._silent_except import silent_exc

try:
    from nucleus.logger import get_module_logger as _get_module_logger
    _logger = _get_module_logger("pulse.module.heartbeat")
except Exception:  # pragma: no cover - 仅为健壮性兜底
    import logging
    _logger = logging.getLogger("pulse.module.heartbeat")


def _distributed_enabled() -> bool:
    try:
        import config
        return bool(getattr(config, "ENABLE_DISTRIBUTED", False))
    except Exception as e:
        silent_exc(e, where="nucleus.distributed.heartbeat::_distributed_enabled L29")
        return False


def _heartbeat_interval() -> float:
    try:
        import config
        return float(getattr(config, "DISTRIBUTED_HEARTBEAT_INTERVAL", 30))
    except Exception as e:
        silent_exc(e, where="nucleus.distributed.heartbeat::_heartbeat_interval L37")
        return 30.0


def _health_threshold() -> float:
    try:
        import config
        return float(getattr(config, "DISTRIBUTED_HEALTH_THRESHOLD", 0.5))
    except Exception as e:
        silent_exc(e, where="nucleus.distributed.heartbeat::_health_threshold L45")
        return 0.5


class HeartbeatManager:
    """心跳管理器：向注册表上报心跳，并基于心跳延迟计算健康分。"""

    def __init__(self, registry: Any = None) -> None:
        self._registry = registry
        self._interval = _heartbeat_interval()
        self._threshold = _health_threshold()
        # 每个节点的本地心跳状态：{node_id: {"last_beat": ts, "health": float}}
        self._state: Dict[str, Dict[str, Any]] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._stops: Dict[str, bool] = {}
        self._lock = threading.RLock()

    # ---------- 健康分计算 ----------
    def compute_health_score(self, node_id: str,
                             last_beat_ts: float,
                             cpu_usage: float = 0.0,
                             memory_usage: float = 0.0,
                             queue_depth: int = 0) -> float:
        """基于心跳延迟 / CPU / 内存 / 队列深度计算健康分（0.0~1.0）。

        权重（与设计文档一致）：
          - 心跳延迟 30%
          - CPU 25%
          - 内存 25%
          - 队列深度 20%
        """
        _now = time.time()
        _delay = max(0.0, _now - float(last_beat_ts))
        # 心跳延迟得分：间隔内满分，超过 5 个间隔线性归零
        _delay_score = max(0.0, 1.0 - _delay / max(1e-6, self._interval * 5))
        _cpu = max(0.0, min(1.0, float(cpu_usage)))
        _mem = max(0.0, min(1.0, float(memory_usage)))
        _queue = max(0.0, min(1.0, float(queue_depth) / max(1, 100)))
        _score = (0.30 * _delay_score + 0.25 * (1.0 - _cpu)
                  + 0.25 * (1.0 - _mem) + 0.20 * (1.0 - _queue))
        return max(0.0, min(1.0, _score))

    def check_health(self, node_id: str) -> Dict[str, Any]:
        """检查节点健康状态，返回结构化结果（含是否超时 / 健康分 / 是否可路由）。"""
        with self._lock:
            _st = self._state.get(node_id)
        if _st is None:
            return {"node_id": node_id, "status": "unknown",
                    "health_score": 0.0, "routable": False}
        _score = self.compute_health_score(
            node_id, _st.get("last_beat", 0.0),
            _st.get("cpu_usage", 0.0), _st.get("memory_usage", 0.0),
            _st.get("queue_depth", 0))
        _now = time.time()
        _delay = _now - _st.get("last_beat", 0.0)
        _missed = int(_delay // max(1e-6, self._interval)) if self._interval > 0 else 0
        if _missed >= 5:
            _status = "removed"
        elif _missed >= 3:
            _status = "inactive"
        else:
            _status = "active"
        return {
            "node_id": node_id,
            "status": _status,
            "health_score": _score,
            "missed_intervals": _missed,
            "routable": _score >= self._threshold and _status != "removed",
        }

    def get_health_score(self, node_id: str) -> float:
        """计算并返回节点健康分。"""
        return self.check_health(node_id).get("health_score", 0.0)

    # ---------- 心跳上报（被动 / 主动）----------
    def report_heartbeat(self, node_id: str, health_score: float = 1.0,
                         cpu_usage: float = 0.0, memory_usage: float = 0.0,
                         queue_depth: int = 0) -> bool:
        """被动上报心跳（由节点主动调用，或本地模拟调用）。"""
        if not node_id:
            return False
        with self._lock:
            self._state[node_id] = {
                "last_beat": time.time(),
                "health_score": max(0.0, min(1.0, float(health_score))),
                "cpu_usage": float(cpu_usage),
                "memory_usage": float(memory_usage),
                "queue_depth": int(queue_depth),
            }
        if self._registry is not None:
            try:
                self._registry.mark_heartbeat(node_id, health_score)
            except Exception as _e:
                _logger.debug("[T3] 心跳上报注册表失败(已忽略): %s: %s",
                              type(_e).__name__, _e)
        return True

    # ---------- 心跳发送线程（主动）----------
    def start_heartbeat(self, node_id: str, interval: float = 0.0,
                        sender: Optional[Callable[[str], None]] = None) -> bool:
        """启动后台心跳线程（每 interval 秒上报一次）。"""
        if not node_id:
            return False
        with self._lock:
            if node_id in self._threads and self._threads[node_id].is_alive():
                return True
            self._stops[node_id] = False
        _interval = float(interval or self._interval or 30)

        def _loop() -> None:
            try:
                while True:
                    with self._lock:
                        if self._stops.get(node_id, False):
                            break
                    if sender is not None:
                        try:
                            sender(node_id)
                        except Exception as _e:
                            _logger.debug("[T3] 心跳发送失败(已忽略): %s: %s",
                                          type(_e).__name__, _e)
                    else:
                        self.report_heartbeat(node_id)
                    time.sleep(_interval)
            except Exception as _e:
                _logger.warning("[T3] 心跳线程异常退出(已忽略): %s: %s",
                                type(_e).__name__, _e)

        _t = threading.Thread(target=_loop, name="heartbeat-%s" % node_id,
                              daemon=True)
        _t.start()
        with self._lock:
            self._threads[node_id] = _t
        return True

    def stop_heartbeat(self, node_id: str) -> bool:
        """停止心跳线程。"""
        with self._lock:
            self._stops[node_id] = True
        _t = self._threads.get(node_id)
        if _t is not None:
            try:
                _t.join(timeout=1.0)
            except Exception as e:
                silent_exc(e, "heartbeat.py:187:stop_heartbeat", level="warning")
            with self._lock:
                self._threads.pop(node_id, None)
        return True

    def stop_all(self) -> None:
        with self._lock:
            _ids = list(self._stops.keys())
        for _id in _ids:
            self.stop_heartbeat(_id)
