# -*- coding: utf-8 -*-
"""主线第67批 T6：快照性能止血(T1) / 增量日志(T2) / KAL(T3) / 自适应降频(T4) / WriteGuard(T5) 测试。

隔离约定：
- 所有落盘一律用 tempfile.mkdtemp()，绝不写生产 data/；
- PulseSnapshot 用 __new__ 轻量实例化（避免重依赖），只挂被测方法需要的属性。
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
import nucleus.data.write_guard as _wg  # noqa: E402
import nucleus.knowledge_access_layer as _kal  # noqa: E402
import nucleus.runtime_metrics as _rm  # noqa: E402
from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot  # noqa: E402


class _FakeNode:
    """轻量假节点：只提供被测代码用到的字段。"""

    def __init__(self, nid, value="", level="L1", keywords=None, linked=None):
        self.node_id = nid
        self.value = value
        self.keywords = keywords or []
        self.evol_level = level
        self.linked_nodes = linked or []
        self.space_path = ""
        self.created_at = None
        self.source_organ = ""

    def to_dict(self):
        return {"node_id": self.node_id, "value": self.value,
                "evol_level": self.evol_level}


def _mk_snap(tmpdir, batch_size=1000):
    s = PulseSnapshot.__new__(PulseSnapshot)
    s.snapshot_path = os.path.join(tmpdir, "snapshot.json")
    s._logger = logging.getLogger("test_m67")
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
    s.inference_cache = {}
    s.node_pool = None
    s._batch_override = batch_size
    return s


class _CfgSwitch:
    """临时改写 config 属性，退出还原。"""

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


# ==================== T1 快照保存性能止血 ====================

class TestSnapshotPerf(unittest.TestCase):
    def setUp(self):
        self._dirs = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _d(self):
        d = tempfile.mkdtemp(prefix="m67_")
        self._dirs.append(d)
        return d

    def test_11_streaming_write_produces_valid_json(self):
        """分批流式写：产出合法 JSON，节点数与内容正确（替代一次性构建大列表）。"""
        s = _mk_snap(self._d())
        nodes = [_FakeNode("n%d" % i, value="v%d" % i) for i in range(25)]
        with _CfgSwitch(SNAPSHOT_BATCH_SIZE=7):
            s._m67_write_snapshot_streaming({"version": "v9.5", "total_nodes_all": 25},
                                            nodes)
        with open(s.snapshot_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data["nodes"]), 25)
        self.assertEqual(data["nodes"][0]["node_id"], "n0")
        self.assertEqual(data["nodes"][24]["value"], "v24")
        self.assertEqual(data["total_nodes_all"], 25)

    def test_12_batch_size_zero_disables_streaming(self):
        """SNAPSHOT_BATCH_SIZE<=0 → 关闭流式（回退路径判据），get_interval 判据一致。"""
        s = _mk_snap(self._d())
        with _CfgSwitch(SNAPSHOT_BATCH_SIZE=0):
            self.assertEqual(s._m67_batch_size(), 0)
            self.assertFalse(s._m67_batch_size() > 0)
        with _CfgSwitch(SNAPSHOT_BATCH_SIZE=1000):
            self.assertTrue(s._m67_batch_size() > 0)

    def test_13_async_save_submits_and_returns_immediately(self):
        """异步保存：立即返回 True，后台线程执行同步主体（不阻塞调用方）。"""
        s = _mk_snap(self._d())
        calls = []
        s._save_sync = lambda force_full=False: (calls.append(1), True)[1]
        with _CfgSwitch(SNAPSHOT_ASYNC_SAVE=True):
            r = s._save_async()
        self.assertTrue(r)
        for _ in range(50):
            if calls:
                break
            time.sleep(0.05)
        self.assertEqual(len(calls), 1, "异步线程应执行一次同步主体")

    def test_14_async_skip_when_already_saving(self):
        """已有保存进行中 → 跳过本次触发（避免线程堆积形成新恶性循环）。"""
        s = _mk_snap(self._d())
        calls = []
        s._save_sync = lambda force_full=False: (calls.append(1), True)[1]
        s._m67_is_saving = True
        # ★第81批 T3：建模真实 in-flight 状态——生产里 _m67_save_start_time 与
        #   _m67_is_saving 同时置位（PulseSnapshot._save_async :793-794）；否则第80批 T3
        #   的卡死 watchdog 会把 time.time()-0 误判为卡死并强制重启保存。
        s._m67_save_start_time = time.time()
        r = s._save_async()
        self.assertTrue(r)
        self.assertEqual(len(calls), 0, "进行中时不得再提交")
        # ★第81批 T3：78批 T4 把跳过文案从「跳过本次触发」改为「已有快照保存进行中，
        #   本次触发合并为待保存」，此处对齐真实输出（验证走了合并/跳过分支）。
        self.assertTrue(any("已有快照保存进行中" in x for x in s._logs))

    def test_15_async_switch_off(self):
        """异步开关关闭 → _m67_async_save_enabled() 为 False（走同步路径）。"""
        s = _mk_snap(self._d())
        with _CfgSwitch(SNAPSHOT_ASYNC_SAVE=False):
            self.assertFalse(s._m67_async_save_enabled())
        with _CfgSwitch(SNAPSHOT_ASYNC_SAVE=True):
            self.assertTrue(s._m67_async_save_enabled())

    def test_16_timeout_config_present(self):
        """保存超时阈值配置存在且为合理正数（超时仅告警不中断）。"""
        self.assertTrue(hasattr(config, "SNAPSHOT_SAVE_TIMEOUT"))
        self.assertGreater(float(config.SNAPSHOT_SAVE_TIMEOUT), 0)


# ==================== T2 增量日志 ====================

class TestIncrementalLog(unittest.TestCase):
    def setUp(self):
        self._dirs = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _d(self):
        d = tempfile.mkdtemp(prefix="m67i_")
        self._dirs.append(d)
        return d

    def _snap(self, d):
        s = _mk_snap(d)
        # ★B156-3 T-A09 接线点：生产 _m67_incremental_log_save 现以第二返回值
        #   （当前节点 id 集合）计算存活数/删除集（D154 熔断）。原 stub 返回 []
        #   使存活=0 触发「降级全量保存」→ 增量日志不落盘。改为回传真实 id 集合。
        s._get_changed_nodes = lambda nodes: (nodes, {getattr(n, "node_id", None) for n in nodes})
        return s

    def test_21_log_written_and_counted(self):
        """增量日志：变更写入 jsonl，行数正确统计。"""
        s = self._snap(self._d())
        nodes = [_FakeNode("a", value="va"), _FakeNode("b", value="vb")]
        with _CfgSwitch(SNAPSHOT_USE_INCREMENTAL_LOG=True,
                        SNAPSHOT_INCREMENTAL_LOG_MAX_LINES=10000):
            ok = s._m67_incremental_log_save(nodes, "chk1", time.time())
        self.assertTrue(ok)
        self.assertEqual(s._m67_incremental_log_lines(), 2)

    def test_22_clear_log(self):
        """清空增量日志：行数归零。"""
        s = self._snap(self._d())
        with _CfgSwitch(SNAPSHOT_USE_INCREMENTAL_LOG=True):
            s._m67_incremental_log_save([_FakeNode("a")], "c", time.time())
            self.assertEqual(s._m67_incremental_log_lines(), 1)
            s._m67_clear_incremental_log()
        self.assertEqual(s._m67_incremental_log_lines(), 0)

    def test_23_replay_applies_upsert(self):
        """启动恢复：重放 upsert 经 PulseNode.from_dict 转成 PulseNode（类型统一），字段更新正确。"""
        s = self._snap(self._d())
        node = _FakeNode("a", value="old")
        payload = {"node_id": "a", "action": "upsert", "ts": time.time(),
                   "data": {"node_id": "a", "value": "new"}}
        p = s._m67_incremental_log_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        with _CfgSwitch(SNAPSHOT_USE_INCREMENTAL_LOG=True):
            out = s._m67_apply_incremental_log([node])
        self.assertEqual(len(out), 1)
        self.assertNotIsInstance(out[0], dict, "重放结果应为 PulseNode 而非 dict（类型统一）")
        self.assertEqual(out[0].value, "new")

    def test_24_corrupt_line_skipped(self):
        """增量日志损坏：坏行跳过并告警，好行仍正常重放（不得影响启动）。"""
        s = self._snap(self._d())
        node = _FakeNode("a", value="old")
        good = {"node_id": "a", "action": "upsert", "ts": time.time(),
                "data": {"node_id": "a", "value": "good"}}
        p = s._m67_incremental_log_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("这不是合法JSON{{{\n")
            f.write(json.dumps(good, ensure_ascii=False) + "\n")
        with _CfgSwitch(SNAPSHOT_USE_INCREMENTAL_LOG=True):
            out = s._m67_apply_incremental_log([node])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].value, "good")
        self.assertTrue(any("损坏" in x for x in s._logs), "应有损坏告警留痕")

    def test_25_delete_action(self):
        """重放 delete：节点被移除。"""
        s = self._snap(self._d())
        rec = {"node_id": "a", "action": "delete", "ts": time.time()}
        p = s._m67_incremental_log_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with _CfgSwitch(SNAPSHOT_USE_INCREMENTAL_LOG=True):
            out = s._m67_apply_incremental_log([_FakeNode("a"), _FakeNode("b")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].node_id, "b")

    def test_26_switch_off_disables_log(self):
        """开关关闭 → 增量日志不启用（回退既有 _incremental_save）。"""
        s = self._snap(self._d())
        with _CfgSwitch(SNAPSHOT_USE_INCREMENTAL_LOG=False):
            self.assertFalse(s._m67_incremental_log_enabled())


# ==================== T3 KAL ====================

class TestKAL(unittest.TestCase):
    def setUp(self):
        self._pool = _FakePool()

    def test_31_interface_complete(self):
        """KAL 接口完整：任务书列举的方法全部存在。"""
        need = ["get_node", "get_nodes_batch", "get_all_nodes", "iter_nodes",
                "search_by_keywords", "search_by_path", "search_by_evol_level",
                "search_by_time_range", "search_by_source_organ", "semantic_search",
                "get_linked_nodes", "find_path", "get_activation_history",
                "get_node_count", "get_node_count_by_evol_level", "get_storage_size",
                "save_node", "delete_node", "trigger_full_save",
                "trigger_incremental_save", "get_storage_backend", "get_cache_stats"]
        for m in need:
            self.assertTrue(hasattr(_kal.KnowledgeAccessLayer, m), "缺少接口: %s" % m)

    def test_32_singleton(self):
        """KAL 单例：多次 get_kal() 返回同一实例。"""
        a = _kal.get_kal(refresh=True)
        b = _kal.get_kal()
        self.assertIs(a, b)

    def test_33_get_node_and_cache(self):
        """get_node 命中并写入 LRU 缓存；二次访问计为 hit。"""
        k = _kal.KnowledgeAccessLayer(node_pool=self._pool)
        n = k.get_node("n1")
        self.assertIsNotNone(n)
        self.assertEqual(n.node_id, "n1")
        k.get_node("n1")
        st = k.get_cache_stats()
        self.assertGreaterEqual(st["hits"], 1)
        self.assertGreater(st["hit_rate"], 0.0)

    def test_34_search_by_keywords(self):
        """关键词检索：按 value/keywords 匹配。"""
        k = _kal.KnowledgeAccessLayer(node_pool=self._pool)
        out = k.search_by_keywords(["深度学习"], top_k=10)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].node_id, "n2")

    def test_35_semantic_search_cosine(self):
        """语义检索：暴力余弦，按相似度降序。"""
        pool = _FakePool()
        pool.nodes[0].vector = [1.0, 0.0]
        pool.nodes[1].vector = [0.0, 1.0]
        k = _kal.KnowledgeAccessLayer(node_pool=pool)
        out = k.semantic_search([1.0, 0.0], top_k=5)
        self.assertEqual(len(out), 2)
        self.assertAlmostEqual(out[0][1], 1.0, places=5)
        self.assertEqual(out[0][0].node_id, "n1")

    def test_36_linked_nodes_and_path(self):
        """图遍历：get_linked_nodes 与 find_path（BFS）。"""
        k = _kal.KnowledgeAccessLayer(node_pool=self._pool)
        linked = k.get_linked_nodes("n1", max_depth=1)
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0][0].node_id, "n2")
        path = k.find_path("n1", "n3", max_depth=3)
        self.assertEqual(path, ["n1", "n2", "n3"])

    def test_37_backend_and_count(self):
        """存储后端与节点计数。"""
        k = _kal.KnowledgeAccessLayer(node_pool=self._pool)
        self.assertEqual(k.get_storage_backend(), "json")
        self.assertEqual(k.get_node_count(), 3)
        self.assertIn("L1", k.get_node_count_by_evol_level())

    def test_38_critical_save_without_pool(self):
        """无节点池时写入安全返回 False，不抛异常。"""
        k = _kal.KnowledgeAccessLayer(node_pool=None)
        self.assertFalse(k.save_node(_FakeNode("x")))
        self.assertFalse(k.delete_node("x"))


class _FakePool:
    """轻量假节点池：三个节点构成 n1 -> n2 -> n3 链。"""

    def __init__(self):
        self.nodes = [
            _FakeNode("n1", value="人工智能", keywords=["AI"]),
            _FakeNode("n2", value="深度学习", keywords=["DL"], linked=["n3"]),
            _FakeNode("n3", value="神经网络", keywords=["NN"]),
        ]
        self.nodes[0].linked_nodes = ["n2"]

    def get_all_including_evicted(self):
        return list(self.nodes)


# ==================== T4 自适应降频 ====================

class TestAdaptiveFrequency(unittest.TestCase):
    def test_41_load_levels(self):
        """负载评估：四个等级边界正确。"""
        self.assertEqual(_rm.assess_load_level(queue_depth=99), "LOW")
        self.assertEqual(_rm.assess_load_level(queue_depth=100), "MEDIUM")
        self.assertEqual(_rm.assess_load_level(queue_depth=199), "MEDIUM")
        self.assertEqual(_rm.assess_load_level(queue_depth=200), "HIGH")
        self.assertEqual(_rm.assess_load_level(queue_depth=300), "CRITICAL")
        self.assertEqual(_rm.assess_load_level(queue_depth=4000), "CRITICAL")

    def test_42_cpu_and_snapshot(self):
        """CPU 与快照保存中也会抬升等级。"""
        self.assertEqual(_rm.assess_load_level(cpu_percent=90), "CRITICAL")
        self.assertEqual(_rm.assess_load_level(cpu_percent=75), "HIGH")
        self.assertEqual(_rm.assess_load_level(cpu_percent=60), "MEDIUM")
        self.assertEqual(_rm.assess_load_level(snapshot_saving=True), "MEDIUM")

    def test_43_interval_scales_with_level(self):
        """非关键操作间隔随负载等级放大；CRITICAL 暂停（inf）。"""
        c = _rm.AdaptiveFrequencyController()
        c.register("stomach_digest", 30.0)
        c.set_level("LOW")
        self.assertAlmostEqual(c.get_interval("stomach_digest"), 30.0)
        c.set_level("HIGH")
        self.assertAlmostEqual(c.get_interval("stomach_digest"), 60.0)
        c.set_level("CRITICAL")
        self.assertEqual(c.get_interval("stomach_digest"), float("inf"))

    def test_44_critical_ops_never_throttled(self):
        """关键操作（对话/心跳/快照）白名单：任何等级都不降频。"""
        c = _rm.AdaptiveFrequencyController()
        c.set_level("CRITICAL")
        for name in ("chat", "心跳", "snapshot_save"):
            self.assertTrue(c.is_critical_op(name))
            self.assertEqual(c.get_interval(name), 0.0)
            self.assertTrue(c.should_execute(name))

    def test_45_should_execute_throttle(self):
        """同一操作在间隔内被限流；超过间隔后可再次执行。"""
        c = _rm.AdaptiveFrequencyController()
        c.register("code_learning", 60.0)
        c.set_level("LOW")
        t0 = 1000.0
        self.assertTrue(c.should_execute("code_learning", now=t0))
        self.assertFalse(c.should_execute("code_learning", now=t0 + 10))
        self.assertTrue(c.should_execute("code_learning", now=t0 + 61))

    def test_46_switch_off_always_true(self):
        """开关关闭 → 非关键操作恒可执行（零回归）。"""
        c = _rm.AdaptiveFrequencyController()
        c.set_level("CRITICAL")
        with _CfgSwitch(ENABLE_ADAPTIVE_FREQUENCY=False):
            self.assertTrue(c.should_execute("stomach_digest"))
        with _CfgSwitch(ENABLE_ADAPTIVE_FREQUENCY=True):
            c2 = _rm.AdaptiveFrequencyController()
            c2.set_level("CRITICAL")
            self.assertFalse(c2.should_execute("stomach_digest"))


# ==================== T5 WriteGuard 环境判定 ====================

class TestWriteGuardEnv(unittest.TestCase):
    def setUp(self):
        self._saved_force = getattr(config, "WRITE_GUARD_FORCE_ENV", None)
        self._saved_pf = os.environ.get("PULSE_FRAMEWORK")

    def tearDown(self):
        config.WRITE_GUARD_FORCE_ENV = self._saved_force
        if self._saved_pf is None:
            os.environ.pop("PULSE_FRAMEWORK", None)
        else:
            os.environ["PULSE_FRAMEWORK"] = self._saved_pf

    def test_51_framework_not_misjudged(self):
        """★核心修复：框架主进程即使 pytest 被 import，也不得判为测试环境。"""
        os.environ["PULSE_FRAMEWORK"] = "1"
        config.WRITE_GUARD_FORCE_ENV = None
        self.assertTrue(_wg.is_framework_process())
        self.assertEqual(_wg.resolve_env(), "production")
        self.assertFalse(_wg.is_test_like_env(), "框架进程被误判为测试环境")

    def test_52_force_env_override(self):
        """强制环境配置：test/production 均可覆盖自动判定。"""
        os.environ["PULSE_FRAMEWORK"] = "1"
        # ★第81批补2：进入时保存原值、finally 还原原值（原实现线性改到 "production"
        #   后不再还原，会把 WRITE_GUARD_FORCE_ENV 污染给同进程后续用例）。
        _orig_force_env = config.WRITE_GUARD_FORCE_ENV
        try:
            config.WRITE_GUARD_FORCE_ENV = "test"
            self.assertEqual(_wg.resolve_env(), "test")
            self.assertTrue(_wg.is_test_like_env())
            config.WRITE_GUARD_FORCE_ENV = "production"
            self.assertEqual(_wg.resolve_env(), "production")
            self.assertFalse(_wg.is_test_like_env())
        finally:
            config.WRITE_GUARD_FORCE_ENV = _orig_force_env

    def test_53_env_reason_not_empty(self):
        """判定依据有输出（便于排查误判）。"""
        os.environ["PULSE_FRAMEWORK"] = "1"
        config.WRITE_GUARD_FORCE_ENV = None
        self.assertTrue(_wg.env_reason())
        self.assertIn("框架", _wg.env_reason())


if __name__ == "__main__":
    unittest.main()
