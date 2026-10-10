# -*- coding: utf-8 -*-
"""
test_call_graph_m21.py —— 主线第21批 门控单测（PHASE18 阶段二：跨文件调用图 P3-3）

覆盖：
  1. CallGraphAnalyzer 基础（6 例）
  2. 健康度分析（6 例）
  3. 画像整合（4 例）
  4. 报告增强（3 例）
  5. 脚本开关（2 例）
  6. 边界 / 隔离（3 例）

隔离：全部在 tmp/_cg_m21_tests/ 构造测试项目，不触碰真实项目数据。
"""

import contextlib
import io
import os
import shutil
import sys
import time
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from nucleus.self_awareness.CallGraphAnalyzer import (  # noqa: E402
    CallGraphAnalyzer,
)
from nucleus.self_awareness.SelfAwarenessEngine import (  # noqa: E402
    SelfAwarenessEngine,
    SelfAwarenessProfile,
)

_SCRATCH = os.path.join(_PROJECT_ROOT, "tmp", "_cg_m21_tests")
_SCRIPT_DIR = os.path.join(_PROJECT_ROOT, "tools")


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
            if _v is None:
                if hasattr(config, _k):
                    delattr(config, _k)
            else:
                setattr(config, _k, _v)
        return False


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _build_sample(root, with_broken=True, n_extra=0):
    """构造标准样本项目：跨文件调用 + 循环 + 孤立 + 动态调用。"""
    shutil.rmtree(root, ignore_errors=True)
    _write(os.path.join(root, "pkg", "__init__.py"), "")
    _write(os.path.join(root, "pkg", "a.py"), '''
from pkg.b import b2


def a1():
    return b2()


def a2():
    return a1()


class Service:
    def run(self):
        return a2()

    def helper(self):
        return self.run()
''')
    _write(os.path.join(root, "pkg", "b.py"), '''
from pkg.a import a2


def b2():
    return a2()


def never_called_public():
    return 1


def _never_called_internal():
    return 2


def dynamic_user(obj):
    return getattr(obj, "something")()
''')
    _write(os.path.join(root, "pkg", "c.py"), '''
def d1():
    return d2()


def d2():
    return d3()


def d3():
    return 1


def orphan_here():
    return 0
''')
    if with_broken:
        _write(os.path.join(root, "bad", "broken.py"), "def x(:\n  pass\n")
    for _i in range(n_extra):
        _write(os.path.join(root, "pkg", "gen_%03d.py" % _i),
               "def g%d_a():\n    return g%d_b()\n\n\ndef g%d_b():\n    return %d\n"
               % (_i, _i, _i, _i))


