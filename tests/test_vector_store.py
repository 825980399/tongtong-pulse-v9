# -*- coding: utf-8 -*-
"""向量库测试（≥10 例）：向量写入/读取、维度校验、模型指纹、删除与压缩、持久化往返。

无需真实编码器：用 FakeEncoder（见 conftest）经 put(node, text, vec) 直接写预计算向量；
检索用真实 VectorEncoder.cosine_matrix 静态方法。
"""
import numpy as np
import pytest


def _vec(*vals, dim=4):
    return np.array(list(vals) + [0.0] * (dim - len(vals)), dtype=np.float32)


class TestVectorWriteRead:
    def test_put_返回成功(self, tmp_store):
        ok, _ = tmp_store.put("n1", "文本一", _vec(1, 0, 0, 0))
        assert ok is True

    def test_put_后has_vector为True(self, tmp_store):
        tmp_store.put("n1", "文本一", _vec(1, 0, 0, 0))
        assert tmp_store.has_vector("n1") is True

    def test_新库has_vector为False(self, tmp_store):
        assert tmp_store.has_vector("nope") is False

    def test_维度不符被拒(self, tmp_store):
        import numpy as _np
        ok, why = tmp_store.put("bad", "文本", _np.array([1.0, 0.0], dtype=_np.float32))  # dim=2 ≠ 4
        assert ok is False
        assert "dim" in why.lower()
        assert tmp_store.has_vector("bad") is False

    def test_空node_id被拒(self, tmp_store):
        ok, _ = tmp_store.put("", "x", _vec(1, 0, 0, 0))
        assert ok is False


class TestVectorDeleteCompact:
    def test_remove_后has_vector为False(self, tmp_store):
        tmp_store.put("n1", "a", _vec(1, 0, 0, 0))
        assert tmp_store.remove("n1") is True
        assert tmp_store.has_vector("n1") is False

    def test_remove_不存在返回False(self, tmp_store):
        assert tmp_store.remove("ghost") is False

    def test_all_ids(self, tmp_store):
        for i in range(3):
            tmp_store.put(f"n{i}", f"t{i}", _vec(i, 0, 0, 0))
        assert set(tmp_store.all_ids()) == {"n0", "n1", "n2"}


class TestVectorSearch:
    def test_search_by_vector_最近邻(self, tmp_store):
        tmp_store.put("a", "a", _vec(1, 0, 0, 0))
        tmp_store.put("b", "b", _vec(0, 1, 0, 0))
        hits = tmp_store.search_by_vector(_vec(1, 0, 0, 0), top_k=5)
        assert hits and hits[0][0] == "a"

    def test_search_by_vector_空库返回空(self, tmp_store):
        assert tmp_store.search_by_vector(_vec(1, 0, 0, 0)) == []

    def test_search_by_vector_topk截断(self, tmp_store):
        for i in range(5):
            tmp_store.put(f"n{i}", f"t{i}", _vec(i, 0, 0, 0))
        hits = tmp_store.search_by_vector(_vec(0, 0, 0, 0), top_k=2)
        assert len(hits) <= 2


class TestVectorPersist:
    def test_flush_写盘(self, tmp_store, tmp_path):
        tmp_store.put("n1", "a", _vec(1, 0, 0, 0))
        assert tmp_store.flush(force=True) is True
        assert (tmp_path / "vectors.npz").exists()
        assert (tmp_path / "vectors_meta.json").exists()

    def test_持久化往返(self, store_builder, tmp_path):
        a = store_builder(tmp_path, preloaded=True)
        a.put("n1", "a", _vec(1, 0, 0, 0))
        a.put("n2", "b", _vec(0, 1, 0, 0))
        assert a.flush(force=True) is True
        # 第二个实例从磁盘加载
        b = store_builder(tmp_path, preloaded=False)
        assert b.has_vector("n1") is True
        assert b.has_vector("n2") is True
        hits = b.search_by_vector(_vec(0, 1, 0, 0), top_k=5)
        assert hits and hits[0][0] == "n2"

    def test_status_含关键字段(self, tmp_store):
        s = tmp_store.status()
        assert isinstance(s, dict)
        for k in ("count", "dim", "model", "slots"):
            assert k in s

    def test_模型指纹写入meta(self, store_builder, tmp_path):
        import json
        a = store_builder(tmp_path, model="bge-test-123", preloaded=True)
        a.put("n1", "a", _vec(1, 0, 0, 0))
        a.flush(force=True)
        meta = json.loads((tmp_path / "vectors_meta.json").read_text(encoding="utf-8"))
        assert meta["model"] == "bge-test-123"
        assert meta["dim"] == 4
        assert meta["node_ids"] == ["n1"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
