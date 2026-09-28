# -*- coding: utf-8 -*-
"""
test_self_awareness_m18.py —— 主线第18批 门控单测（PHASE18 阶段一）

覆盖：
  一、SelfAwarenessEngine 核心（单例/注册/执行/超时/异常隔离/序列化/合并/报告/开关/线程安全）
  二、ProductionConsumptionMatcher（open/json/np/无消费/动态路径/报告持久化/目录排除）
  三、FakeLoopDetector（save-load 配对/五类问题/评分/抽象接口排除/报告持久化）
  四、集成（引擎注册两分析器 / main 初始化路径）

隔离：全部产物落在 tmp/_sa_m18_tests/，不触碰 data/。
"""
# ★第18批 T5 修正标记

import json
import os
import shutil
import sys
import threading
import time
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from nucleus.self_awareness import (  # noqa: E402
    FakeLoopDetector,
    ProductionConsumptionMatcher,
    SelfAwarenessEngine,
    SelfAwarenessProfile,
    get_self_awareness_engine,
    reset_self_awareness_engine,
)
from nucleus.self_awareness.FakeLoopDetector import analyze_fake_loops  # noqa: E402
from nucleus.self_awareness.ProductionConsumptionMatcher import (  # noqa: E402
    analyze_production_consumption,
)

_SCRATCH = os.path.join(_PROJECT_ROOT, "tmp", "_sa_m18_tests")


class _Switch:
    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        for _k, _v in self._kw.items():
            self._old[_k] = getattr(config, _k, None)
            setattr(config, _k, _v)
        return self

    def __exit__(self, *exc):
        for _k, _v in self._old.items():
            setattr(config, _k, _v)
        return False


def _scratch_cleanup():
    """若 scratch 目录已空则一并删除（保持 tmp/ 干净）。"""
    try:
        if os.path.isdir(_SCRATCH) and not os.listdir(_SCRATCH):
            os.rmdir(_SCRATCH)
    except OSError:
        pass


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _make_probe(name, files):
    """在 tmp 下造一个样本项目，返回其根路径。"""
    root = os.path.join(_SCRATCH, name)
    shutil.rmtree(root, ignore_errors=True)
    for rel, text in files.items():
        _write(os.path.join(root, rel.replace("/", os.sep)), text)
    return root


