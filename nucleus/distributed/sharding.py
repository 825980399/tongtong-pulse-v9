# -*- coding: utf-8 -*-
"""分布式分片策略 —— 主线第70批 T3/P1（**只做设计与接口定义，不实施**）。

第70批不引入分布式运行时，本模块提供：
  - 分片计算（一致性哈希 / evol_level 分片 / 混合策略）
  - 分片元数据管理（路由表 / 健康状态）
  - 复制与一致性接口定义（供第71批+实现）

★开关 ``ENABLE_DISTRIBUTED`` 默认 False；本模块纯计算，无 IO、无副作用，
  即使被导入也不会改变任何现有行为。
"""
import hashlib
from typing import Any

try:
    from nucleus.logger import get_module_logger as _get_module_logger
    _logger = _get_module_logger("pulse.module.sharding")
except Exception:  # pragma: no cover
    import logging
    _logger = logging.getLogger("pulse.module.sharding")


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


class ShardStrategy:
    """分片策略（推荐：evol_level + 一致性哈希混合）。

    混合策略的理由：
      - 纯哈希：L1 热节点被打散到所有分片，热数据无法集中优化；
      - 纯 evol_level：单层级内数据倾斜无解（L1 可能远大于 L3）；
      - 混合：先按 evol_level 分大区，区内再哈希 —— 既保留层级局部性，
        又能把单层的热点摊平。
    """

    def __init__(self, shard_count: int = 0, replica_count: int = 0,
                 strategy: str = "hybrid") -> None:
        self._shard_count = int(shard_count or _cfg("DISTRIBUTED_SHARD_COUNT", 16))
        self._replica_count = int(replica_count or _cfg("DISTRIBUTED_REPLICA_COUNT", 3))
        self._strategy = strategy or "hybrid"
        # 分层权重：L1 热节点占比小但访问高 → 给它独立且较少的分片数
        self._level_shards = {"L1": 2, "L2": 6, "L3": 8}

    # ---------- 分片计算 ----------
    def _hash(self, key: str) -> int:
        """稳定哈希（md5，跨进程/跨平台一致）。"""
        return int(hashlib.md5(str(key).encode("utf-8")).hexdigest(), 16)

    def get_shard_id(self, node_id: str, evol_level: str = "") -> int:
        """计算节点所属分片。

        Args:
            node_id: 节点 ID
            evol_level: 进化层级（L1/L2/L3），混合策略下参与分区

        Returns:
            分片 ID，范围 [0, shard_count)
        """
        if self._strategy == "hash":
            return self._hash(node_id) % self._shard_count

        _lvl = str(evol_level or "").upper()
        if self._strategy == "level" or _lvl in self._level_shards:
            # 混合 / 纯层级：先定层内偏移，再层内哈希
            _offsets = {"L1": 0, "L2": 2, "L3": 8}
            _offset = _offsets.get(_lvl, 0)
            _n = self._level_shards.get(_lvl, self._shard_count)
            _n = max(1, min(_n, self._shard_count - _offset))
            return (_offset + self._hash(node_id) % _n) % self._shard_count

        return self._hash(node_id) % self._shard_count

    def get_shard_nodes(self, shard_id: int) -> list[str]:
        """获取分片所在的存储节点（副本列表）。

        ★第70批为设计实现：返回按副本数推导的逻辑节点名，
          第71批+接入真实节点注册表后替换为实际地址。
        """
        if not (0 <= shard_id < self._shard_count):
            return []
        _n = max(1, min(self._replica_count, 3))
        return [f"node-{(shard_id + _i) % self._shard_count + 1}" for _i in range(_n)]

    def migrate_shard(self, shard_id: int, target_node: str) -> bool:
        """迁移分片（第70批：接口定义，返回 False 表示未实施）。"""
        _logger.info("[T3] 分片迁移为第71批+实施项: shard=%s -> %s", shard_id, target_node)
        return False

    # ---------- 元数据 ----------
    def get_shard_stats(self) -> dict[str, Any]:
        """分片元数据概览。"""
        return {
            "enabled": _distributed_enabled(),
            "strategy": self._strategy,
            "shard_count": self._shard_count,
            "replica_count": self._replica_count,
            "level_shards": dict(self._level_shards),
        }

    def get_distribution(self, node_ids: list[str],
                         evol_levels: dict[str, str] | None = None) -> dict[int, int]:
        """统计给定节点的分片分布（用于验证是否倾斜）。"""
        _dist: dict[int, int] = {}
        for _nid in node_ids:
            _lvl = (evol_levels or {}).get(_nid, "")
            _sid = self.get_shard_id(_nid, _lvl)
            _dist[_sid] = _dist.get(_sid, 0) + 1
        return _dist


# ---------- 一致性接口（第70批：设计定义） ----------
class ConsistencyLevel:
    """一致性级别常量。"""

    EVENTUAL = "eventual"  # 最终一致性：性能优先，推荐默认
    STRONG = "strong"      # 强一致性：关键数据（如 L1 热节点）

    @staticmethod
    def validate(level: str) -> bool:
        return str(level).lower() in (ConsistencyLevel.EVENTUAL, ConsistencyLevel.STRONG)


def write_with_consistency(node_id: str, data: Any,
                           consistency_level: str = ConsistencyLevel.EVENTUAL) -> bool:
    """带一致性级别的写入（第70批：接口定义，返回 False 表示未实施）。"""
    if not _distributed_enabled():
        return False
    if not ConsistencyLevel.validate(consistency_level):
        _logger.warning("[T3] 未知一致性级别: %s", consistency_level)
        return False
    _logger.debug("[T3] write_with_consistency 为第71批+实施项: %s", node_id)
    return False


def read_with_consistency(node_id: str,
                          consistency_level: str = ConsistencyLevel.EVENTUAL):
    """带一致性级别的读取（第70批：接口定义，返回 None 表示未实施）。"""
    if not _distributed_enabled():
        return
    if not ConsistencyLevel.validate(consistency_level):
        return
    return


def sync_replicas(shard_id: int) -> bool:
    """同步副本（第70批：接口定义）。"""
    _logger.debug("[T3] sync_replicas 为第71批+实施项: shard=%s", shard_id)
    return False


def get_sharding() -> ShardStrategy:
    """获取分片策略实例（进程内单例语义由调用方持有）。"""
    return ShardStrategy()
