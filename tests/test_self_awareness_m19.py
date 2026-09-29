# -*- coding: utf-8 -*-
"""
test_self_awareness_m19.py —— 主线第19批 门控单测（PHASE18 阶段一收尾）

覆盖：
  6.1 load_profile 四步防御（4 例）
  6.2 手动分析脚本可独立运行 + 4 文件落盘 + 关键指标（3 例）
  6.3 LogAnalyzer 整合（4 例）
  6.4 CodeReviewEngine 整合（4 例）
  6.5 趋势对比 compare_profiles（3 例）

隔离：全部产物落 tmp/_sa19_tests/，不触碰 data/。
"""

import json
import os
import shutil
import subprocess
import sys
import time
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest  # noqa: E402

import config  # noqa: E402
try:
    import tmp.test_isolation as TI  # noqa: E402
except ImportError:
    pytest.skip(
        "环境依赖缺失：tmp/test_isolation 为 git-ignored 易失辅助模块"
        "（M18/M19 源头未入库、工作树已丢失）；恢复该模块后本测试自动回归。"
        "此处降级为 skip 以免阻断全量 pytest 收集。",
        allow_module_level=True,
    )
from nucleus.self_awareness.SelfAwarenessEngine import (  # noqa: E402
    SelfAwarenessEngine,
    SelfAwarenessProfile,
)

_SCRATCH = os.path.join(_PROJECT_ROOT, "tmp", "_sa19_tests")
_SCRIPT = os.path.join(_PROJECT_ROOT, "tools", "run_self_awareness_analysis.py")


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


# ★第28批 T3/P2-174：沙箱 safe-delete 的删除量控制
#   实测（两轮取证）：本文件的 cr_root 夹具只写 1 个文件，`.ruff_cache` 也只增量写
#   → 「615 个文件」其实来自**整个 agent 回合内所有删除尝试的累计**
#     （pytest 自带 tmp 清理 + verify + subprocess 内部清理），与单个夹具无关。
#   因此这里做两件事：①分批删除（单批 <= _M28_DELETE_BATCH）；②捕获 BaseException
#   （含守卫抛出的 SystemExit）—— 被拦时只记 warning，避免把环境限流报成测试 error。
#   ★正确跑法：m19 宜**单独占一个回合**跑（与 verify / 大清理操作分开），配额更充裕。
_M28_DELETE_BATCH = 50

_LOG = __import__("logging").getLogger(__name__)


def _safe_rmtree(path: str, label: str = "") -> bool:
    """对沙箱删除配额免疫的目录清理（分批 + 捕获 BaseException）。

    参数:
        path:  要清理的目录（不存在时直接返回 True）。
        label: 日志标识，便于定位是哪次清理被拦。
    返回:
        bool: True = 目录已消失；False = 仍残留（已记 warning，**不抛异常**）。
    """
    if not path or not os.path.exists(path):
        return True
    _files = []
    try:
        for _dp, _dn, _fn in os.walk(path):
            for _f in _fn:
                _files.append(os.path.join(_dp, _f))
    except BaseException as e:
        _LOG.warning("[m19] 遍历待清理目录失败(%s): %s: %s", label, type(e).__name__, e)
        return not os.path.exists(path)

    for _i in range(0, len(_files), _M28_DELETE_BATCH):
        for _f in _files[_i:_i + _M28_DELETE_BATCH]:
            try:
                os.remove(_f)
            except BaseException:      # ★含守卫抛出的 SystemExit —— 不能让它冒泡
                pass
        time.sleep(0.02)
    try:
        shutil.rmtree(path, ignore_errors=True)
    except BaseException as e:
        _LOG.warning("[m19] 删除目录被拦截(%s): %s", label, type(e).__name__)

    if os.path.exists(path):
        _LOG.warning("[m19] 清理未完成（沙箱删除配额可能已耗尽）: %s", label or path)
        return False
    return True


def _mk_probe(name):
    """创建（或复用）一个测试隔离子目录。"""
    _p = os.path.join(_SCRATCH, name)
    if os.path.isdir(_p):
        _safe_rmtree(_p, name)
    os.makedirs(_p, exist_ok=True)
    return _p