# ======================================================================
# 一、SelfAwarenessEngine
# ======================================================================
class TestEngineCore(unittest.TestCase):
    def setUp(self):
        reset_self_awareness_engine()
        os.makedirs(_SCRATCH, exist_ok=True)

    def tearDown(self):
        reset_self_awareness_engine()
        _scratch_cleanup()
        _scratch_cleanup()

    def test_config_defaults(self):
        self.assertIs(config.ENABLE_SELF_AWARENESS_ENGINE, True)
        self.assertEqual(config.SELF_AWARENESS_TIMEOUT_SEC, 30)
        self.assertIs(config.ENABLE_PRODUCTION_CONSUMPTION_MATCHER, True)
        self.assertIs(config.ENABLE_FAKE_LOOP_DETECTOR, True)
        self.assertEqual(config.SELF_AWARENESS_OUTPUT_DIR, "data/self_awareness")

    def test_singleton_and_reset(self):
        _a = get_self_awareness_engine()
        self.assertIs(_a, get_self_awareness_engine())
        reset_self_awareness_engine()
        self.assertIsNot(_a, get_self_awareness_engine())

    def test_register_and_list_analyzers(self):
        e = SelfAwarenessEngine()
        self.assertTrue(e.register_analyzer("fake_loops", lambda _e: {"a": 1}))
        self.assertFalse(e.register_analyzer("fake_loops", lambda _e: {}),
                         "重复注册应被忽略")
        self.assertTrue(e.register_analyzer("fake_loops", lambda _e: {}, replace=True))
        self.assertEqual(e.list_analyzers(), ["fake_loops"])
        self.assertTrue(e.unregister_analyzer("fake_loops"))
        self.assertEqual(e.list_analyzers(), [])

    def test_register_validates_args(self):
        e = SelfAwarenessEngine()
        with self.assertRaises(TypeError):
            e.register_analyzer("x", "not-callable")
        with self.assertRaises(ValueError):
            e.register_analyzer("", lambda _e: {})

    def test_run_all_analyses_aggregates_to_fields(self):
        e = SelfAwarenessEngine(timeout_sec=5)
        e.register_analyzer("production_consumption",
                            lambda _e: {"scanned_files": 3},
                            "production_consumption")
        e.register_analyzer("fake_loops", lambda _e: {"candidates": 2}, "fake_loops")
        p = e.run_all_analyses()
        self.assertEqual(p.production_consumption["scanned_files"], 3)
        self.assertEqual(p.fake_loops["candidates"], 2)
        self.assertTrue(p.timestamp)
        self.assertIn("产出消费", p.summary)
        self.assertEqual(e.get_stats()["run_count"], 1)

    def test_scope_filters_analyzers(self):
        e = SelfAwarenessEngine(timeout_sec=5)
        e.register_analyzer("fake_loops", lambda _e: {"only": 1}, "fake_loops")
        e.register_analyzer("production_consumption",
                            lambda _e: {"other": 1}, "production_consumption")
        p = e.run_all_analyses(scope="fake_loops")
        self.assertEqual(p.fake_loops, {"only": 1})
        self.assertEqual(p.production_consumption, {})

    def test_analyzer_timeout_is_skipped(self):
        e = SelfAwarenessEngine(timeout_sec=0.2)
        e.register_analyzer("slow", lambda _e: (time.sleep(2) or {"x": 1}),
                            "extra")
        _t0 = time.perf_counter()
        p = e.run_all_analyses()
        self.assertLess(time.perf_counter() - _t0, 1.5, "超时保护应快速返回")
        self.assertNotIn("slow", p.extra)
        self.assertEqual(e.get_stats()["last_run"]["slow"]["status"], "timeout")

    def test_analyzer_exception_isolated(self):
        e = SelfAwarenessEngine(timeout_sec=5)

        def _boom(_e):
            raise RuntimeError("boom")

        e.register_analyzer("boom", _boom, "extra")
        e.register_analyzer("ok", lambda _e: {"ok": 1}, "code_health")
        p = e.run_all_analyses()
        self.assertEqual(p.code_health, {"ok": 1})
        self.assertIn("error", e.get_stats()["last_run"]["boom"]["status"])

    def test_profile_serialization_roundtrip(self):
        p = SelfAwarenessProfile(
            timestamp="2026-09-11T12:00:00", code_health={"e": 1},
            fake_loops={"c": 2}, summary="X")
        _d = p.to_dict()
        p2 = SelfAwarenessProfile.from_dict(_d)
        self.assertEqual(p2.to_dict(), _d)
        # 容错：非字典 / 缺字段
        self.assertEqual(SelfAwarenessProfile.from_dict(None).to_dict()["extra"], {})
        self.assertEqual(SelfAwarenessProfile.from_dict({"unknown": 1}).summary, "")

    def test_profile_merge(self):
        a = SelfAwarenessProfile(timestamp="2026-09-11T10:00:00",
                                 code_health={"e": 1}, summary="A")
        b = SelfAwarenessProfile(timestamp="2026-09-11T11:00:00",
                                 code_health={"w": 2}, fake_loops={"c": 3},
                                 summary="B")
        a.merge(b)
        self.assertEqual(a.code_health, {"e": 1, "w": 2})
        self.assertEqual(a.fake_loops, {"c": 3})
        self.assertEqual(a.timestamp, "2026-09-11T11:00:00")
        self.assertIn("A", a.summary)
        self.assertIn("B", a.summary)
        self.assertIs(a.merge(None), a)

    def test_generate_report_and_persist_files(self):
        e = SelfAwarenessEngine(timeout_sec=5)
        e.register_analyzer("code_health", lambda _e: {"ruff_errors": 0},
                            "code_health")
        e.run_all_analyses()
        _out = os.path.join(_SCRATCH, "report", "sa_report.txt")
        _text = e.generate_report(_out)
        self.assertIn("自我认知画像报告", _text)
        self.assertIn("代码健康", _text)
        self.assertTrue(os.path.isfile(_out))
        # save/load profile
        _pj = os.path.join(_SCRATCH, "report", "profile.json")
        self.assertTrue(e.save_profile(_pj))
        _loaded = e.load_profile(_pj)
        self.assertIsNotNone(_loaded)
        self.assertEqual(_loaded.code_health["ruff_errors"], 0)
        self.assertIsNone(e.load_profile(os.path.join(_SCRATCH, "nope.json")))

    def test_switch_off_zero_overhead(self):
        e = SelfAwarenessEngine(timeout_sec=5)
        _calls = []
        e.register_analyzer("probe", lambda _e: _calls.append(1) or {}, "extra")
        with _Switch(ENABLE_SELF_AWARENESS_ENGINE=False):
            p = e.run_all_analyses()
        self.assertEqual(_calls, [], "开关关闭时不得执行任何分析器")
        self.assertEqual(p.extra, {})
        self.assertEqual(e.get_stats()["skipped_off"], 1)

    def test_thread_safety_profile_access(self):
        e = SelfAwarenessEngine(timeout_sec=5)
        e.register_analyzer("code_health", lambda _e: {"n": 1}, "code_health")
        _errs = []

        def _worker():
            try:
                for _ in range(30):
                    e.run_all_analyses()
                    e.get_profile()
                    e.get_stats()
            except Exception as _ex:
                _errs.append(_ex)

        _ts = [threading.Thread(target=_worker) for _ in range(4)]
        for _t in _ts:
            _t.start()
        for _t in _ts:
            _t.join()
        self.assertEqual(_errs, [])
        self.assertEqual(e.get_stats()["run_count"], 120)

    def test_integrate_interfaces_revert_on_switch_off(self):
        """★第19批 T3/T4 已填充两个整合接口；此处验证「关闭开关仍可回退为空 dict」。

        （原断言「无条件返回空 dict」在接口填充后不再成立，故改为回退语义。）
        """
        e = SelfAwarenessEngine()
        with _Switch(ENABLE_LOG_ANALYZER_INTEGRATION=False):
            self.assertEqual(e.integrate_log_analyzer(), {})
        with _Switch(ENABLE_CODE_REVIEW_INTEGRATION=False):
            self.assertEqual(e.integrate_code_review(), {})


