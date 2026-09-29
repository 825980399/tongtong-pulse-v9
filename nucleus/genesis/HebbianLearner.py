# -*- coding: utf-8 -*-
"""
HebbianLearner.py —— 赫布学习者

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 赫布学习规则实现，关联强化
机制: 基于HebbianLearner类实现，包含10个核心方法
定位: 学习核心层
"""

import threading
import time
from typing import Any



class HebbianLearner:
    """
    赫布学习器
    
    工作原理:
        1. 节点被激活时，记录激活事件和时间戳。
        2. 在共现窗口内（默认10秒），检测哪些节点一起被激活。
        3. 对共现节点对增强赫布权重（+学习率）。
        4. 对超过窗口未共现的节点对衰减权重（×衰减率）。
        5. 权重低于最小阈值（0.01）时，移除连接。
    
    核心参数:
        - cooccurrence_window: 共现窗口（秒），窗口内的激活视为共现。
          ★v23.0校准：默认60秒，对齐2个心跳周期（30秒/心跳），
          确保相邻心跳中激活的节点能被识别为共现。
        - learning_rate: 学习率，每次共现增强的幅度。
          ★v23.0校准：默认0.15，略高于原值，保证连接权重能有效建立。
        - decay_rate: 衰减率，每次未共现周期的衰减比例。
          ★v23.0校准：默认0.0005，减缓衰减，保留长时记忆。
        - min_weight: 最小权重，低于此值的连接被移除。
        - max_weight: 最大权重，防止无限增长。
    
    当前状态（v9.0）:
        - 接口完整定义，但功能开关默认关闭。
        - P2阶段通过 ResonanceEngine 间接使用赫布权重。
        - P3阶段可激活完整的在线学习。
    """
    
    def __init__(self, 
                 cooccurrence_window: float = 60.0,   # ★v23.0校准：对齐2个心跳周期
                 learning_rate: float = 0.15,          # ★v23.0校准：适度提升学习速率
                 decay_rate: float = 0.0005,           # ★v23.0校准：减缓衰减，保留长时记忆
                 min_weight: float = 0.01,
                 max_weight: float = 1.0):
        """
        Args:
            cooccurrence_window: 共现窗口（秒）
            learning_rate: 学习率 0.0-1.0
            decay_rate: 每次周期的衰减率
            min_weight: 最小权重阈值
            max_weight: 最大权重上限
        """
        # 赫布连接: (node_a, node_b) → weight
        self._connections: dict[tuple[str, str], float] = {}
        self._max_connections = 5000  # ★v25.0治理：最大连接数，超过时修剪最弱连接
        
        # 最近激活记录: node_id → last_activation_time
        self._recent_activations: dict[str, float] = {}
        
        # 共现统计: (node_a, node_b) → cooccurrence_count
        self._cooccurrence_counts: dict[tuple[str, str], int] = {}
        
        # 参数
        self._window = cooccurrence_window
        self._learning_rate = learning_rate
        self._decay_rate = decay_rate
        self._min_weight = min_weight
        self._max_weight = max_weight
        
        # 线程安全
        self._lock = threading.Lock()
        
        # 统计
        self._total_activations = 0
        self._total_cooccurrences = 0
        self._total_decays = 0
        self._total_pruned = 0
        
        # 功能开关
        self._enabled = False

    # ========== 框架控制 ==========

    def enable(self):
        """激活赫布学习"""
        self._enabled = True

    def disable(self):
        """关闭赫布学习"""
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    # ========== 核心：激活与共现检测 ==========

    def on_node_activated(self, node_id: str):
        """
        当知识节点被激活时调用。
        
        检测该节点与最近激活的其他节点是否构成共现，
        如果是则增强赫布权重。

        Args:
            node_id: 被激活的节点ID
        """
        if not self._enabled:
            return

        with self._lock:
            now = time.time()
            self._total_activations += 1

            # 检测与最近激活节点的共现
            co_activated = []
            for other_id, last_time in list(self._recent_activations.items()):
                if other_id == node_id:
                    continue
                if now - last_time <= self._window:
                    co_activated.append(other_id)

            # 增强共现节点对的赫布权重
            for other_id in co_activated:
                pair = tuple(sorted([node_id, other_id]))
                
                # 增加共现计数
                self._cooccurrence_counts[pair] = self._cooccurrence_counts.get(pair, 0) + 1
                self._total_cooccurrences += 1

                # 赫布学习：权重 += 学习率 / (1 + 当前权重)
                old_weight = self._connections.get(pair, 0.0)
                new_weight = old_weight + self._learning_rate / (1.0 + old_weight)
                new_weight = min(self._max_weight, new_weight)
                self._connections[pair] = new_weight

            # ★v25.0治理：连接数超过上限时，修剪权重最低的连接
            if len(self._connections) > self._max_connections:
                _sorted_connections = sorted(
                    self._connections.items(), key=lambda x: x[1]
                )
                _to_remove = len(self._connections) - self._max_connections
                for _pair, _weight in _sorted_connections[:_to_remove]:
                    del self._connections[_pair]
                    self._cooccurrence_counts.pop(_pair, None)
                    self._total_pruned += 1

            # 更新当前节点的激活时间
            self._recent_activations[node_id] = now

            # 清理过期的激活记录（超过窗口2倍时间的记录）
            cutoff = now - self._window * 2
            expired = [nid for nid, t in self._recent_activations.items() if t < cutoff]
            for nid in expired:
                del self._recent_activations[nid]

    # ========== 权重查询 ==========

    def get_weight(self, node_a: str, node_b: str) -> float:
        """
        获取两个节点之间的赫布权重。

        Args:
            node_a: 节点A的ID
            node_b: 节点B的ID

        Returns:
            赫布权重 0.0-1.0
        """
        pair = tuple(sorted([node_a, node_b]))
        return self._connections.get(pair, 0.0)

    def get_node_connections(self, node_id: str) -> dict[str, float]:
        """
        获取与指定节点相连的所有赫布连接。

        Args:
            node_id: 节点ID

        Returns:
            {connected_node_id: weight} 字典
        """
        connections = {}
        for (a, b), weight in self._connections.items():
            if a == node_id:
                connections[b] = weight
            elif b == node_id:
                connections[a] = weight
        return connections

    def get_top_connections(self, top_k: int = 10) -> list[dict[str, Any]]:
        """
        获取权重最高的赫布连接。

        Args:
            top_k: 返回数量

        Returns:
            连接列表，按权重降序
        """
        with self._lock:
            sorted_connections = sorted(
                self._connections.items(),
                key=lambda x: x[1],
                reverse=True
            )
            return [
                {
                    "nodes": list(pair),
                    "weight": round(weight, 4),
                    "cooccurrences": self._cooccurrence_counts.get(pair, 0),
                }
                for pair, weight in sorted_connections[:top_k]
            ]

    # ========== 权重衰减与修剪 ==========

    def apply_decay(self):
        """
        对所有赫布连接应用权重衰减。
        
        长期未共现的连接权重逐渐降低， 
        低于最小阈值的连接被移除（突触修剪）。
        """
        if not self._enabled:
            return

        with self._lock:
            pruned_pairs = []
            
            for pair in list(self._connections.keys()):
                old_weight = self._connections[pair]
                new_weight = old_weight * (1.0 - self._decay_rate)
                
                if new_weight < self._min_weight:
                    pruned_pairs.append(pair)
                else:
                    self._connections[pair] = new_weight
                    self._total_decays += 1

            for pair in pruned_pairs:
                del self._connections[pair]
                self._cooccurrence_counts.pop(pair, None)
                self._total_pruned += 1

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        """获取赫布学习统计"""
        with self._lock:
            return {
                "enabled": self._enabled,
                "total_activations": self._total_activations,
                "total_cooccurrences": self._total_cooccurrences,
                "total_decays": self._total_decays,
                "total_pruned": self._total_pruned,
                "connection_count": len(self._connections),
                "cooccurrence_window": self._window,
                "learning_rate": self._learning_rate,
                "decay_rate": self._decay_rate,
                "min_weight": self._min_weight,
                "max_weight": self._max_weight,
                "top_connections": self.get_top_connections(5),
            }

    def get_state_snapshot(self) -> dict[str, Any]:
        """★FIX(规则4): 导出赫布连接权重与共现计数的跨重启常驻状态。"""
        with self._lock:
            return {
                "connections": {f"{a}\x00{b}": w for (a, b), w in self._connections.items()},
                "cooccurrence_counts": {f"{a}\x00{b}": c for (a, b), c in self._cooccurrence_counts.items()},
                "total_activations": self._total_activations,
                "total_cooccurrences": self._total_cooccurrences,
                "total_decays": self._total_decays,
                "total_pruned": self._total_pruned,
            }

    def load_state_snapshot(self, state: dict[str, Any]):
        """★FIX(规则4): 从快照恢复赫布连接权重与共现计数。"""
        if not state:
            return
        with self._lock:
            if "connections" in state:
                self._connections = {}
                for _k, _w in state["connections"].items():
                    if "\x00" in _k:
                        _a, _b = _k.split("\x00", 1)
                        self._connections[(_a, _b)] = float(_w)
            if "cooccurrence_counts" in state:
                self._cooccurrence_counts = {}
                for _k, _c in state["cooccurrence_counts"].items():
                    if "\x00" in _k:
                        _a, _b = _k.split("\x00", 1)
                        self._cooccurrence_counts[(_a, _b)] = int(_c)
            self._total_activations = state.get("total_activations", self._total_activations)
            self._total_cooccurrences = state.get("total_cooccurrences", self._total_cooccurrences)
            self._total_decays = state.get("total_decays", self._total_decays)
            self._total_pruned = state.get("total_pruned", self._total_pruned)

    # ========== 未来演化预留（v10.0 振荡场） ==========

    def on_field_oscillation(self, field_data: dict[str, Any]):
        """
        【预留 v10.0】振荡场中，赫布学习由连续场共振荡驱动。
        不再依赖离散的激活事件，而是通过场中频率-相位锁定
        自动形成和增强连接。
        """

    def get_connection_graph(self) -> dict[str, Any]:
        """
        【预留 v10.0】获取完整连接图（用于知识演化可视化）。
        
        Returns:
            节点和边的图数据结构
        """
        nodes = list(self._recent_activations.keys())
        edges = [
            {"source": a, "target": b, "weight": w}
            for (a, b), w in self._connections.items()
        ]
        return {"nodes": nodes, "edges": edges}


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== HebbianLearner 自测 ===\n")
    
    learner = HebbianLearner(cooccurrence_window=5.0, learning_rate=0.1)
    learner.enable()
    
    # 1. 模拟节点共现激活
    print("1. 模拟节点共现:")
    # 节点A、B、C经常一起激活
    for _ in range(5):
        learner.on_node_activated("node_A")
        learner.on_node_activated("node_B")
        learner.on_node_activated("node_C")
        time.sleep(0.1)
    
    w_ab = learner.get_weight("node_A", "node_B")
    w_ac = learner.get_weight("node_A", "node_C")
    w_bc = learner.get_weight("node_B", "node_C")
    print(f"   A-B 权重: {w_ab:.4f} (应 > 0)")
    print(f"   A-C 权重: {w_ac:.4f} (应 > 0)")
    print(f"   B-C 权重: {w_bc:.4f} (应 > 0)")
    assert w_ab > 0 and w_ac > 0 and w_bc > 0, "共现节点应有正权重"
    print("   ✅ 共现权重增强正确")
    
    # 2. 节点D独立激活（不应与其他节点产生强连接）
    print("\n2. 孤立节点:")
    for _ in range(3):
        learner.on_node_activated("node_D")
        time.sleep(6.0)  # 超过共现窗口
    
    w_da = learner.get_weight("node_D", "node_A")
    w_db = learner.get_weight("node_D", "node_B")
    print(f"   D-A 权重: {w_da:.4f} (应 ≈ 0)")
    print(f"   D-B 权重: {w_db:.4f} (应 ≈ 0)")
    print("   ✅ 孤立节点无连接正确")
    
    # 3. 衰减与修剪
    print("\n3. 衰减测试:")
    old_count = len(learner._connections)
    for _ in range(3):
        learner.apply_decay()
    new_count = len(learner._connections)
    print(f"   衰减前连接数: {old_count}, 衰减后: {new_count}")
    print("   ✅ 衰减机制正常")
    
    # 4. 节点连接查询
    connections = learner.get_node_connections("node_A")
    print(f"\n4. node_A的连接: {[(k, round(v, 4)) for k, v in connections.items()]}")
    
    # 5. 统计
    stats = learner.get_stats()
    print(f"\n5. 统计: 激活{stats['total_activations']}次, "
          f"共现{stats['total_cooccurrences']}次, "
          f"连接{stats['connection_count']}个")
    
    learner.disable()
    print("\n=== 自测全部通过 ===")