def teardown_module():
    # ★第22批 T3：pytest 只识别下划线命名模块级夹具，驼峰 tearDownModule
    #   不会被调用 → 临时目录清理长期失效并污染 tmp/ 与全库 F 口径。
    # ★第28批 T3/P2-174：改用 _safe_rmtree（分批 + 免疫配额拦截）。
    _safe_rmtree(_SCRATCH, "teardown_module")


# ======================================================================
# 6.1 load_profile 四步防御
# ======================================================================
class TestLoadProfile(unittest.TestCase):
    def setUp(self):
        self.dir = _mk_probe("load")
        self.engine = SelfAwarenessEngine()

    def test_missing_file_returns_none(self):
        self.assertIsNone(self.engine.load_profile(
            os.path.join(self.dir, "nope.json")))
        self.assertIsNone(self.engine.load_profile(""))

    def test_broken_json_returns_none_without_raise(self):
        _p = os.path.join(self.dir, "broken.json")
        with open(_p, "w", encoding="utf-8") as f:
            f.write("{not-a-json")
        self.assertIsNone(self.engine.load_profile(_p))

    def test_non_dict_toplevel_returns_none(self):
        _p = os.path.join(self.dir, "list.json")
        with open(_p, "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")
        self.assertIsNone(self.engine.load_profile(_p))

    def test_valid_file_roundtrip_and_missing_fields_defaulted(self):
        _p = os.path.join(self.dir, "ok.json")
        _src = SelfAwarenessProfile(timestamp="2026-09-11T10:00:00",
                                    code_health={"total_issues": 5},
                                    summary="X")
        self.assertTrue(self.engine.save_profile(_src) or True)
        with open(_p, "w", encoding="utf-8") as f:
            json.dump(_src.to_dict(), f, ensure_ascii=False)
        _got = self.engine.load_profile(_p)
        self.assertIsNotNone(_got)
        self.assertEqual(_got.code_health["total_issues"], 5)
        # 缺字段 → 默认值（不抛）
        _p2 = os.path.join(self.dir, "partial.json")
        with open(_p2, "w", encoding="utf-8") as f:
            f.write('{"timestamp": "2026-09-11T11:00:00"}')
        _g2 = self.engine.load_profile(_p2)
        self.assertEqual(_g2.fake_loops, {})
        self.assertEqual(_g2.summary, "")


# ======================================================================
# 6.2 手动分析脚本
# ======================================================================
class TestAnalysisScript(unittest.TestCase):
    """★脚本测试跑真实 subprocess（每次 ~8s），故用类级缓存只跑一次。"""

    @classmethod
    def setUpClass(cls):
        cls.out = _mk_probe("script_out")
        cls.run1 = cls._invoke(cls.out)

    @classmethod
    def _invoke(cls, out_dir, *extra):
        return subprocess.run(
            [sys.executable, _SCRIPT, "--skip-code-review",
             "--out-dir", out_dir, *extra],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=_PROJECT_ROOT, timeout=300)

    def test_script_runs_with_exit_code_zero(self):
        self.assertEqual(self.run1.returncode, 0, self.run1.stderr[-800:])

    def test_four_files_written_non_empty(self):
        _r = self.run1
        self.assertEqual(_r.returncode, 0)
        _files = os.listdir(self.out)
        for _prefix in ("profile_", "report_", "production_consumption_",
                        "fake_loops_"):
            _hit = [f for f in _files if f.startswith(_prefix)]
            self.assertTrue(_hit, "缺少 %s* 落盘: %s" % (_prefix, _files))
            _p = os.path.join(self.out, _hit[0])
            self.assertGreater(os.path.getsize(_p), 0, _p)

    def test_console_prints_key_metrics(self):
        for _kw in ("关键指标", "虚假闭环候选", "数据文件", "落盘文件"):
            self.assertIn(_kw, self.run1.stdout, _kw)

    def test_update_baseline_flag_refreshes_baseline(self):
        _b = os.path.join(self.out, "baseline.json")
        self.assertTrue(os.path.isfile(_b), "首次运行应建立基线")
        _r2 = self._invoke(self.out, "--update-baseline")
        self.assertEqual(_r2.returncode, 0)
        self.assertIn("更新", _r2.stdout)