# ======================================================================
# 二、ProductionConsumptionMatcher
# ======================================================================
_SAMPLE_PROD = '''
import json
import numpy as np

def save_state(path='probe_data/state.json'):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({"a": 1}, f)

def write_static():
    with open("probe_data/static.json", "w") as f:
        f.write("{}")

def write_dynamic(name):
    with open(os.path.join("data", name + ".json"), "w") as fh:
        fh.write("{}")
'''
_SAMPLE_CONS = '''
import json
import numpy as np

def load_static():
    with open("probe_data/static.json", "r") as f:
        return json.load(f)

def read_orphan():
    with open("probe_data/orphan.json", "rb") as f:
        return f.read()

def save_npz():
    np.savez("probe_data/vec.npz", x=[1])
'''


class TestProductionConsumptionMatcher(unittest.TestCase):
    def setUp(self):
        self.root = _make_probe("pc", {
            "pkg/producer.py": _SAMPLE_PROD,
            "pkg/consumer.py": _SAMPLE_CONS,
            "tests/test_x.py": "open('data/should_not_scan.json', 'w')\n",
            "tmp/keep_out.py": "open('data/never.json', 'w')\n",
            # ★主线第39批 T2（P2-240）：白名单新增 `data/knowledge` 等**运行态目录**
            #   → 原探针目录 `data/knowledge/` 会被整体排除，使"无产出方"用例
            #   （orphan.json）落入 excluded 而非 no_producer。改为**不在白名单**的
            #   `probe_data/`，保持探针语义不变（# _m39_probe_dir）。
            # ★主线第37批 T3（P2-212）：`_finalize` 新增**存在性判定** ——
            #   「无消费 / 无产出 / 正常」三类只在**文件真实存在**时给出；
            #   解析后不存在者单列 `path_not_found`（误报收敛，原 11 条里 7 条
            #   即指向不存在的路径）。故探针工程必须**真实创建**这三个数据文件；
            #   原测试只写路径字面量（纯静态解析时代可行）。
            "probe_data/static.json": "{}",
            "probe_data/vec.npz": "",
            "probe_data/orphan.json": "{}",
        })
        self.m = ProductionConsumptionMatcher(project_root=self.root,
                                              scan_dirs=["pkg", "tests", "tmp"])

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        _scratch_cleanup()

    def test_identifies_open_write_as_producer(self):
        r = self.m.scan()
        e = r["files"]["probe_data/static.json"]
        self.assertEqual(len(e["producers"]), 1)
        self.assertEqual(e["producers"][0]["how"], "open(w)")
        self.assertTrue(
            e["producers"][0]["location"].startswith("pkg.producer"),
            e["producers"][0]["location"])
        self.assertEqual(e["producers"][0]["file"], "pkg/producer.py")

    def test_identifies_open_read_as_consumer(self):
        r = self.m.scan()
        e = r["files"]["probe_data/static.json"]
        self.assertEqual(len(e["consumers"]), 1)
        self.assertEqual(e["consumers"][0]["how"], "open(r)")

    def test_identifies_json_and_numpy(self):
        r = self.m.scan()
        self.assertEqual(r["files"]["probe_data/vec.npz"]["category"],
                         "no_consumer")
        self.assertEqual(r["files"]["probe_data/vec.npz"]["producers"][0]["how"],
                         "np.savez")

    def test_no_consumer_marked(self):
        r = self.m.scan()
        self.assertEqual(r["files"]["probe_data/vec.npz"]["category"],
                         "no_consumer")
        self.assertIn("无消费", r["files"]["probe_data/vec.npz"]["suggestion"])

    def test_no_producer_marked(self):
        r = self.m.scan()
        self.assertEqual(r["files"]["probe_data/orphan.json"]["category"],
                         "no_producer")

    def test_normal_when_both_sides_exist(self):
        r = self.m.scan()
        self.assertEqual(r["files"]["probe_data/static.json"]["category"],
                         "normal")

    def test_dynamic_path_recorded_separately(self):
        r = self.m.scan()
        _hows = [d["how"] for d in r["dynamic_calls"]]
        self.assertIn("open(w)", _hows)
        self.assertGreaterEqual(r["summary"]["dynamic_calls"], 1)

    def test_excludes_tests_and_tmp_dirs(self):
        r = self.m.scan()
        self.assertNotIn("data/should_not_scan.json", r["files"])
        self.assertNotIn("data/never.json", r["files"])

    def test_report_and_persist(self):
        r = self.m.scan()
        _txt = self.m.build_report(r)
        self.assertIn("产出-消费配对报告", _txt)
        self.assertIn("疑似无消费", _txt)
        _out = os.path.join(_SCRATCH, "pc_out")
        _paths = self.m.persist(r, out_dir=_out)
        self.assertTrue(os.path.isfile(_paths["json"]))
        self.assertTrue(os.path.isfile(_paths["report"]))
        with open(_paths["json"], encoding="utf-8") as f:
            self.assertIn("files", json.load(f))

    def test_analyzer_entry_respects_switch(self):
        with _Switch(ENABLE_PRODUCTION_CONSUMPTION_MATCHER=False,
                     PRODUCTION_CONSUMPTION_SCAN_DIRS=["pkg"]):
            self.assertEqual(analyze_production_consumption(), {})
        with _Switch(PRODUCTION_CONSUMPTION_SCAN_DIRS=[]):
            self.assertIsInstance(analyze_production_consumption(), dict)


