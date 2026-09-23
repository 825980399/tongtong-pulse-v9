# -*- coding: utf-8 -*-
"""主线第72批 T5：分布式主从复制与一致性级别（逻辑实现，单机模拟）。

设计要点
--------
- 主节点（master）：负责读写，定期同步到从节点。
- 从节点（slave）：负责读（可配置），接收主节点同步。
- 一致性级别：
    * eventual（默认）：写入主节点即返回，异步同步到从节点（本单机模拟直接复制，无网络延迟）。
    * strong（关键数据）：写入主节点并等待至少 N 个从节点确认才返回。
- 冲突解决：LWW（Last-Write-Wins，最后写入胜出）——版本号高者胜，版本相同则时间戳新者胜。
- 复制延迟监控：记录每个从节点的 replication_lag_ms。
- 主节点故障：手动提升从节点为新主节点（promote_slave）。
- 按分片一致性：L1 热节点 strong，L2/L3 冷节点 eventual（consistency_level_for_shard）。

说明：本批只做逻辑层实现（单机模拟），不实际多机部署（任务书风险5：第一阶段只做设计）。
所有读写默认零副作用、线程安全（内部 RLock），不依赖外部服务。
"""
import threading
import time


class ReplicationNode:
    """单个复制节点（主或从）。"""

    def __init__(self, node_id, role="slave", data=None):
        self.node_id = node_id
        self.role = role  # "master" / "slave"
        self.data = dict(data or {})
        self.version = 0          # 逻辑时钟（LWW 判定）
        self.updated_at = time.time()
        self.last_synced_at = time.time()
        self.replication_lag_ms = 0.0


class MasterSlaveReplication:
    """主从复制管理器（单机模拟，内存副本）。"""

    def __init__(self, default_consistency="eventual", replica_count=3,
                 strong_acks=2, lock=None):
        self._nodes = {}  # node_id -> ReplicationNode
        self.default_consistency = default_consistency
        self.replica_count = replica_count
        self.strong_acks = strong_acks
        self._lock = lock or threading.RLock()
        self._stats = {"writes": 0, "reads": 0, "conflicts": 0, "promotions": 0}

    # ---- 节点注册 / 查询 ----
    def register_node(self, node_id, role="slave", data=None):
        """注册一个节点（主或从）。已存在返回 False。"""
        with self._lock:
            if node_id in self._nodes:
                return False
            self._nodes[node_id] = ReplicationNode(node_id, role, data)
            return True

    def get_node(self, node_id):
        with self._lock:
            return self._nodes.get(node_id)

    # ---- 写入（带一致性级别）----
    def write_with_consistency(self, node_id, data, consistency=None, replica_count=None):
        """写入节点（主），按一致性级别同步到从节点。返回已确认副本数（含主）。"""
        consistency = consistency or self.default_consistency
        replica_count = replica_count or self.replica_count
        with self._lock:
            master = self._nodes.get(node_id)
            if master is None:
                master = ReplicationNode(node_id, "master", data)
                self._nodes[node_id] = master
            # LWW：新版本更高（或等版本且更新）才覆盖
            if isinstance(data, dict):
                _ver = data.get("__version__", master.version + 1)
                _ts = data.get("__updated_at__", time.time())
            else:
                _ver = master.version + 1
                _ts = time.time()
            if _ver < master.version or (_ver == master.version and _ts <= master.updated_at):
                self._stats["conflicts"] += 1
                return 1  # 旧写入被 LWW 丢弃，仅主自身确认
            master.data = dict(data) if isinstance(data, dict) else {"value": data}
            master.version = _ver
            master.updated_at = _ts
            self._stats["writes"] += 1

            _acks = 1  # 主节点自身
            for k in range(1, replica_count + 1):
                _sid = "%s#slave-%d" % (node_id, k)
                _slave = self._nodes.get(_sid)
                if _slave is None:
                    _slave = ReplicationNode(_sid, "slave", master.data)
                    self._nodes[_sid] = _slave
                _slave.data = dict(master.data)
                _slave.version = master.version
                _slave.updated_at = master.updated_at
                _slave.last_synced_at = time.time()
                _slave.replication_lag_ms = (time.time() - master.updated_at) * 1000.0
                _acks += 1
                if consistency == "strong" and _acks >= self.strong_acks:
                    break
            return _acks

    # ---- 读取（带一致性级别）----
    def read_with_consistency(self, node_id, consistency=None, replica_count=None):
        """读取节点。strong：返回主节点当前版本；eventual：返回版本最高副本（收敛后读）。"""
        consistency = consistency or self.default_consistency
        with self._lock:
            master = self._nodes.get(node_id)
            if master is None:
                return None
            self._stats["reads"] += 1
            if consistency == "strong":
                return dict(master.data)
            replica_count = replica_count or self.replica_count
            _candidates = [master] + [self._nodes.get("%s#slave-%d" % (node_id, k))
                                      for k in range(1, replica_count + 1)]
            _valid = [c for c in _candidates if c is not None]
            if not _valid:
                return dict(master.data)
            _best = max(_valid, key=lambda c: (c.version, c.updated_at))
            return dict(_best.data)

    # ---- 故障处理 ----
    def promote_slave(self, slave_id):
        """主节点故障时手动提升从节点为新的主节点。成功 True，否则 False。"""
        with self._lock:
            _slave = self._nodes.get(slave_id)
            if _slave is None or _slave.role != "slave":
                return False
            _slave.role = "master"
            self._stats["promotions"] += 1
            return True

    # ---- 监控 ----
    def get_replication_lag(self, node_id, replica_count=None):
        """返回该节点所有从节点中的最大复制延迟（ms）。"""
        replica_count = replica_count or self.replica_count
        with self._lock:
            _lags = []
            for k in range(1, replica_count + 1):
                _s = self._nodes.get("%s#slave-%d" % (node_id, k))
                if _s is not None:
                    _lags.append(_s.replication_lag_ms)
            return max(_lags) if _lags else 0.0

    def get_replication_stats(self):
        with self._lock:
            _masters = sum(1 for n in self._nodes.values() if n.role == "master")
            _slaves = sum(1 for n in self._nodes.values() if n.role == "slave")
            return {"nodes": len(self._nodes),
                    "masters": _masters, "slaves": _slaves,
                    "default_consistency": self.default_consistency,
                    "writes": self._stats["writes"], "reads": self._stats["reads"],
                    "conflicts_resolved": self._stats["conflicts"],
                    "promotions": self._stats["promotions"]}

    # ---- 分片一致性策略 ----
    def consistency_level_for_shard(self, evol_level):
        """按分片配置一致性级别：L1 热节点 strong，L2/L3 冷节点 eventual。"""
        if evol_level in ("L1", 1, "1"):
            return "strong"
        return "eventual"