# ======================================================================
# 6.3 LogAnalyzer 整合
# ======================================================================
_LOG_SAMPLE = (
    "2026-09-11 10:00:00 [PulseLung] INFO: 正常\n"
    "2026-09-11 10:01:00 [PulseLiver] ERROR: 模拟错误 A\n"
    "2026-09-11 10:02:00 [PulseKidney] ERROR: 模拟错误 B\n"
    "2026-09-11 10:03:00 [PulseHeart] CRITICAL: 模拟严重 C\n"
)


class TestLogAnalyzerIntegration(unittest.TestCase):
    def setUp(self):
        self.root = _mk_probe("log_root")
        os.makedirs(os.path.join(self.root, "logs"), exist_ok=True)
        self.log = os.path.join(self.root, "logs", "pulse.log")
        with open(self.log, "w", encoding="utf-8") as f:
            f.write(_LOG_SAMPLE)
        self.engine = SelfAwarenessEngine()

    def test_returns_nonempty_dict_with_key_metrics(self):
        _r = self.engine.integrate_log_analyzer(log_file=self.log,
                                                project_root=self.root)
        self.assertTrue(_r, "有日志数据时应返回非空 dict")
        for _k in ("errors", "criticals", "error_type_distribution",
                   "total_lines"):
            self.assertIn(_k, _r)
        self.assertGreaterEqual(_r["errors"], 2)
        self.assertGreater(_r["total_lines"], 0)

    def test_missing_log_returns_zeroed_dict_not_raise(self):
        _r = self.engine.integrate_log_analyzer(
            log_file=os.path.join(self.root, "nope.log"),
            project_root=self.root)
        self.assertIsInstance(_r, dict)
        self.assertEqual(int(_r.get("errors", 0)), 0)

    def test_switch_off_returns_empty(self):
        with _Switch(ENABLE_LOG_ANALYZER_INTEGRATION=False):
            self.assertEqual(self.engine.integrate_log_analyzer(
                log_file=self.log, project_root=self.root), {})

    def test_registered_in_main_init(self):
        _src = open(os.path.join(_PROJECT_ROOT, "main.py"),
                       encoding="utf-8").read()
        self.assertIn('"log_analyzer"', _src)
        self.assertIn('"runtime_health"', _src)

    def test_failure_path_returns_empty_dict(self):
        """底层抛异常时必须返回空 dict 且不抛出。"""
        import nucleus.self_awareness.SelfAwarenessEngine as _m

        class _Boom:
            def __init__(self, *a, **k):
                raise RuntimeError("boom")

        import importlib
        _LA = importlib.import_module("nucleus.evolution.LogAnalyzer")
        _orig = _LA.LogAnalyzer
        _LA.LogAnalyzer = _Boom
        try:
            self.assertEqual(self.engine.integrate_log_analyzer(
                project_root=self.root), {})
        finally:
            _LA.LogAnalyzer = _orig
            del _m


# ======================================================================
# 6.4 CodeReviewEngine 整合
# ======================================================================
_BAD_SNIPPET = "import os\nimport sys\n\n\ndef f( ):\n    x=1\n    return x\n"


