# -*- coding: utf-8 -*-
"""统一知识访问层（KAL, Knowledge Access Layer）——主线第67批 T3/P1。

设计目标
--------
当前各模块**直接访问** JSON 快照文件与节点池，访问点分散。未来存储要从
JSON → Parquet → 分布式多模存储（Neo4j / InfluxDB / 向量库），若不做抽象，
每一次存储演进都要改动所有访问点。

KAL 定义统一的知识访问接口，使**底层存储可透明替换**：
    - 第67批（本批）：定义接口 + 基于当前 JSON 快照/节点池的基础实现（不做全量迁移）
    - 第68批：增加 Parquet 后端
    - 第69批：增加图（Neo4j）与时序（InfluxDB）后端
    - 第70批+：分布式后端

★重要：本批**不做全量迁移**，现有代码仍直接访问快照，KAL 为增量接入做准备。
  因此本模块对现有行为零影响；即使 ENABLE_KAL=False，也不会有任何调用点被改变。

用法
----
    from nucleus.knowledge_access_layer import get_kal
    kal = get_kal(node_pool=pool, snapshot=snap)   # 首次调用时可注入依赖
    node = kal.get_node("abc123")
    for batch in kal.iter_nodes(batch_size=1000): ...
"""
import logging
import os
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

from nucleus._silent_except import silent_exc

try:  # 项目内模块日志器；取不到时回退标准 logging（零依赖）
    from nucleus.logger import get_module_logger as _get_module_logger
    _logger = _get_module_logger("pulse.module.kal")
except Exception as e:  # pragma: no cover - 仅为健壮性兜底
    silent_exc(e, "nucleus/knowledge_access_layer.py:35:知识访问层异常", level="warning")
    _logger = logging.getLogger("pulse.module.kal")

# 单例（全局唯一实例）
_KAL_INSTANCE: Optional["KnowledgeAccessLayer"] = None