# ======================================================================
# 三、FakeLoopDetector
# ======================================================================
_SAMPLE_LOOPS = '''
import json
from abc import abstractmethod


class NoopSave:
    def __init__(self):
        self.save_path = "a.json"

    def save(self):
        print("保存成功")

    def load(self):
        with open(self.save_path, "r") as f:
            return f.read()


class Mismatch:
    def __init__(self):
        self.save_path = "a.json"
        self.file_path = "b.json"

    def save(self):
        with open(self.save_path, "w") as f:
            f.write("x")

    def load(self):
        with open(self.file_path, "r") as f:
            return f.read()


class SilentExcept:
    def __init__(self):
        self.storage_path = "c.json"

    def save(self):
        try:
            with open(self.storage_path, "w") as f:
                f.write("x")
        except Exception:
            pass

    def load(self):
        try:
            with open(self.storage_path, "r") as f:
                return f.read()
        except Exception:
            return None


class NoopLoad:
    def __init__(self):
        self.load_path = "e.json"

    def save(self):
        with open(self.load_path, "w") as f:
            f.write("x")

    def load(self):
        print("加载成功")


class AbstractStore:
    @abstractmethod
    def save(self):
        raise NotImplementedError

    @abstractmethod
    def load(self):
        raise NotImplementedError


class Healthy:
    def __init__(self):
        self._storage_path = "d.json"

    def save(self, data):
        with open(self._storage_path, "w") as f:
            json.dump(data, f)

    def load(self):
        with open(self._storage_path, "r") as f:
            data = json.load(f)
        if not data:
            return {}
        return data
'''


