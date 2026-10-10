# -*- coding: utf-8 -*-
"""主线第81批 T4：冷存批量写 + 批量/索引召回 + 启动顺序 + 召回分层修正 测试（验收 A6）。

隔离约定：tempfile.mkdtemp()，绝不写生产 data/；真实 PulseNode + 真实 pyarrow + 真实临时文件；
不 mock 被测函数本身（79批 InfluxDB 8/8 mock、m79 mock 掉修复本身是教训）。

覆盖（先红后绿）：
  A6-1 compaction 收敛：5483 风格历史碎文件（hive 分区 evol_level=LX/，每节点一文件）合并为 1 个
        flat 文件，节点零丢失；legacy 分区子目录被清除。
  A6-2 批量召回性能：构造 N(=1000) 冷节点（每节点一碎文件），get_all_including_evicted 由
        O(N×全扫) 降到一次整读量级（实测 <10s，目标 <5–10s）。
  A6-3 单点召回命中索引：read_row_group 直读，毫秒–百毫秒级（<200ms）。
  A6-4 冷召回 evol_level 真实：混合 L1/L2/L3 驱逐后召回，层级不塌缩（不再全 L1）。
  A6-5 7 字段往返不丢：source_url/evidence_chain/source_time/acquired_time/source_timestamp/
        quality_flag/quality_reason 经「驱逐→召回」一致。
  A6-6 compaction 跳过文件 ERROR 可见（COLD_COMPACT_SKIP_ERROR_ENABLED）+ 占用重试后好文件仍合并。
"""
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyarrow.parquet as pq  # noqa: E402

import config  # noqa: E402
import nucleus.mnemosyne.PulseNodePool as PNP  # noqa: E402
from nucleus.mnemosyne.pa_compat import table_from_rows  # noqa: E402
from nucleus.mnemosyne.PulseNode import PulseNode  # noqa: E402
from nucleus.mnemosyne.PulseNodePool import PulseNodePool  # noqa: E402

# 5483 历史碎文件规模在隔离测试中完整复现成本过高（逐文件 pyarrow 写盘），
# 此处用代表性规模验证同一代码路径的收敛比；生产 5483 文件走完全相同的 compact_cold_storage。
N_FRAG = 2000          # compaction 收敛规模（复现 5483 碎片场景，收敛比 2000→1）
N_PERF = 1000         # 批量召回性能规模（任务书 A6 目标 1000 节点 <5–10s）
N_MIXED = 600         # 层级保真 / 单点索引召回规模


def _make_node(i: int, level: str) -> "PulseNode":
    """构造一个带真实层级 + 7 新字段的节点。"""
    _n = PulseNode(
        value=f"cold-node-{i}-{level}",
        keywords=[f"k{i % 5}", f"lvl-{level}"],
        source_organ="test",
        evol_level=level,
        importance="C",
    )
    # 7 个数据字段（T1-① 补齐，A6-5 往返不丢）
    _n.source_url = f"http://example.com/{i}"
    _n.evidence_chain = [f"e{i}a", f"e{i}b"]
    _n.source_time = float(1000 + i)
    _n.acquired_time = float(2000 + i)
    _n.source_timestamp = float(3000 + i)
    _n.quality_flag = "clean" if i % 3 else "suspect"
    _n.quality_reason = f"reason-{i}"
    _n.linked_nodes = [f"lk{i}", f"lk{i}x"]
    return _n


def _write_legacy_fragment(cold_dir: str, node: "PulseNode") -> None:
    """用历史（旧）格式写一个冷存碎片：evol_level 仅作分区列（文件内不含），无 7 新字段。

    复现 Dxxx / Dxxx 真实历史形态：write_to_dataset(partition_cols=["evol_level"])，
    分区列不进文件，召回时曾因硬编码 L1 而塌缩。
    """
    _row = {
        "node_id": node.node_id,
        "value": json.dumps(node.value, ensure_ascii=False),
        "keywords": list(node.keywords),
        "evol_level": node.evol_level,          # 仅作分区列，写盘后从文件移除、编码进目录名
        "importance": node.importance,
        "abstraction": node.abstraction,
        "created_at": node.created_at,
        "last_activated": node.last_activated,
        "activation_count": node.activation_count,
        "space_path": node.space_path,
        "state": getattr(node, "state", "active"),
        "source_organ": node.source_organ,
        "trigger_reason": getattr(node, "trigger_reason", ""),
        "frequency_signature": node.frequency_signature,
        "linked_nodes": list(node.linked_nodes),
        "semantic_relations": json.dumps([], ensure_ascii=False),
        "hebbian_weight": node.hebbian_weight,
        "cooccurrence_count": node.cooccurrence_count,
        "version": node.version,
        "updated_at": node.updated_at,
        "checksum": node.checksum,
        "instinct": node.instinct,
        "instinct_at": node.instinct_at,
        "instinct_active_times": node.instinct_active_times,
        "instinct_last_use": node.instinct_last_use,
        "ephemeral": getattr(node, "ephemeral", False),
        "view_mode": getattr(node, "view_mode", "OUTER_VIEW"),
        "trust_score": getattr(node, "trust_score", 50.0),
        "verification_history": json.dumps([], ensure_ascii=False),
    }
    _t = table_from_rows([_row])
    pq.write_to_dataset(_t, root_path=cold_dir, partition_cols=["evol_level"], compression="snappy")


