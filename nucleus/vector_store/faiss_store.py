# -*- coding: utf-8 -*-
"""FAISS 向量存储封装 —— 第69批 T2。

提供 FAISS 索引的构建、检索、增删改、保存/加载能力。
支持 IndexFlatL2（精确搜索，小规模）和 IndexIVFFlat（倒排，大规模）。
当 faiss 不可用时，自动回退到暴力余弦计算。

用法
----
    from nucleus.vector_store.faiss_store import get_faiss_store
    store = get_faiss_store()
    store.add_vectors(node_ids, vectors)
    results = store.search(query_vector, top_k=10)
"""
import logging
import os
import threading
from typing import Any, List, Optional, Tuple

try:
    from nucleus.logger import get_module_logger as _get_module_logger
    _logger = _get_module_logger("pulse.module.faiss_store")
except Exception:
    _logger = logging.getLogger("pulse.module.faiss_store")

# 单例
_FAISS_STORE_INSTANCE: Optional["FAISSVectorStore"] = None


def _faiss_enabled() -> bool:
    """FAISS 总开关。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_FAISS_VECTOR_STORE", True))
    except Exception:
        return True


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


class FAISSVectorStore:
    """FAISS 向量存储封装。

    - add_vectors: 批量添加向量
    - search: 语义检索，返回 TopK 相似节点
    - save / load: 索引持久化
    - 回退: faiss 不可用时回退到暴力余弦
    """

    def __init__(self, dimension: int = 512,
                 index_type: str = "auto",
                 index_path: str = "",
                 batch_size: int = 1000) -> None:
        self._dimension = int(dimension)
        self._index_type = str(index_type)
        self._index_path = str(index_path)
        self._batch_size = int(batch_size)
        self._lock = threading.Lock()
        self._index = None
        self._id_map: List[str] = []  # FAISS 内部 id → node_id
        self._reverse_map: dict[str, int] = {}  # node_id → faiss internal id
        self._vectors: dict[str, Any] = {}  # node_id → vector (用于回退+增量更新)
        self._initialized = False
        self._faiss_available = False
        # [M73-T1] 诊断与统计字段（索引构建/检索路径/重建）
        self._last_build_time: float = 0.0          # 上次索引构建耗时（秒）
        self._index_trained_count: int = 0          # 构建索引时的向量数（用于增量重建阈值）
        self._search_faiss_count: int = 0           # 走 FAISS 索引的检索次数
        self._search_brute_count: int = 0           # 走暴力余弦的检索次数
        self._brute_warn_emitted: bool = False      # 暴力回退 WARNING 仅提示一次
        self._rebuild_count: int = 0                # 索引重建次数
        try:
            import faiss
            self._faiss_available = True
            _logger.info("[T2] faiss-cpu 已加载, version=%s", getattr(faiss, "__version__", "?"))
        except ImportError:
            self._faiss_available = False
            _logger.warning("[T2] faiss 未安装，回退到暴力余弦计算")

    def _ensure_state(self) -> None:
        if self._initialized:
            return
        self._initialized = True

    def _auto_select_index_type(self, n_vectors: int) -> str:
        """根据向量数量自动选择索引类型。"""
        if self._index_type != "auto":
            return self._index_type
        if n_vectors < 100000:
            return "FlatL2"
        return "IVFFlat"

    def _build_index(self, vectors: List[Tuple[str, Any]]) -> None:
        """构建 FAISS 索引。

        [M73-T1] 修复：该方法现在会在 add_vectors 首次添加向量时自动被调用，
        使索引真正构建（第69批埋下的 bug：add_vectors 从不调用本方法，
        导致 _index 恒为 None、检索永远走暴力余弦）。
        """
        if not self._faiss_available:
            return
        try:
            import time
            import faiss
            import numpy as np

            ids = [vid for vid, _ in vectors]
            vecs = np.array([v for _, v in vectors], dtype=np.float32)
            # [第82批 T-g] 单条向量时 np.array 可能得到 (d,) 1 维数组，faiss add 要求 2 维
            vecs = np.atleast_2d(vecs)
            if vecs.shape[0] == 0:
                return
            # [第82批 T-g] 维度一致性校验：与索引维度不符时告警并跳过（不崩），
            #   向量仍落内存 → search 走暴力余弦回退，检索正确性不受影响；
            #   否则 index.add 会抛空消息 AssertionError 且向量丢失 → ADD_UNDERFLOW。
            if vecs.shape[1] != self._dimension:
                _logger.warning(
                    "[第82批 T-g] 向量维度不一致（实际 %d vs 期望 %d），跳过 FAISS "
                    "索引构建，保留向量走暴力余弦回退",
                    int(vecs.shape[1]), self._dimension)
                self._index = None
                for _vid, _v in vectors:
                    self._vectors[_vid] = _v
                return

            idx_type = self._auto_select_index_type(vecs.shape[0])

            _t0 = time.time()
            if idx_type == "FlatL2":
                index = faiss.IndexFlatL2(self._dimension)
            elif idx_type == "IVFFlat":
                quantizer = faiss.IndexFlatL2(self._dimension)
                nlist = max(1, int(np.sqrt(vecs.shape[0])))
                # [第82批 T-g] 训练样本不足以支撑 nlist 时降级 FlatL2（避免 train 断言）
                if vecs.shape[0] < nlist:
                    _logger.warning(
                        "[第82批 T-g] IVFFlat 样本数 %d < nlist %d，降级 FlatL2",
                        int(vecs.shape[0]), nlist)
                    index = faiss.IndexFlatL2(self._dimension)
                else:
                    # faiss 1.x 的 IndexIVFFlat 构造需显式传入维度 d
                    index = faiss.IndexIVFFlat(quantizer, self._dimension, nlist, faiss.METRIC_L2)
                    index.train(vecs)
            else:
                index = faiss.IndexFlatL2(self._dimension)

            index.add(vecs)
            self._index = index
            self._id_map = ids
            self._reverse_map = {vid: i for i, vid in enumerate(ids)}
            for vid, v in vectors:
                self._vectors[vid] = v
            self._index_trained_count = vecs.shape[0]
            self._last_build_time = time.time() - _t0
            _logger.info("[M73-T1] FAISS 索引构建完成: %d 向量, type=%s, 耗时=%.3fs",
                         len(ids), idx_type, self._last_build_time)
        except Exception as e:
            _logger.warning("[M73-T1] FAISS 索引构建失败: %s: %s", type(e).__name__, e)
            self._index = None

    def add_vectors(self, node_ids: List[str], vectors: List[Any]) -> bool:
        """批量添加向量。

        [M73-T1] 修复核心：当索引尚未构建（_index is None）时，自动把「已存内存的向量
        + 本次新向量」合并后构建索引，使 FAISS 优化真正生效；之后走增量 add。
        若 faiss 不可用，则仅存内存（暴力余弦回退，保持零回归）。
        """
        with self._lock:
            if not self._faiss_available:
                # 回退模式：只存内存
                for nid, vec in zip(node_ids, vectors):
                    self._vectors[nid] = vec
                return False
            # [M73-T1] 首次/索引缺失：自动构建（含已存内存向量，避免丢失）
            if self._index is None:
                _combined = []
                for _vid, _v in self._vectors.items():
                    if _v is not None:
                        _combined.append((_vid, _v))
                for _nid, _vec in zip(node_ids, vectors):
                    _combined.append((_nid, _vec))
                # 去重：同一 node_id 以最新向量为准
                _dedup: dict = {}
                for _vid, _v in _combined:
                    _dedup[_vid] = _v
                _merged = list(_dedup.items())
                self._build_index(_merged)
                return self._index is not None
            # 索引已存在：增量添加
            try:
                import numpy as np
                vecs = np.atleast_2d(np.array(vectors, dtype=np.float32))
                # [第82批 T-g] 增量路径同样校验维度：不一致时跳过（不崩），既有索引保持可用
                if vecs.ndim < 2 or vecs.shape[1] != self._dimension:
                    _logger.warning(
                        "[第82批 T-g] 增量向量维度与索引不一致（%s vs %d），跳过本次增量",
                        getattr(vecs, "shape", "?"), self._dimension)
                    return False
                self._index.add(vecs)
                for _i, _nid in enumerate(node_ids):
                    self._reverse_map[_nid] = len(self._id_map)
                    self._id_map.append(_nid)
                    self._vectors[_nid] = vectors[_i] if _i < len(vectors) else None
                # [M73-T1] 重建阈值：IVFFlat 规模翻倍时重建（FlatL2 无需重建，直接增长）
                if (self._index_type == "IVFFlat"
                        and self._index_trained_count > 0
                        and len(self._vectors) > 2 * self._index_trained_count):
                    _logger.info("[M73-T1] 向量数 %d 超过训练数 %d 的 2 倍，触发索引重建",
                                 len(self._vectors), self._index_trained_count)
                    self._rebuild_from_memory()
                return True
            except Exception as e:
                _logger.warning("[M73-T1] add_vectors 增量失败: %s: %s", type(e).__name__, e)
                return False

    def _rebuild_from_memory(self) -> None:
        """[M73-T1] 从内存中的 _vectors 重建索引（锁定调用方需持锁）。"""
        _merged = [(_vid, _v) for _vid, _v in self._vectors.items() if _v is not None]
        self._index = None
        self._build_index(_merged)
        self._rebuild_count += 1

    def update_vectors(self, node_id: str, vector: Any) -> None:
        """更新单个向量（重建索引或增量更新）。"""
        with self._lock:
            self._vectors[node_id] = vector
            if self._faiss_available and self._index is not None:
                # FAISS 不支持原地更新，标记需要重建
                self._index = None
                _logger.debug("[T2] 向量更新标记索引需重建: %s", node_id)

    def remove_vector(self, node_id: str) -> None:
        """删除向量（标记索引需重建）。"""
        with self._lock:
            self._vectors.pop(node_id, None)
            if node_id in self._reverse_map:
                del self._reverse_map[node_id]
            if self._index is not None:
                self._index = None
                _logger.debug("[T2] 向量删除标记索引需重建: %s", node_id)

    def search(self, query_vector: Any, top_k: int = 10) -> List[Tuple[str, float]]:
        """语义检索，返回 [(node_id, similarity), ...]。"""
        with self._lock:
            if self._faiss_available and self._index is not None:
                try:
                    import numpy as np
                    qv = np.array([query_vector], dtype=np.float32)
                    distances, indices = self._index.search(qv, min(top_k, len(self._id_map)))
                    results = []
                    for i in range(len(indices[0])):
                        idx = indices[0][i]
                        if idx >= 0 and idx < len(self._id_map):
                            nid = self._id_map[idx]
                            # 距离转相似度（L2 距离越小越相似）
                            sim = 1.0 / (1.0 + float(distances[0][i]))
                            results.append((nid, sim))
                    self._search_faiss_count += 1
                    return results
                except Exception as e:
                    _logger.warning("[T2] FAISS 搜索失败回退暴力: %s: %s", type(e).__name__, e)

            # 回退：暴力余弦相似度
            if not self._brute_warn_emitted and self._faiss_available:
                self._brute_warn_emitted = True
                _logger.warning("[M73-T1] FAISS 索引未构建，search 走暴力余弦回退（性能未达 FAISS 优化目标）")
            self._search_brute_count += 1
            return self._brute_force_search(query_vector, top_k)

    def _brute_force_search(self, query_vector: Any, top_k: int) -> List[Tuple[str, float]]:
        """暴力余弦相似度搜索。"""
        try:
            import numpy as np
            qv = np.array(query_vector, dtype=np.float32)
            results = []
            for nid, vec in self._vectors.items():
                v = np.array(vec, dtype=np.float32)
                # 余弦相似度
                norm_q = np.linalg.norm(qv)
                norm_v = np.linalg.norm(v)
                if norm_q > 0 and norm_v > 0:
                    sim = float(np.dot(qv, v) / (norm_q * norm_v))
                else:
                    sim = 0.0
                results.append((nid, sim))
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:top_k]
        except Exception as e:
            _logger.warning("[T2] 暴力搜索失败: %s: %s", type(e).__name__, e)
            return []

    def save(self, path: str = "") -> bool:
        """保存 FAISS 索引到磁盘。

        [M73-T1] 同时持久化 _vectors 为 .npy，使 load 后能完整恢复向量内容
        （否则增量 add_vectors 会丢失已加载向量）。
        """
        if not self._faiss_available or self._index is None:
            return False
        try:
            import faiss  # noqa: F401
            import json
            import numpy as np
            p = path or self._index_path
            if not p:
                return False
            os.makedirs(os.path.dirname(p), exist_ok=True)
            faiss.write_index(self._index, p)
            # 保存 id_map
            map_path = p + ".ids.json"
            with open(map_path, "w", encoding="utf-8") as f:
                json.dump(self._id_map, f)
            # [M73-T1] 保存向量内容
            vec_path = p + ".vectors.npy"
            try:
                _arr = np.array([self._vectors.get(_nid) for _nid in self._id_map],
                                dtype=np.float32)
                np.save(vec_path, _arr)
            except Exception as _e:
                _logger.debug("[M73-T1] 向量 npy 保存失败（不影响索引）: %s", _e)
            _logger.info("[T2] FAISS 索引已保存: %s (%d 向量)", p, len(self._id_map))
            return True
        except Exception as e:
            _logger.warning("[T2] FAiss 索引保存失败: %s: %s", type(e).__name__, e)
            return False

    def load(self, path: str = "") -> bool:
        """从磁盘加载 FAISS 索引。

        [M73-T1] 修复：加载后重建 _vectors（旧实现只恢复 _index/_id_map，不恢复 _vectors，
        导致后续 add_vectors 合并时会丢失已加载向量）。优先从同名 .npy 恢复，失败则
        用 index.reconstruct 反推（FlatL2/IVFFlat 均支持）。
        """
        if not self._faiss_available:
            return False
        try:
            import faiss  # noqa: F401
            import json
            import numpy as np
            p = path or self._index_path
            if not p or not os.path.isfile(p):
                return False
            self._index = faiss.read_index(p)
            map_path = p + ".ids.json"
            if os.path.isfile(map_path):
                with open(map_path, "r", encoding="utf-8") as f:
                    self._id_map = json.load(f)
                self._reverse_map = {nid: i for i, nid in enumerate(self._id_map)}
            else:
                # 无 id_map：按索引顺序推断（仅 npy/重建能补全向量内容）
                self._id_map = [str(i) for i in range(self._index.ntotal)]
                self._reverse_map = {nid: i for i, nid in enumerate(self._id_map)}
            # [M73-T1] 恢复 _vectors
            self._vectors = {}
            _npy = p + ".vectors.npy"
            if os.path.isfile(_npy):
                try:
                    _arr = np.load(_npy)
                    for _i, _nid in enumerate(self._id_map):
                        if _i < _arr.shape[0]:
                            self._vectors[_nid] = _arr[_i]
                except Exception as _e:
                    _logger.debug("[M73-T1] 从 npy 恢复向量失败，改用 reconstruct: %s", _e)
                    self._vectors = {}
            if not self._vectors and self._index is not None and hasattr(self._index, "reconstruct"):
                try:
                    for _i, _nid in enumerate(self._id_map):
                        self._vectors[_nid] = self._index.reconstruct(_i)
                except Exception as _e:
                    _logger.warning("[M73-T1] index.reconstruct 恢复向量失败: %s: %s",
                                    type(_e).__name__, _e)
                    self._vectors = {}
            self._index_trained_count = len(self._id_map)
            _logger.info("[M73-T1] FAISS 索引已加载: %s (%d 向量, %d 个向量内容已恢复)",
                         p, len(self._id_map), len(self._vectors))
            return True
        except Exception as e:
            _logger.warning("[T2] FAISS 索引加载失败: %s: %s", type(e).__name__, e)
            return False

    def is_available(self) -> bool:
        """FAISS 是否可用。"""
        return self._faiss_available and self._index is not None

    def is_index_built(self) -> bool:
        """[M73-T1] 索引是否真正构建完成（与 is_available 语义一致，便于诊断）。"""
        return self._faiss_available and self._index is not None

    def rebuild_index(self) -> bool:
        """[M73-T1] 手动触发索引重建（从内存中 _vectors 重建）。"""
        if not self._faiss_available:
            return False
        with self._lock:
            self._rebuild_from_memory()
            return self._index is not None

    def get_index_stats(self) -> dict:
        """[M73-T1] 返回索引诊断统计（构建状态/类型/维度/数量/检索路径/重建次数）。"""
        return {
            "faiss_available": self._faiss_available,
            "index_ready": self._index is not None,
            "index_built": self.is_index_built(),
            "vector_count": len(self._vectors),
            "id_map_size": len(self._id_map),
            "index_type": self._auto_select_index_type(len(self._vectors)),
            "configured_index_type": self._index_type,
            "dimension": self._dimension,
            "trained_count": self._index_trained_count,
            "last_build_time_sec": round(self._last_build_time, 6),
            "rebuild_count": self._rebuild_count,
            "search_faiss_count": self._search_faiss_count,
            "search_brute_count": self._search_brute_count,
            "search_path": "faiss" if (self._faiss_available and self._index is not None) else "brute_force",
        }

    def get_stats(self) -> dict:
        """返回统计信息。"""
        return {
            "faiss_available": self._faiss_available,
            "index_ready": self._index is not None,
            "index_built": self.is_index_built(),
            "vector_count": len(self._vectors),
            "id_map_size": len(self._id_map),
            "dimension": self._dimension,
            "index_type": self._index_type,
            "search_faiss_count": self._search_faiss_count,
            "search_brute_count": self._search_brute_count,
        }


def get_faiss_store() -> "FAISSVectorStore":
    """获取 FAISS 向量存储单例。"""
    global _FAISS_STORE_INSTANCE
    if _FAISS_STORE_INSTANCE is None:
        dim = int(_cfg("VECTOR_DIMENSION", 512))
        idx_type = str(_cfg("FAISS_INDEX_TYPE", "auto"))
        idx_path = str(_cfg("FAISS_INDEX_PATH", "data/knowledge/faiss/index.faiss"))
        batch_sz = int(_cfg("FAISS_BATCH_SIZE", 1000))
        _FAISS_STORE_INSTANCE = FAISSVectorStore(
            dimension=dim, index_type=idx_type,
            index_path=idx_path, batch_size=batch_sz)
    return _FAISS_STORE_INSTANCE