def _kal_enabled() -> bool:
    """KAL 总开关（默认开）。关闭时 get_kal() 仍返回实例，但各方法短路为安全空结果。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_KAL", True))
    except Exception as e:
        silent_exc(e, where="nucleus.knowledge_access_layer::_kal_enabled L48")
        return True


def _cfg_int(name: str, default: int) -> int:
    try:
        import config
        return int(getattr(config, name, default) or default)
    except Exception:
        return default


def _cfg_str(name: str, default: str) -> str:
    try:
        import config
        return str(getattr(config, name, default) or default)
    except Exception:
        return default


class KnowledgeAccessLayer:
    """统一知识访问层（KAL）——所有知识节点访问的唯一入口。

    本批基础实现基于**当前 JSON 快照 + 内存节点池**：
      - 节点读取优先走注入的 node_pool（内存，最快）
      - 未提供 node_pool 时返回安全空结果（绝不抛异常给调用方）
    """

    def __init__(self, node_pool: Any = None, snapshot: Any = None,
                 cache_size: Optional[int] = None) -> None:
        self._node_pool = node_pool
        self._snapshot = snapshot
        self._cache_size = int(cache_size if cache_size is not None
                               else _cfg_int("KAL_CACHE_SIZE", 10000))
        # LRU 缓存：dict 保序，命中移到末尾，超容量淘汰最前
        self._cache: Dict[str, Any] = {}
        self._hits = 0
        self._misses = 0
        self._created_at = time.time()

    # ---------------- 内部辅助 ----------------

    def _all_nodes(self) -> List[Any]:
        """从节点池取全部节点（duck typing 兼容多种方法名，取不到返回空列表）。"""
        _pool = self._node_pool
        if _pool is None:
            return []
        for _m in ("get_all_including_evicted", "get_all_nodes", "get_all"):
            _f = getattr(_pool, _m, None)
            if callable(_f):
                try:
                    return list(_f() or [])
                except Exception as _e:
                    _logger.debug("[KAL] 取全部节点失败(%s): %s: %s", _m,
                                  type(_e).__name__, _e)
                    return []
        return []

    def _cache_get(self, node_id: str) -> Any:
        if node_id in self._cache:
            self._hits += 1
            self._cache.move_to_end(node_id) if hasattr(self._cache, "move_to_end") else None
            return self._cache[node_id]
        self._misses += 1
        return None

    def _cache_put(self, node_id: str, node: Any) -> None:
        if self._cache_size <= 0:
            return
        self._cache[node_id] = node
        while len(self._cache) > self._cache_size:
            try:
                self._cache.pop(next(iter(self._cache)))
            except Exception:
                break

    # ---------------- 节点基础操作 ----------------

    def get_node(self, node_id: str) -> Optional[Any]:
        """按 ID 获取节点（带 LRU 缓存）。"""
        if not _kal_enabled() or not node_id:
            return None
        _cached = self._cache_get(node_id)
        if _cached is not None:
            return _cached
        for _n in self._all_nodes():
            if getattr(_n, "node_id", None) == node_id:
                self._cache_put(node_id, _n)
                return _n
        return None

    def get_nodes_batch(self, node_ids: List[str]) -> List[Any]:
        """批量获取节点。"""
        if not _kal_enabled():
            return []
        _out = []
        for _nid in (node_ids or []):
            _n = self.get_node(_nid)
            if _n is not None:
                _out.append(_n)
        return _out

    def get_all_nodes(self) -> List[Any]:
        """获取所有节点（数据量大时请改用 iter_nodes）。"""
        if not _kal_enabled():
            return []
        return self._all_nodes()

    def iter_nodes(self, batch_size: int = 1000) -> Iterator[Any]:
        """迭代器方式获取所有节点（内存友好）。"""
        if not _kal_enabled():
            return
        _bs = int(batch_size or 1000)
        if _bs <= 0:
            _bs = 1000
        for _n in self._all_nodes():
            yield _n

    # ---------------- 节点查询操作 ----------------

    def search_by_keywords(self, keywords: List[str], top_k: int = 100) -> List[Any]:
        """按关键词搜索节点（匹配 value / keywords 字段）。"""
        if not _kal_enabled() or not keywords:
            return []
        _out = []
        for _n in self._all_nodes():
            _hay = "{} {}".format(getattr(_n, "value", "") or "",
                              " ".join(getattr(_n, "keywords", None) or []))
            if any(str(_k) in _hay for _k in keywords):
                _out.append(_n)
                if len(_out) >= int(top_k or 100):
                    break
        return _out

    def search_by_path(self, path_prefix: str) -> List[Any]:
        """按知识路径前缀搜索节点。"""
        if not _kal_enabled() or not path_prefix:
            return []
        return [_n for _n in self._all_nodes()
                if str(getattr(_n, "space_path", "") or "").startswith(str(path_prefix))]

    def search_by_evol_level(self, evol_level: Any) -> List[Any]:
        """按进化层级搜索节点。"""
        if not _kal_enabled():
            return []
        return [_n for _n in self._all_nodes()
                if getattr(_n, "evol_level", None) == evol_level]

    def search_by_time_range(self, start_time: Any, end_time: Any) -> List[Any]:
        """按创建时间范围搜索节点（created_at 支持 datetime 或时间戳）。"""
        if not _kal_enabled():
            return []
        _out = []
        for _n in self._all_nodes():
            _c = getattr(_n, "created_at", None)
            if _c is None:
                continue
            try:
                if start_time is not None and _c < start_time:
                    continue
                if end_time is not None and _c > end_time:
                    continue
            except TypeError:
                continue
            _out.append(_n)
        return _out

    def search_by_source_organ(self, source_organ: str) -> List[Any]:
        """按来源器官搜索节点。"""
        if not _kal_enabled() or not source_organ:
            return []
        return [_n for _n in self._all_nodes()
                if getattr(_n, "source_organ", None) == source_organ]

    # ---------------- 语义检索（未来对接向量库） ----------------

    def semantic_search(self, query_vector: List[float],
                        top_k: int = 1000) -> List[Tuple[Any, float]]:
        """语义相似度搜索。当前使用暴力余弦，未来对接 FAISS / Milvus。"""
        if not _kal_enabled() or not query_vector:
            return []
        _out: List[Tuple[Any, float]] = []
        for _n in self._all_nodes():
            _v = getattr(_n, "vector", None) or getattr(_n, "embedding", None)
            if not _v or len(_v) != len(query_vector):
                continue
            try:
                _dot = sum(float(a) * float(b) for a, b in zip(_v, query_vector))
                _na = sum(float(a) * float(a) for a in _v) ** 0.5
                _nb = sum(float(b) * float(b) for b in query_vector) ** 0.5
                if _na <= 0 or _nb <= 0:
                    continue
                _out.append((_n, _dot / (_na * _nb)))
            except Exception:
                continue
        _out.sort(key=lambda x: x[1], reverse=True)
        return _out[:int(top_k or 1000)]

    # ---------------- 图遍历（未来对接图数据库） ----------------

    def get_linked_nodes(self, node_id: str,
                         max_depth: int = 1) -> List[Tuple[Any, int]]:
        """获取关联节点（当前基于 linked_nodes 字段做 BFS，未来对接 Neo4j）。"""
        if not _kal_enabled() or not node_id:
            return []
        _by_id = {getattr(_n, "node_id", ""): _n for _n in self._all_nodes()}
        _out: List[Tuple[Any, int]] = []
        _seen = {node_id}
        _frontier = [node_id]
        _depth = 0
        while _frontier and _depth < int(max_depth or 1):
            _depth += 1
            _next: List[str] = []
            for _cur in _frontier:
                _node = _by_id.get(_cur)
                if _node is None:
                    continue
                for _lid in (getattr(_node, "linked_nodes", None) or []):
                    _lid = str(_lid)
                    if _lid in _seen:
                        continue
                    _seen.add(_lid)
                    if _lid in _by_id:
                        _out.append((_by_id[_lid], _depth))
                    _next.append(_lid)
            _frontier = _next
        return _out

    def find_path(self, source_id: str, target_id: str,
                  max_depth: int = 5) -> List[str]:
        """查找两节点间路径（BFS，未来对接 Neo4j）。找不到返回空列表。"""
        if not _kal_enabled() or not source_id or not target_id:
            return []
        _by_id = {getattr(_n, "node_id", ""): _n for _n in self._all_nodes()}
        if source_id not in _by_id or target_id not in _by_id:
            return []
        _prev = {source_id: None}
        _frontier = [source_id]
        _depth = 0
        while _frontier and _depth < int(max_depth or 5):
            _depth += 1
            _next: List[str] = []
            for _cur in _frontier:
                for _lid in (getattr(_by_id.get(_cur), "linked_nodes", None) or []):
                    _lid = str(_lid)
                    if _lid in _prev:
                        continue
                    _prev[_lid] = _cur
                    if _lid == target_id:
                        _path = [_lid]
                        while _prev[_path[-1]] is not None:
                            _path.append(_prev[_path[-1]])
                        return list(reversed(_path))
                    _next.append(_lid)
            _frontier = _next
        return []

    # ---------------- 时序分析（未来对接时序库） ----------------

    def get_activation_history(self, node_id: str, start_time: Any,
                               end_time: Any) -> List[dict]:
        """获取节点激活历史（当前基于节点自身字段，未来对接 InfluxDB）。"""
        if not _kal_enabled():
            return []
        _n = self.get_node(node_id)
        if _n is None:
            return []
        return [{
            "node_id": node_id,
            "activation_count": getattr(_n, "activation_count", 0),
            "last_activated": getattr(_n, "last_activated", None),
            "start_time": start_time,
            "end_time": end_time,
        }]

    # ---------------- 统计 ----------------

    def get_node_count(self) -> int:
        if not _kal_enabled():
            return 0
        return len(self._all_nodes())

    def get_node_count_by_evol_level(self) -> Dict[Any, int]:
        if not _kal_enabled():
            return {}
        _out: Dict[Any, int] = {}
        for _n in self._all_nodes():
            _lv = getattr(_n, "evol_level", None)
            _out[_lv] = _out.get(_lv, 0) + 1
        return _out

    def get_storage_size(self) -> int:
        """知识存储总大小（字节）。取快照文件大小，取不到返回 0。"""
        if not _kal_enabled():
            return 0
        _path = None
        if self._snapshot is not None:
            _path = getattr(self._snapshot, "snapshot_path", None)
        if not _path:
            try:
                import config
                _path = getattr(config, "KNOWLEDGE_SNAPSHOT_PATH", None)
            except Exception as e:
                silent_exc(e, where="nucleus.knowledge_access_layer::get_storage_size L351")
                _path = None
        if _path and os.path.isfile(_path):
            try:
                return os.path.getsize(_path)
            except OSError as e:
                silent_exc(e, where="nucleus.knowledge_access_layer::get_storage_size L356")
                return 0
        return 0

    # ---------------- 写入 ----------------

    def save_node(self, node: Any) -> bool:
        """保存单个节点（委托节点池；无节点池时返回 False，不抛异常）。"""
        if not _kal_enabled() or node is None:
            return False
        _pool = self._node_pool
        _f = (getattr(_pool, "add_node", None)
             or getattr(_pool, "update_node", None)
             or getattr(_pool, "add", None))
        if not callable(_f):
            return False
        try:
            _f(node)
            _nid = getattr(node, "node_id", None)
            if _nid:
                self._cache_put(_nid, node)
            return True
        except Exception as _e:
            _logger.warning("[KAL] 保存节点失败: %s: %s", type(_e).__name__, _e)
            return False

    def delete_node(self, node_id: str) -> bool:
        """删除单个节点（委托节点池）。"""
        if not _kal_enabled() or not node_id:
            return False
        _pool = self._node_pool
        _f = (getattr(_pool, "remove_node", None)
             or getattr(_pool, "delete_node", None)
             or getattr(_pool, "remove", None))
        if not callable(_f):
            return False
        try:
            _f(node_id)
            self._cache.pop(node_id, None)
            return True
        except Exception as _e:
            _logger.warning("[KAL] 删除节点失败: %s: %s", type(_e).__name__, _e)
            return False

    # ---------------- Neo4j 双写接口（主线第71批 T1） ----------------
    def neo4j_dual_write_enabled(self) -> bool:
        """Neo4j 双写是否生效（两开关同开）。"""
        try:
            import config
            return bool(getattr(config, "ENABLE_NEO4J_GRAPH_STORE", False)
                       and getattr(config, "ENABLE_NEO4J_DUAL_WRITE", False))
        except Exception as e:
            silent_exc(e, where="nucleus.knowledge_access_layer::neo4j_dual_write_enabled L406")
            return False

    def get_neo4j_dual_write_stats(self) -> dict:
        """返回 Neo4j 双写统计（委托节点池；池无接口时返回空统计）。"""
        _pool = self._node_pool
        _fn = getattr(_pool, "get_dual_write_stats", None)
        if callable(_fn):
            try:
                return _fn()
            except Exception as _e:
                _logger.debug("[KAL] 取双写统计失败: %s: %s", type(_e).__name__, _e)
        return {"enabled": self.neo4j_dual_write_enabled(), "note": "pool 未提供接口"}

    def sync_node_to_neo4j(self, node_id: str) -> int:
        """按节点 linked_nodes 全量同步关系到 Neo4j（委托节点池）。"""
        _pool = self._node_pool
        _fn = getattr(_pool, "sync_node_relationships", None)
        if callable(_fn):
            try:
                return int(_fn(node_id) or 0)
            except Exception as _e:
                _logger.debug("[KAL] 同步关系到 Neo4j 失败: %s: %s", type(_e).__name__, _e)
                return 0
        return 0

    # [M71-T1-KAL]

    # ---------------- Neo4j 双读接口（主线第72批 T4） ----------------
    def neo4j_read_enabled(self) -> bool:
        """Neo4j 双读是否生效（三开关同开）。"""
        try:
            import config
            return bool(getattr(config, "ENABLE_NEO4J_GRAPH_STORE", False)
                       and getattr(config, "ENABLE_NEO4J_DUAL_WRITE", False)
                       and getattr(config, "ENABLE_NEO4J_READ", False))
        except Exception as e:
            silent_exc(e, where="nucleus.knowledge_access_layer::neo4j_read_enabled L442")
            return False

    def query_relationships(self, node_id: str, direction: str = "both") -> List[dict]:
        """查询节点关联关系：优先 Neo4j，回退节点内 linked_nodes。"""
        if not _kal_enabled() or not node_id:
            return []
        _pool = self._node_pool
        if _pool is not None and hasattr(_pool, "get_relationships_neo4j"):
            try:
                return _pool.get_relationships_neo4j(node_id, direction)
            except Exception as e:
                _logger.debug("[KAL] query_relationships 回退: %s", e)
        _node = self.get_node(node_id)
        if _node is None:
            return []
        _out = []
        for _lid in (getattr(_node, "linked_nodes", None) or []):
            _tid = _lid if isinstance(_lid, str) else str(getattr(_lid, "node_id", "") or "")
            if not _tid or _tid == node_id:
                continue
            _out.append({"from": node_id, "to": _tid, "rel_type": "RELATED"})
        return _out

    def query_multi_hop(self, node_id: str, max_depth: int = 2) -> List[str]:
        """多跳关联查询：优先 Neo4j，回退节点内 BFS。"""
        if not _kal_enabled() or not node_id:
            return []
        _pool = self._node_pool
        _store = None
        if _pool is not None and hasattr(_pool, "_m71_neo4j_store"):
            try:
                if self.neo4j_read_enabled():
                    _store = _pool._m71_neo4j_store()
            except Exception:
                _store = None
        if _store is not None and _store.is_available() and hasattr(_store, "query_multi_hop"):
            try:
                return [str(x) for x in _store.query_multi_hop(node_id, int(max_depth or 2))]
            except Exception as e:
                _logger.debug("[KAL] query_multi_hop Neo4j 失败回退: %s", e)
        try:
            return [str(x[0].node_id) if hasattr(x[0], "node_id") else str(x[0])
                    for x in self.get_linked_nodes(node_id, max_depth=int(max_depth or 2))
                    if x]
        except Exception:
            return []

    def query_common_neighbors(self, node_id_1: str, node_id_2: str) -> List[str]:
        """共同邻居查询：优先 Neo4j，回退集合交集。"""
        if not _kal_enabled() or not node_id_1 or not node_id_2:
            return []
        _pool = self._node_pool
        _store = None
        if _pool is not None and hasattr(_pool, "_m71_neo4j_store"):
            try:
                if self.neo4j_read_enabled():
                    _store = _pool._m71_neo4j_store()
            except Exception:
                _store = None
        if _store is not None and _store.is_available() and hasattr(_store, "query_common_neighbors"):
            try:
                return [str(x) for x in _store.query_common_neighbors(node_id_1, node_id_2)]
            except Exception as e:
                _logger.debug("[KAL] query_common_neighbors Neo4j 失败回退: %s", e)
        _a = set()
        _b = set()
        _na = self.get_node(node_id_1)
        _nb = self.get_node(node_id_2)
        if _na is not None:
            for _lid in (getattr(_na, "linked_nodes", None) or []):
                _a.add(_lid if isinstance(_lid, str) else str(getattr(_lid, "node_id", "") or ""))
        if _nb is not None:
            for _lid in (getattr(_nb, "linked_nodes", None) or []):
                _b.add(_lid if isinstance(_lid, str) else str(getattr(_lid, "node_id", "") or ""))
        return [x for x in (_a & _b) if x]

    def get_neo4j_read_stats(self) -> dict:
        """返回 Neo4j 双读统计（委托节点池）。"""
        _pool = self._node_pool
        if _pool is not None and hasattr(_pool, "get_read_stats"):
            try:
                return _pool.get_read_stats()
            except Exception as e:
                _logger.debug("[KAL] 取双读统计失败: %s", e)
        return {"enabled": self.neo4j_read_enabled(), "note": "pool 未提供接口"}

    def get_neo4j_read_compare_stats(self) -> dict:
        """[M73-T4] 返回双读一致性比对统计（委托节点池）。"""
        _pool = self._node_pool
        if _pool is not None and hasattr(_pool, "get_read_compare_stats"):
            try:
                return _pool.get_read_compare_stats()
            except Exception as e:
                _logger.debug("[KAL] 取一致性比对统计失败: %s", e)
        return {"enabled": self.neo4j_read_enabled(), "note": "pool 未提供接口"}

    def reset_neo4j_read_compare(self) -> None:
        """[M73-T4] 重置双读一致性比对统计（委托节点池）。"""
        _pool = self._node_pool
        if _pool is not None and hasattr(_pool, "reset_read_compare"):
            try:
                _pool.reset_read_compare()
            except Exception as e:
                _logger.debug("[KAL] 重置一致性比对失败: %s", e)

    def repair_inconsistent_node(self, node_id: str) -> int:
        """[M73-T4] 从节点内重新同步关联到 Neo4j（委托节点池）。"""
        _pool = self._node_pool
        if _pool is not None and hasattr(_pool, "repair_inconsistent_node"):
            try:
                return int(_pool.repair_inconsistent_node(node_id) or 0)
            except Exception as e:
                _logger.debug("[KAL] 修复不一致节点失败: %s", e)
        return 0

    # [M72-T4-KAL]

    # ---------------- 存储管理 ----------------

    def trigger_full_save(self) -> bool:
        """触发全量保存（委托快照对象）。"""
        if not _kal_enabled() or self._snapshot is None:
            return False
        _f = getattr(self._snapshot, "save", None)
        if not callable(_f):
            return False
        try:
            return bool(_f(force_full=True))
        except Exception as _e:
            _logger.warning("[KAL] 触发全量保存失败: %s: %s", type(_e).__name__, _e)
            return False

    def trigger_incremental_save(self) -> bool:
        """触发增量保存（委托快照对象）。"""
        if not _kal_enabled() or self._snapshot is None:
            return False
        _f = getattr(self._snapshot, "save", None)
        if not callable(_f):
            return False
        try:
            return bool(_f(force_full=False))
        except Exception as _e:
            _logger.warning("[KAL] 触发增量保存失败: %s: %s", type(_e).__name__, _e)
            return False

    def get_storage_backend(self) -> str:
        """当前存储后端类型（json / parquet / distributed）。"""
        return _cfg_str("KAL_STORAGE_BACKEND", "json")

    def get_cache_stats(self) -> Dict[str, Any]:
        """缓存命中统计（命中率可观测）。"""
        _total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_size": self._cache_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": (self._hits / _total) if _total else 0.0,
            "backend": self.get_storage_backend(),
            "enabled": _kal_enabled(),
        }


    # ===== Batch-110: ungated behavior-preserving pass-throughs to node_pool =====
    # These are intentionally NOT gated by _kal_enabled(): they mirror the
    # previous direct `self.node_pool.X(...)` organ calls 1:1, so migrating
    # `self.node_pool.X` -> `self._kal.Y` is a pure refactor with no behavior change.
    def query_nodes(self, evol_level=None, importance=None, source_organ=None,
                    space_path_prefix=None, limit=100):
        """透传 self._node_pool.query（等价于原直连，不受 _kal_enabled 闸门影响）。"""
        if self._node_pool is None:
            return []
        return self._node_pool.query(evol_level=evol_level, importance=importance,
                                     source_organ=source_organ,
                                     space_path_prefix=space_path_prefix, limit=limit)

    def count(self, *args, **kwargs):
        if self._node_pool is None:
            return 0
        return self._node_pool.count(*args, **kwargs)

    def get_stats(self, *args, **kwargs):
        if self._node_pool is None:
            return {}
        return self._node_pool.get_stats(*args, **kwargs)

    def get_instincts(self, *args, **kwargs):
        if self._node_pool is None:
            return []
        return self._node_pool.get_instincts(*args, **kwargs)

    @property
    def get_instinct_count(self):
        if self._node_pool is None:
            return 0
        return self._node_pool.instinct_count

    def get_all_including_evicted(self, *args, **kwargs):
        if self._node_pool is None:
            return []
        return self._node_pool.get_all_including_evicted(*args, **kwargs)

    def add_node(self, *args, **kwargs):
        if self._node_pool is None:
            return None
        return self._node_pool.add(*args, **kwargs)

    def remove_node(self, *args, **kwargs):
        if self._node_pool is None:
            return None
        return self._node_pool.remove(*args, **kwargs)

    def upgrade_to_instinct(self, *args, **kwargs):
        if self._node_pool is None:
            return None
        return self._node_pool.upgrade_to_instinct(*args, **kwargs)

    def downgrade_instinct(self, *args, **kwargs):
        if self._node_pool is None:
            return None
        return self._node_pool.downgrade_instinct(*args, **kwargs)

    def upgrade_node_level(self, *args, **kwargs):
        if self._node_pool is None:
            return None
        return self._node_pool.upgrade_node_level(*args, **kwargs)


def get_kal(node_pool: Any = None, snapshot: Any = None,
            refresh: bool = False) -> KnowledgeAccessLayer:
    """获取 KAL 全局单例。

    - 首次调用可注入 node_pool / snapshot 依赖；
    - refresh=True 时用新依赖重建实例（测试用）。
    """
    global _KAL_INSTANCE
    if _KAL_INSTANCE is None or refresh:
        _KAL_INSTANCE = KnowledgeAccessLayer(node_pool=node_pool, snapshot=snapshot)
    elif node_pool is not None and _KAL_INSTANCE._node_pool is None:
        _KAL_INSTANCE._node_pool = node_pool
    elif snapshot is not None and _KAL_INSTANCE._snapshot is None:
        _KAL_INSTANCE._snapshot = snapshot
    return _KAL_INSTANCE
