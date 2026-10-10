# -*- coding: utf-8 -*-
"""主线第81批【补2】增量日志 × Parquet 全量检查点闭环 —— 端到端测试 C1-C7。

与旧 m81 T3 测试的区别（星轨补正要点）：
- 旧测试用 ``PulseSnapshot.__new__`` 残缺实例 + 直调内部方法（`_m67_incremental_log_save`）；
- 本测试**走真实 `save()`/`load()` 编排** + 真实 `pyarrow` 写读 tmp parquet + 真实
  `PulseNodePool`（`get_all_including_evicted()` 返回真实节点），不 mock 被测检查点逻辑。

C1 全量检查点清空 jsonl + 清空安全（先红）  C2 Parquet 失败保留日志零丢失
C3 熔断路径刷 Parquet + 清日志（先红）      C4 达阈值路径刷 Parquet + 清日志（先红）
C5 jsonl 损坏回退不崩                        C6 开关 False 零回归
C7 分层保真贯穿

隔离：tempfile.mkdtemp() + 真实临时 parquet；绝不写生产 data/。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from nucleus.mnemosyne.PulseNode import PulseNode  # noqa: E402
from nucleus.mnemosyne.PulseNodePool import PulseNodePool  # noqa: E402
from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot  # noqa: E402

try:
    import pyarrow.parquet as pq  # noqa: E402
    HAS_PYARROW = True
except Exception:
    HAS_PYARROW = False
    pq = None


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


def _mk_node(nid, level):
    """真实 PulseNode（关键字逐节点唯一，避免池内容级去重合并）。"""
    n = PulseNode.from_dict({
        "node_id": nid, "value": "v_" + nid, "keywords": ["kw_" + nid],
        "evol_level": level, "importance": "B", "abstraction": 0.5,
        "created_at": 1000.0, "last_activated": 900.0, "activation_count": 3,
        "space_path": "/", "state": "active", "source_organ": "test",
        "trigger_reason": "test", "frequency_signature": 0.1,
        "linked_nodes": ["x", "y"], "semantic_relations": [{"rel": "a", "target": "b"}],
        "hebbian_weight": 0.2, "cooccurrence_count": 1, "version": 1,
        "updated_at": 950.0, "checksum": "cs_" + nid, "ephemeral": False,
        "view_mode": "OUTER_VIEW", "trust_score": 50.0,
        "verification_history": [{"status": "ok"}],
    })
    n.checksum = "cs_" + nid
    return n


def _mk_pool(nodes):
    p = PulseNodePool(max_hot=10_000_000, max_warm=10_000_000)
    for n in nodes:
        p.add(n)
    return p


def _new_snap(path):
    """真实构造 PulseSnapshot（走 __init__），并绕开写盘节流以便确定性触发增量分支。"""
    s = PulseSnapshot(snapshot_path=path)
    s.INCREMENTAL_MIN_INTERVAL = 0
    return s


def _read_jsonl(snap):
    p = snap._m67_incremental_log_path()
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8", errors="replace") as f:
        for _l in f:
            if _l.strip():
                try:
                    out.append(json.loads(_l))
                except Exception:
                    pass
    return out


def _parquet_ids(parquet_dir):
    if not (HAS_PYARROW and os.path.isdir(parquet_dir)):
        return set()
    try:
        _t = pq.read_table(parquet_dir)
        return {r.get("node_id") for r in _t.to_pylist()}
    except Exception:
        return set()


_BASE_CFG = dict(
    SNAPSHOT_USE_INCREMENTAL_LOG=True,
    PARQUET_AS_PRIMARY_STORAGE=True,
    SNAPSHOT_SAVE_JSON_BACKUP=True,
    SNAPSHOT_HOT_COLD_LOAD=False,
    SNAPSHOT_ASYNC_SAVE=False,
    ENABLE_TEMP_SNAPSHOT_CLEANUP=False,
)


@unittest.skipUnless(HAS_PYARROW, "pyarrow 不可用，跳过增量检查点端到端测试")
class TestIncrementalCheckpointM81b2(unittest.TestCase):
    def setUp(self):
        self._dirs = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _d(self):
        d = tempfile.mkdtemp(prefix="m81b2_")
        self._dirs.append(d)
        return d

    def _assert_levels(self, nodes, expect):
        _c = {"L1": 0, "L2": 0, "L3": 0}
        for n in nodes:
            _lv = str(getattr(n, "evol_level", "L1")).upper()
            _c[_lv] = _c.get(_lv, 0) + 1
        self.assertEqual(_c, expect, "分层计数应保真: {} vs {}".format(_c, expect))

    # ==================== C1 全量检查点清空 jsonl + 清空安全（先红） ====================
    def test_C1_full_checkpoint_clears_jsonl(self):
        d = self._d()
        sp = os.path.join(d, "snapshot.json")
        with _CfgSwitch(**_BASE_CFG):
            s = _new_snap(sp)
            pool = _mk_pool([_mk_node("n1", "L1"), _mk_node("n2", "L1"),
                             _mk_node("n3", "L2"), _mk_node("n4", "L3")])
            s.set_node_pool(pool)
            _jl = s._m67_incremental_log_path()

            # ① 初始全量（JSON+Parquet）
            self.assertTrue(s.save(force_full=True), "初始全量保存应成功")
            self.assertFalse(os.path.exists(_jl))
            self.assertEqual(_parquet_ids(s.parquet_dir), {"n1", "n2", "n3", "n4"})

            # ② 增量轮：新增1 + 修改1 + 删除1 → jsonl
            pool.add(_mk_node("n5", "L1"))
            _n1 = pool.get("n1")
            _n1.value = "v_n1_x"
            _n1.checksum = "cs_n1_x"
            pool.remove("n2")
            s._last_write_time = 0.0
            self.assertTrue(s.save(), "增量轮保存应成功")
            self.assertTrue(os.path.exists(_jl), "增量轮应生成 jsonl")
            _recs = _read_jsonl(s)
            self.assertTrue(any(r["action"] == "upsert" for r in _recs))
            self.assertTrue(any(r["action"] == "delete" and r["node_id"] == "n2" for r in _recs))

            # ③ 新实例 load() → Parquet + jsonl 重放零丢失
            s2 = _new_snap(sp)
            _nodes2 = s2.load()
            _by2 = {n.node_id: n for n in _nodes2}
            self.assertEqual(set(_by2), {"n1", "n3", "n4", "n5"},
                             "重放后应为最新全集（旧逻辑/rpc 缺失会丢 n5 或还原不了删除）")
            self.assertEqual(_by2["n1"].value, "v_n1_x", "修改节点应取回新值")
            self._assert_levels(_nodes2, {"L1": 2, "L2": 1, "L3": 1})

            # ④ 再全量 → 检查点达成应清空 jsonl（先红：未修复时 jsonl 仍在）
            s._last_write_time = 0.0
            s._last_saved_checksum = ""   # harness：强制全量路径执行（绕开「无变更短路」）
            self.assertTrue(s.save(force_full=True), "第二次全量保存应成功")
            self.assertFalse(os.path.exists(_jl), "★全量检查点后 jsonl 应被清空")

            # ⑤ 第三实例 load()（纯 Parquet）→ 仍是同一最新全集，分层不塌缩
            s3 = _new_snap(sp)
            _nodes3 = s3.load()
            self.assertEqual(set(n.node_id for n in _nodes3), {"n1", "n3", "n4", "n5"},
                             "旧增量应已物化进 Parquet，清空不依赖 jsonl")
            self._assert_levels(_nodes3, {"L1": 2, "L2": 1, "L3": 1})

    # ==================== C2 Parquet 刷新失败保留日志且零丢失 ====================
    def test_C2_parquet_fail_keeps_jsonl(self):
        d = self._d()
        sp = os.path.join(d, "snapshot.json")
        with _CfgSwitch(**_BASE_CFG):
            s = _new_snap(sp)
            pool = _mk_pool([_mk_node("n1", "L1"), _mk_node("n2", "L1"),
                             _mk_node("n3", "L2"), _mk_node("n4", "L3")])
            s.set_node_pool(pool)
            _jl = s._m67_incremental_log_path()
            self.assertTrue(s.save(force_full=True))

            _n1 = pool.get("n1")
            _n1.value = "v_n1_c2"
            _n1.checksum = "cs_n1_c2"
            pool.remove("n2")
            s._last_write_time = 0.0
            self.assertTrue(s.save())
            self.assertTrue(os.path.exists(_jl))

            # 故障注入：save_parquet 返回 False（注入依赖失败，非 mock 检查点逻辑）
            s._last_write_time = 0.0
            s._last_saved_checksum = ""
            with mock.patch.object(PulseSnapshot, "save_parquet", return_value=False):
                self.assertTrue(s.save(force_full=True), "JSON 成功时检查点返回应如实为 True")
            self.assertTrue(os.path.exists(_jl), "★Parquet 物化失败 → jsonl 必须保留（不丢数据）")

            # 新实例 load()：旧 Parquet + jsonl 重放 → 仍最新
            s2 = _new_snap(sp)
            _by2 = {n.node_id: n for n in s2.load()}
            self.assertEqual(set(_by2), {"n1", "n3", "n4"})
            self.assertEqual(_by2["n1"].value, "v_n1_c2", "jsonl 重放应带回未物化的修改")

    # ==================== C3 熔断路径刷 Parquet + 清日志（先红） ====================
    def test_C3_circuit_breaker_refreshes_parquet(self):
        d = self._d()
        sp = os.path.join(d, "snapshot.json")
        _cfg = dict(_BASE_CFG)
        _cfg["SNAPSHOT_INCREMENTAL_LOG_DELETE_RATIO_MAX"] = 0.5
        with _CfgSwitch(**_cfg):
            s = _new_snap(sp)
            # 6 节点删 3：存活 3（=50%，不触发「快照保护」<50% 短路），删除占比 100% > 50% → 熔断
            _ids6 = ["n1", "n2", "n3", "n4", "n5", "n6"]
            pool = _mk_pool([_mk_node(_i, ("L1" if _i in ("n1", "n2", "n3") else "L2"))
                             for _i in _ids6])
            s.set_node_pool(pool)
            _jl = s._m67_incremental_log_path()
            self.assertTrue(s.save(force_full=True))
            self.assertEqual(_parquet_ids(s.parquet_dir), set(_ids6))

            # 删除 3/6（存活 3）→ 占比 100% > 50% → 熔断（绕过快照保护）
            for _x in ("n4", "n5", "n6"):
                pool.remove(_x)
            s._last_write_time = 0.0
            self.assertTrue(s.save(), "熔断降级全量保存应成功")
            self.assertEqual(_parquet_ids(s.parquet_dir), {"n1", "n2", "n3"},
                             "★熔断降级应刷新 Parquet（先红：未修复不刷 Parquet）")
            self.assertFalse(os.path.exists(_jl), "熔断检查点后 jsonl 应清空")

            s2 = _new_snap(sp)
            self.assertEqual({n.node_id for n in s2.load()}, {"n1", "n2", "n3"},
                             "熔断后 load 零丢失")

    # ==================== C4 达阈值路径刷 Parquet + 清日志（先红） ====================
    def test_C4_threshold_refreshes_parquet(self):
        d = self._d()
        sp = os.path.join(d, "snapshot.json")
        _cfg = dict(_BASE_CFG)
        _cfg["SNAPSHOT_INCREMENTAL_LOG_MAX_LINES"] = 1
        with _CfgSwitch(**_cfg):
            s = _new_snap(sp)
            pool = _mk_pool([_mk_node("n1", "L1"), _mk_node("n2", "L1"),
                             _mk_node("n3", "L2"), _mk_node("n4", "L3")])
            s.set_node_pool(pool)
            _jl = s._m67_incremental_log_path()
            self.assertTrue(s.save(force_full=True))

            _n1 = pool.get("n1")
            _n1.value = "v_n1_c4"
            _n1.checksum = "cs_n1_c4"
            s._last_write_time = 0.0
            self.assertTrue(s.save(), "达阈值合并应成功")
            self.assertFalse(os.path.exists(_jl), "达阈值合并后 jsonl 应清空")
            self.assertEqual(_parquet_ids(s.parquet_dir), {"n1", "n2", "n3", "n4"},
                             "★达阈值应刷新 Parquet（先红：未修复不刷 Parquet）")

            s2 = _new_snap(sp)
            _by2 = {n.node_id: n for n in s2.load()}
            self.assertEqual(_by2["n1"].value, "v_n1_c4",
                             "★达阈值刷新 Parquet 后 load 应取到最新值（否则=数据丢失，先红）")

    # ==================== C5 jsonl 损坏回退不崩 ====================
    def test_C5_corrupt_jsonl_no_crash(self):
        d = self._d()
        sp = os.path.join(d, "snapshot.json")
        with _CfgSwitch(**_BASE_CFG):
            s = _new_snap(sp)
            pool = _mk_pool([_mk_node("n1", "L1"), _mk_node("n2", "L1"),
                             _mk_node("n3", "L2"), _mk_node("n4", "L3")])
            s.set_node_pool(pool)
            self.assertTrue(s.save(force_full=True))

            _jl = s._m67_incremental_log_path()
            os.makedirs(os.path.dirname(_jl), exist_ok=True)
            with open(_jl, "w", encoding="utf-8") as f:
                f.write('{"node_id": "n1", "action": "upsert", ')   # 截断
                f.write("\nnot-json-garbage\n")
                f.write("\x00\x01\x02binary\x00\n")

            s2 = _new_snap(sp)
            _nodes2 = s2.load()   # 不应抛异常
            self.assertEqual(set(n.node_id for n in _nodes2), {"n1", "n2", "n3", "n4"},
                             "坏 jsonl 应跳过、回退 Parquet 全量，不返回空、不塌缩")
            self._assert_levels(_nodes2, {"L1": 2, "L2": 1, "L3": 1})

    # ==================== C6 开关 False 零回归 ====================
    def test_C6_switch_off_no_regression(self):
        d = self._d()
        sp = os.path.join(d, "snapshot.json")
        _cfg = dict(_BASE_CFG)
        _cfg["SNAPSHOT_USE_INCREMENTAL_LOG"] = False
        with _CfgSwitch(**_cfg):
            s = _new_snap(sp)
            pool = _mk_pool([_mk_node("n1", "L1"), _mk_node("n2", "L1"),
                             _mk_node("n3", "L2"), _mk_node("n4", "L3")])
            s.set_node_pool(pool)
            _jl = s._m67_incremental_log_path()
            self.assertTrue(s.save(force_full=True))
            self.assertFalse(os.path.exists(_jl), "关开关不应生成 jsonl")

            _n1 = pool.get("n1")
            _n1.value = "v_n1_off"
            _n1.checksum = "cs_n1_off"
            s._last_write_time = 0.0
            self.assertTrue(s.save(), "关开关时增量保存仍应成功（回退旧 _incremental_save）")
            self.assertFalse(os.path.exists(_jl), "★关开关时任何分支都不生成/不清空 jsonl")

            # 再一次全量 → 正常刷新 Parquet（检查点清空分支因开关关而 no-op）
            s._last_write_time = 0.0
            s._last_saved_checksum = ""
            self.assertTrue(s.save(force_full=True))
            self.assertFalse(os.path.exists(_jl), "★关开关时全量也不生成/不清空 jsonl")

            s2 = _new_snap(sp)
            _by2 = {n.node_id: n for n in s2.load()}
            self.assertEqual(set(_by2), {"n1", "n2", "n3", "n4"})
            self.assertEqual(_by2["n1"].value, "v_n1_off",
                             "全量刷新后 load 取到最新（行为与改动前逐字节一致）")

    # ==================== C7 分层保真贯穿 ====================
    def test_C7_layer_fidelity_across_cycles(self):
        d = self._d()
        sp = os.path.join(d, "snapshot.json")
        with _CfgSwitch(**_BASE_CFG):
            s = _new_snap(sp)
            pool = _mk_pool([_mk_node("a1", "L1"), _mk_node("a2", "L2"), _mk_node("a3", "L3"),
                             _mk_node("a4", "L1"), _mk_node("a5", "L2")])
            s.set_node_pool(pool)

            self.assertTrue(s.save(force_full=True))
            self._assert_levels(s.load(), {"L1": 2, "L2": 2, "L3": 1})

            pool.add(_mk_node("a6", "L3"))
            s._last_write_time = 0.0
            self.assertTrue(s.save())
            # 增量重放后分层保真
            self._assert_levels(_new_snap(sp).load(), {"L1": 2, "L2": 2, "L3": 2})

            s._last_write_time = 0.0
            s._last_saved_checksum = ""
            self.assertTrue(s.save(force_full=True))
            self._assert_levels(_new_snap(sp).load(), {"L1": 2, "L2": 2, "L3": 2})
            # 全量检查点后纯 Parquet 加载，分层不塌缩（呼应 G0 事故 / 第80批 T2）


if __name__ == "__main__":
    unittest.main(verbosity=2)
