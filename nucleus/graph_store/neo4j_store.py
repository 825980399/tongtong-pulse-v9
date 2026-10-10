# -*- coding: utf-8 -*-
"""Neo4j 图存储封装 —— 主线第70批 T1/P1。

把节点关联网络从「节点内 linked_nodes 字段」迁移到专业图数据库，
支持多跳关联、最短路径、共同邻居等图查询。

★第70批只做**设计与基础封装**，不启用、不迁移生产数据：
  - ``ENABLE_NEO4J_GRAPH_STORE`` 默认 False
  - neo4j 驱动未安装时，本模块零副作用（is_available() 恒 False）
  - 双写过渡期：关联同时写节点内字段与 Neo4j，查询优先 Neo4j，失败回退节点内字段

用法
----
    from nucleus.graph_store.neo4j_store import get_neo4j_store
    store = get_neo4j_store()
    if store.is_available():
        store.add_node("n1", {"evol_level": "L1"})
        store.add_relationship("n1", "n2", "RELATED")
        hops = store.query_multi_hop("n1", max_depth=2)
"""
import logging
import os
import threading
from typing import Any, List, Optional, Tuple

from nucleus._silent_except import silent_exc

try:
    from nucleus.logger import get_module_logger as _get_module_logger
    _logger = _get_module_logger("pulse.module.neo4j_store")
except Exception:
    _logger = logging.getLogger("pulse.module.neo4j_store")

_NEO4J_STORE: Optional["Neo4jStore"] = None


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


