# -*- coding: utf-8 -*-
"""主线第36批（PHASE18 阶段一·第1批）：自我认知引擎核实与端到端验证测试。

覆盖：
  T1  集成状态（main.py 接线 + 分析器注册）/ 21 个公共方法 / Profile 字段 / 调度缺口表征
  T2  产出-消费配对器：scan 契约 / 分类 / 扫描范围 / 开关 / 端到端（临时迷你工程）
  T3  虚假闭环检测器：scan 契约 / score_of 档次 / 开关 / 私有下划线方法识别缺口表征
  T4  报告生成：段落齐全 / 落盘 / 五维总览
  T5  方法级单测：Profile merge/from_dict 边界、get_stats、异常隔离、超时

★说明：扫描类用例一律在**临时迷你工程**上运行（毫秒级），不扫描真实仓库。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import nucleus.self_awareness as _sa_pkg  # noqa: E402
from nucleus.self_awareness.FakeLoopDetector import (  # noqa: E402
    FakeLoopDetector,
    analyze_fake_loops,
    score_of,
)
from nucleus.self_awareness.ProductionConsumptionMatcher import (  # noqa: E402
    ProductionConsumptionMatcher,
    analyze_production_consumption,
)
from nucleus.self_awareness.SelfAwarenessEngine import (  # noqa: E402
    SelfAwarenessEngine,
    SelfAwarenessProfile,
    get_self_awareness_engine,
    reset_self_awareness_engine,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Switch:
    def __init__(self, **kw):
        self._kw = kw

    def __enter__(self):
        self._old = {k: getattr(config, k) for k in self._kw}
        for k, v in self._kw.items():
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._old.items():
            setattr(config, k, v)
        return False


# ----------------------------------------------------------------------
# 临时迷你工程（供扫描类用例使用）
_PROD = '''# -*- coding: utf-8 -*-
import json


def write_it():
    with open("data/a.json", "w", encoding="utf-8") as f:
        json.dump({"x": 1}, f)
'''

_CONS = '''# -*- coding: utf-8 -*-
import json


def read_it():
    with open("data/a.json", "r", encoding="utf-8") as f:
        return json.load(f)
'''

_ORPHAN = '''# -*- coding: utf-8 -*-
import json


def write_orphan():
    with open("data/b_unread.json", "w", encoding="utf-8") as f:
        json.dump({"y": 2}, f)
'''

_FAKE_LOOP = '''# -*- coding: utf-8 -*-


class FakeLoop:
    """save/load 都为空实现 → 应被判定为虚假闭环候选。"""

    def save(self):
        return None

    def load(self):
        return None
'''

_PRIVATE_LOAD = '''# -*- coding: utf-8 -*-


class PrivLoad:
    """加载方法是私有 `_load` → 现行前缀规则识别不到（缺口表征）。"""

    def save(self):
        return 1

    def _load(self):
        return 1
'''


def _mk_proj(extra=None):
    """构造临时迷你工程（源码置于 pkg/ 子目录），返回根目录。

    ★注意：`scan_dirs=["."]` 会被 `_EXCLUDE_DIR_PREFIXES=('.bak', '.')` 过滤掉
    （"." 以点开头）→ 必须用**具名子目录**作为扫描入口。
    """
    d = tempfile.mkdtemp(prefix="m36sa_")
    os.makedirs(os.path.join(d, "data"), exist_ok=True)
    os.makedirs(os.path.join(d, "pkg"), exist_ok=True)
    files = {"prod.py": _PROD, "cons.py": _CONS, "orphan.py": _ORPHAN}
    if extra:
        files.update(extra)
    for name, src in files.items():
        with open(os.path.join(d, "pkg", name), "w", encoding="utf-8") as f:
            f.write(src)
    # ★主线第37批 T3（P2-212）：`_finalize` 新增**存在性判定** ——
    #   「无消费/无产出/正常」只在文件**真实存在**时给出；不存在者单列
    #   `path_not_found`（误报收敛）。故探针工程必须真实创建数据文件。
    #   （原测试只写路径字面量，在"纯静态解析"时代可行。）
    # _m37_m36sync
    for _rel in ("data/a.json", "data/b_unread.json"):
        with open(os.path.join(d, _rel.replace("/", os.sep)), "w",
                  encoding="utf-8") as f:
            f.write("{}")
    return d


class TestEnginePublicAPI(unittest.TestCase):
    """T1：公共方法齐备 + 单例语义。"""

    def test_01_public_method_counts(self):
        _engine_pub = [m for m in dir(SelfAwarenessEngine) if not m.startswith("_")]
        _profile_pub = [m for m in dir(SelfAwarenessProfile) if not m.startswith("_")]
        # 任务书称 21 个公共方法 = 引擎 18 + 画像 3
        for m in ("register_analyzer", "unregister_analyzer", "list_analyzers",
                  "run_all_analyses", "run_analyzer", "get_profile", "set_profile",
                  "generate_report", "save_profile", "load_profile",
                  "compare_profiles", "get_stats",
                  "integrate_log_analyzer", "integrate_code_review",
                  "integrate_event_tap", "analyze_organ_activity",
                  "integrate_call_graph", "integrate_knowledge_quality"):
            self.assertIn(m, _engine_pub, m)
        for m in ("to_dict", "from_dict", "merge"):
            self.assertIn(m, _profile_pub, m)

    def test_02_singleton_semantics(self):
        reset_self_awareness_engine()
        a = get_self_awareness_engine()
        self.assertIs(a, get_self_awareness_engine())
        reset_self_awareness_engine()
        self.assertIsNot(a, get_self_awareness_engine())
        reset_self_awareness_engine()

    def test_03_package_exports(self):
        for n in ("SelfAwarenessEngine", "SelfAwarenessProfile",
                  "ProductionConsumptionMatcher", "FakeLoopDetector",
                  "get_self_awareness_engine", "reset_self_awareness_engine"):
            self.assertIn(n, getattr(_sa_pkg, "__all__", []) or dir(_sa_pkg), n)


class TestEngineScheduling(unittest.TestCase):
    """T1/T5：注册/调度/异常隔离/超时/开关。"""

    def setUp(self):
        reset_self_awareness_engine()
        self.e = SelfAwarenessEngine()

    def tearDown(self):
        reset_self_awareness_engine()

    def test_10_register_list_unregister(self):
        self.assertTrue(self.e.register_analyzer("x", lambda _e: {"a": 1}, "extra"))
        self.assertEqual(self.e.list_analyzers(), ["x"])
        self.assertFalse(self.e.register_analyzer("x", lambda _e: {"a": 2}, "extra"))
        self.assertTrue(self.e.register_analyzer("x", lambda _e: {"a": 3}, "extra",
                                                replace=True))
        self.assertEqual(self.e.list_analyzers(), ["x"])
        self.assertTrue(self.e.unregister_analyzer("x"))
        self.assertEqual(self.e.list_analyzers(), [])

    def test_11_run_all_analyses_aggregates(self):
        self.e.register_analyzer("pc", lambda _e: {"k": 1}, "production_consumption")
        self.e.register_analyzer("fl", lambda _e: {"c": 2}, "fake_loops")
        p = self.e.run_all_analyses()
        self.assertEqual(p.production_consumption.get("k"), 1)
        self.assertEqual(p.fake_loops.get("c"), 2)

    def test_12_scope_filters(self):
        self.e.register_analyzer("pc", lambda _e: {"k": 1}, "production_consumption")
        self.e.register_analyzer("fl", lambda _e: {"c": 2}, "fake_loops")
        p = self.e.run_all_analyses(scope="fake_loops")
        self.assertEqual(p.fake_loops.get("c"), 2)
        self.assertFalse(p.production_consumption)

    def test_13_analyzer_exception_isolated(self):
        def _boom(_e):
            raise RuntimeError("boom")

        self.e.register_analyzer("bad", _boom, "extra")
        self.e.register_analyzer("good", lambda _e: {"ok": 1}, "fake_loops")
        p = self.e.run_all_analyses()
        self.assertEqual(p.fake_loops.get("ok"), 1, "单个分析器异常不得影响其它分析器")
        st = self.e.get_stats()["last_run"]
        self.assertNotEqual(st["bad"]["status"], "ok")

    def test_14_timeout_marks_and_continues(self):
        import time as _t

        def _slow(_e):
            _t.sleep(0.5)
            return {"late": 1}

        e = SelfAwarenessEngine(timeout_sec=0.05)
        e.register_analyzer("slow", _slow, "extra")
        e.register_analyzer("fast", lambda _x: {"ok": 1}, "fake_loops")
        p = e.run_all_analyses()
        self.assertEqual(p.fake_loops.get("ok"), 1)
        self.assertEqual(e.get_stats()["last_run"]["slow"]["status"], "timeout")

    def test_15_switch_off_returns_untouched_profile(self):
        self.e.register_analyzer("pc", lambda _e: {"k": 1}, "production_consumption")
        with _Switch(ENABLE_SELF_AWARENESS_ENGINE=False):
            p = self.e.run_all_analyses()
            self.assertFalse(p.production_consumption)

    def test_16_get_stats_shape(self):
        self.e.register_analyzer("pc", lambda _e: {"k": 1}, "production_consumption")
        self.e.run_all_analyses()
        st = self.e.get_stats()
        self.assertIn("analyzers", st)
        self.assertIn("analyzer_count", st)
        self.assertIn("last_run", st)

    def test_17_run_analyzer_does_not_touch_profile(self):
        self.e.register_analyzer("pc", lambda _e: {"k": 9}, "production_consumption")
        r = self.e.run_analyzer("pc")
        self.assertEqual(r.get("k"), 9)
        self.assertFalse(self.e.get_profile().production_consumption)
        self.assertIsNone(self.e.run_analyzer("不存在"))


class TestProfileModel(unittest.TestCase):
    """T1/T5：Profile 字段完整性 + 序列化边界。"""

    _DESIGN_6 = ("code_health", "runtime_health", "knowledge_health",
                 "evolution_health", "production_consumption")

    def test_20_design_dimensions_present(self):
        p = SelfAwarenessProfile()
        for f in self._DESIGN_6:
            self.assertTrue(hasattr(p, f), f)
        self.assertTrue(hasattr(p, "timestamp"))
        # 超集维度（第20/21批新增）
        for f in ("fake_loops", "runtime_events", "organ_activity",
                  "call_graph_health", "extra", "summary"):
            self.assertTrue(hasattr(p, f), f)

    def test_21_roundtrip(self):
        p = SelfAwarenessProfile(timestamp="2026-09-12T00:00:00")
        p.code_health = {"a": 1}
        p.extra = {"z": 9}
        d = p.to_dict()
        q = SelfAwarenessProfile.from_dict(d)
        self.assertEqual(q.code_health, {"a": 1})
        self.assertEqual(q.extra, {"z": 9})
        self.assertEqual(q.timestamp, p.timestamp)

    def test_22_from_dict_tolerant(self):
        self.assertEqual(SelfAwarenessProfile.from_dict(None).code_health, {})
        self.assertEqual(SelfAwarenessProfile.from_dict([1, 2]).code_health, {})
        self.assertEqual(SelfAwarenessProfile.from_dict({"code_health": "x"}).code_health, {})

    def test_23_merge_semantics(self):
        a = SelfAwarenessProfile(timestamp="2026-01-01T00:00:00")
        a.code_health = {"x": 1, "y": 2}
        b = SelfAwarenessProfile(timestamp="2026-02-01T00:00:00")
        b.code_health = {"y": 99, "z": 3}
        a.merge(b)
        self.assertEqual(a.code_health, {"x": 1, "y": 99, "z": 3})
        self.assertEqual(a.timestamp, "2026-02-01T00:00:00")

    def test_24_merge_none_is_noop(self):
        a = SelfAwarenessProfile()
        a.code_health = {"x": 1}
        a.merge(None)
        self.assertEqual(a.code_health, {"x": 1})

    def test_25_overall_score_and_top_issues_gap_closed(self):
        """★缺口**已闭合**（第38批 T1 / P2-213）：三个字段现为独立字段。

        第36批此处是「缺口表征」（断言字段不存在）；第38批 T1 补齐后
        **反向同步**为正向断言 —— 否则该用例会阻止正确修复
        （见 skill §32.8「缺口表征测试在缺口修复后必须反向同步」）。
        """
        p = SelfAwarenessProfile()
        self.assertTrue(hasattr(p, "overall_score"))
        self.assertTrue(hasattr(p, "top_issues"))
        self.assertTrue(hasattr(p, "health_level"))
        self.assertIsNone(p.overall_score)
        self.assertEqual(p.top_issues, [])
        self.assertEqual(p.health_level, "unknown")
        self.assertTrue(hasattr(p, "summary"))

    def test_26_evolution_health_analyzer_available(self):
        """★缺口**已闭合**（第38批 T2 / P2-214）：evolution_health 有分析器接入口。

        第36批此处是「缺口表征」（断言无分析器 → 恒空）；第38批 T2 补齐后反向同步。
        """
        e = SelfAwarenessEngine()
        e.register_analyzer("pc", lambda _x: {"k": 1}, "production_consumption")
        e.register_analyzer("fl", lambda _x: {"c": 1}, "fake_loops")
        p = e.run_all_analyses()
        # 未注册进化分析器时该维仍为空（行为不变）
        self.assertFalse(p.evolution_health)
        # 但引擎已具备接入口（生产接线见 main.py + tests/test_profile_overall_m38.py）
        self.assertTrue(hasattr(e, "integrate_evolution_health"))
        e.register_analyzer("eh", lambda _x: {"status": "ok", "score": 88.0},
                            "evolution_health")
        self.assertEqual(e.run_all_analyses().evolution_health["score"], 88.0)


class TestProfilePersistence(unittest.TestCase):
    """T1/T5：save/load 持久化与四步防御。"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="m36persist_")
        self.e = SelfAwarenessEngine()

    def test_30_save_load_roundtrip(self):
        """★注意：`set_profile` 语义是 **merge**（timestamp 取较新），
        因此断言落在**字典字段**上而非自设时间戳。"""
        _p = SelfAwarenessProfile(timestamp="2026-09-12T00:00:00")
        _p.code_health = {"total_issues": 7}
        self.e.set_profile(_p)
        p = os.path.join(self.d, "p.json")
        self.assertTrue(self.e.save_profile(p))
        got = self.e.load_profile(p)
        self.assertIsNotNone(got)
        self.assertEqual(got.code_health.get("total_issues"), 7)
        self.assertTrue(got.timestamp)

    def test_31_load_never_raises(self):
        self.assertIsNone(self.e.load_profile(""))
        self.assertIsNone(self.e.load_profile(os.path.join(self.d, "nope.json")))
        bad = os.path.join(self.d, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{ not json")
        self.assertIsNone(self.e.load_profile(bad))
        nondict = os.path.join(self.d, "nd.json")
        with open(nondict, "w", encoding="utf-8") as f:
            f.write("[1,2,3]")
        self.assertIsNone(self.e.load_profile(nondict))

    def test_32_load_missing_fields_uses_defaults(self):
        p = os.path.join(self.d, "partial.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"timestamp": "2026-09-12T00:00:00"}, f)
        got = self.e.load_profile(p)
        self.assertIsNotNone(got)
        self.assertEqual(got.code_health, {})


class TestReportGeneration(unittest.TestCase):
    """T4：报告生成。"""

    def test_40_report_sections(self):
        """★注意：`get_profile()` 返回**副本**，改动它不会影响引擎（实测踩到）。"""
        e = SelfAwarenessEngine()
        _p = SelfAwarenessProfile(timestamp="2026-09-12T00:00:00")
        _p.code_health = {"total_issues": 1}
        _p.production_consumption = {"summary": {"data_files": 3}}
        _p.fake_loops = {"summary": {"candidates": 1}}
        e.set_profile(_p)
        txt = e.generate_report()
        self.assertIn("曈曈 PulseNet · 自我认知画像报告", txt)
        self.assertIn("【代码健康】", txt)
        self.assertIn("【产出-消费配对】", txt)
        self.assertIn("【虚假闭环检测】", txt)
        self.assertIn("—— 报告结束 ——", txt)
        self.assertIn("生成时间", txt)
        # 空画像时不得出现【代码健康】段（段标题按"非空才渲染"）
        self.assertNotIn("【代码健康】", SelfAwarenessEngine().generate_report())

    def test_41_report_written_to_disk(self):
        d = tempfile.mkdtemp(prefix="m36rep_")
        out = os.path.join(d, "sub", "r.txt")
        e = SelfAwarenessEngine()
        txt = e.generate_report(out)
        self.assertTrue(os.path.exists(out))
        self.assertEqual(open(out, encoding="utf-8").read(), txt)

    def test_42_five_dimension_overview_always_rendered(self):
        """★实测：五维总览**恒渲染**；无数据时逐项标"不可用"（而非省略段落）。"""
        e = SelfAwarenessEngine()
        empty = e.generate_report()
        self.assertIn("【五维健康度总览】", empty)
        self.assertIn("不可用", empty)
        _p = SelfAwarenessProfile(timestamp="2026-09-12T00:00:00")
        _p.knowledge_health = {"score": 93.45, "level": "优秀"}
        e.set_profile(_p)
        self.assertIn("【知识质量健康度】", e.generate_report())

    def test_43_compare_profiles(self):
        a = SelfAwarenessProfile(timestamp="2026-01-01T00:00:00")
        b = SelfAwarenessProfile(timestamp="2026-01-02T00:00:00")
        r = SelfAwarenessEngine.compare_profiles(a, b)
        self.assertIsNotNone(r)


class TestProductionConsumptionMatcher(unittest.TestCase):
    """T2：扫描契约/分类/范围/开关/端到端（临时工程）。"""

    @classmethod
    def setUpClass(cls):
        cls.proj = _mk_proj()
        cls.res = ProductionConsumptionMatcher(
            project_root=cls.proj, scan_dirs=["pkg"]).scan()

    def test_50_scan_contract(self):
        for k in ("generated_at", "project_root", "scan_dirs", "scanned_files",
                  "total_calls", "elapsed_ms", "files", "dynamic_calls",
                  "parse_errors", "summary"):
            self.assertIn(k, self.res, k)

    def test_51_only_py_files_scanned(self):
        # 目录里是 3 个 .py；data/ 下的 json 不是 .py
        self.assertEqual(self.res["scanned_files"], 3)

    def test_52_categories(self):
        files = self.res["files"]
        # ★主线第37批 T3（P2-212）：分类新增 `path_not_found`（解析后不存在）
        #   与 `dynamic_path`（含占位符的路径模板），允许集合同步扩展。
        _ALLOWED = {"no_consumer", "no_producer", "normal", "unknown",
                    "path_not_found", "dynamic_path", "suspected_fake_loop"}
        cats = {e.get("category") for e in files.values()}
        self.assertTrue(cats <= _ALLOWED, cats)
        _a = files.get("data/a.json")
        self.assertIsNotNone(_a, "data/a.json 应被识别")
        self.assertEqual(_a["category"], "normal")
        _b = files.get("data/b_unread.json")
        self.assertIsNotNone(_b)
        self.assertEqual(_b["category"], "no_consumer")

    def test_53_summary_consistent_with_files(self):
        s = self.res["summary"]
        files = self.res["files"]
        self.assertEqual(s["data_files"], len(files))
        self.assertEqual(s["no_consumer"],
                         sum(1 for e in files.values() if e["category"] == "no_consumer"))
        self.assertEqual(s["no_producer"],
                         sum(1 for e in files.values() if e["category"] == "no_producer"))

    def test_54_producer_and_consumer_recorded(self):
        e = self.res["files"]["data/a.json"]
        self.assertTrue(e["producers"] and e["consumers"])
        self.assertEqual(e["producers"][0]["kind"], "produce")
        self.assertEqual(e["consumers"][0]["kind"], "consume")

    def test_55_build_report_and_persist(self):
        m = ProductionConsumptionMatcher(project_root=self.proj, scan_dirs=["pkg"])
        txt = m.build_report(self.res)
        self.assertIn("产出-消费", txt)
        d = tempfile.mkdtemp(prefix="m36pc_")
        paths = m.persist(self.res, d)
        for v in paths.values():
            self.assertTrue(os.path.exists(v))

    def test_56_switch_off_returns_empty(self):
        with _Switch(ENABLE_PRODUCTION_CONSUMPTION_MATCHER=False):
            self.assertEqual(analyze_production_consumption(), {})


class TestFakeLoopDetector(unittest.TestCase):
    """T3：扫描契约/评分档次/开关/私有方法缺口。"""

    @classmethod
    def setUpClass(cls):
        cls.proj = _mk_proj(extra={"fl.py": _FAKE_LOOP, "pl.py": _PRIVATE_LOAD})
        cls.res = FakeLoopDetector(project_root=cls.proj, scan_dirs=["pkg"]).scan()

    def test_60_scan_contract(self):
        for k in ("generated_at", "project_root", "scan_dirs", "scanned_files",
                  "elapsed_ms", "candidates", "module_path_consts",
                  "parse_errors", "summary"):
            self.assertIn(k, self.res, k)

    def test_61_candidate_detected(self):
        mods = {c["module"] for c in self.res["candidates"]}
        self.assertIn("pkg.fl", mods, "save/load 空实现应成为候选")

    def test_62_candidate_fields(self):
        c = next(c for c in self.res["candidates"] if c["module"] == "pkg.fl")
        for k in ("module", "class", "file", "line", "save_methods",
                  "load_methods", "problems", "score"):
            self.assertIn(k, c, k)
        self.assertTrue(c["problems"])
        for p in c["problems"]:
            self.assertIn("type", p)
            self.assertIn("severity", p)

    def test_63_summary_shape(self):
        s = self.res["summary"]
        for k in ("candidates", "by_score", "by_problem", "critical", "elapsed_ms"):
            self.assertIn(k, s, k)
        self.assertEqual(s["candidates"], len(self.res["candidates"]))
        self.assertEqual(s["critical"],
                         sum(1 for c in self.res["candidates"] if c["score"] <= 30))

    def test_64_score_of_ladder(self):
        self.assertEqual(score_of([]), 100)
        self.assertEqual(score_of([{"severity": "minor"}]), 80)
        self.assertEqual(score_of([{"severity": "medium"}]), 60)
        self.assertEqual(score_of([{"severity": "severe"}]), 30)
        self.assertEqual(score_of([{"severity": "severe"}, {"severity": "medium"}]), 0)
        self.assertEqual(score_of([{"severity": "severe"}, {"severity": "severe"}]), 0)

    def test_65_private_load_now_recognized(self):
        """★缺口**已闭合**（第38批 T3 / P2-215）：私有 `_load` 现可被识别。

        第36批此处是「缺口表征」（断言含私有 `_load` 的模块**不**进入候选）；
        第38批 T3 扩展私有下划线命名识别后**反向同步**为正向断言
        （见 skill §32.8「缺口表征在修复后必须反向同步」）。
        """
        c = next(c for c in self.res["candidates"] if c["module"] == "pkg.pl")
        self.assertTrue(c["has_private_hit"])
        self.assertIn("_load", c["private_load_methods"])

    def test_66_build_report_and_persist(self):
        d = FakeLoopDetector(project_root=self.proj, scan_dirs=["pkg"])
        txt = d.build_report(self.res)
        self.assertIn("虚假闭环", txt)
        out = tempfile.mkdtemp(prefix="m36fl_")
        paths = d.persist(self.res, out)
        for v in paths.values():
            self.assertTrue(os.path.exists(v))

    def test_67_switch_off_returns_empty(self):
        with _Switch(ENABLE_FAKE_LOOP_DETECTOR=False):
            self.assertEqual(analyze_fake_loops(), {})


class TestSourceWiring(unittest.TestCase):
    """T1：框架接线（源码级）。"""

    @classmethod
    def setUpClass(cls):
        cls.main = open(os.path.join(_ROOT, "main.py"), encoding="utf-8").read()

    def test_70_main_initializes_engine(self):
        self.assertIn("def _init_self_awareness(self)", self.main)
        self.assertIn("self._init_self_awareness()", self.main)
        self.assertIn("[SelfAwareness] 自我认知引擎已初始化", self.main)

    def test_71_main_registers_analyzers(self):
        """第36批 T1 补齐后：main.py 应注册 6 个分析器（原 4 + call_graph + knowledge_quality）。"""
        for name in ("production_consumption", "fake_loops", "log_analyzer",
                     "code_review", "call_graph", "knowledge_quality"):
            self.assertIn('"{}"'.format(name), self.main, name)

    def test_74_registration_is_side_effect_free(self):
        """注册分析器不得触发扫描（启动期零开销）。"""
        _e = SelfAwarenessEngine()
        _e.register_analyzer("cg", lambda _x: {"score": 1}, "call_graph_health")
        self.assertEqual(_e.get_stats()["last_run"], {})

    def test_72_init_failure_does_not_block_startup(self):
        self.assertIn("自我认知引擎初始化失败（不影响启动）", self.main)

    def test_73_production_trigger_exists(self):
        """★主线第37批 T1（P2-210）**已修复**本缺口 —— 断言方向同步反转。

        原为「缺口表征」用例（断言生产代码**无** `run_all_analyses` 触发点，
        第36批核实结论）。第37批 T1 在 `main.py:_init_self_awareness()` 接线
        `start_daily_schedule()`，由 `nucleus/self_awareness/DailyScheduler.py`
        的 `run_once()` 调用 `engine.run_all_analyses()` → 缺口消除。
        本用例同步为**正向断言**（触发点存在且位于调度模块），
        避免"修复后无人发现 / 被误改回无调度"。
        """
        import pathlib

        _found = []
        for _p in pathlib.Path(_ROOT).rglob("*.py"):
            _s = str(_p).replace("\\", "/")
            if "/.bak" in _s or "/tmp/" in _s or "/tests/" in _s:
                continue
            if _p.name in ("SelfAwarenessEngine.py", "run_self_awareness_analysis.py"):
                continue
            try:
                # ★用「调用形态」判定：config.py 的提及在**注释**中（无左括号）
                if "run_all_analyses(" in _p.read_text(encoding="utf-8", errors="ignore"):
                    _found.append(_s)
            except Exception:
                continue
        self.assertTrue(_found, "第37批 T1 后应存在 run_all_analyses 生产触发点")
        self.assertTrue(
            any(p.endswith("nucleus/self_awareness/DailyScheduler.py")
                for p in _found),
            f"触发点应位于每日调度模块（DailyScheduler.py），实际: {_found}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