class TestFakeLoopDetector(unittest.TestCase):
    def setUp(self):
        self.root = _make_probe("fl", {"pkg/loops.py": _SAMPLE_LOOPS})
        self.d = FakeLoopDetector(project_root=self.root, scan_dirs=["pkg"])
        self._r = self.d.scan()
        self._by = {c["class"]: c for c in self._r["candidates"]}

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        _scratch_cleanup()

    def test_detects_save_load_pair_classes(self):
        self.assertIn("NoopSave", self._by)
        self.assertIn("Mismatch", self._by)
        self.assertIn("SilentExcept", self._by)

    def test_save_noop_detected(self):
        _types = [p["type"] for p in self._by["NoopSave"]["problems"]]
        self.assertIn("save_noop", _types)
        # NoopSave.load 有真实 open(r)，故**不应**报 load_noop
        self.assertNotIn("load_noop", _types)
        self.assertEqual(self._by["NoopSave"]["score"], 30)

    def test_load_noop_detected(self):
        _p = [p for p in self._by["NoopLoad"]["problems"]
              if p["type"] == "load_noop"][0]
        self.assertEqual(_p["severity"], "severe")
        self.assertGreater(_p["line"], 0)
        self.assertEqual(self._by["NoopLoad"]["score"], 30)

    def test_path_mismatch_detected(self):
        _types = [p["type"] for p in self._by["Mismatch"]["problems"]]
        self.assertIn("path_mismatch", _types)
        _p = [p for p in self._by["Mismatch"]["problems"]
              if p["type"] == "path_mismatch"][0]
        self.assertIn("save_path", _p["detail"])
        self.assertIn("file_path", _p["detail"])

    def test_silent_except_detected(self):
        _types = [p["type"] for p in self._by["SilentExcept"]["problems"]]
        self.assertEqual(_types.count("silent_except"), 2)
        self.assertEqual(self._by["SilentExcept"]["score"], 60)

    def test_abstract_interface_excluded(self):
        self.assertNotIn("AbstractStore", self._by)

    def test_healthy_class_not_reported(self):
        self.assertNotIn("Healthy", self._by)

    def test_score_levels(self):
        from nucleus.self_awareness.FakeLoopDetector import score_of
        self.assertEqual(score_of([]), 100)
        self.assertEqual(score_of([{"severity": "minor"}]), 80)
        self.assertEqual(score_of([{"severity": "medium"}]), 60)
        self.assertEqual(score_of([{"severity": "severe"}]), 30)
        self.assertEqual(score_of([{"severity": "severe"},
                                   {"severity": "medium"}]), 0)

    def test_report_and_persist(self):
        _txt = self.d.build_report(self._r)
        self.assertIn("虚假闭环检测报告", _txt)
        self.assertIn("NoopSave", _txt)
        _out = os.path.join(_SCRATCH, "fl_out")
        _paths = self.d.persist(self._r, out_dir=_out)
        self.assertTrue(os.path.isfile(_paths["json"]))
        self.assertTrue(os.path.isfile(_paths["report"]))
        with open(_paths["json"], encoding="utf-8") as f:
            self.assertIn("candidates", json.load(f))

    def test_analyzer_entry_respects_switch(self):
        with _Switch(ENABLE_FAKE_LOOP_DETECTOR=False, FAKE_LOOP_SCAN_DIRS=["pkg"]):
            self.assertEqual(analyze_fake_loops(), {})
        with _Switch(FAKE_LOOP_SCAN_DIRS=[]):
            self.assertIsInstance(analyze_fake_loops(), dict)


