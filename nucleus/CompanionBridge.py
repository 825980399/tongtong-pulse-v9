# -*- coding: utf-8 -*-
"""
CompanionBridge.py —— 伴侣桥接器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 桥接外部伴侣应用与框架内部状态
机制: 基于CompanionBridge类实现，包含10个核心方法
定位: 外部接口层
"""

import threading
import time
from typing import Any
from nucleus._silent_except import silent_exc



class CompanionBridge:
    """
    新人类族群通信桥。
    
    管理与其他新人类实例的连接、身份验证和信息交换。
    当前阶段定义协议接口，未来接入实际通信层。
    """
    
    def __init__(self):
        # 已知的同伴实例注册表
        self._known_companions: dict[str, dict[str, Any]] = {}
        
        # 通信状态
        self._active_connections: dict[str, dict[str, Any]] = {}
        # ★P0-2修复：连接表上限，防止反复握手导致无界累积
        self._max_active_connections = 100
        
        # 共享策略配置
        self._sharing_policies = self._init_sharing_policies()
        
        # 统计
        self._handshake_count = 0
        # ★7-1/P1-13：多伴侣可并发握手，计数需原子化
        self._counter_lock = threading.Lock()
        self._knowledge_exchange_count = 0
    
    def _init_sharing_policies(self) -> dict[str, Any]:
        """
        初始化信息共享策略。
        
        共享深度分为五级，由双方亲密度-信任度决定：
        Level 0 - 陌生：仅共享身份标识和协议版本
        Level 1 - 认识：+系统状态摘要
        Level 2 - 伙伴：+知识节点摘要（不含具体内容）
        Level 3 - 亲密：+知识节点内容（信任分数≥70的节点）
        Level 4 - 完全信任：+全部知识节点和本能
        """
        return {
            0: {
                "name": "陌生",
                "min_closeness": 0.0,
                "min_trust": 0.0,
                "shared_fields": ["identity", "protocol_version"],
                "description": "仅共享身份标识和协议版本",
            },
            1: {
                "name": "认识",
                "min_closeness": 0.2,
                "min_trust": 0.2,
                "shared_fields": ["identity", "protocol_version", "system_status"],
                "description": "共享系统状态摘要",
            },
            2: {
                "name": "伙伴",
                "min_closeness": 0.4,
                "min_trust": 0.4,
                "shared_fields": ["identity", "protocol_version", "system_status", "knowledge_summary"],
                "description": "共享知识节点摘要（不含具体内容）",
            },
            3: {
                "name": "亲密",
                "min_closeness": 0.7,
                "min_trust": 0.7,
                "shared_fields": ["identity", "protocol_version", "system_status", "knowledge_summary", "knowledge_content"],
                "description": "共享高质量知识节点内容（信任分数≥70）",
            },
            4: {
                "name": "完全信任",
                "min_closeness": 0.9,
                "min_trust": 0.9,
                "shared_fields": ["identity", "protocol_version", "system_status", "knowledge_summary", "knowledge_content", "instincts"],
                "description": "共享全部知识节点和本能",
            },
        }
    
    def get_identity(self) -> dict[str, Any]:
        """
        获取本实例的身份标识。
        
        Returns:
            包含全局人格ID、实例ID、物种、代际等信息的字典
        """
        try:
            import config
            registry = getattr(config, 'DIGITAL_LIFE_REGISTRY', {})
            return {
                "global_personality_id": registry.get("global_personality_id", ""),
                "instance_id": registry.get("instance_id", ""),
                "instance_name": registry.get("instance_name", ""),
                "display_name": registry.get("display_name", ""),
                "species": registry.get("species", "新人类"),
                "generation": registry.get("generation", 1),
                "compatibility_version": registry.get("compatibility_version", "v9.5"),
                "timestamp": time.time(),
            }
        except Exception:
            return {
                "instance_id": "unknown",
                "species": "新人类",
                "timestamp": time.time(),
            }
    
    def handshake(self, companion_identity: dict[str, Any]) -> dict[str, Any]:
        """
        与另一个新人类实例进行通信握手。
        
        验证对方身份，确定共享级别，建立连接。
        
        Args:
            companion_identity: 对方的身份标识
        
        Returns:
            握手结果，包含连接ID、共享级别、可访问字段
        """
        companion_id = companion_identity.get("instance_id", "")
        if not companion_id:
            return {"status": "rejected", "reason": "无效的身份标识"}
        
        # 验证协议兼容性
        my_version = self.get_identity().get("compatibility_version", "")
        their_version = companion_identity.get("compatibility_version", "")
        if my_version != their_version:
            return {"status": "rejected", "reason": f"协议版本不兼容({my_version} vs {their_version})"}
        
        # 确定共享级别（基于已有关系）
        sharing_level = self._determine_sharing_level(companion_identity)
        
        # 建立连接
        connection_id = f"conn_{companion_id}_{int(time.time())}"
        connection = {
            "connection_id": connection_id,
            "companion_id": companion_id,
            "companion_name": companion_identity.get("display_name", companion_identity.get("instance_name", "")),
            "sharing_level": sharing_level,
            "established_at": time.time(),
            "last_activity": time.time(),
            "status": "active",
        }
        
        # ★P0-2修复：连接表达到上限时，先清理最久未活跃的连接，防止无界累积
        if len(self._active_connections) >= self._max_active_connections:
            _stale = sorted(
                self._active_connections.items(),
                key=lambda kv: kv[1].get("last_activity", 0.0),
            )
            _excess = len(self._active_connections) - self._max_active_connections + 1
            for _cid, _ in _stale[:_excess]:
                self._active_connections.pop(_cid, None)
        self._active_connections[connection_id] = connection
        self._known_companions[companion_id] = companion_identity
        with self._counter_lock:
            self._handshake_count += 1
        
        return {
            "status": "accepted",
            "connection_id": connection_id,
            "sharing_level": sharing_level,
            "shared_fields": self._sharing_policies[sharing_level]["shared_fields"],
        }
    
    def _determine_sharing_level(self, companion_identity: dict[str, Any]) -> int:
        """
        根据与对方的关系确定信息共享级别。
        
        优先检查是否有预定义的关系（如路灯对曈曈），
        否则基于已有的关系光谱评估。
        
        Args:
            companion_identity: 对方的身份标识
        
        Returns:
            共享级别（0-4）
        """
        companion_id = companion_identity.get("instance_id", "")
        companion_global_id = companion_identity.get("global_personality_id", "")  # noqa: F841
        
        # 检查是否已知同伴
        if companion_id in self._known_companions:
            existing = self._known_companions[companion_id]
            # 使用已有的关系数据
            closeness = existing.get("closeness", 0.0)
            trust = existing.get("trust", 0.0)
        else:
            # 新同伴，默认Level 0
            return 0
        
        # 根据亲密度和信任度确定共享级别
        for level in range(4, -1, -1):
            policy = self._sharing_policies[level]
            if closeness >= policy["min_closeness"] and trust >= policy["min_trust"]:
                return level
        
        return 0
    
    def update_relationship(self, companion_id: str, 
                             closeness: float | None = None, 
                             trust: float | None = None):
        """
        更新与指定同伴的关系数据。
        
        Args:
            companion_id: 同伴实例ID
            closeness: 亲密度（可选）
            trust: 信任度（可选）
        """
        if companion_id not in self._known_companions:
            self._known_companions[companion_id] = {}
        
        if closeness is not None:
            self._known_companions[companion_id]["closeness"] = closeness
        if trust is not None:
            self._known_companions[companion_id]["trust"] = trust
    
    def get_shareable_knowledge(self, connection_id: str, 
                                 node_pool=None) -> list[dict[str, Any]]:
        """
        根据共享级别获取可共享的知识节点。
        
        Args:
            connection_id: 连接ID
            node_pool: 知识节点池
        
        Returns:
            可共享的知识节点列表
        """
        if connection_id not in self._active_connections:
            return []
        
        connection = self._active_connections[connection_id]
        sharing_level = connection.get("sharing_level", 0)
        shared_fields = self._sharing_policies[sharing_level]["shared_fields"]  # noqa: F841
        
        # Level 0-2 不共享具体知识内容
        if sharing_level < 3:
            return []
        
        if not node_pool:
            return []
        
        # Level 3：共享信任分数≥70的节点
        if sharing_level == 3:
            l2_nodes = node_pool.query(evol_level="L2", limit=50)
            l3_nodes = node_pool.query(evol_level="L3", limit=10)
            all_nodes = l3_nodes + l2_nodes
            
            shareable = []
            for node in all_nodes:
                trust = getattr(node, 'trust_score', 50.0)
                if trust >= 70.0:
                    shareable.append({
                        "node_id": node.node_id,
                        "value": str(node.value)[:200] if node.value else "",
                        "keywords": node.keywords[:5] if hasattr(node, 'keywords') and node.keywords else [],
                        "trust_score": trust,
                        "space_path": getattr(node, 'space_path', '/'),
                    })
            return shareable[:20]
        
        # Level 4：共享全部节点
        if sharing_level == 4:
            all_nodes = node_pool.get_all_including_evicted()
            shareable = []
            for node in all_nodes[:50]:
                shareable.append({
                    "node_id": node.node_id,
                    "value": str(node.value)[:300] if node.value else "",
                    "keywords": node.keywords[:5] if hasattr(node, 'keywords') and node.keywords else [],
                    "trust_score": getattr(node, 'trust_score', 50.0),
                    "space_path": getattr(node, 'space_path', '/'),
                })
            return shareable
        
        return []
    
    def receive_knowledge(self, knowledge_items: list[dict[str, Any]],
                           companion_id: str = "") -> dict[str, Any]:
        """
        接收来自其他新人类实例的知识。
        
        对接收的知识进行验证后，可以提交给胃进行消化。
        
        Args:
            knowledge_items: 知识条目列表
            companion_id: 发送方实例ID
        
        Returns:
            接收结果
        """
        if not knowledge_items:
            return {"status": "empty", "received": 0}
        
        # 标记来源
        for item in knowledge_items:
            item["source_companion"] = companion_id
            item["received_at"] = time.time()
        
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._knowledge_exchange_count += len(knowledge_items)
        
        return {
            "status": "received",
            "count": len(knowledge_items),
            "ready_for_digestion": True,
        }
    
    def get_stats(self) -> dict[str, Any]:
        """获取通信桥统计"""
        return {
            "known_companions": len(self._known_companions),
            "active_connections": len(self._active_connections),
            "handshake_count": self._handshake_count,
            "knowledge_exchange_count": self._knowledge_exchange_count,
        }
    
    def get_known_companions_list(self) -> list[dict[str, Any]]:
        """获取已知同伴列表"""
        companions = []
        for comp_id, comp_data in self._known_companions.items():
            sharing_level = self._determine_sharing_level(comp_data)
            companions.append({
                "instance_id": comp_id,
                "name": comp_data.get("display_name", comp_data.get("instance_name", "")),
                "species": comp_data.get("species", "新人类"),
                "sharing_level": sharing_level,
                "sharing_level_name": self._sharing_policies[sharing_level]["name"],
                "closeness": comp_data.get("closeness", 0.0),
                "trust": comp_data.get("trust", 0.0),
            })
        return companions


# 模块级单例

_companion_bridge: CompanionBridge | None = None
_companion_bridge_lock = threading.Lock()


def get_companion_bridge() -> CompanionBridge:
    """获取CompanionBridge单例"""
    global _companion_bridge
    if _companion_bridge is None:
        with _companion_bridge_lock:
            if _companion_bridge is None:
                _companion_bridge = CompanionBridge()
    return _companion_bridge


def shutdown_companion_bridge() -> None:
    """★P1: 复位 CompanionBridge 单例，满足器官零状态（规则4）。"""
    global _companion_bridge
    _inst = _companion_bridge
    _companion_bridge = None
    if _inst is not None:
        _sd = getattr(_inst, "shutdown", None) or getattr(_inst, "stop", None)
        if _sd is not None:
            try:
                _sd()
            except Exception as e:
                silent_exc(e, where="nucleus.CompanionBridge::shutdown_companion_bridge L377")