# ======================================================================
# 组 1：CallGraphAnalyzer 基础（6 例）
# ======================================================================
class TestAnalyzerBasics(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(_SCRATCH, "proj")
        _build_sample(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _cg(self, dirs=("pkg", "bad")):
        return CallGraphAnalyzer(project_root=self.root, scan_dirs=list(dirs))

    def test_config_default_enabled(self):
        self.assertIs(getattr(config, "ENABLE_CALL_GRAPH_ANALYSIS", None), True)

    def test_analyze_returns_nodes_edges_stats(self):
        d = self._cg().analyze()
        for _k in ("nodes", "edges", "stats"):
            self.assertIn(_k, d)
        st = d["stats"]
        for _k in ("total_files", "total_functions", "total_calls",
                   "cross_file_calls", "dynamic_calls_unresolved"):
            self.assertIn(_k, st)
        self.assertGreater(st["total_functions"], 0)
        self.assertGreater(len(d["edges"]), 0)
        _ids = {n["id"] for n in d["nodes"]}
        self.assertIn("pkg.a:a1", _ids)
        self.assertIn("pkg.a:Service.run", _ids)

    def test_empty_dir(self):
        _empty = os.path.join(_SCRATCH, "empty")
        shutil.rmtree(_empty, ignore_errors=True)
        os.makedirs(_empty, exist_ok=True)
        d = CallGraphAnalyzer(project_root=_empty, scan_dirs=["."]).analyze()
        self.assertEqual(d["nodes"], [])
        self.assertEqual(d["edges"], [])
        self.assertEqual(d["stats"]["total_functions"], 0)

    def test_syntax_error_file_skipped(self):
        d = self._cg().analyze()
        self.assertEqual(d["stats"]["parse_errors"], 1)
        self.assertIn("bad/broken.py", d["parse_error_files"])
        # 其他文件仍被分析
        self.assertTrue(any(n["id"] == "pkg.a:a1" for n in d["nodes"]))

    def test_dynamic_call_marked(self):
        d = self._cg().analyze()
        _ids = {n["id"] for n in d["nodes"]}
        self.assertIn("pkg.b:dynamic_user", _ids)
        self.assertGreaterEqual(d["stats"]["dynamic_calls"], 1,
                                "getattr(obj,'x')() 应被标记为动态调用")

    def test_import_resolution_cross_file(self):
        d = self._cg().analyze()
        _edges = {(e["caller"], e["callee"]) for e in d["edges"]}
        self.assertIn(("pkg.a:a1", "pkg.b:b2"), _edges)
        self.assertIn(("pkg.b:b2", "pkg.a:a2"), _edges)
        self.assertGreaterEqual(d["stats"]["cross_file_calls"], 2)

    def test_export_json(self):
        cg = self._cg()
        cg.analyze()
        _p = os.path.join(_SCRATCH, "export.json")
        _size = cg.export_json(_p)
        self.assertGreater(_size, 0)
        self.assertTrue(os.path.isfile(_p))
        os.remove(_p)


# ======================================================================
# 组 2：健康度分析（6 例）
# ======================================================================
class TestHealth(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(_SCRATCH, "proj2")
        _build_sample(self.root)
        self.cg = CallGraphAnalyzer(project_root=self.root, scan_dirs=["pkg"])

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_isolated_functions_detected(self):
        _iso = {x["name"] for x in self.cg.get_isolated_functions()}
        self.assertIn("never_called_public", _iso)
        self.assertIn("_never_called_internal", _iso)
        self.assertNotIn("a2", _iso)          # a2 被 b2 调用
        # d1 是链头（无人调用它）→ 按「从未被调用」定义仍属孤立（其被调者另有入度）
        self.assertIn("d1", _iso)
        self.assertNotIn("d2", _iso)          # d2 被 d1 调用
        # 魔术方法不列入
        self.assertNotIn("__init__", _iso)

    def test_hot_functions_sorted_desc(self):
        _hot = self.cg.get_hot_functions(10)
        self.assertTrue(_hot)
        _calls = [x["calls"] for x in _hot]
        self.assertEqual(_calls, sorted(_calls, reverse=True))
        self.assertEqual(_hot[0]["name"], "a2")     # a1 与 b2 各调 1 次

    def test_cyclic_calls_detected(self):
        _cyc = self.cg.get_cyclic_calls()
        self.assertTrue(_cyc, "a1→b2→a2→a1 应被检出")
        _all_nodes = set()
        for _c in _cyc:
            _all_nodes.update(_c["nodes"])
        self.assertIn("pkg.a:a1", _all_nodes)
        self.assertIn("pkg.b:b2", _all_nodes)
        self.assertEqual(_cyc[0]["kind"], "indirect")   # 3 节点 → 间接环

    def test_direct_cycle_kind(self):
        _r = os.path.join(_SCRATCH, "direct")
        shutil.rmtree(_r, ignore_errors=True)
        _write(os.path.join(_r, "m.py"),
               "def p():\n    return q()\n\n\ndef q():\n    return p()\n")
        _c = CallGraphAnalyzer(project_root=_r, scan_dirs=["."]).get_cyclic_calls()
        self.assertTrue(_c)
        self.assertEqual(_c[0]["kind"], "direct")
        shutil.rmtree(_r, ignore_errors=True)

    def test_deepest_call_chain(self):
        _chain = self.cg.get_deepest_call_chain()
        self.assertTrue(_chain, "样本应存在非空最深链")
        # helper → run → a2 → ... 至少 3 层
        self.assertGreaterEqual(len(_chain), 3)

    def test_analyze_health_shape_and_score(self):
        _cg = self.cg.analyze()
        h = self.cg.analyze_health(_cg)
        for _k in ("score", "grade", "isolated", "hot", "cyclic", "depth",
                   "coupling", "recommendations", "dimensions"):
            self.assertIn(_k, h)
        self.assertGreaterEqual(h["score"], 0)
        self.assertLessEqual(h["score"], 100)
        self.assertIn(h["grade"], ("优秀", "良好", "一般", "需改善"))
        _dims = h["dimensions"]
        self.assertEqual(len(_dims), 5)
        for _v in _dims.values():
            self.assertGreaterEqual(_v, 0)
            self.assertLessEqual(_v, 20)
        self.assertTrue(h["recommendations"])

    def test_health_no_data(self):
        cg = CallGraphAnalyzer(project_root=_PROJECT_ROOT, scan_dirs=["__nope__"])
        h = cg.analyze_health({"nodes": [], "edges": [], "stats": {}})
        self.assertTrue(h.get("no_data"))


# ======================================================================
# 组 3：画像整合（4 例）
# ======================================================================
class TestProfileIntegration(unittest.TestCase):
    def test_profile_field_default(self):
        p = SelfAwarenessProfile()
        self.assertEqual(p.call_graph_health, {})
        self.assertIn("call_graph_health", p.to_dict())

    def test_old_json_backward_compatible(self):
        _old = {"timestamp": "x", "code_health": {"a": 1}}
        p = SelfAwarenessProfile.from_dict(_old)
        self.assertEqual(p.call_graph_health, {})
        self.assertEqual(p.code_health, {"a": 1})

    def test_roundtrip_preserves_field(self):
        p = SelfAwarenessProfile()
        p.call_graph_health = {"score": 88.0, "grade": "优秀"}
        p2 = SelfAwarenessProfile.from_dict(p.to_dict())
        self.assertEqual(p2.call_graph_health["score"], 88.0)

    def test_integrate_call_graph_exception_returns_empty(self):
        e = SelfAwarenessEngine()
        with mock.patch(
                "nucleus.self_awareness.CallGraphAnalyzer.CallGraphAnalyzer.analyze",
                side_effect=RuntimeError("boom")):
            self.assertEqual(e.integrate_call_graph(project_root=_PROJECT_ROOT), {})

    def test_integrate_call_graph_switch_off(self):
        e = SelfAwarenessEngine()
        with _Switch(ENABLE_CALL_GRAPH_ANALYSIS=False):
            self.assertEqual(e.integrate_call_graph(), {})

    def test_integrate_call_graph_on_sample(self):
        _root = os.path.join(_SCRATCH, "proj3")
        _build_sample(_root)
        e = SelfAwarenessEngine()
        r = e.integrate_call_graph(project_root=_root, scan_dirs=["pkg"])
        self.assertIn("score", r)
        self.assertIn("graph_stats", r)
        self.assertGreater(r["graph_stats"]["total_functions"], 0)
        shutil.rmtree(_root, ignore_errors=True)


# ======================================================================
# 组 4：报告增强（3 例）
# ======================================================================
class TestReport(unittest.TestCase):
    def test_report_contains_call_graph_section(self):
        e = SelfAwarenessEngine()
        p = SelfAwarenessProfile(timestamp="2026-09-11T16:00:00")
        p.call_graph_health = {
            "score": 78.5, "grade": "良好",
            "isolated": {"count": 10, "ratio": 0.05, "internal_count": 3,
                         "top20": [{"name": "foo", "file": "a.py", "line": 1}],
                         "level": "正常"},
            "hot": {"top20": [{"name": "bar", "calls": 12}],
                    "super_functions": [], "level": "正常"},
            "cyclic": {"count": 1, "chains": [
                {"kind": "direct", "length": 2, "nodes": ["m:p", "m:q"]}],
                "level": "需关注"},
            "depth": {"deepest": 7, "average": 2.1, "chain": [], "level": "正常"},
            "coupling": {"cross_file_ratio": 0.3, "core_modules": [],
                         "edge_modules": [], "level": "正常"},
            "recommendations": ["建议A", "建议B"],
        }
        p.summary = "测试"
        e.set_profile(p)
        _rep = e.generate_report()
        self.assertIn("【代码结构健康度】", _rep)
        self.assertIn("综合评分", _rep)
        self.assertIn("孤立函数", _rep)
        self.assertIn("调用深度", _rep)
        self.assertIn("建议1", _rep)

    def test_report_no_data_message(self):
        _sec = SelfAwarenessEngine._report_call_graph_section(SelfAwarenessProfile())
        self.assertIn("调用图数据不可用", "\n".join(_sec))

    def test_existing_sections_not_removed(self):
        e = SelfAwarenessEngine()
        p = SelfAwarenessProfile(timestamp="t", summary="s")
        p.code_health = {"total_issues": 1}
        e.set_profile(p)
        _rep = e.generate_report()
        for _k in ("曈曈 PulseNet · 自我认知画像报告", "【总结】",
                   "【代码健康】", "—— 报告结束 ——"):
            self.assertIn(_k, _rep, "现有段落被破坏: {}".format(_k))


# ======================================================================
# 组 5：脚本开关（2 例）
# ======================================================================
class TestScriptSwitches(unittest.TestCase):
    def _run(self, argv):
        _buf = io.StringIO()
        if _SCRIPT_DIR not in sys.path:
            sys.path.insert(0, _SCRIPT_DIR)
        import run_self_awareness_analysis as _script
        with contextlib.redirect_stdout(_buf):
            _rc = _script.main(argv)
        return _rc, _buf.getvalue()

    def _base(self, extra):
        return (["--skip-code-review", "--out-dir",
                 os.path.join("tmp", "_cg_m21_tests", "out"),
                 "--no-include-event-tap", *list(extra)])

    def test_no_include_call_graph(self):
        _rc, _out = self._run(self._base(["--no-include-call-graph"]))
        self.assertEqual(_rc, 0)
        _line = [x for x in _out.splitlines() if "已注册分析器" in x]
        self.assertTrue(_line)
        self.assertNotIn("call_graph", _line[0])

    def test_call_graph_only(self):
        _rc, _out = self._run(self._base(["--call-graph-only"]))
        self.assertEqual(_rc, 0)
        _line = [x for x in _out.splitlines() if "已注册分析器" in x]
        self.assertTrue(_line)
        self.assertEqual(_line[0].split(":", 1)[1].strip(), "call_graph")
        self.assertIn("代码结构健康度", _out)


# ======================================================================
# 组 6：边界 / 隔离（3 例）
# ======================================================================
class TestBoundary(unittest.TestCase):
    def test_performance_100_files(self):
        _root = os.path.join(_SCRATCH, "perf")
        _build_sample(_root, with_broken=False, n_extra=100)
        _t0 = time.perf_counter()
        d = CallGraphAnalyzer(project_root=_root, scan_dirs=["pkg"]).analyze()
        _elapsed = time.perf_counter() - _t0
        self.assertLess(_elapsed, 30.0, "100 文件分析超时: {:.2f}s".format(_elapsed))
        self.assertGreater(d["stats"]["total_functions"], 200)
        shutil.rmtree(_root, ignore_errors=True)

    def test_tests_dir_excluded_by_default(self):
        _root = os.path.join(_SCRATCH, "excl")
        _build_sample(_root, with_broken=False)
        _write(os.path.join(_root, "tests", "test_z.py"),
               "def should_be_excluded():\n    pass\n")
        _write(os.path.join(_root, "tmp", "junk.py"),
               "def also_excluded():\n    pass\n")
        d = CallGraphAnalyzer(project_root=_root, scan_dirs=["."]).analyze()
        _files = {n["file"] for n in d["nodes"]}
        self.assertFalse(any(f.startswith("tests/") for f in _files), _files)
        self.assertFalse(any(f.startswith("tmp/") for f in _files), _files)
        shutil.rmtree(_root, ignore_errors=True)

    def test_bak_dir_excluded(self):
        _root = os.path.join(_SCRATCH, "bak")
        _build_sample(_root, with_broken=False)
        _write(os.path.join(_root, ".bak_batch1", "old.py"),
               "def bak_fn():\n    pass\n")
        d = CallGraphAnalyzer(project_root=_root, scan_dirs=["."]).analyze()
        _files = {n["file"] for n in d["nodes"]}
        self.assertFalse(any(".bak" in f for f in _files), _files)
        shutil.rmtree(_root, ignore_errors=True)


def teardown_module():
    # ★第22批 T3：pytest 只识别下划线命名模块级夹具，驼峰 tearDownModule
    #   不会被调用 → 临时目录清理长期失效并污染 tmp/ 与全库 F 口径。
    """★模块级收尾：尽力删除 scratch 目录。

    注意：本模块会构造含 ~100 文件的性能样本 + 脚本落盘产物，scratch 可达数百文件；
    沙箱 `safe-delete` 按 **turn 级累计删除数**限流（阈值 50），rmtree 可能抛 SystemExit。
    此处**静默容错**，不把环境限流误报成测试失败；残留由独立清理命令处理。
    """
    try:
        shutil.rmtree(_SCRATCH, ignore_errors=True)
    except SystemExit:
        _silent = True
    except Exception:
        _silent = True


if __name__ == "__main__":
    unittest.main()