# ======================================================================
# 四、集成
# ======================================================================
class TestIntegration(unittest.TestCase):
    def setUp(self):
        reset_self_awareness_engine()
        os.makedirs(_SCRATCH, exist_ok=True)

    def tearDown(self):
        reset_self_awareness_engine()

    def test_engine_with_two_analyzers_summarizes(self):
        """引擎注册两个真实分析器后 run_all_analyses 汇总正确。"""
        _root = _make_probe("integ", {"pkg/loops.py": _SAMPLE_LOOPS})
        try:
            with _Switch(PRODUCTION_CONSUMPTION_SCAN_DIRS=["pkg"],
                         FAKE_LOOP_SCAN_DIRS=["pkg"]):
                e = SelfAwarenessEngine(timeout_sec=30)
                e.register_analyzer("production_consumption",
                                    analyze_production_consumption,
                                    "production_consumption")
                e.register_analyzer("fake_loops", analyze_fake_loops, "fake_loops")
                # ★注意：`__init__.py` 导出的**类名与子模块同名**，
                #   故必须用 importlib 取模块对象（`import a.b.C as x` 会得到类）
                import importlib as _il
                _pc = _il.import_module(
                    "nucleus.self_awareness.ProductionConsumptionMatcher")
                _fl = _il.import_module("nucleus.self_awareness.FakeLoopDetector")
                _old_pc = _pc.ProductionConsumptionMatcher
                _old_fl = _fl.FakeLoopDetector

                class _PC(_old_pc):
                    def __init__(self, *a, **k):
                        super().__init__(project_root=_root, scan_dirs=["pkg"])

                class _FL(_old_fl):
                    def __init__(self, *a, **k):
                        super().__init__(project_root=_root, scan_dirs=["pkg"])

                _pc.ProductionConsumptionMatcher = _PC
                _fl.FakeLoopDetector = _FL
                try:
                    p = e.run_all_analyses()
                finally:
                    _pc.ProductionConsumptionMatcher = _old_pc
                    _fl.FakeLoopDetector = _old_fl
                self.assertEqual(
                    e.get_stats()["last_run"]["fake_loops"]["status"], "ok")
                self.assertEqual(
                    e.get_stats()["last_run"]["production_consumption"]["status"],
                    "ok")
                self.assertIn("虚假闭环", p.summary)
                self.assertTrue(p.fake_loops, "画像应含虚假闭环结果")
        finally:
            shutil.rmtree(_root, ignore_errors=True)
            _scratch_cleanup()

    def test_main_init_path_no_error(self):
        """模拟 main._init_self_awareness：不抛异常、注册分析器、不触发分析。

        ★主线第36批 T1：main.py 补齐 call_graph / knowledge_quality 两维注册，
        故期望值由 4 个改为 6 个（断言仍为 `==` 精确计数）。
        """
        from main import PulseFramework
        _f = PulseFramework.__new__(PulseFramework)
        _msgs = []
        _f._log = lambda lv, m: _msgs.append((str(lv), m))
        _f._init_self_awareness()
        _e = get_self_awareness_engine()
        # ★第36批 T1：4 个（原）+ call_graph / knowledge_quality（新增接线）
        # ★第38批 T2：+ evolution_health（P2-214 接线）→ 7 个。
        #   判据（skill §31.5）：该断言守的是**当前设计契约**（生产接线清单），
        #   故**同步期望值并保持 == 精确计数**，而非放宽为 assertGreaterEqual。
        self.assertEqual(sorted(_e.list_analyzers()),
                         ["call_graph", "code_review", "evolution_health",
                          "fake_loops", "knowledge_quality", "log_analyzer",
                          "production_consumption"])
        self.assertEqual(_e.get_stats()["run_count"], 0, "启动不得自动分析")
        self.assertTrue(any("SelfAwareness" in m for _, m in _msgs))


def teardown_module():
    # ★第22批 T3：pytest 只识别下划线命名模块级夹具，驼峰 tearDownModule
    #   不会被调用 → 临时目录清理长期失效并污染 tmp/ 与全库 F 口径。
    """★模块级收尾：整个测试模块跑完后删除 scratch 目录（保证 tmp/ 干净）。"""
    shutil.rmtree(_SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
