# -*- coding: utf-8 -*-
"""
KnowledgeTree.py —— 知识树

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 为每个PulseNode提供唯一space_path空间坐标，五维归属计算确定新知识最优父节点，知识集群自动拆分与碎片合并，拓扑健康巡检
机制: 基于KnowledgeTree类实现，五维空间坐标系，节点数≥12且纯度≤0.8时自动拆分，空闲时合并小集群，四维健康评分
定位: 记忆结构层，知识空间组织核心
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import math
import threading
import time
from typing import Any


"""
KnowledgeTree —— 知识树空间坐标系
版本: v9.0 PulseNet
设计: 路灯、小林、星轨
日期: 2026年6月9日

职责:
    1. 为每个 PulseNode 提供唯一的 space_path（空间坐标）
    2. 五维归属计算 —— 确定新知识的最优父节点
    3. 知识集群拆分 —— 节点数≥12且纯度≤0.8时自动拆分
    4. 碎片合并 —— 空闲时合并小集群
    5. 拓扑健康巡检 —— 四维健康评分

与 v8.0 的区别:
    - v8.0: KnowledgeTree 承担存储+索引+拓扑，三合一
    - v9.0: KnowledgeTree 只负责空间坐标系，知识内容由 PulseNodePool 管理
"""


class KnowledgeTree:
    """
    知识树空间坐标系
    
    以树状路径（类似文件路径）组织知识的空间位置。
    例如: /技术/编程/Python/语法/列表推导式
    
    每个路径节点记录其下的子节点数量和最后更新时间，
    用于拆分/合并决策。
    """
    
    def __init__(self):
        # 路径节点统计: space_path → {"count": N, "updated_at": timestamp}
        self._path_stats: dict[str, dict[str, Any]] = {}
        
        # 根节点
        self._path_stats["/"] = {"count": 0, "updated_at": time.time()}
        # 【v12.0新增】预注册自我认知路径
        for _self_path in ["/自我", "/自我/状态", "/自我/状态/知识", "/自我/状态/系统", "/自我/状态/健康",
                           "/自我理解", "/自我理解/代码"]:
            self._path_stats[_self_path] = {"count": 0, "updated_at": time.time()}
        self._lock = threading.Lock()          # ← 新增        
        # 统计
        self._total_find_calls = 0
        self._total_splits = 0
        self._total_merges = 0
        
    # ========== 核心方法：五维归属 ==========
    
    def find_optimal_parent(self, 
                            keywords: list[str],
                            abstraction: float = 0.0,
                            candidate_paths: list[str] | None = None) -> str:
        """
        五维归属计算 —— 为新知识确定最优父节点。
        
        算法:
            1. 从候选路径（或全部路径）中筛选
            2. 对每个候选计算关键词重叠度 × 抽象层级匹配度
            3. 返回得分最高的路径
            
        Args:
            keywords: 新知识的关键词列表
            abstraction: 新知识的抽象度 0.0-1.0
            candidate_paths: 候选父路径列表（None = 全部路径）
            
        Returns:
            最优父路径（如 "/技术/编程/Python"）
        """
        self._total_find_calls += 1
        
        if candidate_paths is None:
            # ★P1-3修复：加锁获取路径快照，避免并发修改导致RuntimeError
            with self._lock:
                candidate_paths = list(self._path_stats.keys())
        
        if not candidate_paths:
            return "/"
        
        best_path = "/"
        best_score = 0.0
        
        for path in candidate_paths:
            score = self._calculate_affinity(keywords, abstraction, path)
            if score > best_score:
                best_score = score
                best_path = path
        
        # 如果没有匹配（best_score == 0），返回根节点
        return best_path
    
    def get_cluster_path(self, prefix: str) -> list[str]:
        if not prefix.endswith("/"):
            prefix += "/"
        # ★P1-3修复：加锁获取快照后遍历
        with self._lock:
            paths_snapshot = list(self._path_stats.keys())
        return [p for p in paths_snapshot if p.startswith(prefix)]
    
    # ========== 路径注册与更新 ==========
    def register_path(self, space_path: str):
        """
        注册或更新一个空间路径。
        新知识写入时调用，递增该路径及其所有祖先路径的节点计数。
        线程安全：写操作在锁保护下执行。
        """
        
        if not space_path:
            return
        
        if not space_path.startswith("/"):
            space_path = "/" + space_path
        
        with self._lock:
            # 更新当前路径
            if space_path in self._path_stats:
                self._path_stats[space_path]["count"] += 1
                self._path_stats[space_path]["updated_at"] = time.time()
            else:
                self._path_stats[space_path] = {
                    "count": 1,
                    "updated_at": time.time(),
                }
            
            # 级联更新所有祖先路径（包括根节点）
            parts = space_path.strip("/").split("/")
            
            # 更新根节点
            if "/" in self._path_stats:
                self._path_stats["/"]["count"] += 1
                self._path_stats["/"]["updated_at"] = time.time()
            
            # 更新中间祖先
            for i in range(1, len(parts)):
                ancestor = "/" + "/".join(parts[:i])
                if ancestor in self._path_stats:
                    self._path_stats[ancestor]["count"] += 1
                    self._path_stats[ancestor]["updated_at"] = time.time()
                else:
                    self._path_stats[ancestor] = {
                        "count": 1,
                        "updated_at": time.time(),
                    }


    def register_paths_batch(self, paths: list[str]) -> int:
        """★启动优化：批量注册路径，单次锁获取，减少8130次锁竞争。

        返回成功注册的路径数。
        """
        if not paths:
            return 0
        _count = 0
        with self._lock:
            for space_path in paths:
                if not space_path:
                    continue
                if not space_path.startswith("/"):
                    space_path = "/" + space_path
                # 更新当前路径
                if space_path in self._path_stats:
                    self._path_stats[space_path]["count"] += 1
                    self._path_stats[space_path]["updated_at"] = time.time()
                else:
                    self._path_stats[space_path] = {
                        "count": 1,
                        "updated_at": time.time(),
                    }
                # 级联更新祖先路径
                parts = space_path.strip("/").split("/")
                if "/" in self._path_stats:
                    self._path_stats["/"]["count"] += 1
                    self._path_stats["/"]["updated_at"] = time.time()
                else:
                    self._path_stats["/"] = {"count": 1, "updated_at": time.time()}
                for i in range(1, len(parts)):
                    ancestor = "/" + "/".join(parts[:i])
                    if ancestor in self._path_stats:
                        self._path_stats[ancestor]["count"] += 1
                        self._path_stats[ancestor]["updated_at"] = time.time()
                    else:
                        self._path_stats[ancestor] = {
                            "count": 1,
                            "updated_at": time.time(),
                        }
                _count += 1
        return _count

    def unregister_path(self, space_path: str):
        """
        注销一个空间路径（节点被淘汰时调用）。
        线程安全：写操作在锁保护下执行。
        """
        with self._lock:
            if not space_path or space_path not in self._path_stats:
                return
            
            self._path_stats[space_path]["count"] = max(0, self._path_stats[space_path]["count"] - 1)
            
            # 如果计数归零且非根节点，保留路径（作为历史坐标）
            # 但标记为"空"
            if self._path_stats[space_path]["count"] <= 0 and space_path != "/":
                self._path_stats[space_path]["count"] = 0
            
            # 级联更新所有祖先路径（包括根节点）
            parts = space_path.strip("/").split("/")
            
            # 显式更新根节点
            if "/" in self._path_stats:
                self._path_stats["/"]["count"] = max(0, self._path_stats["/"]["count"] - 1)
            
            # 更新中间祖先
            for i in range(1, len(parts)):
                ancestor = "/" + "/".join(parts[:i])
                if ancestor in self._path_stats:
                    self._path_stats[ancestor]["count"] = max(0, self._path_stats[ancestor]["count"] - 1)
    
    # ========== 公开快照接口（P0-批次3：替代跨器官私有穿透 AP1/规则14） ==========
    def get_all_paths(self) -> list[str]:
        """线程安全返回全部路径列表（替代直接读取 _path_stats.keys()）。"""
        with self._lock:
            return list(self._path_stats.keys())

    def get_path_stats_snapshot(self) -> dict[str, dict[str, Any]]:
        """线程安全返回路径统计的副本（替代直接迭代 _path_stats.items()）。"""
        with self._lock:
            return dict(self._path_stats)

    def reset_path_count(self, space_path: str):
        """将某路径计数归零（不删除路径），线程安全。
        用于「已注册但未沉淀内容」的占位路径，替代直接写 _path_stats[path] = {...}。"""
        if not space_path:
            return
        if not space_path.startswith("/"):
            space_path = "/" + space_path
        with self._lock:
            if space_path in self._path_stats:
                self._path_stats[space_path]["count"] = 0
                self._path_stats[space_path]["updated_at"] = time.time()

    # ========== 集群拆分 ==========
    def split_cluster(self, space_path: str, min_size: int = 12, 
                       max_purity: float = 0.8) -> list[str] | None:
        # ★P1-3修复：加锁读取集群信息和子路径快照
        with self._lock:
            if space_path not in self._path_stats:
                return None
            
            cluster_info = self._path_stats[space_path]
            count = cluster_info["count"]
            
            if count < min_size:
                return None
            
            # 获取所有直接子路径快照
            prefix = space_path.rstrip("/") + "/"
            children = [p for p in self._path_stats.keys()  # noqa: SIM118
                       if p.startswith(prefix) and p.count("/") == space_path.count("/") + 1]

            # ★P0-批次2修复：纯度计算需在锁内完成，
            # _calculate_purity 会读取 self._path_stats，若在锁外调用会与并发写产生 RuntimeError
            purity = self._calculate_purity(space_path, children)
            if purity > max_purity:
                return None

        if len(children) < 2:
            return None

        # 执行拆分（写操作在锁保护下进行）
        new_paths = []
        base_path = space_path.rstrip("/")
        chunk_size = max(1, len(children) // 3)
        
        with self._lock:
            for i in range(3):
                start = i * chunk_size
                if i == 2:
                    chunk = children[start:]
                else:
                    chunk = children[start:start + chunk_size]
                
                if not chunk:
                    continue
                
                first_child = chunk[0]
                cluster_name = first_child.rstrip("/").split("/")[-1]
                new_path = f"{base_path}/{cluster_name}_cluster"
                
                if new_path not in self._path_stats:
                    self._path_stats[new_path] = {
                        "count": sum(self._path_stats.get(c, {}).get("count", 0) for c in chunk),
                        "updated_at": time.time(),
                    }
                    new_paths.append(new_path)
            
            if new_paths:
                self._total_splits += 1
                moved_count = sum(self._path_stats[p]["count"] for p in new_paths)
                self._path_stats[space_path]["count"] = max(0, count - moved_count)
        
        return new_paths or None
    
    # ========== 碎片合并 ==========
    def merge_fragments(self, max_fragments: int = 3, 
                         min_age_seconds: float = 0.0) -> int:
        now = time.time()
        
        # ★P1-3修复：加锁收集所有路径和中间路径快照
        with self._lock:
            all_paths = set(self._path_stats.keys())
            intermediate_paths = set()
            
            for path in all_paths:
                if path == "/":
                    continue
                prefix = path + "/" if not path.endswith("/") else path
                has_children = any(p != path and p.startswith(prefix) for p in all_paths)
                if has_children:
                    intermediate_paths.add(path)
            
            # 找出碎片候选（在锁内完成遍历和收集）
            candidates = [
                (path, dict(info)) for path, info in self._path_stats.items()
                if path in intermediate_paths
                and path != "/"
                and info["count"] <= max_fragments
                and (now - info["updated_at"]) > min_age_seconds
            ]
        
        candidates.sort(key=lambda x: x[0].count("/"), reverse=True)
        
        # 执行合并（写操作在锁保护下进行）
        merged = 0
        with self._lock:
            for path, info in candidates:
                if path not in self._path_stats:
                    continue
                current_count = self._path_stats[path]["count"]
                if current_count > max_fragments:
                    continue
                
                parent = "/".join(path.strip("/").split("/")[:-1])
                parent = "/" + parent if parent else "/"
                
                if parent in self._path_stats:
                    self._path_stats[parent]["count"] += current_count
                    self._path_stats[parent]["updated_at"] = now
                
                del self._path_stats[path]
                merged += 1
        
        self._total_merges += merged
        return merged
    # ========== 拓扑健康 ==========
    
    def run_topology_health_check(self) -> dict[str, Any]:
        """
        四维健康评分。
        
        检查:
            1. 路径有效性（无孤立节点）
            2. 深度分布（不过深也不过浅）
            3. 集群大小均衡度
            4. 空节点比例
        """
        # ★P1-3修复：加锁获取路径快照
        with self._lock:
            paths_snapshot = dict(self._path_stats)
        
        total_paths = len(paths_snapshot)
        if total_paths <= 1:
            return {"score": 0, "status": "空树", "details": "知识树为空或仅根节点，需要注入知识"}        
        scores = {}
        
        # 1. 路径有效性（所有非根路径都有父路径）
        orphan_count = 0
        for path in paths_snapshot:
            if path == "/":
                continue
            parent = "/".join(path.strip("/").split("/")[:-1])
            parent = "/" + parent if parent else "/"
            if parent not in paths_snapshot:
                orphan_count += 1
        
        orphan_ratio = orphan_count / max(1, total_paths - 1)
        scores["validity"] = max(0, 100 - orphan_ratio * 100)
        
        # 2. 深度分布
        depths = [p.count("/") for p in paths_snapshot.keys() if p != "/"]  # noqa: SIM118
        if depths:
            avg_depth = sum(depths) / len(depths)
            scores["depth"] = 100 if 2 <= avg_depth <= 6 else (70 if avg_depth < 10 else 40)
        else:
            scores["depth"] = 100
        
        # 3. 集群均衡度（空路径占比）
        empty_paths = sum(1 for p, info in paths_snapshot.items() 
                         if p != "/" and info["count"] == 0)
        empty_ratio = empty_paths / max(1, total_paths - 1)
        scores["balance"] = max(0, 100 - empty_ratio * 200)
        
        # 4. 综合评分
        overall = (scores["validity"] * 0.3 + scores["depth"] * 0.3 + 
                   scores["balance"] * 0.4)
        
        status = "健康" if overall >= 80 else ("亚健康" if overall >= 50 else "需要维护")
        
        return {
            "score": round(overall, 1),
            "status": status,
            "details": {
                "total_paths": total_paths,
                "orphan_count": orphan_count,
                "empty_paths": empty_paths,
                "avg_depth": round(sum(depths) / len(depths), 1) if depths else 0,
            },
            "dimension_scores": scores,
        }
    
    # ========== 统计 ==========
    
    def get_stats(self) -> dict[str, Any]:
        """获取知识树统计（★P1-3修复：加锁获取快照）"""
        with self._lock:
            total_paths = len(self._path_stats)
            active_paths = sum(1 for p, info in self._path_stats.items() 
                              if info["count"] > 0)
            total_find = self._total_find_calls
            total_splits = self._total_splits
            total_merges = self._total_merges
        return {
            "total_paths": total_paths,
            "active_paths": active_paths,
            "empty_paths": total_paths - active_paths,
            "total_find_calls": total_find,
            "total_splits": total_splits,
            "total_merges": total_merges,
        }
    
    # ========== 内部方法 ==========
    
    def _calculate_affinity(self, keywords: list[str], abstraction: float, 
                             path: str) -> float:
        """
        计算关键词与路径的亲和度。
        
        路径的每一级作为"上下文关键词"与输入关键词做重叠计算。
        """
        if not keywords or path == "/":
            return 0.0
        
        # 将路径拆分为上下文关键词
        path_parts = path.strip("/").lower().split("/")
        path_keywords = set()
        for part in path_parts:
            # 拆分复合路径段（如 "Python语法" → ["python", "语法"]）
            # 简化处理：按常见分隔符拆分
            for sep in ["_", "-", " "]:
                part = part.replace(sep, " ")
            path_keywords.update(part.split())
        
        input_keywords = {k.lower() for k in keywords}
        
        if not path_keywords:
            return 0.0
        
        # 关键词重叠度
        overlap = len(input_keywords & path_keywords)
        overlap_score = overlap / max(1, len(input_keywords))
        
        # 抽象层级匹配度（路径越深，越适合低抽象度知识）
        depth = path.count("/")
        depth_score = 1.0 - abs(abstraction - min(1.0, depth / 10.0))
        
        return overlap_score * 0.7 + depth_score * 0.3
    
    def _calculate_purity(self, space_path: str, 
                           children: list[str]) -> float:
        """
        计算集群纯度。
        
        简化：基于子节点数量的离散度。
        如果所有子节点数量接近，说明集群均匀（纯度高）；
        如果差异大，说明内部已经分化（纯度低）。
        """
        if len(children) < 2:
            return 1.0
        
        counts = [self._path_stats.get(c, {}).get("count", 0) for c in children]
        if not counts or max(counts) == 0:
            return 1.0
        
        avg = sum(counts) / len(counts)
        if avg == 0:
            return 1.0
        
        # 变异系数（标准差/均值）
        variance = sum((c - avg) ** 2 for c in counts) / len(counts)
        cv = math.sqrt(variance) / avg
        
        # CV越小越均匀 → 纯度越高
        return max(0.0, min(1.0, 1.0 - cv))


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== KnowledgeTree 自测 ===\n")
    
    tree = KnowledgeTree()
    
    # 1. 注册路径
    paths = [
        "/技术/编程/Python",
        "/技术/编程/Python/语法",
        "/技术/编程/Python/库",
        "/技术/编程/JavaScript",
        "/技术/架构/脉冲场",
        "/身份/家庭/小林",
        "/身份/自我",
    ]
    
    for p in paths:
        tree.register_path(p)
    
    print(f"1. 注册路径: {len(paths)} 条")
    print(f"   根节点计数: {tree._path_stats['/']['count']} (应为 {len(paths)})")
    
    # 2. 五维归属
    result = tree.find_optimal_parent(
        keywords=["Python", "列表", "推导式"],
        abstraction=0.1,
    )
    print(f"2. 最优父节点: {result}")
    assert "Python" in result, f"应匹配到Python相关路径，实际: {result}"
    print("   ✅ 归属正确")
    
    # 3. 获取集群路径
    cluster = tree.get_cluster_path("/技术/编程")
    print(f"3. /技术/编程 集群: {len(cluster)} 条路径")
    for c in cluster:
        print(f"   - {c} ({tree._path_stats[c]['count']} 节点)")
    
    # 4. 注销路径
    tree.unregister_path("/技术/编程/Python/库")
    print(f"4. 注销后 /技术/编程/Python/库 计数: {tree._path_stats['/技术/编程/Python/库']['count']}")
    
    # 5. 碎片合并
    tree.register_path("/技术/编程/Go")  # 新增小集群
    merged = tree.merge_fragments(max_fragments=3)
    print(f"5. 碎片合并: {merged} 个 (Go集群计数≤3，应被合并)")
    
    # 6. 拓扑健康
    health = tree.run_topology_health_check()
    print(f"6. 拓扑健康: {health['score']}分 - {health['status']}")
    print(f"   详情: {health['details']}")
    
    # 7. 统计
    stats = tree.get_stats()
    print(f"7. 统计: {stats['total_paths']}路径 {stats['active_paths']}活跃 "
          f"拆分{stats['total_splits']}次 合并{stats['total_merges']}次")
    
    print("\n=== 自测全部通过 ===")