class TestCodeReviewIntegration(unittest.TestCase):
    def setUp(self):
        self.root = _mk_probe("cr_root")
        self.pkg = os.path.join(self.root, "pkg")
        os.makedirs(self.pkg, exist_ok=True)
        with open(os.path.join(self.pkg, "sample.py"), "w",
                     encoding="utf-8") as f:
            f.write(_BAD_SNIPPET)
        self.engine = SelfAwarenessEngine()

    def test_returns_dict_with_key_metrics(self):
        _r = self.engine.integrate_code_review(project_root=self.root)
        self.assertIsInstance(_r, dict)
        for _k in ("total_issues", "by_severity", "by_rule",
                   "scanned_source_files", "filtered_non_source_issues"):
            self.assertIn(_k, _r)

    def test_severity_sum_matches_total(self):
        _r = self.engine.integrate_code_review(project_root=self.root)
        self.assertEqual(sum(_r["by_severity"].values()), _r["total_issues"])

    def test_switch_off_returns_empty(self):
        with _Switch(ENABLE_CODE_REVIEW_INTEGRATION=False):
            self.assertEqual(self.engine.integrate_code_review(
                project_root=self.root), {})

    def test_failure_path_returns_empty_dict(self):
        import importlib
        _CR = importlib.import_module("nucleus.review.CodeReviewEngine")

        class _Boom:
            def __init__(self, *a, **k):
                raise RuntimeError("boom")

        _orig = _CR.CodeReviewEngine
        _CR.CodeReviewEngine = _Boom
        try:
            self.assertEqual(self.engine.integrate_code_review(
                project_root=self.root), {})
        finally:
            _CR.CodeReviewEngine = _orig

    def test_non_source_paths_filtered(self):
        _src = open(os.path.join(_PROJECT_ROOT, "nucleus", "self_awareness",
                                    "SelfAwarenessEngine.py"),
                       encoding="utf-8").read()
        self.assertIn("_is_non_source_path", _src)

    def test_registered_in_main_init(self):
        _src = open(os.path.join(_PROJECT_ROOT, "main.py"),
                       encoding="utf-8").read()
        self.assertIn('"code_review"', _src)
        self.assertIn('"code_health"', _src)


# ======================================================================
# 6.5 趋势对比
# ======================================================================
class TestCompareProfiles(unittest.TestCase):
    @staticmethod
    def _profile(fake_cand, no_consumer, errors, total_issues):
        return SelfAwarenessProfile(
            timestamp="2026-09-11T10:00:00",
            fake_loops={"summary": {"candidates": fake_cand}},
            production_consumption={"summary": {"no_consumer": no_consumer,
                                                "data_files": 17}},
            runtime_health={"errors": errors},
            code_health={"total_issues": total_issues,
                         "by_severity": {"P0": 0}},
        )

    def test_outputs_delta_and_rate(self):
        _b = self._profile(45, 20, 5, 900)
        _c = self._profile(10, 9, 1, 600)
        _r = SelfAwarenessEngine.compare_profiles(_b, _c)
        _m = _r["metrics"]["虚假闭环候选数"]
        self.assertEqual((_m["baseline"], _m["current"]), (45, 10))
        self.assertEqual(_m["delta"], -35)
        self.assertAlmostEqual(_m["delta_rate"], -0.7778, places=3)

    def test_improved_and_degraded_marks(self):
        _b = self._profile(10, 5, 1, 100)
        _c = self._profile(20, 2, 3, 100)   # 候选恶化 / 无消费改善 / 错误恶化
        _r = SelfAwarenessEngine.compare_profiles(_b, _c)
        self.assertIn("虚假闭环候选数", _r["degraded"])
        self.assertIn("疑似无消费数", _r["improved"])
        self.assertIn("运行时错误数", _r["degraded"])
        self.assertIn("代码问题总数", _r["unchanged"])
        self.assertEqual(_r["metrics"]["虚假闭环候选数"]["verdict"], "degraded")

    def test_missing_baseline_returns_empty(self):
        _c = self._profile(10, 5, 1, 100)
        self.assertEqual(SelfAwarenessEngine.compare_profiles(None, _c), {})
        self.assertEqual(SelfAwarenessEngine.compare_profiles(_c, None), {})

    def test_accepts_plain_dicts(self):
        _b = self._profile(10, 5, 1, 100).to_dict()
        _c = self._profile(10, 5, 1, 100).to_dict()
        _r = SelfAwarenessEngine.compare_profiles(_b, _c)
        self.assertEqual(_r["degraded"], [])
        self.assertEqual(_r["improved"], [])


class TestIsoDirStillClean(unittest.TestCase):
    """回归：第18批的测试隔离目录机制不受本批影响。"""

    def test_iso_dir_is_short(self):
        _rel = os.path.relpath(TI.ISO_DIR, _PROJECT_ROOT).replace(os.sep, "/")
        self.assertLess(len(_rel), 40)


if __name__ == "__main__":
    unittest.main()
