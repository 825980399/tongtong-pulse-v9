# -*- coding: utf-8 -*-
"""第405批 刀1 判据：分析器超时分级 / 分片缓存 / 异步化 / 运行账本。

判据设计原则（对齐批次纪律）：
  · 钉**语义**不钉措辞：不锁具体毫秒数、不锁缓存文件的精确字节数。
  · 钉**可分辨性**：`stale_seconds` 的 None / 0 / 正数三种状态必须能分开。
  · 钉**等价性**：分片路径与全量路径的输出必须逐项一致（这是本刀最硬的判据）。
  · 不依赖生产 data/：全部落在 tmp 隔离目录。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

from nucleus._silent_except import silent_exc
from nucleus.self_awareness import AnalyzerCache as _acmod
from nucleus.self_awareness.AnalyzerCache import (
    AnalyzerCache,
    file_sig,
    log_analyzer_run,
    prune_analyzer_runs,
    reset_analyzer_cache,
)


def _purge_dir(path: str) -> None:
    """清空并删除临时目录（测试清理专用）。

    ★为什么单独抽出来：项目铁律要求**每个 except 都要 silent_exc**，
    `tests/` 同样受管。原先每处清理都写 `except OSError: pass`，一个测试
    文件就积了 14 处静默 except，被 cw2 门禁按"新增静默 handler"阻断。
    收敛成一个函数后，清理失败仍不牵连其余删除，但每次失败都留痕。
    """
    try:
        if not os.path.isdir(path):
            return
        for _n in os.listdir(path):
            try:
                os.remove(os.path.join(path, _n))
            except OSError as _e:
                silent_exc(_e, where="test_m405_k1::_purge_dir/rm", level="debug")
        os.rmdir(path)
    except OSError as _e:
        silent_exc(_e, where="test_m405_k1::_purge_dir", level="debug")


class TestTimeoutTiers(unittest.TestCase):
    """刀1 ①：超时分级。"""

    def test_01_tiers_present(self):
        """三个档位必须都能取到，且量级正确（120/60/30）。"""
        from nucleus.self_awareness.SelfAwarenessEngine import (
            _analyzer_timeout_sec,
        )
        self.assertGreaterEqual(_analyzer_timeout_sec("call_graph"), 120.0)
        self.assertGreaterEqual(_analyzer_timeout_sec("knowledge_quality"), 60.0)
        # 其余分析器：不得比历史默认更紧（30s），否则是隐性收紧
        for _nm in ("fake_loops", "production_consumption", "log_analyzer",
                    "code_review", "evolution_health", "event_tap",
                    "global_awareness", "structure_change"):
            self.assertGreaterEqual(
                _analyzer_timeout_sec(_nm), 30.0,
                "分析器 %s 的超时被收紧到 30s 以下" % _nm)

    def test_02_tier_wins_over_ctor_fallback(self):
        """内置分级必须赢过构造参数（构造参数是全局默认，分级是特调）。"""
        from nucleus.self_awareness.SelfAwarenessEngine import (
            _analyzer_timeout_sec,
        )
        self.assertGreaterEqual(
            _analyzer_timeout_sec("call_graph", fallback=1.0), 120.0,
            "构造参数把 call_graph 的分级覆盖掉了")
        # 未列入分级的分析器，fallback 应当生效
        self.assertEqual(_analyzer_timeout_sec("some_other", fallback=7.5), 7.5)

    def test_03_config_override_wins(self):
        """config.ANALYZER_TIMEOUT_OVERRIDES 优先级最高（运维热调）。"""
        import config
        from nucleus.self_awareness.SelfAwarenessEngine import (
            _analyzer_timeout_sec,
        )
        _old = getattr(config, "ANALYZER_TIMEOUT_OVERRIDES", None)
        try:
            config.ANALYZER_TIMEOUT_OVERRIDES = {"call_graph": 5.0}
            self.assertEqual(_analyzer_timeout_sec("call_graph"), 5.0)
            # 非法值（<=0）必须被忽略，不得让分析器立刻超时
            config.ANALYZER_TIMEOUT_OVERRIDES = {"call_graph": 0}
            self.assertGreaterEqual(_analyzer_timeout_sec("call_graph"), 120.0)
        finally:
            if _old is None:
                delattr(config, "ANALYZER_TIMEOUT_OVERRIDES")
            else:
                config.ANALYZER_TIMEOUT_OVERRIDES = _old

    def test_04_config_defaults_exist(self):
        """任务书要求的开关必须真实存在于 config（不是只写在注释里）。"""
        import config
        for _k, _typ in (("ANALYZER_TIMEOUT_OVERRIDES", dict),
                         ("ANALYZER_SHARD_SIZE", int),
                         ("ANALYZER_SHARD_PARALLEL", int),
                         ("ANALYZER_CACHE_ENABLED", bool),
                         ("ANALYZER_CACHE_TTL_SEC", int),
                         ("ANALYZER_RUNS_KEEP_DAYS", int),
                         ("ANALYZER_POOL_WORKERS", int)):
            self.assertTrue(hasattr(config, _k), "config 缺 %s" % _k)
            self.assertIsInstance(getattr(config, _k), _typ,
                                  "config.%s 类型不对" % _k)
        self.assertLessEqual(config.ANALYZER_SHARD_SIZE, 120,
                             "分片大小超过任务书 405 刀1 的 ≤120 要求")


class TestFileSigAndCache(unittest.TestCase):
    """刀1 ②：文件签名与缓存语义。"""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="m405_k1_")
        self._fp = os.path.join(self._dir, "a.py")
        with open(self._fp, "w", encoding="utf-8") as f:
            f.write("x = 1\n")

    def tearDown(self):
        _purge_dir(self._dir)

    def test_10_file_sig_shape(self):
        """签名必须是 [mtime_ns, size] 二元组，且内容变了签名就变。

        ★不锁精确字节数：Windows 下 `open(w)` 会把 `\\n` 展开成 `\\r\\n`，
        锁 `len("x = 1\\n")` 等于锁平台换行策略，是典型的"钉措辞"。
        这里只钉语义：size 必须等于真实文件长度、且内容变长后必须跟着变。
        """
        _s1 = file_sig(self._fp)
        self.assertIsInstance(_s1, list)
        self.assertEqual(len(_s1), 2)
        self.assertEqual(_s1[1], os.path.getsize(self._fp),
                         "size 与真实文件长度不符")
        time.sleep(0.01)
        with open(self._fp, "w", encoding="utf-8") as f:
            f.write("x = 22\n")           # 长度变了，size 必须跟着变
        _s2 = file_sig(self._fp)
        self.assertGreater(_s2[1], _s1[1], "size 没跟上文件变化")
        # 不存在的文件 → None（表示"永不缓存也不复用"）
        self.assertIsNone(file_sig(os.path.join(self._dir, "nope.py")))

    def test_11_cache_hit_and_sig_mismatch(self):
        """同签名命中；签名变了必须 miss（不能拿旧快照冒充新结果）。"""
        _c = AnalyzerCache(path=os.path.join(self._dir, "cache.json"))
        _sig = [111, 222]
        self.assertIsNone(_c.get("k", _sig), "空缓存不该命中")
        self.assertTrue(_c.put("k", _sig, {"v": 1}))
        self.assertEqual(_c.get("k", _sig), {"v": 1})
        self.assertIsNone(_c.get("k", [111, 333]), "签名不一致仍命中")
        self.assertIsNone(_c.get("k", None), "sig=None 时必须 miss")
        _st = _c.stats()
        self.assertEqual(_st["hit"], 1)
        self.assertGreaterEqual(_st["miss"], 2)

    def test_12_cache_ttl_expiry(self):
        """TTL 过期必须 miss。用极小 TTL 验证，不靠 sleep 大数。"""
        _c = AnalyzerCache(path=os.path.join(self._dir, "c2.json"), ttl_sec=0.05)
        _sig = [1, 2]
        _c.put("k", _sig, "v")
        self.assertEqual(_c.get("k", _sig), "v")
        time.sleep(0.08)
        self.assertIsNone(_c.get("k", _sig), "TTL 过期后仍命中")

    def test_13_cache_roundtrip_and_version(self):
        """落盘/加载；版本不符必须整体作废（防旧结构被当新数据读）。"""
        _p = os.path.join(self._dir, "c3.json")
        _c = AnalyzerCache(path=_p)
        _c.put("a", [1, 2], {"x": [1, 2, 3]})
        _c.put("b", [3, 4], "s")
        self.assertTrue(_c.save())
        _c2 = AnalyzerCache(path=_p)
        self.assertEqual(_c2.load(), 2)
        self.assertEqual(_c2.get("a", [1, 2]), {"x": [1, 2, 3]})
        # 破坏版本号 → 加载 0 条
        with open(_p, encoding="utf-8") as f:
            _raw = json.load(f)
        _raw["version"] = 999
        with open(_p, "w", encoding="utf-8") as f:
            json.dump(_raw, f)
        _c3 = AnalyzerCache(path=_p)
        self.assertEqual(_c3.load(), 0, "版本不符仍加载了条目")

    def test_14_cache_corrupt_file_is_survivable(self):
        """缓存文件损坏时按"全未命中"处理，绝不抛（红线）。"""
        _p = os.path.join(self._dir, "c4.json")
        with open(_p, "w", encoding="utf-8") as f:
            f.write("{ not json at all")
        _c = AnalyzerCache(path=_p)
        self.assertEqual(_c.load(), 0)
        self.assertIsNone(_c.get("k", [1, 1]))

    def test_15_prune_drops_expired(self):
        """prune 丢弃过期条目并返回条数。"""
        _c = AnalyzerCache(path=os.path.join(self._dir, "c5.json"), ttl_sec=0.05)
        _c.put("a", [1, 1], "v")
        time.sleep(0.08)
        self.assertEqual(_c.prune(), 1)
        self.assertIsNone(_c.get("a", [1, 1]))

    def test_16_singleton_reset(self):
        """单例可替换（测试隔离的前提）。"""
        _mine = AnalyzerCache(path=os.path.join(self._dir, "c6.json"))
        reset_analyzer_cache(_mine)
        self.assertIs(_acmod.get_analyzer_cache(), _mine)
        reset_analyzer_cache(None)


class TestShardedGraphEquivalence(unittest.TestCase):
    """刀1 ② 核心判据：分片路径与全量路径**输出等值**。

    这是本刀最硬的一条：分片是为了加速，绝不能改变分析结果。只跑小范围
    扫描目录（`nucleus/self_awareness`）以保证单测时长可控。
    """

    _SCAN = ["nucleus/self_awareness"]

    @classmethod
    def setUpClass(cls):
        from nucleus.self_awareness.CallGraphAnalyzer import CallGraphAnalyzer
        cls._CGA = CallGraphAnalyzer
        cls._base = cls._CGA(scan_dirs=list(cls._SCAN))
        cls._g_base = cls._base.analyze()

    @staticmethod
    def _fingerprint(g):
        _nodes = sorted(n["id"] for n in (g.get("nodes") or []))
        _edges = sorted("%s->%s#%d:%s" % (e["caller"], e["callee"], e["count"],
                                          e.get("kind"))
                        for e in (g.get("edges") or []))
        _st = g.get("stats") or {}
        _pick = {k: _st.get(k) for k in (
            "total_files", "parsed_files", "total_functions", "total_calls",
            "cross_file_calls", "dynamic_calls_unresolved", "dynamic_calls",
            "unresolved_calls", "external_calls", "parse_errors")}
        return _nodes, _edges, _pick

    def test_20_sharded_equals_full(self):
        """无缓存分片：节点/边/11 项统计必须逐项一致。"""
        _sh = self._CGA(scan_dirs=list(self._SCAN))
        _g = _sh.analyze_sharded(shard_size=3, parallel=2, cache=None)
        self.assertGreaterEqual(_g["stats"]["shards"], 2,
                                "文件数不足以切出多片，本判据失去意义")
        self.assertEqual(self._fingerprint(_g), self._fingerprint(self._g_base))

    def test_21_serial_sharded_equals_parallel(self):
        """并行度 1 与 2 的结果必须一致（并行不得引入竞态）。"""
        _a = self._CGA(scan_dirs=list(self._SCAN))
        _ga = _a.analyze_sharded(shard_size=3, parallel=1)
        _b = self._CGA(scan_dirs=list(self._SCAN))
        _gb = _b.analyze_sharded(shard_size=3, parallel=2)
        self.assertEqual(self._fingerprint(_ga), self._fingerprint(_gb))

    def test_22_cache_hit_path_equals_full(self):
        """全命中缓存路径的输出必须与全量一致（缓存不得篡改结果）。"""
        _dir = tempfile.mkdtemp(prefix="m405_k1_cg_")
        _p = os.path.join(_dir, "cache.json")
        try:
            _c1 = AnalyzerCache(path=_p)
            _a = self._CGA(scan_dirs=list(self._SCAN))
            _g1 = _a.analyze_sharded(shard_size=3, parallel=1, cache=_c1)
            self.assertTrue(_c1.save())
            # 新缓存对象 = 模拟重启后从磁盘加载
            _c2 = AnalyzerCache(path=_p)
            self.assertGreater(_c2.load(), 0)
            _b = self._CGA(scan_dirs=list(self._SCAN))
            _g2 = _b.analyze_sharded(shard_size=3, parallel=1, cache=_c2)
            self.assertEqual(_g2["stats"]["cache_hit_files"],
                             _g2["stats"]["total_files"],
                             "第二轮没有全命中缓存")
            self.assertEqual(self._fingerprint(_g1), self._fingerprint(_g2))
            self.assertEqual(self._fingerprint(_g2),
                             self._fingerprint(self._g_base))
        finally:
            _purge_dir(_dir)

    def test_23_sharded_exposes_observability_fields(self):
        """stats 必须多出 shards / cache_hit_files（只增不改）。"""
        _a = self._CGA(scan_dirs=list(self._SCAN))
        _g = _a.analyze_sharded(shard_size=3, parallel=1)
        self.assertIn("shards", _g["stats"])
        self.assertIn("cache_hit_files", _g["stats"])
        # 原有键一个不能少
        for _k in ("total_files", "parsed_files", "total_functions",
                   "total_calls", "cross_file_calls", "dynamic_calls_unresolved",
                   "unresolved_calls", "external_calls", "parse_errors"):
            self.assertIn(_k, _g["stats"], "原有统计键 %s 丢了" % _k)

    def test_24_snapshot_is_json_serializable(self):
        """快照必须纯 JSON —— 否则缓存落盘会静默失效。"""
        _a = self._CGA(scan_dirs=list(self._SCAN))
        _snap = _a._collect_snapshot("nucleus/self_awareness/AnalyzerCache.py")
        self.assertIsInstance(_snap, dict)
        json.dumps(_snap)          # 抛异常即失败
        for _k in ("rel_file", "module", "defs", "bindings", "raw_calls"):
            self.assertIn(_k, _snap)


class TestTreeSigAndKQACache(unittest.TestCase):
    """刀1 ② 补：知识质量的数据源级缓存（其输入是 parquet，不是 .py）。"""

    def test_25_tree_sig_shape_and_sensitivity(self):
        """tree_sig 必须是 [md5, 文件数]，且内容变了签名就变。"""
        from nucleus.self_awareness.AnalyzerCache import tree_sig
        _dir = tempfile.mkdtemp(prefix="m405_k1_tree_")
        try:
            _f1 = os.path.join(_dir, "a.parquet")
            with open(_f1, "wb") as f:
                f.write(b"AAA")
            _s1 = tree_sig([_dir])
            self.assertIsInstance(_s1, list)
            self.assertEqual(len(_s1), 2)
            self.assertEqual(_s1[1], 1)
            # 内容变了 → 签名必须变（否则会拿旧报告冒充新结果）
            time.sleep(0.01)
            with open(_f1, "wb") as f:
                f.write(b"BBBBB")
            _s2 = tree_sig([_dir])
            self.assertNotEqual(_s1[0], _s2[0], "数据源变了但签名没变")
            # 空路径集 → None（不缓存，也不复用）
            self.assertIsNone(tree_sig([os.path.join(_dir, "nothing")]))
            self.assertIsNone(tree_sig([]))
        finally:
            _purge_dir(_dir)

    def test_26_kqa_second_call_reuses_cached_report(self):
        """同一份数据源连跑两次：第二次必须命中缓存且结果相同。

        ★用真实生产数据源（只读），不断言耗时（会随机器抖动），
        只断言「报告内容一致」+「缓存条目真的被读到了」。
        """
        import config
        from nucleus.self_awareness.SelfAwarenessEngine import SelfAwarenessEngine
        from nucleus.self_awareness.AnalyzerCache import (
            AnalyzerCache, reset_analyzer_cache, tree_sig,
        )
        _dir = tempfile.mkdtemp(prefix="m405_k1_kqa_")
        _old_cache = getattr(config, "ANALYZER_CACHE_ENABLED", True)
        try:
            config.ANALYZER_CACHE_ENABLED = True
            _c = AnalyzerCache(path=os.path.join(_dir, "c.json"))
            reset_analyzer_cache(_c)
            _e = SelfAwarenessEngine()
            _e._analyzers = []
            _r1 = _e.integrate_knowledge_quality()
            if not _r1:
                self.skipTest("生产数据源不可用，跳过（判据依赖真实 parquet/L1）")
            _r2 = _e.integrate_knowledge_quality()
            self.assertEqual(_r1.get("score"), _r2.get("score"),
                             "两次知识质量评分不一致")
            self.assertEqual(_r1.get("level"), _r2.get("level"))
            self.assertEqual(_r1.get("top_issues"), _r2.get("top_issues"))
            _st = _c.stats()
            self.assertGreaterEqual(_st["hit"], 1,
                                    "第二次没有命中数据源缓存（hit=%s）" % _st)
            # 数据源签名确实存在
            _sig = tree_sig([
                os.path.join(os.getcwd(), "data", "knowledge", "parquet"),
                os.path.join(os.getcwd(), "data", "knowledge",
                             "pulse_l1_snapshot.json")])
            self.assertIsNotNone(_sig, "生产数据源签名算不出来")
        finally:
            config.ANALYZER_CACHE_ENABLED = _old_cache
            reset_analyzer_cache(None)
            _purge_dir(_dir)


class TestAsyncAndPortrait(unittest.TestCase):
    """刀1 ③：异步化与 get_portrait 只读语义。"""

    def _engine(self):
        from nucleus.self_awareness.SelfAwarenessEngine import SelfAwarenessEngine
        _e = SelfAwarenessEngine()
        _e._analyzers = []          # 清掉 structure_change，避免触碰真实结构扫描
        return _e

    def test_30_portrait_before_any_run(self):
        """从未跑过 → has_success=False，stale_seconds 必须是 None（不是 0）。"""
        _e = self._engine()
        _p = _e.get_portrait()
        self.assertFalse(_p["has_success"])
        self.assertIsNone(_p["stale_seconds"],
                          "从未成功时 stale_seconds 必须是 None；0 会被误读为刚跑过")
        for _f in ("knowledge_health", "fake_loops", "call_graph_health"):
            self.assertIn(_f, _p)
            self.assertEqual(_p[_f], {})

    def test_31_portrait_never_triggers_analysis(self):
        """get_portrait 绝不能在请求线程里现算（用计数断言，不测耗时）。"""
        _e = self._engine()
        _calls = []
        _e.register_analyzer("probe", lambda _x: _calls.append(1) or {"n": 1},
                             "extra")
        _e.get_portrait()
        _e.get_portrait()
        self.assertEqual(_calls, [], "get_portrait 触发了分析器执行")

    def test_32_portrait_after_success(self):
        """成功一轮后：has_success=True，stale_seconds 是有限正数，三维可读。"""
        _e = self._engine()
        _e.register_analyzer("knowledge_health", lambda _x: {"score": 80},
                             "knowledge_health")
        _e.register_analyzer("fake_loops", lambda _x: {"candidates": 2},
                             "fake_loops")
        _e.run_all_analyses()
        _p = _e.get_portrait()
        self.assertTrue(_p["has_success"])
        self.assertIsNotNone(_p["stale_seconds"])
        self.assertGreaterEqual(_p["stale_seconds"], 0.0)
        self.assertEqual(_p["knowledge_health"], {"score": 80})
        self.assertEqual(_p["fake_loops"], {"candidates": 2})
        self.assertFalse(_p["analysis_running"])

    def test_33_all_timeout_does_not_overwrite_last_success(self):
        """一轮全超时**不得**顶掉上一份成功画像（这正是原设计会丢结果的地方）。"""
        from nucleus.self_awareness.SelfAwarenessEngine import SelfAwarenessEngine
        _e = SelfAwarenessEngine()
        _e._analyzers = []
        _e.register_analyzer("keeper", lambda _x: {"k": 1}, "knowledge_health")
        _e.run_all_analyses()
        self.assertTrue(_e.get_portrait()["has_success"])
        _good = _e.get_portrait()["knowledge_health"]
        # 换成一个必然超时的分析器（超时阈值改小到 0.01s）
        _e._analyzers = []
        _e.register_analyzer("slow", lambda _x: (time.sleep(0.2) or {"s": 1}),
                             "knowledge_health")
        _e._timeout_sec = 0.01
        _e.run_all_analyses()
        _p = _e.get_portrait()
        self.assertTrue(_p["has_success"], "全超时把 has_success 打成 False")
        self.assertEqual(_p["knowledge_health"], _good,
                         "全超时的一轮顶掉了上一份成功画像")

    def test_34_submit_analysis_runs_off_caller_thread(self):
        """异步提交后，分析器**不在调用方线程**执行（任务书"不许在请求线程里现算"）。

        ★断言口径说明（这是实测分层，勿按字面想象）：
          · **一轮分析**跑在 `SelfAwarenessPool` worker 里（本刀新增的池）；
          · **单个分析器**仍由 `_run_with_timeout` 起一次性 daemon 线程执行，
            线程名 `SelfAwareness-<分析器名>`。
        后者是刻意的，不是漏改：超时语义靠「放弃等待一个一次性线程」实现，
        若把分析器塞进固定 worker 池，超时后那个 worker 会被占死（Python
        无法从外部中断线程），2 个 worker 很快耗尽、后续轮次全部排队。
        所以本判据钉的是「不在调用方线程」，而不是「线程名必须叫池名」。
        """
        import threading
        import config
        from nucleus.self_awareness.SelfAwarenessEngine import (
            SelfAwarenessEngine, reset_analyzer_pool,
        )
        _old = getattr(config, "ANALYZER_POOL_WORKERS", 2)
        try:
            config.ANALYZER_POOL_WORKERS = 1
            reset_analyzer_pool()          # 确保按新配置重建
            _e = SelfAwarenessEngine()
            _e._analyzers = []
            _seen = []
            _e.register_analyzer(
                "where", lambda _x: _seen.append(threading.current_thread().name)
                or {"ok": 1}, "extra")
            _caller = threading.current_thread().name
            _fut = _e.submit_analysis()
            self.assertIsNotNone(_fut, "线程池未启用，submit_analysis 返回 None")
            _fut.result(timeout=30)
            self.assertEqual(len(_seen), 1, "分析器没被执行")
            self.assertNotEqual(_seen[0], _caller,
                                "分析器跑在调用方线程里了（实际 = %s）" % _seen[0])
        finally:
            config.ANALYZER_POOL_WORKERS = _old
            reset_analyzer_pool()

    def test_34b_pool_thread_actually_runs_the_round(self):
        """一轮分析的**外层**必须跑在池 worker 里（用轮内注入的探针线程名断言）。"""
        import threading
        import config
        from nucleus.self_awareness.SelfAwarenessEngine import (
            SelfAwarenessEngine, reset_analyzer_pool,
        )
        _old = getattr(config, "ANALYZER_POOL_WORKERS", 2)
        try:
            config.ANALYZER_POOL_WORKERS = 1
            reset_analyzer_pool()
            _e = SelfAwarenessEngine()
            _e._analyzers = []
            _outer = []
            _orig = _e.run_all_analyses

            def _spy(scope="all"):
                _outer.append(threading.current_thread().name)
                return _orig(scope)

            _e.run_all_analyses = _spy
            _caller = threading.current_thread().name
            _fut = _e.submit_analysis()
            self.assertIsNotNone(_fut)
            _fut.result(timeout=30)
            self.assertEqual(len(_outer), 1)
            self.assertIn("SelfAwarenessPool", _outer[0],
                          "本轮分析没跑在池线程里（实际 = %s，调用方 = %s）"
                          % (_outer[0], _caller))
        finally:
            config.ANALYZER_POOL_WORKERS = _old
            reset_analyzer_pool()

    def test_35_pool_disabled_returns_none(self):
        """worker 配 0 → 不启用异步，submit_analysis 返回 None（可判定的降级）。"""
        import config
        from nucleus.self_awareness.SelfAwarenessEngine import (
            SelfAwarenessEngine, reset_analyzer_pool,
        )
        _old = getattr(config, "ANALYZER_POOL_WORKERS", 2)
        try:
            config.ANALYZER_POOL_WORKERS = 0
            reset_analyzer_pool()
            _e = SelfAwarenessEngine()
            _e._analyzers = []
            self.assertIsNone(_e.submit_analysis())
        finally:
            config.ANALYZER_POOL_WORKERS = _old
            reset_analyzer_pool()


class TestAnalyzerRunsLedger(unittest.TestCase):
    """刀1 ④：运行账本 analyzer_runs.jsonl。"""

    def test_40_run_row_written_with_required_fields(self):
        """任务书要求的 5 个字段一个不能少。"""
        import config
        _old = getattr(config, "SELF_AWARENESS_OUTPUT_DIR", "data/self_awareness")
        _dir = tempfile.mkdtemp(prefix="m405_k1_runs_")
        try:
            config.SELF_AWARENESS_OUTPUT_DIR = _dir
            _fp = os.path.join(_dir, "analyzer_runs.jsonl")
            if os.path.isfile(_fp):
                os.remove(_fp)
            self.assertTrue(log_analyzer_run(
                "call_graph", 12345.6, shards=4, cache_hit=True, ok=True))
            with open(_fp, encoding="utf-8") as f:
                _lines = [ln for ln in f if ln.strip()]
            self.assertEqual(len(_lines), 1)
            _row = json.loads(_lines[0])
            for _k in ("name", "elapsed_ms", "shards", "cache_hit", "ok"):
                self.assertIn(_k, _row, "账本缺字段 %s" % _k)
            self.assertEqual(_row["name"], "call_graph")
            self.assertEqual(_row["shards"], 4)
            self.assertTrue(_row["cache_hit"])
            self.assertTrue(_row["ok"])
            self.assertIn("date", _row)
        finally:
            config.SELF_AWARENESS_OUTPUT_DIR = _old
            _purge_dir(_dir)

    def test_41_prune_keeps_recent_drops_old(self):
        """保留期外的行被删、期内保留；坏行不牵连整个文件。"""
        import config
        _old = getattr(config, "SELF_AWARENESS_OUTPUT_DIR", "data/self_awareness")
        _dir = tempfile.mkdtemp(prefix="m405_k1_runsp_")
        try:
            config.SELF_AWARENESS_OUTPUT_DIR = _dir
            _fp = os.path.join(_dir, "analyzer_runs.jsonl")
            _today = time.strftime("%Y%m%d")
            with open(_fp, "w", encoding="utf-8") as f:
                f.write(json.dumps({"date": "20200101", "name": "old",
                                    "elapsed_ms": 1, "shards": 0,
                                    "cache_hit": False, "ok": True}) + "\n")
                f.write(json.dumps({"date": _today, "name": "new",
                                    "elapsed_ms": 1, "shards": 0,
                                    "cache_hit": False, "ok": True}) + "\n")
                f.write("this-is-not-json\n")
            _drop = prune_analyzer_runs(keep_days=30)
            self.assertEqual(_drop, 1, "过期行删除数不对")
            with open(_fp, encoding="utf-8") as f:
                _left = [ln.strip() for ln in f if ln.strip()]
            self.assertEqual(len(_left), 2, "坏行不该被牵连删除")
            self.assertIn('"new"', _left[0] + _left[1])
        finally:
            config.SELF_AWARENESS_OUTPUT_DIR = _old
            _purge_dir(_dir)

    def test_42_run_all_writes_one_row_per_analyzer(self):
        """跑一轮全量分析 → 每个分析器一行账本。"""
        import config
        from nucleus.self_awareness.SelfAwarenessEngine import SelfAwarenessEngine
        _old = getattr(config, "SELF_AWARENESS_OUTPUT_DIR", "data/self_awareness")
        _dir = tempfile.mkdtemp(prefix="m405_k1_runs2_")
        try:
            config.SELF_AWARENESS_OUTPUT_DIR = _dir
            _fp = os.path.join(_dir, "analyzer_runs.jsonl")
            _e = SelfAwarenessEngine()
            _e._analyzers = []
            _e.register_analyzer("a1", lambda _x: {"x": 1}, "knowledge_health")
            _e.register_analyzer("a2", lambda _x: {"y": 2}, "fake_loops")
            _e.run_all_analyses()
            _names = []
            with open(_fp, encoding="utf-8") as f:
                for _ln in f:
                    if _ln.strip():
                        _names.append(json.loads(_ln)["name"])
            self.assertEqual(sorted(_names), ["a1", "a2"])
        finally:
            config.SELF_AWARENESS_OUTPUT_DIR = _old
            _purge_dir(_dir)


if __name__ == "__main__":
    unittest.main(verbosity=2)
