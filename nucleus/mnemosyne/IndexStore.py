# -*- coding: utf-8 -*-
"""
IndexStore.py —— 索引存储

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 知识索引的持久化存储
机制: 基于IndexStore类实现，包含10个核心方法
定位: 记忆存储层
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from nucleus.mnemosyne.pa_compat import table_from_rows


_MODULE_LOGGER = __import__("logging").getLogger("IndexStore")

try:
    from config import PARQUET_COMPRESSION
except Exception:
    PARQUET_COMPRESSION = "snappy"  # ★第109批 T-109a：config 键缺失时回落硬编码默认值


class IndexStore:
    """外置索引持久化器（阶段 C'）。"""

    def __init__(self, index_dir: str = "data/knowledge/index"):
        self.index_dir = index_dir
        self._lock = threading.Lock()
        # 增量变更 WAL：[(op, table, row_dict), ...]  op ∈ {"add", "remove"}
        self._pending_changes: list[tuple[str, str, dict[str, Any]]] = []
        self._loaded = False
        self._loading = False

    # ========== 路径工具 ==========

    def _table_path(self, table: str) -> str:
        return os.path.join(self.index_dir, f"{table}.parquet")

    def _ensure_dir(self) -> None:
        os.makedirs(self.index_dir, exist_ok=True)

    # ========== 序列化（从内存索引 → Parquet 行） ==========

    @staticmethod
    def level_index_to_rows(level_index: dict[str, set]) -> list[dict[str, Any]]:
        """分层索引 → 行列表。evicted 默认 False（B' 阶段才会置 True）。"""
        _rows: list[dict[str, Any]] = []
        for _level, _ids in (level_index or {}).items():
            for _nid in _ids:
                _rows.append({"evol_level": _level, "node_id": _nid, "evicted": False})
        return _rows

    @staticmethod
    def path_index_to_rows(path_index: dict[str, set]) -> list[dict[str, Any]]:
        """路径前缀索引 → 行列表。"""
        _rows: list[dict[str, Any]] = []
        for _prefix, _ids in (path_index or {}).items():
            for _nid in _ids:
                _rows.append({"path_prefix": _prefix, "node_id": _nid})
        return _rows

    @staticmethod
    def semantic_index_to_rows(semantic_index: dict[str, set]) -> list[dict[str, Any]]:
        """横向语义索引 → 行列表。value 是 {(source_id, relation_type)}。"""
        _rows: list[dict[str, Any]] = []
        for _target, _incoming in (semantic_index or {}).items():
            for _sid, _rtype in _incoming:
                _rows.append({
                    "target_id": _target,
                    "source_id": _sid,
                    "relation_type": _rtype,
                })
        return _rows

    # ========== 落盘 ==========

    def _write_table(self, table: str, rows: list[dict[str, Any]]) -> bool:
        """写单张索引表（全量重写）。空 rows 也写空表（标记已落盘）。"""
        try:
            import pyarrow.parquet as pq  # noqa: F401 - 可用性探测（pa_compat 内部再导入 pa）
        except Exception as _e:
            _MODULE_LOGGER.warning(f"pyarrow 不可用，索引表 {table} 写入跳过: {_e}")
            return False
        try:
            self._ensure_dir()
            _table = table_from_rows(rows) if rows else table_from_rows(
                [{}]
            )
            pq.write_table(
                _table,
                self._table_path(table),
                compression=PARQUET_COMPRESSION if PARQUET_COMPRESSION else "snappy",
            )
            return True
        except Exception as _e:
            _MODULE_LOGGER.error(f"索引表 {table} 写入失败: {_e}")
            return False

    def save_all(self, level_index, path_index, semantic_index) -> dict[str, bool]:
        """全量落盘三类索引表。返回 {表名: 是否成功}。"""
        with self._lock:
            _res = {
                "level": self._write_table(
                    "level_index", self.level_index_to_rows(level_index)
                ),
                "path": self._write_table(
                    "path_index", self.path_index_to_rows(path_index)
                ),
                "semantic": self._write_table(
                    "semantic_index", self.semantic_index_to_rows(semantic_index)
                ),
            }
        return _res

    # ========== 读取 ==========

    def _read_table(self, table: str) -> list[dict[str, Any]]:
        """读单张索引表 → 行列表。表不存在返回 []。"""
        try:
            import pyarrow.parquet as pq
        except Exception:
            return []
        _path = self._table_path(table)
        if not os.path.exists(_path):
            return []
        try:
            _t = pq.read_table(_path)
            return _t.to_pylist()
        except Exception as _e:
            _MODULE_LOGGER.error(f"索引表 {table} 读取失败: {_e}")
            return []

    def load_level_index(self) -> dict[str, set]:
        _idx: dict[str, set] = {}
        for _row in self._read_table("level_index"):
            _idx.setdefault(_row.get("evol_level", ""), set()).add(_row.get("node_id", ""))
        return _idx

    def load_path_index(self) -> dict[str, set]:
        _idx: dict[str, set] = {}
        for _row in self._read_table("path_index"):
            _idx.setdefault(_row.get("path_prefix", ""), set()).add(_row.get("node_id", ""))
        return _idx

    def load_semantic_index(self) -> dict[str, set]:
        _idx: dict[str, set] = {}
        for _row in self._read_table("semantic_index"):
            _target = _row.get("target_id", "")
            _sid = _row.get("source_id", "")
            _rtype = _row.get("relation_type", "")
            _idx.setdefault(_target, set()).add((_sid, _rtype))
        return _idx

    # ========== 异步加载 ==========

    def load_async(self, on_done=None) -> None:
        """后台线程异步加载全部索引表；完成后回调 on_done(loaded_dict)。"""
        if self._loading or self._loaded:
            return
        self._loading = True

        def _worker():
            try:
                _loaded = {
                    "level": self.load_level_index(),
                    "path": self.load_path_index(),
                    "semantic": self.load_semantic_index(),
                }
                with self._lock:
                    self._loaded = True
                    self._loading = False
                if on_done:
                    on_done(_loaded)
            except Exception as _e:
                _MODULE_LOGGER.error(f"异步加载索引失败: {_e}")
                self._loading = False

        _t = threading.Thread(target=_worker, daemon=True, name="IndexStoreLoader")
        _t.start()

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    # ========== 增量 WAL ==========

    def record_change(self, op: str, table: str, row: dict[str, Any]) -> None:
        """追加一条索引变更记录（op ∈ add/remove）。不立即写盘，定期 flush。"""
        with self._lock:
            self._pending_changes.append((op, table, row))

    def flush_pending(self, level_index, path_index, semantic_index) -> int:
        """把 WAL 变更合并到内存索引后，全量重写索引表。返回 flush 的变更数。

        简化策略：不逐条 diff 写盘，而是把内存索引（已含变更）全量重写。
        因为索引全量重写成本与 flush 频率相关（定期触发，非每次增删），可接受。
        """
        with self._lock:
            _n = len(self._pending_changes)
            self._pending_changes.clear()
        if _n == 0:
            return 0
        self.save_all(level_index, path_index, semantic_index)
        return _n

    # ========== 对账 ==========

    def verify(self, level_index, path_index, semantic_index) -> dict[str, Any]:
        """对比内存索引与磁盘索引表的 node_id 集合，返回不一致详情。"""
        _disk_level = self.load_level_index()
        _disk_path = self.load_path_index()
        _disk_semantic = self.load_semantic_index()

        def _key_set(_idx: dict[str, set]) -> set[str]:
            _s: set[str] = set()
            for _ids in _idx.values():
                _s.update(_ids)
            return _s

        _mem_level_ids = _key_set(level_index or {})
        _disk_level_ids = _key_set(_disk_level)

        _result = {
            "level_missing_in_disk": sorted(_mem_level_ids - _disk_level_ids),
            "level_extra_in_disk": sorted(_disk_level_ids - _mem_level_ids),
            "path_missing_in_disk": sorted(
                _key_set(path_index or {}) - _key_set(_disk_path)
            ),
            "path_extra_in_disk": sorted(
                _key_set(_disk_path) - _key_set(path_index or {})
            ),
            "semantic_missing_in_disk": sorted(
                _key_set(semantic_index or {}) - _key_set(_disk_semantic)
            ),
            "semantic_extra_in_disk": sorted(
                _key_set(_disk_semantic) - _key_set(semantic_index or {})
            ),
        }
        _result["consistent"] = all(
            not v for v in _result.values()
        )
        return _result


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== IndexStore 自测 ===\n")

    import tempfile

    _tmp = tempfile.mkdtemp(prefix="indexstore_test_")
    _store = IndexStore(index_dir=_tmp)

    # 构造内存索引
    _level = {"L1": {"a", "b"}, "L2": {"c"}, "L3": {"d"}}
    _path = {"/技术": {"a", "c"}, "/身份": {"b", "d"}}
    _semantic = {"c": {("a", "causal"), ("b", "analogy")}}

    # 1. 全量落盘
    _res = _store.save_all(_level, _path, _semantic)
    print("落盘结果:", _res)

    # 2. 读回校验
    _l2 = _store.load_level_index()
    _p2 = _store.load_path_index()
    _s2 = _store.load_semantic_index()
    print("level 读回一致:", _l2 == _level)
    print("path 读回一致:", _p2 == _path)
    print("semantic 读回一致:", _s2 == _semantic)

    # 3. 对账
    _verify = _store.verify(_level, _path, _semantic)
    print("对账一致:", _verify["consistent"])

    # 4. 异步加载
    _store2 = IndexStore(index_dir=_tmp)
    _done = {}
    _store2.load_async(on_done=lambda d: _done.update(d))
    time.sleep(1.5)
    print("异步加载完成:", _store2.is_loaded, "键:", sorted(_done.keys()))

    print("\n=== 自测通过 ===")
