# -*- coding: utf-8 -*-
"""主线第81批 T3：jsonl 增量日志删除语义 + 熔断 + checksum + 重放定序 测试（验收 A5）。

隔离约定：tempfile.mkdtemp()，绝不写生产 data/；真实 PulseNode + 真实临时文件；
不 mock 被测函数本身（仅路由/依赖按需隔离）。

核心回归（先红后绿）：旧逻辑把 _get_changed_nodes 第二返回值（当前全部存活 ID）当删除集，
4 活节点 1 变更 → 日志含 4 条误 delete → 重放得 0 节点。本批修正后：1 upsert / 0 delete，重放得 4 节点。
"""
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from nucleus.mnemosyne.PulseNode import PulseNode  # noqa: E402
from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot  # noqa: E402


class _CfgSwitch:
    """临时切换 config 属性，退出时还原。"""

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


def _mk_snap(tmpdir):
    s = PulseSnapshot.__new__(PulseSnapshot)
    s.snapshot_path = os.path.join(tmpdir, "snapshot.json")
    s.parquet_dir = s._m68_parquet_dir()
    s._logger = logging.getLogger("test_m81_incr")
    s._logs = []
    s._log = lambda level, msg: s._logs.append(str(msg))
    s._lock = threading.RLock()
    s._max_backups = 3
    s._m44_write_allowed = lambda: True
    s._last_saved_checksum = None
    s._last_write_time = 0.0
    s._last_save_time = 0.0
    s._last_save_nodes = 0
    s._total_saves = 0
    s._m67_is_saving = False
    s._extra_state = {}
    s.resonance_engine = None
    s.hebbian_learner = None
    s.inference_cache = {}
    s._last_saved_nodes_map = {}
    return s


def _mk_nodes(ids):
    ns = []
    for _i in ids:
        _n = PulseNode.from_dict({"node_id": _i, "value": "v_" + _i, "evol_level": "L1"})
        _n.checksum = "cs_" + _i
        ns.append(_n)
    return ns