def _enabled() -> bool:
    """Neo4j 总开关（默认关闭）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_NEO4J_GRAPH_STORE", False))
    except Exception as e:
        silent_exc(e, where="nucleus.graph_store.neo4j_store::_enabled L48")
        return False


class Neo4jStore:
    """Neo4j 图存储封装。

    所有方法在「未启用 / 驱动缺失 / 连接失败」时都返回安全空值，
    绝不把异常抛给调用方 —— 图库是增强项，不能拖垮主流程。
    """

    def __init__(self, uri: str = "", user: str = "", password: str = "",
                 database: str = "", pool_size: int = 10) -> None:
        self._uri = uri or str(_cfg("NEO4J_URI", "bolt://localhost:7687"))
        self._user = user or str(_cfg("NEO4J_USER", "neo4j"))
        # 密码：优先环境变量，绝不硬编码
        self._password = password or os.environ.get("NEO4J_PASSWORD", "") or str(_cfg("NEO4J_PASSWORD", ""))
        self._database = database or str(_cfg("NEO4J_DATABASE", "tongtong"))
        self._pool_size = int(pool_size or _cfg("NEO4J_CONNECTION_POOL_SIZE", 10))
        self._batch_size = int(_cfg("NEO4J_BATCH_SIZE", 1000))
        self._lock = threading.Lock()
        self._driver = None
        self._available = False
        self._driver_missing = False
        try:
            import neo4j  # noqa: F401
        except ImportError:
            self._driver_missing = True
            _logger.debug("[T1] neo4j 驱动未安装，图存储处于未启用状态（零副作用）")

    # ---------- 连接管理 ----------
    def connect(self, uri: str = "", user: str = "", password: str = "") -> bool:
        """连接 Neo4j。未启用/驱动缺失 → 返回 False（不抛异常）。"""
        if not _enabled():
            return False
        if self._driver_missing:
            return False
        with self._lock:
            try:
                from neo4j import GraphDatabase
                _uri = uri or self._uri
                _auth = (user or self._user, password or self._password)
                self._driver = GraphDatabase.driver(
                    _uri, auth=_auth,
                    max_connection_pool_size=self._pool_size)
                self._available = True
                _logger.info("[T1] Neo4j 已连接: %s", _uri)
                return True
            except Exception as e:
                self._available = False
                _logger.warning("[T1] Neo4j 连接失败: %s: %s", type(e).__name__, e)
                return False

    def close(self) -> None:
        """关闭连接。"""
        with self._lock:
            if self._driver is not None:
                try:
                    self._driver.close()
                except Exception as e:
                    silent_exc(e, where="nucleus.graph_store.neo4j_store::close L107")
                self._driver = None
            self._available = False

    def is_available(self) -> bool:
        """连接是否可用。"""
        return bool(self._available and self._driver is not None)

    def _run(self, cypher: str, params: Optional[dict] = None) -> List[dict]:
        """执行 Cypher，返回记录字典列表。不可用 → 空列表。"""
        if not self.is_available():
            return []
        try:
            with self._driver.session(database=self._database) as _s:
                _res = _s.run(cypher, params or {})
                return [dict(_r) for _r in _res]
        except Exception as e:
            _logger.warning("[T1] Cypher 执行失败: %s: %s", type(e).__name__, e)
            return []

    # ---------- 节点 CRUD ----------
    def add_node(self, node_id: str, properties: Optional[dict] = None) -> bool:
        """添加/更新节点（MERGE 幂等）。"""
        if not self.is_available():
            return False
        _props = dict(properties or {})
        _props["node_id"] = node_id
        self._run("MERGE (n:KnowledgeNode {node_id: $node_id}) SET n += $props",
                  {"node_id": node_id, "props": _props})
        return True

    def update_node(self, node_id: str, properties: dict) -> bool:
        """更新节点属性。"""
        return self.add_node(node_id, properties)

    def remove_node(self, node_id: str) -> bool:
        """删除节点及其所有关系。"""
        if not self.is_available():
            return False
        self._run("MATCH (n:KnowledgeNode {node_id: $node_id}) DETACH DELETE n",
                  {"node_id": node_id})
        return True

    def get_node(self, node_id: str) -> Optional[dict]:
        """获取节点属性。"""
        rows = self._run("MATCH (n:KnowledgeNode {node_id: $node_id}) RETURN n",
                         {"node_id": node_id})
        return rows[0]["n"] if rows else None

    # ---------- 关系 CRUD ----------
    def add_relationship(self, from_id: str, to_id: str, rel_type: str,
                         properties: Optional[dict] = None) -> bool:
        """添加关系（MERGE 幂等）。"""
        if not self.is_available():
            return False
        _cy = ("MATCH (a:KnowledgeNode {node_id: $from_id}), "
               "(b:KnowledgeNode {node_id: $to_id}) "
               f"MERGE (a)-[r:{rel_type}]->(b) SET r += $props")
        self._run(_cy, {"from_id": from_id, "to_id": to_id, "props": properties or {}})
        return True

    def update_relationship(self, from_id: str, to_id: str, rel_type: str,
                            properties: dict) -> bool:
        """更新关系属性。"""
        return self.add_relationship(from_id, to_id, rel_type, properties)

    def remove_relationship(self, from_id: str, to_id: str, rel_type: str) -> bool:
        """删除关系。"""
        if not self.is_available():
            return False
        _cy = ("MATCH (a:KnowledgeNode {node_id: $from_id})"
               f"-[r:{rel_type}]->(b:KnowledgeNode {{node_id: $to_id}}) DELETE r")
        self._run(_cy, {"from_id": from_id, "to_id": to_id})
        return True

    def get_relationships(self, node_id: str, direction: str = "both") -> List[dict]:
        """获取节点的所有关系。direction: out / in / both。"""
        if not self.is_available():
            return []
        if direction == "out":
            _cy = ("MATCH (a:KnowledgeNode {node_id: $node_id})-[r]->(b) "
                   "RETURN type(r) AS rel_type, b.node_id AS other")
        elif direction == "in":
            _cy = ("MATCH (a:KnowledgeNode {node_id: $node_id})<-[r]-(b) "
                   "RETURN type(r) AS rel_type, b.node_id AS other")
        else:
            _cy = ("MATCH (a:KnowledgeNode {node_id: $node_id})-[r]-(b) "
                   "RETURN type(r) AS rel_type, b.node_id AS other")
        return self._run(_cy, {"node_id": node_id})

    # ---------- 图查询 ----------
    def query_multi_hop(self, node_id: str, max_depth: int = 2,
                        top_k: int = 100) -> List[str]:
        """查询 N 跳关联节点，返回 node_id 列表（不含自身）。"""
        if not self.is_available() or max_depth < 1:
            return []
        _cy = ("MATCH (a:KnowledgeNode {node_id: $node_id})-[*1.."
               f"{int(max_depth)}]-(b) "
               "RETURN DISTINCT b.node_id AS nid LIMIT $top_k")
        rows = self._run(_cy, {"node_id": node_id, "top_k": int(top_k)})
        return [_r["nid"] for _r in rows if _r.get("nid") and _r["nid"] != node_id]

    def query_shortest_path(self, from_id: str, to_id: str) -> List[str]:
        """查询最短路径（节点 id 序列）。无路径 → 空列表。"""
        if not self.is_available():
            return []
        _cy = ("MATCH p = shortestPath((a:KnowledgeNode {node_id: $from_id})"
               "-[*]-(b:KnowledgeNode {node_id: $to_id})) "
               "RETURN [n IN nodes(p) | n.node_id] AS path")
        rows = self._run(_cy, {"from_id": from_id, "to_id": to_id})
        return list(rows[0]["path"]) if rows else []

    def query_common_neighbors(self, node_id_1: str, node_id_2: str) -> List[str]:
        """查询两节点的共同邻居。"""
        if not self.is_available():
            return []
        _cy = ("MATCH (a:KnowledgeNode {node_id: $n1})--(c)--(b:KnowledgeNode {node_id: $n2}) "
               "RETURN DISTINCT c.node_id AS nid")
        rows = self._run(_cy, {"n1": node_id_1, "n2": node_id_2})
        return [_r["nid"] for _r in rows if _r.get("nid")]

    def execute_cypher(self, query: str, parameters: Optional[dict] = None) -> List[dict]:
        """执行自定义 Cypher（转义能力有限，仅内部可信调用）。"""
        return self._run(query, parameters)

    # ---------- 批量同步 ----------
    def sync_from_nodes(self, nodes: List[Any]) -> Tuple[int, int]:
        """从现有节点批量同步关联网络到 Neo4j。

        Args:
            nodes: PulseNode 列表（需有 node_id / linked_nodes / evol_level）

        Returns:
            (成功节点数, 成功关系数)
        """
        if not self.is_available():
            return (0, 0)
        _n_ok = _r_ok = 0
        for _n in nodes:
            try:
                _nid = getattr(_n, "node_id", None)
                if not _nid:
                    continue
                self.add_node(_nid, {
                    "evol_level": str(getattr(_n, "evol_level", "") or ""),
                    "space_path": str(getattr(_n, "space_path", "") or ""),
                })
                _n_ok += 1
                for _t in (getattr(_n, "linked_nodes", None) or []):
                    _tid = _t if isinstance(_t, str) else str(getattr(_t, "node_id", "") or "")
                    if _tid:
                        self.add_relationship(_nid, _tid, "RELATED")
                        _r_ok += 1
            except Exception as e:
                _logger.debug("[T1] 同步节点失败: %s: %s", type(e).__name__, e)
        _logger.info("[T1] Neo4j 批量同步完成: %d 节点 / %d 关系", _n_ok, _r_ok)
        return (_n_ok, _r_ok)

    def get_stats(self) -> dict:
        """返回图存储状态。"""
        return {
            "enabled": _enabled(),
            "driver_installed": not self._driver_missing,
            "available": self.is_available(),
            "uri": self._uri,
            "database": self._database,
            "batch_size": self._batch_size,
        }


def get_neo4j_store() -> "Neo4jStore":
    """获取 Neo4j 存储单例。"""
    global _NEO4J_STORE
    if _NEO4J_STORE is None:
        _NEO4J_STORE = Neo4jStore()
    return _NEO4J_STORE
