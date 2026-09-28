"""第82批 T-g：FAISS ADD_UNDERFLOW 回归（faiss_store.py:143）。

背景
----
`tools/benchmark_hot_cold_faiss_kal.py::stage_faiss` 用单例 `get_faiss_store()`
（dimension 取自 `config.VECTOR_DIMENSION`，默认 512）却喂 64 维向量，
导致 `IndexFlatL2(512).add((200,64))` 抛 AssertionError（faiss 空消息断言）。
`_build_index` 捕获后 `_index=None`，且 `_vectors` 未落盘 →
`stored=0 < count=200` → 基准 `status=ADD_UNDERFLOW`
→ `m72::test_benchmark_synthetic_stages` 长期红灯（17:33 实测场景）。

红线
----
维度不一致时**告警并跳过索引构建（不崩）**，向量仍存内存走暴力余弦回退，
检索正确性不受影响；维度一致时行为零回归。
"""
from __future__ import annotations

import os
import sys

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from nucleus.vector_store.faiss_store import FAISSVectorStore  # noqa: E402


def _vecs(n, dim, seed=0):
    import random
    rng = random.Random(seed)
    return [[rng.random() for _ in range(dim)] for _ in range(n)]


def _store(dim):
    return FAISSVectorStore(dimension=dim, index_type="auto", batch_size=1000)


class TestDimensionMismatchNoCrash:
    def test_mismatch_keeps_vectors_and_skips_index(self):
        """维度不一致：不崩、索引不构建，但向量必须落内存（否则 ADD_UNDERFLOW）。"""
        s = _store(512)
        n = 200
        ok = s.add_vectors(["v%d" % i for i in range(n)], _vecs(n, 64, seed=7))
        assert ok is False  # 未构建索引
        st = s.get_stats()
        assert st["vector_count"] == n, "维度不一致时向量丢失 → stored < count → ADD_UNDERFLOW"
        assert st["index_ready"] is False

    def test_mismatch_brute_force_still_works(self):
        """维度不一致回退暴力余弦，检索仍返回结果（正确性不降）。"""
        s = _store(512)
        s.add_vectors(["a", "b"], [[1.0] + [0.0] * 63, [0.0] * 64])
        res = s.search([1.0] + [0.0] * 63, top_k=2)
        assert res and res[0][0] == "a"

    def test_matching_dim_builds_index(self):
        """维度一致时仍正常构建索引（零回归）。"""
        s = _store(64)
        s.add_vectors(["v%d" % i for i in range(50)], _vecs(50, 64))
        assert s.is_available() is True


class TestSingleVectorShape:
    def test_single_vector_add_no_assertion(self):
        """单条向量不得因 1 维数组触发 faiss add 断言。"""
        s = _store(8)
        s.add_vectors(["only"], [[0.1] * 8])
        assert s.get_stats()["vector_count"] == 1


class TestStageFaissNoUnderflow:
    def test_stage_faiss_dim_mismatch_returns_ok(self):
        """复现 m72 场景：stage_faiss(200, 64, ...) 必须 OK 而非 ADD_UNDERFLOW。"""
        import tools.benchmark_hot_cold_faiss_kal as bench
        sf = bench.stage_faiss(200, 64, 5, 30, 7)
        assert sf["status"] == "OK", f"仍 ADD_UNDERFLOW: {sf!r}"
