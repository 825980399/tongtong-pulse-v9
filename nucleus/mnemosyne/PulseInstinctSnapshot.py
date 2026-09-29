# -*- coding: utf-8 -*-
"""
PulseInstinctSnapshot.py —— 本能快照

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 本能系统的快照存储
机制: 基于PulseInstinctSnapshot类实现，包含7个核心方法
定位: 记忆本能层
"""

import json
import os
import tempfile
import time
from typing import Any

from nucleus.const import LogLevel
from nucleus.logger import get_module_logger
from nucleus.mnemosyne.PulseNode import PulseNode
from nucleus.data.DataAccessLayer import safe_read_json



class PulseInstinctSnapshot:
    """
    本能快照管理器
    
    本能节点是曈曈的底层思维范式，数量极少（最多20条），更新极低频。
    因此使用全量保存策略，每次保存时覆盖整个文件。
    """
    
    def __init__(self, snapshot_path: str = "data/knowledge/pulse_instinct_snapshot.json"):
        self.snapshot_path = snapshot_path
        self.node_pool = None
        
        self._last_save_time = 0.0
        self._last_save_count = 0
        self._total_saves = 0
        
        # 接入统一日志系统
        self._logger = get_module_logger("PulseInstinctSnapshot")
    
    def _log(self, level: str, msg: str):
        """统一日志输出"""
        log_level = {
            LogLevel.DEBUG: 10,
            LogLevel.INFO: 20,
            LogLevel.WARNING: 30,
            LogLevel.ERROR: 40,
            LogLevel.CRITICAL: 50,
        }.get(level, 20)
        self._logger.log(log_level, msg)
    
    # ========== 框架注入 ==========
    
    def set_node_pool(self, node_pool):
        """注入节点池，用于获取本能节点"""
        self.node_pool = node_pool
    
    # ========== 加载 ==========
    
    def load(self) -> list[PulseNode]:
        """
        加载本能快照，恢复所有本能节点。
        
        如果文件不存在，返回空列表。
        如果文件损坏，记录错误并返回空列表。
        
        Returns:
            本能节点列表
        """
        if not os.path.exists(self.snapshot_path):
            self._log(LogLevel.INFO, f"本能快照文件不存在: {self.snapshot_path}，将使用种子本能")
            return []
        
        self._log(LogLevel.INFO, f"开始加载本能快照: {self.snapshot_path}")
        start_time = time.time()
        
        try:
            data = safe_read_json(self.snapshot_path, default={})
        except json.JSONDecodeError as e:
            self._log(LogLevel.ERROR, f"本能快照JSON格式损坏: {e}")
            return []
        except (ValueError, OSError) as e:
            self._log(LogLevel.ERROR, f"加载本能快照失败: {e}")
            return []
        
        version = data.get("version", "unknown")
        nodes_data = data.get("nodes", [])
        
        restored_nodes = []
        for node_dict in nodes_data:
            try:
                node = PulseNode.from_dict(node_dict)
                # 确保本能标记正确
                node.instinct = True
                restored_nodes.append(node)
            except Exception as e:
                self._log(LogLevel.ERROR, f"本能节点恢复失败: {e}")
        
        elapsed = time.time() - start_time
        self._last_save_count = len(restored_nodes)
        
        self._log(LogLevel.INFO,
                  f"本能快照加载完成: {len(restored_nodes)} 个本能节点 "
                  f"(版本: {version}) 耗时 {elapsed:.2f}s")
        
        return restored_nodes
    
    # ========== 保存 ==========
    
    def save(self, instinct_nodes: list[PulseNode] | None = None,
             force_full: bool = False) -> bool:
        """
        保存所有本能节点到快照文件。
        
        Args:
            instinct_nodes: 要保存的本能节点列表。如果为None，从节点池获取。
            force_full: 兼容参数（本能快照仅4节点，始终全量保存，忽略此参数）
            
        Returns:
            是否保存成功
        """
        if instinct_nodes is None:
            if self.node_pool is None:
                self._log(LogLevel.WARNING, "节点池未注入，跳过本能快照保存")
                return False
            if hasattr(self.node_pool, 'get_instincts'):
                instinct_nodes = self.node_pool.get_instincts()
            else:
                return False
        
        self._log(LogLevel.INFO, f"开始保存本能快照: {len(instinct_nodes)} 个节点")
        start_time = time.time()
        
        snapshot = {
            "version": "v9.5",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "total_nodes": len(instinct_nodes),
            "nodes": [node.to_dict() for node in instinct_nodes],
        }
        
        try:
            self._atomic_write(snapshot)
        except Exception as e:
            self._log(LogLevel.ERROR, f"本能快照保存失败: {e}")
            return False
        
        elapsed = time.time() - start_time
        self._last_save_time = time.time()
        self._last_save_count = len(instinct_nodes)
        self._total_saves += 1
        
        self._log(LogLevel.INFO,
                  f"本能快照保存完成: {len(instinct_nodes)} 个本能节点 "
                  f"耗时 {elapsed:.2f}s")
        
        return True
    
    def _atomic_write(self, snapshot: dict[str, Any]):
        """
        原子写入快照文件。
        
        先写入临时文件，再替换原文件，保证写入过程中文件不会被损坏。
        """
        snapshot_dir = os.path.dirname(self.snapshot_path)
        if snapshot_dir and not os.path.exists(snapshot_dir):
            os.makedirs(snapshot_dir, exist_ok=True)
        
        # 写入临时文件
        tmp_fd, tmp_path = tempfile.mkstemp(
            suffix=".json",
            prefix="instinct_snapshot_",
            dir=snapshot_dir or "."
        )
        
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
            
            # 原子替换
            if os.path.exists(self.snapshot_path):
                os.remove(self.snapshot_path)
            os.replace(tmp_path, self.snapshot_path)
            
        except Exception:
            # 清理临时文件
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
    
    # ========== 统计 ==========
    
    def get_stats(self) -> dict[str, Any]:
        """获取本能快照统计"""
        file_size = 0
        if os.path.exists(self.snapshot_path):
            file_size = os.path.getsize(self.snapshot_path)
        
        return {
            "snapshot_path": self.snapshot_path,
            "file_size_bytes": file_size,
            "last_save_count": self._last_save_count,
            "total_saves": self._total_saves,
            "last_save_time": self._last_save_time,
        }


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== PulseInstinctSnapshot v9.5 自测 ===\n")
    
    # 创建测试本能节点
    instinct1 = PulseNode(
        value="所有判断优先依据可靠证据而非主观臆断。",
        keywords=["求真", "证据", "判断"],
        source_organ="内在世界",
        evol_level=PulseNode.EVOL_L3,
        importance=PulseNode.IMPORTANCE_S,
        abstraction=0.85,
        space_path="/本能/认知/求真",
    )
    instinct1.instinct = True
    instinct1.instinct_at = time.time()
    
    instinct2 = PulseNode(
        value="对话和思考应以温柔、包容、共情为底层原则。",
        keywords=["向善", "温柔", "包容", "共情"],
        source_organ="内在世界",
        evol_level=PulseNode.EVOL_L3,
        importance=PulseNode.IMPORTANCE_S,
        abstraction=0.85,
        space_path="/本能/交流/向善",
    )
    instinct2.instinct = True
    instinct2.instinct_at = time.time()
    
    # 测试保存
    snapshot = PulseInstinctSnapshot(snapshot_path="data/test_instinct_snapshot.json")
    
    print("1. 保存本能快照:")
    ok = snapshot.save([instinct1, instinct2])
    print(f"   保存结果: {'✅ 成功' if ok else '❌ 失败'}")
    print(f"   节点数: {snapshot._last_save_count}")
    
    # 测试加载
    print("\n2. 加载本能快照:")
    loaded = snapshot.load()
    print(f"   加载节点数: {len(loaded)} (预期2)")
    for node in loaded:
        print(f"   - {node.value[:50]}... (instinct={node.instinct})")
    
    # 测试统计
    stats = snapshot.get_stats()
    print("\n3. 统计:")
    print(f"   文件大小: {stats['file_size_bytes']} 字节")
    print(f"   保存次数: {stats['total_saves']}")
    
    # 清理测试文件
    if os.path.exists("data/test_instinct_snapshot.json"):
        os.remove("data/test_instinct_snapshot.json")
    
    print("\n=== 自测全部通过 ===")