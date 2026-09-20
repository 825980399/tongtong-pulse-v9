# -*- coding: utf-8 -*-
"""主线第81批 T1+T5：Parquet schema 补全(7字段) + 读路径统一校验 测试。

验收映射：
- A1 schema 完整：7 字段零丢（写盘包含 + 读回保真 + 旧 parquet 兼容补默认）
- A2 分层保真 + FAIL-fast：缺列 / 旧版本 / 元数据谎报塌缩 / 磁盘vs内存塌缩 四态
- A3 三遍保真零偏移：save→load 三轮字段稳定
- A7 开关组合矩阵：PARQUET_SCHEMA_M81_COMPLETE 开关影响列集合

隔离约定：一律 tempfile.mkdtemp()，绝不写生产 data/；真实 pyarrow + 真实临时文件，
不 mock 被测函数本身（仅路由测试 mock 下游统一入口以验证接线）。
"""
import os
import sys
import shutil
import tempfile
import threading
import unittest
import logging
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                    # noqa: E402
from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot        # noqa: E402
from nucleus.mnemosyne.PulseNode import PulseNode                # noqa: E402

try:
    import pyarrow as pa                                        # noqa: E402
    import pyarrow.parquet as pq                                # noqa: E402
    HAS_PYARROW = True
except Exception:
    HAS_PYARROW = False
    pa = pq = None


def _mk_snap(tmpdir):
    s = PulseSnapshot.__new__(PulseSnapshot)
    s.snapshot_path = os.path.join(tmpdir, "pulse_knowledge_snapshot.json")
    s.parquet_dir = s._m68_parquet_dir()
    s._logger = logging.getLogger("test_m81")
    s._logs = []
    s._log = lambda level, msg: s._logs.append(str(msg))
    s._lock = threading.RLock()
    return s