class _ErrorCapturer(logging.Handler):
    """捕获冷存 compaction 的 ERROR 日志（A6-6 跳过文件可见性）。

    ★必须按**级别**判定，不能只判文本含「跳过」：既有代码（第68批）对前3个跳过文件已输出
    WARNING 且文案同样含「跳过」，只判文本会导致 T4-⑥ 未实现也「假绿」。
    """

    def __init__(self):
        super().__init__()
        self.messages = []
        self.records = []          # [(levelname, message), ...]

    def emit(self, record):
        self.messages.append(record.getMessage())
        self.records.append((record.levelname, record.getMessage()))


class ColdStorageBatchM81Test(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m81_cold_")
        # 确保新行为开关开启（config 已有默认值，显式置位以隔离）
        self._saved = {}
        for _k, _v in {
            "COLD_STORAGE_SCHEMA_M81_COMPLETE": True,
            "COLD_BATCH_RECALL_ENABLED": True,
            "COLD_SIDECAR_INDEX_ENABLED": True,
            "COLD_COMPACT_SKIP_ERROR_ENABLED": True,
            "COLD_STARTUP_COMPACT_WAIT_SECONDS": 0.0,
            "COLD_WRITE_BATCH_SIZE": 200,
            # 关闭后台启动 compaction daemon：本测试需确定性地「先统计碎片数 → 再手动 compact」，
            # 否则 daemon 会在 _new_pool() 后台提前合并，与手动 compact 形成竞态（count 随机为 1 或 N）。
            "COLD_DISABLE_STARTUP_COMPACT": True,
        }.items():
            self._saved[_k] = getattr(config, _k, None)
            setattr(config, _k, _v)
        # 冷存开关在生产为 False，零爆炸半径；测试显式开启
        self._saved["ENABLE_COLD_STORAGE"] = getattr(config, "ENABLE_COLD_STORAGE", None)
        # 不依赖全局 ENABLE_COLD_STORAGE，池内 set_cold_storage(True) 直接启用

    def tearDown(self):
        for _k, _v in self._saved.items():
            if _v is None:
                if hasattr(config, _k):
                    try:
                        delattr(config, _k)
                    except Exception:
                        pass
            else:
                setattr(config, _k, _v)
        try:
            shutil.rmtree(self._tmp, ignore_errors=True)
        except Exception:
            pass

    def _new_pool(self) -> "PulseNodePool":
        _pool = PulseNodePool(max_hot=10_000_000, max_warm=10_000_000)
        _pool.set_cold_storage(True, max_cold_cache=10_000_000, cold_dir=self._tmp)
        return _pool

    # ---------- A6-1：compaction 收敛 + 节点零丢失 + legacy 子目录清除 ----------
    def test_01_compaction_converges_legacy_fragments(self):
        # 构造 N_FRAG 个历史碎文件（混合真实层级，复现 5483 碎片场景）
        _nodes = []
        for _i in range(N_FRAG):
            _lvl = "L1" if _i % 3 == 0 else ("L2" if _i % 3 == 1 else "L3")
            _nodes.append(_make_node(_i, _lvl))
        for _n in _nodes:
            _write_legacy_fragment(self._tmp, _n)

        _pool = self._new_pool()
        _before_files = _pool.count_cold_parquet_files()
        self.assertEqual(_before_files, N_FRAG,
                         f"碎片文件数应为 {N_FRAG}，实际 {_before_files}")

        _res = _pool.compact_cold_storage()
        self.assertTrue(_res.get("success"), f"compaction 应成功: {_res}")
        self.assertEqual(_res.get("before_files"), N_FRAG)
        self.assertEqual(_res.get("after_files"), 1,
                         "compaction 后每分区应收敛为 1 个 flat 文件")
        self.assertEqual(_res.get("node_count"), N_FRAG,
                         f"节点应零丢失（去重后 {N_FRAG}），实际 {_res.get('node_count')}")

        # 召回校验节点零丢失 + 层级保真
        _recalled = _pool.recall_cold_nodes_batch()
        self.assertEqual(len(_recalled), N_FRAG,
                          f"召回节点数应={N_FRAG}，实际 {len(_recalled)}")
        _by_id = {n.node_id: n for n in _recalled}
        for _n in _nodes:
            self.assertIn(_n.node_id, _by_id, f"节点 {_n.node_id} 召回丢失")
            self.assertEqual(_by_id[_n.node_id].evol_level, _n.evol_level,
                             "compaction 后层级应保真，不应塌缩为 L1")

        # legacy 分区子目录应被清除（只剩 1 个 flat 文件）
        _flat = 0
        _subdirs = []
        for _root, _ds, _fs in os.walk(self._tmp):
            for _f in _fs:
                if _f.endswith(".parquet"):
                    _flat += 1
            for _d in _ds:
                if "=" in _d and _d.split("=", 1)[0] == "evol_level":
                    _subdirs.append(_d)
        self.assertEqual(_flat, 1, "compaction 后应仅 1 个 flat 文件")
        self.assertEqual(len(_subdirs), 0, "legacy evol_level=* 子目录应被清除")

    # ---------- A6-2：批量召回性能（O(N×全扫) → 一次整读）----------
    def test_02_batch_recall_performance(self):
        # 构造 N_PERF 个历史碎文件（每节点一文件，复现 O(N²) 全扫场景）
        _nodes = [_make_node(_i, "L1") for _i in range(N_PERF)]
        for _n in _nodes:
            _write_legacy_fragment(self._tmp, _n)

        _pool = self._new_pool()
        # 登记驱逐集合（模拟已被驱逐到冷存）
        _pool._cold_evicted = {n.node_id for n in _nodes}

        _t0 = time.perf_counter()
        _recalled = _pool.get_all_including_evicted()
        _elapsed = time.perf_counter() - _t0
        # 仅统计磁盘召回部分耗时（去掉内存节点拼接）
        self.assertEqual(len(_recalled), N_PERF,
                          f"批量召回应返回 {N_PERF} 节点，实际 {len(_recalled)}")
        self.assertLess(_elapsed, 10.0,
                        f"1000 节点批量召回应 <10s（目标 5–10s），实际 {_elapsed:.3f}s")
        # 盲区：若仍走 O(N×全扫)，耗时将远超此阈值；一次整读量级应在数秒内

    # ---------- A6-3：单点召回命中索引（毫秒–百毫秒级）----------
    def test_03_single_point_index_recall(self):
        _nodes = [_make_node(_i, "L2") for _i in range(N_MIXED)]
        _pool = self._new_pool()
        for _n in _nodes:
            _pool._cold[_n.node_id] = _n
            self.assertTrue(_pool._evict_cold_node(_n))
        _pool.flush_cold_buffer()   # 落盘 + 重建侧车索引

        _sample = _nodes[:50]
        _max_elapsed = 0.0
        for _n in _sample:
            _t0 = time.perf_counter()
            _got = _pool.recall_cold_node(_n.node_id)   # 走索引 read_row_group
            _dt = time.perf_counter() - _t0
            _max_elapsed = max(_max_elapsed, _dt)
            self.assertIsNotNone(_got, f"单点召回 {_n.node_id} 应命中索引")
            self.assertEqual(_got.node_id, _n.node_id)
        self.assertLess(_max_elapsed, 0.2,
                        f"单点索引召回应在百毫秒级（<200ms），实际峰值 {_max_elapsed*1000:.1f}ms")

    # ---------- A6-4：冷召回 evol_level 真实（不塌 L1）----------
    def test_04_level_fidelity_not_collapsed(self):
        _levels = ["L1", "L2", "L3"]
        _nodes = [_make_node(_i, _levels[_i % 3]) for _i in range(N_MIXED)]
        _pool = self._new_pool()
        for _n in _nodes:
            _pool._cold[_n.node_id] = _n
            self.assertTrue(_pool._evict_cold_node(_n))
        _pool.flush_cold_buffer()

        _recalled = _pool.recall_cold_nodes_batch()
        self.assertEqual(len(_recalled), N_MIXED)
        _recalled_levels = {n.evol_level for n in _recalled}
        # 关键：召回层级应与原始一致，不得全塌为 L1
        self.assertEqual(_recalled_levels, {"L1", "L2", "L3"},
                         f"冷召回层级应保真（L1/L2/L3 都在），实际 {_recalled_levels}")
        _by_id = {n.node_id: n for n in _recalled}
        for _n in _nodes:
            self.assertEqual(_by_id[_n.node_id].evol_level, _n.evol_level,
                             f"节点 {_n.node_id} 层级应={_n.evol_level}")

    # ---------- A6-5：7 字段往返不丢 ----------
    def test_05_seven_fields_roundtrip(self):
        _n = _make_node(7, "L3")
        _pool = self._new_pool()
        _pool._cold[_n.node_id] = _n
        self.assertTrue(_pool._evict_cold_node(_n))
        _pool.flush_cold_buffer()

        _got = _pool.recall_cold_node(_n.node_id)
        self.assertIsNotNone(_got)
        self.assertEqual(_got.source_url, _n.source_url)
        self.assertEqual(_got.evidence_chain, _n.evidence_chain)
        self.assertAlmostEqual(float(_got.source_time), float(_n.source_time), places=3)
        self.assertAlmostEqual(float(_got.acquired_time), float(_n.acquired_time), places=3)
        self.assertAlmostEqual(float(_got.source_timestamp), float(_n.source_timestamp), places=3)
        self.assertEqual(_got.quality_flag, _n.quality_flag)
        self.assertEqual(_got.quality_reason, _n.quality_reason)
        self.assertEqual(_got.linked_nodes, _n.linked_nodes)

    # ---------- A6-6：compaction 跳过文件 ERROR 可见 + 占用重试后好文件仍合并 ----------
    def test_06_skipped_files_error_and_retry(self):
        _capturer = _ErrorCapturer()
        PNP._module_logger.addHandler(_capturer)
        try:
            # 25 个有效碎片 + 1 个损坏文件（>= COLD_COMPACT_MIN_FILES 触发合并）
            _valid = [_make_node(_i, "L1") for _i in range(25)]
            for _n in _valid:
                _write_legacy_fragment(self._tmp, _n)
            _corrupt_path = os.path.join(self._tmp, "evol_level=L1", "corrupt.parquet")
            with open(_corrupt_path, "wb") as _f:
                _f.write(b"this is not a valid parquet file at all")

            _pool = self._new_pool()
            _res = _pool.compact_cold_storage()
            self.assertTrue(_res.get("success"), f"compaction 应成功（好文件仍合并）: {_res}")
            self.assertGreaterEqual(_res.get("skipped_files", 0), 1,
                                     "损坏文件应被计入 skipped_files")
            self.assertEqual(_res.get("node_count"), 25,
                             f"好文件节点应全部合并（25），实际 {_res.get('node_count')}")
            # ERROR 可见性：必须是 **ERROR 级** 且含「跳过」（不能靠既有 WARNING 蒙混过关）
            _err_hits = [_m for _lvl, _m in _capturer.records
                         if _lvl == "ERROR" and "跳过" in _m]
            self.assertTrue(
                _err_hits,
                f"跳过文件应 ERROR 级可见（T4-⑥），实际捕获日志: {_capturer.records}")
        finally:
            PNP._module_logger.removeHandler(_capturer)

    # ---------- A6-6b：灰度回退（开关关闭时零回归，不产生 ERROR）----------
    def test_07_skip_error_grayscale_off(self):
        _capturer = _ErrorCapturer()
        PNP._module_logger.addHandler(_capturer)
        try:
            config.COLD_COMPACT_SKIP_ERROR_ENABLED = False
            _valid = [_make_node(_i, "L1") for _i in range(5)]
            for _n in _valid:
                _write_legacy_fragment(self._tmp, _n)
            _corrupt_path = os.path.join(self._tmp, "evol_level=L1", "corrupt.parquet")
            with open(_corrupt_path, "wb") as _f:
                _f.write(b"this is not a valid parquet file at all")

            _pool = self._new_pool()
            _res = _pool.compact_cold_storage()
            self.assertTrue(_res.get("success"), f"compaction 应成功: {_res}")
            self.assertGreaterEqual(_res.get("skipped_files", 0), 1,
                                    "损坏文件仍应被计入 skipped_files")
            _err_msgs = [_m for _lvl, _m in _capturer.records if _lvl == "ERROR"]
            self.assertEqual(len(_err_msgs), 0,
                             f"灰度关闭时不应产生 ERROR（回退旧行为），实际: {_err_msgs}")
        finally:
            PNP._module_logger.removeHandler(_capturer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