class TestIncrementalLogDeleteM81(unittest.TestCase):
    def setUp(self):
        self._dirs = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _d(self):
        d = tempfile.mkdtemp(prefix="m81_incr_")
        self._dirs.append(d)
        return d

    def _read_log(self, s):
        p = s._m67_incremental_log_path()
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8", errors="replace") as f:
            return [json.loads(l) for l in f if l.strip()]

    # ==================== A5 删除集根因修正 ====================

    def test_31_change_round_one_upsert_zero_delete(self):
        """★A5(核心)：4 活节点 / 1 变更 → 日志仅 1 upsert、0 误 delete；重放得 4 节点且字段完整。"""
        s = _mk_snap(self._d())
        with _CfgSwitch(SNAPSHOT_USE_INCREMENTAL_LOG=True):
            self.assertTrue(s._m67_incremental_log_save(_mk_nodes(["a", "b", "c", "d"]),
                                                         "chk0", time.time()))
            s._m67_clear_incremental_log()   # 隔离出「变更轮」
            _n2 = _mk_nodes(["a", "b", "c", "d"])
            _n2[0].value = "v_a_x"
            _n2[0].checksum = "cs_a_x"       # 仅 a 变更（checksum 同步变）
            _before = s._m67_incremental_log_lines()
            self.assertTrue(s._m67_incremental_log_save(_n2, "chk1", time.time()))
            _after = s._m67_incremental_log_lines()
            _recs = self._read_log(s)[-(_after - _before):]
            _ups = [r for r in _recs if r["action"] == "upsert"]
            _del = [r for r in _recs if r["action"] == "delete"]
            self.assertEqual(len(_ups), 1, "变更轮应只 1 条 upsert")
            self.assertEqual(len(_del), 0, "变更轮应 0 条误 delete（根因已修）")
            _out = s._m67_apply_incremental_log(_mk_nodes(["a", "b", "c", "d"]))
            self.assertEqual(len(_out), 4, "重放后仍是 4 节点（旧逻辑得 0）")
            self.assertTrue(all(isinstance(x, PulseNode) for x in _out))
            _by = {x.node_id: x for x in _out}
            self.assertEqual(_by["a"].value, "v_a_x", "变更节点取回新值")

    def test_32_delete_one_node_correct(self):
        """★A5：删除 1 节点 → 恰好 1 条 delete（node_id 正确）；重放后该节点被移除。"""
        s = _mk_snap(self._d())
        with _CfgSwitch(SNAPSHOT_USE_INCREMENTAL_LOG=True):
            s._m67_incremental_log_save(_mk_nodes(["a", "b", "c", "d"]), "chk0", time.time())
            s._m67_clear_incremental_log()
            _before = s._m67_incremental_log_lines()
            s._m67_incremental_log_save(_mk_nodes(["b", "c", "d"]), "chk1", time.time())
            _after = s._m67_incremental_log_lines()
            _recs = self._read_log(s)[-(_after - _before):]
            _del = [r for r in _recs if r["action"] == "delete"]
            self.assertEqual(len(_del), 1, "删除 1 节点应 1 条 delete")
            self.assertEqual(_del[0]["node_id"], "a")
            _out = s._m67_apply_incremental_log(_mk_nodes(["a", "b", "c", "d"]))
            self.assertEqual({x.node_id for x in _out}, {"b", "c", "d"})

    def test_33_circuit_breaker_high_delete_ratio(self):
        """★A5：删除占比 >50% → 熔断拒写 delete + ERROR 留痕 + 降级全量保存（日志清空）。"""
        s = _mk_snap(self._d())
        with _CfgSwitch(SNAPSHOT_USE_INCREMENTAL_LOG=True,
                        SNAPSHOT_INCREMENTAL_LOG_DELETE_RATIO_MAX=0.5):
            s._m67_incremental_log_save(_mk_nodes(["a", "b", "c", "d"]), "chk0", time.time())
            s._m67_clear_incremental_log()
            # 仅保留 a（1 个），删除 b/c/d（3 个）→ 占比 3/1 = 300% > 50%
            _ok = s._m67_incremental_log_save(_mk_nodes(["a"]), "chk1", time.time())
            self.assertTrue(_ok)
            _del = [r for r in self._read_log(s) if r["action"] == "delete"]
            self.assertEqual(len(_del), 0, "熔断应拒写 delete（日志已清空）")
            self.assertTrue(any("熔断" in x for x in s._logs), "应有熔断 ERROR 留痕")
            # 降级全量保存写入了快照（含存活节点 a）
            self.assertTrue(os.path.exists(s.snapshot_path))

    # ==================== A5 重放定序 + 幂等 + 类型统一 + checksum ====================

    def test_34_ordering_delete_final_and_bad_checksum(self):
        """★A5：delete 为最终态正确移除；坏 checksum 行被跳过（保留原值），其余正常。"""
        s = _mk_snap(self._d())
        with _CfgSwitch(SNAPSHOT_USE_INCREMENTAL_LOG=True):
            p = s._m67_incremental_log_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            _t = time.time()
            _lines = [
                json.dumps({"node_id": "x", "action": "upsert", "ts": 10,
                            "checksum": "cs1", "data": {"node_id": "x", "value": "v1", "checksum": "cs1"}}),
                json.dumps({"node_id": "x", "action": "upsert", "ts": 20,
                            "checksum": "cs2", "data": {"node_id": "x", "value": "v2", "checksum": "cs2"}}),
                json.dumps({"node_id": "x", "action": "delete", "ts": 30}),  # 最终删除
                json.dumps({"node_id": "y", "action": "upsert", "ts": 40,
                            "checksum": "WRONG",  # 与 data.checksum 不符 → 跳过
                            "data": {"node_id": "y", "value": "vy", "checksum": "RIGHT"}}),
            ]
            with open(p, "w", encoding="utf-8") as f:
                f.write("\n".join(_lines) + "\n")
            _out = s._m67_apply_incremental_log(_mk_nodes(["x", "y"]))
            _ids = {x.node_id for x in _out}
            self.assertEqual(_ids, {"y"}, "x 被最终 delete 移除；y 因坏 checksum 跳过")
            _by = {x.node_id: x for x in _out}
            self.assertEqual(_by["y"].value, "v_y", "坏 checksum 行跳过 → y 保留原值")

    def test_35_newer_upsert_wins_regardless_of_order(self):
        """★A5：同 id 多条 upsert 以 ts 最大者为准（乱序不影响）。"""
        s = _mk_snap(self._d())
        with _CfgSwitch(SNAPSHOT_USE_INCREMENTAL_LOG=True):
            p = s._m67_incremental_log_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            _lines = [
                json.dumps({"node_id": "x", "action": "upsert", "ts": 20,
                            "checksum": "cs2", "data": {"node_id": "x", "value": "v2", "checksum": "cs2"}}),
                json.dumps({"node_id": "x", "action": "upsert", "ts": 10,
                            "checksum": "cs1", "data": {"node_id": "x", "value": "v1", "checksum": "cs1"}}),
            ]
            with open(p, "w", encoding="utf-8") as f:
                f.write("\n".join(_lines) + "\n")
            _out = s._m67_apply_incremental_log(_mk_nodes(["x"]))
            self.assertEqual(len(_out), 1)
            self.assertEqual(_out[0].value, "v2", "取 ts 最大（v2）者")

    def test_36_replay_all_pulsenode(self):
        """★A5：重放结果全部为 PulseNode（类型统一，不再混 dict）。"""
        s = _mk_snap(self._d())
        with _CfgSwitch(SNAPSHOT_USE_INCREMENTAL_LOG=True):
            p = s._m67_incremental_log_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            _lines = [
                json.dumps({"node_id": "a", "action": "upsert", "ts": 1,
                            "checksum": "cs", "data": {"node_id": "a", "value": "va", "checksum": "cs"}}),
                json.dumps({"node_id": "b", "action": "upsert", "ts": 2,
                            "checksum": "cs", "data": {"node_id": "b", "value": "vb", "checksum": "cs"}}),
            ]
            with open(p, "w", encoding="utf-8") as f:
                f.write("\n".join(_lines) + "\n")
            _out = s._m67_apply_incremental_log(_mk_nodes(["a", "b"]))
            self.assertTrue(all(isinstance(x, PulseNode) for x in _out))
            self.assertFalse(any(isinstance(x, dict) for x in _out))

    def test_37_idempotent_replay(self):
        """★A5：同一日志重复重放结果相同（幂等）。"""
        s = _mk_snap(self._d())
        with _CfgSwitch(SNAPSHOT_USE_INCREMENTAL_LOG=True):
            p = s._m67_incremental_log_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            _lines = [
                json.dumps({"node_id": "a", "action": "upsert", "ts": 1,
                            "checksum": "cs", "data": {"node_id": "a", "value": "va", "checksum": "cs"}}),
            ]
            with open(p, "w", encoding="utf-8") as f:
                f.write("\n".join(_lines) + "\n")
            _inp = _mk_nodes(["a", "b"])
            _r1 = s._m67_apply_incremental_log(_inp)
            _r2 = s._m67_apply_incremental_log(_inp)
            self.assertEqual({x.node_id for x in _r1}, {x.node_id for x in _r2})
            self.assertEqual({x.value for x in _r1}, {x.value for x in _r2})


if __name__ == "__main__":
    unittest.main()