class _CfgSwitch:
    """临时切换 config 属性，退出时还原（含 None 删除）。"""

    def __init__(self, **kw):
        self._kw = kw
        self._saved = {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._saved[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._saved.items():
            if v is None:
                if hasattr(config, k):
                    try:
                        delattr(config, k)
                    except Exception:
                        pass
            else:
                setattr(config, k, v)


def _mk_node(nid, level, fields=None):
    d = {
        "node_id": nid,
        "value": "v_" + nid,
        "keywords": ["k1", "k2"],
        "evol_level": level,
        "importance": "B",
        "abstraction": 0.5,
        "created_at": 1000.0,
        "last_activated": 900.0,
        "activation_count": 3,
        "space_path": "/",
        "state": "active",
        "source_organ": "unknown",
        "trigger_reason": "test",
        "frequency_signature": 0.1,
        "linked_nodes": ["x", "y"],
        "semantic_relations": [{"rel": "a", "target": "b"}],
        "hebbian_weight": 0.2,
        "cooccurrence_count": 1,
        "version": 1,
        "updated_at": 950.0,
        "checksum": "cs_" + nid,
        "instinct": False,
        "instinct_at": 0.0,
        "instinct_active_times": 0,
        "instinct_last_use": 0.0,
        "ephemeral": False,
        "view_mode": "OUTER_VIEW",
        "trust_score": 50.0,
        "verification_history": [{"status": "ok"}],
    }
    if fields:
        d.update(fields)
    return d


@unittest.skipUnless(HAS_PYARROW, "pyarrow 不可用，跳过 Parquet T1/T5 测试")
class TestParquetSchemaM81(unittest.TestCase):
    def setUp(self):
        self._dirs = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _d(self):
        d = tempfile.mkdtemp(prefix="m81_")
        self._dirs.append(d)
        return d

    # ==================== A1 schema 完整 + 旧兼容 ====================

    def test_21_schema_complete_roundtrip(self):
        """★A1：7 字段写盘包含 + 读回逐字段保真（合成 L1/L2/L3 各 15 节点）。"""
        s = _mk_snap(self._d())
        nodes = []
        for lv in ("L1", "L2", "L3"):
            for i in range(15):
                fields = {
                    "source_url": "http://ex.com/%s_%d" % (lv, i),
                    "evidence_chain": [{"step": i, "desc": "d"}],
                    "source_time": float(1000 + i),
                    "acquired_time": float(2000 + i),
                    "source_timestamp": float(3000 + i),
                    "quality_flag": "suspect" if i % 5 == 0 else "clean",
                    "quality_reason": "manual" if i % 5 == 0 else "",
                }
                nodes.append(PulseNode.from_dict(_mk_node("%s_%d" % (lv, i), lv, fields)))
        s.node_pool = SimpleNamespace(get_all_including_evicted=lambda: nodes)
        self.assertTrue(s.save_parquet(), "save_parquet 应成功")
        out = s._m81_load_parquet_unified()
        self.assertIsNotNone(out, "统一加载应返回节点")
        self.assertEqual(len(out), 45)
        by_id = {n.node_id: n for n in out}
        for n in nodes:
            loaded = by_id.get(n.node_id)
            self.assertIsNotNone(loaded, "节点 %s 应完整读回" % n.node_id)
            self.assertEqual(getattr(loaded, "source_url", ""), n.source_url)
            self.assertEqual(getattr(loaded, "evidence_chain", []), n.evidence_chain)
            self.assertAlmostEqual(float(getattr(loaded, "source_time", 0) or 0), n.source_time)
            self.assertAlmostEqual(float(getattr(loaded, "acquired_time", 0) or 0), n.acquired_time)
            self.assertAlmostEqual(float(getattr(loaded, "source_timestamp", 0) or 0), n.source_timestamp)
            self.assertEqual(getattr(loaded, "quality_flag", "clean"), n.quality_flag)
            self.assertEqual(getattr(loaded, "quality_reason", ""), n.quality_reason)

    def test_22_required_columns_include_7_when_on(self):
        """★A1：灰度开时必需列集合包含 7 新字段。"""
        s = _mk_snap(self._d())
        cols = s._m81_parquet_required_columns()
        for c in ("source_url", "evidence_chain", "source_time",
                  "acquired_time", "source_timestamp", "quality_flag", "quality_reason"):
            self.assertIn(c, cols, "灰度开应要求列 %s" % c)

    def test_23_required_columns_exclude_7_when_off(self):
        """★A7：灰度关时必需列集合不含 7 新字段（复现旧 29 列行为，可回退）。"""
        s = _mk_snap(self._d())
        with _CfgSwitch(PARQUET_SCHEMA_M81_COMPLETE=False):
            cols = s._m81_parquet_required_columns()
            for c in ("source_url", "evidence_chain", "source_time",
                      "acquired_time", "source_timestamp", "quality_flag", "quality_reason"):
                self.assertNotIn(c, cols, "灰度关不应要求列 %s" % c)

    def test_26_old_parquet_compat_defaults(self):
        """★A1：旧 parquet 无 7 列时，_parquet_row_to_dict 补默认，不炸。"""
        s = _mk_snap(self._d())
        base_row = {"node_id": "x", "value": "v", "evol_level": "L1"}
        d = s._parquet_row_to_dict(base_row, level="L1")
        self.assertEqual(d["source_url"], "")
        self.assertEqual(d["evidence_chain"], [])
        self.assertEqual(float(d["source_time"] or 0), 0.0)
        self.assertEqual(float(d["acquired_time"] or 0), 0.0)
        self.assertEqual(float(d["source_timestamp"] or 0), 0.0)
        self.assertEqual(d["quality_flag"], "clean")
        self.assertEqual(d["quality_reason"], "")

    def test_27_save_columns_exclude_7_when_off(self):
        """★A7：灰度关时写盘列不含 7 新字段（旧列布局可回退）。"""
        s = _mk_snap(self._d())
        n = PulseNode.from_dict(_mk_node("z", "L1"))
        with _CfgSwitch(PARQUET_SCHEMA_M81_COMPLETE=False):
            rows = s._nodes_to_parquet_columns([n])
            self.assertNotIn("source_url", rows[0])
            self.assertNotIn("quality_flag", rows[0])

    # ==================== A2 分层保真 + FAIL-fast ====================

    def test_24_collapse_metadata_mismatch_fails(self):
        """★A2：元数据谎报分层计数（l2=100 实际缺失）→ FAIL-fast 回退 JSON(None)。"""
        s = _mk_snap(self._d())
        nodes = [PulseNode.from_dict(_mk_node("n%d" % i, lv))
                 for lv in ("L1", "L2", "L3") for i in range(20)]
        rows = s._nodes_to_parquet_columns(nodes)
        tbl = pa.Table.from_pylist(rows).replace_schema_metadata({
            b"m81_schema_version": b"m81.v1",
            b"node_count": b"60", b"l1_count": b"20",
            b"l2_count": b"100",   # 谎报：实际仅 20
            b"l3_count": b"20", b"node_list_checksum": b"x", b"write_time": b"t"})
        pq.write_to_dataset(tbl, root_path=s.parquet_dir,
                            partition_cols=["evol_level"], compression="snappy")
        # 模拟塌缩：删除 L2 分区目录（元数据仍声称 100）
        shutil.rmtree(os.path.join(s.parquet_dir, "evol_level=L2"), ignore_errors=True)
        out = s._m81_load_parquet_unified()
        self.assertIsNone(out, "分层塌缩(元数据谎报)应 FAIL-fast 回退 JSON")

    def test_25_collapse_disk_vs_memory_fails(self):
        """★A2：旧 parquet 无元数据也稳健——磁盘某层有行但内存加载 0（解析失败）→ FAIL-fast。"""
        s = _mk_snap(self._d())
        nodes = [PulseNode.from_dict(_mk_node("n%d" % i, lv))
                 for lv in ("L1", "L2", "L3") for i in range(20)]
        rows = s._nodes_to_parquet_columns(nodes)
        # 整列 activation_count 强制推断为 string；L2 行置不可解析值 → from_dict 全程失败
        for r in rows:
            r["activation_count"] = str(r["activation_count"])
        for r in rows:
            if r.get("evol_level") == "L2":
                r["activation_count"] = "abc"
        tbl = pa.Table.from_pylist(rows)
        pq.write_to_dataset(tbl, root_path=s.parquet_dir,
                            partition_cols=["evol_level"], compression="snappy")
        out = s._m81_load_parquet_unified()
        self.assertIsNone(out, "磁盘L2有行但内存加载0(解析失败)应 FAIL-fast")

    def test_28_missing_column_fails(self):
        """★A2：缺必需列（去掉 quality_flag）→ FAIL-fast 回退 JSON(None)。"""
        s = _mk_snap(self._d())
        nodes = [PulseNode.from_dict(_mk_node("n%d" % i, lv))
                 for lv in ("L1", "L2", "L3") for i in range(10)]
        rows = s._nodes_to_parquet_columns(nodes)
        for r in rows:
            r.pop("quality_flag", None)
        tbl = pa.Table.from_pylist(rows)
        pq.write_to_dataset(tbl, root_path=s.parquet_dir,
                            partition_cols=["evol_level"], compression="snappy")
        out = s._m81_load_parquet_unified()
        self.assertIsNone(out, "缺列应 FAIL-fast 回退 JSON")

    def test_29_old_schema_version_fails(self):
        """★A2：schema 版本过旧（m80.old）→ FAIL-fast 回退 JSON(None)。"""
        s = _mk_snap(self._d())
        nodes = [PulseNode.from_dict(_mk_node("n%d" % i, lv))
                 for lv in ("L1", "L2", "L3") for i in range(10)]
        rows = s._nodes_to_parquet_columns(nodes)
        tbl = pa.Table.from_pylist(rows).replace_schema_metadata({
            b"m81_schema_version": b"m80.old"})
        pq.write_to_dataset(tbl, root_path=s.parquet_dir,
                            partition_cols=["evol_level"], compression="snappy")
        out = s._m81_load_parquet_unified()
        self.assertIsNone(out, "旧 schema 版本应 FAIL-fast 回退 JSON")

    def test_210_no_parquet_dir_returns_none(self):
        """★A2：无 Parquet 目录时统一入口返回 None（触发 JSON 回退），不抛异常。"""
        s = _mk_snap(self._d())
        self.assertIsNone(s._m81_load_parquet_unified())

    def test_211_verify_parquet_matches_load(self):
        """★A2：_m68_verify_parquet 返回行数 == 统一加载节点数（双校验一致）。"""
        s = _mk_snap(self._d())
        nodes = [PulseNode.from_dict(_mk_node("n%d" % i, lv))
                 for lv in ("L1", "L2", "L3") for i in range(20)]
        s.node_pool = SimpleNamespace(get_all_including_evicted=lambda: nodes)
        self.assertTrue(s.save_parquet())
        self.assertEqual(s._m68_verify_parquet(), 60)
        self.assertEqual(len(s._m81_load_parquet_unified()), 60)

    # ==================== A3 三遍保真零偏移 ====================

    def test_212_three_rounds_stable(self):
        """★A3：save→load 三轮，节点数与 7 字段值零偏移。"""
        s = _mk_snap(self._d())
        nodes = []
        for lv in ("L1", "L2", "L3"):
            for i in range(12):
                fields = {
                    "source_url": "http://ex.com/%s_%d" % (lv, i),
                    "evidence_chain": [{"step": i, "desc": "d"}],
                    "source_time": float(1000 + i),
                    "acquired_time": float(2000 + i),
                    "source_timestamp": float(3000 + i),
                    "quality_flag": "clean",
                    "quality_reason": "",
                }
                nodes.append(PulseNode.from_dict(_mk_node("%s_%d" % (lv, i), lv, fields)))
        s.node_pool = SimpleNamespace(get_all_including_evicted=lambda: nodes)
        prev = {(n.node_id, n.source_url, n.source_time) for n in nodes}
        for _round in range(3):
            self.assertTrue(s.save_parquet(), "第%d轮 save 应成功" % (_round + 1))
            out = s._m81_load_parquet_unified()
            self.assertIsNotNone(out)
            self.assertEqual(len(out), 36, "第%d轮节点数应稳定=36" % (_round + 1))
            cur = {(n.node_id, getattr(n, "source_url", ""),
                    float(getattr(n, "source_time", 0) or 0)) for n in out}
            self.assertEqual(cur, prev, "第%d轮 7 字段值应零偏移" % (_round + 1))
            # 用读回节点作为下一轮输入（真实 round-trip）
            s.node_pool = SimpleNamespace(get_all_including_evicted=lambda: out)


if __name__ == "__main__":
    unittest.main()
