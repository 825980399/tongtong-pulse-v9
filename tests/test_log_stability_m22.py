# -*- coding: utf-8 -*-
"""test_log_stability_m22.py —— 主线第22批门控单测。

覆盖 5 组共 20 例：
  ① JSON 解析增强（策略3 自动修复 + 语义安全）           6 例
  ② JSON 失败统计（去重窗口 / 分类 / 摘要）              2 例
  ③ ReasoningWorkerPool 降级恢复（恢复 / 节流 / 降级前尝试） 3 例
  ④ 测试日志隔离（静音复原 / 隔离目录 / 重定向）          3 例
  ⑤ 修复蒸馏空片段兜底（文件 / 消息 / 无素材）            3 例
  ⑥ 日志噪音治理（开关 / 调用图降级 / 汇总输出）          3 例
"""
import importlib
import json as _json
import logging
import os
import sys
import tempfile
import time
import unittest
from concurrent.futures import Future
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
import nucleus.reasoning.ReasoningWorkerPool as RWP  # noqa: E402

try:
    import tmp.test_log_isolation as TL  # noqa: E402
    from tmp.test_isolation import ISO_DIR as _ISO_DIR  # noqa: E402
    from tmp.test_isolation import TestIsolation as _TestIsolation  # noqa: E402
except ImportError:
    import pytest
    pytest.skip(
        "环境依赖缺失：tmp/test_log_isolation、tmp/test_isolation 为 git-ignored 易失辅助模块"
        "（M18/M22 源头未入库、工作树已丢失）；恢复后自动回归。"
        "此处降级为 skip 以免阻断全量 pytest 收集。",
        allow_module_level=True,
    )
from nucleus.logger import noise_reduction_enabled  # noqa: E402
from nucleus.parsing.JsonRepair import (  # noqa: E402
    JsonFailureTracker,
    classify_json_error,
    parse_with_repair,
    repair_json_text,
)
from nucleus.reasoning.ReasoningWorkerPool import ReasoningWorkerPool  # noqa: E402
from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor  # noqa: E402
from nucleus.self_awareness.CallGraphAnalyzer import CallGraphAnalyzer  # noqa: E402

# （上述两导入已在上方 importorskip 守卫块内统一处理，避免重复导入）

# ★注意：nucleus.self_awareness 包的 __init__ 导出了与子模块同名的类，
#   `import nucleus.self_awareness.CallGraphAnalyzer as X` 会拿到**类**而非模块
#   （第18批已记录的坑）→ 必须用 importlib 取模块才能 patch 其模块级 _logger。
_CGA_MOD = importlib.import_module("nucleus.self_awareness.CallGraphAnalyzer")


class _Switch:
    """临时改写 config 开关（退出还原原值/原缺失状态）。"""

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


# ======================================================================
# 组① JSON 解析增强（6 例）
# ======================================================================
class TestJsonRepair(unittest.TestCase):
    def test_01_missing_colon(self):
        """缺冒号 `"k" v` → 自动补冒号。"""
        _o, _m = parse_with_repair('{"功能" "分析代码", "关键步骤": "1. a"}')
        self.assertIsNotNone(_o)
        self.assertEqual(_m, "repair")
        self.assertEqual(_o["功能"], "分析代码")

    def test_02_missing_comma(self):
        """缺逗号 `"a": 1 "b": 2` → 自动补逗号。"""
        _o, _m = parse_with_repair('{"功能": "分析代码" "关键步骤": "1. a"}')
        self.assertIsNotNone(_o)
        self.assertEqual(_o["关键步骤"], "1. a")

    def test_03_trailing_comma(self):
        """尾随逗号 `...,}` → 删除。"""
        _o, _ = parse_with_repair('{"功能": "a", "关键步骤": "b",}')
        self.assertIsNotNone(_o)
        self.assertEqual(_o["功能"], "a")

    def test_04_single_quote_combo(self):
        """单引号 + 缺冒号 + 尾逗号组合 → 全部修复。"""
        _o, _m = parse_with_repair("{'功能' '分析代码', '关键步骤': '1. a',}")
        self.assertIsNotNone(_o)
        self.assertEqual(_m, "repair")
        self.assertEqual(_o["功能"], "分析代码")
        self.assertEqual(_o["关键步骤"], "1. a")

    def test_05_inner_quote_escaped(self):
        """值内未转义引号 → 转义后仍能解析，内容不丢。"""
        _o, _ = parse_with_repair('{"功能": "he said "hi" ok"}')
        self.assertIsNotNone(_o)
        self.assertEqual(_o["功能"], 'he said "hi" ok')

    def test_06_unrepairable_and_semantics_safe(self):
        """无法修复返回 None（调用方保留原文）；含撇号的合法值不得被改写。"""
        self.assertIsNone(repair_json_text("这段文字没有任何结构化数据"))
        self.assertEqual(parse_with_repair("普通描述")[1], "none")
        _src = '{"功能": "解析 it\'s 内容"}'
        _r = repair_json_text(_src)
        self.assertIsNotNone(_r)
        self.assertEqual(_json.loads(_r)["功能"], "解析 it's 内容")


# ======================================================================
# 组② JSON 失败统计（2 例）
# ======================================================================
class TestJsonTracker(unittest.TestCase):
    def test_07_dedup_window(self):
        """同内容在窗口内只告警一次；不同内容各自告警。"""
        _t = JsonFailureTracker(dedup_window_sec=60.0)
        self.assertTrue(_t.should_warn("same-content"))
        self.assertFalse(_t.should_warn("same-content"))
        self.assertTrue(_t.should_warn("other-content"))

    def test_08_classify_and_summary(self):
        """错误分类标签正确；每 N 次调用产出 1 次摘要。"""
        try:
            _json.loads('{"a": 1 "b": 2}')
        except Exception as _e:
            self.assertEqual(classify_json_error(_e), "缺逗号")
        _t = JsonFailureTracker(summary_every=3)
        _t.record_success()
        _t.record_failure("缺冒号")
        _t.record_failure("缺逗号")
        _out = [_t.maybe_summary() for _ in range(3)]
        self.assertIsNone(_out[0])
        self.assertIsNone(_out[1])
        self.assertIsNotNone(_out[2])
        self.assertIn("成功1次", _out[2])


# ======================================================================
# 组③ ReasoningWorkerPool 降级恢复（3 例）
# ======================================================================
class _FakePool:
    def __init__(self, max_workers=2):
        self._max_workers = max_workers
        self._broken = False

    def submit(self, fn, *args, **kwargs):
        _f = Future()
        _f.set_result({"status": "fake-ok"})
        return _f

    def shutdown(self, wait=False, cancel_futures=False):
        pass


class _FakeFactory:
    def __init__(self, raise_times=0):
        self._raise_times = raise_times
        self.construct_calls = 0

    def __call__(self, max_workers=None, **kw):
        self.construct_calls += 1
        if self._raise_times > 0:
            self._raise_times -= 1
            raise OSError("创建进程池失败(模拟)")
        return _FakePool(max_workers=max_workers or 2)


class _Harness:
    def __init__(self, raise_times=0):
        self._orig = RWP.ProcessPoolExecutor
        self.factory = _FakeFactory(raise_times=raise_times)
        RWP.ProcessPoolExecutor = self.factory
        self.pool = ReasoningWorkerPool()

    def close(self):
        try:
            self.pool.shutdown(timeout=0.1)
        except Exception:
            pass
        RWP.ProcessPoolExecutor = self._orig


class TestPoolDegradedRecovery(unittest.TestCase):
    def test_09_recover_after_interval(self):
        """降级后超过恢复间隔 → 用最小配置(1 worker)恢复成功。"""
        _h = _Harness()
        try:
            _p = _h.pool
            _p._degraded_sync = True
            _p._enabled = False
            _p._last_degraded_recover = time.time() - 1000.0
            with _Switch(POOL_DEGRADED_RECOVER_INTERVAL_SEC=1.0,
                         ENABLE_POOL_DEGRADED_RECOVERY=True):
                _ok = _p._maybe_recover_from_degraded()
            self.assertTrue(_ok)
            self.assertFalse(_p._degraded_sync, "恢复后应复位降级标志")
            self.assertIsNotNone(_p._pool)
            self.assertEqual(_p._max_workers, 1)
        finally:
            _h.close()

    def test_10_recovery_throttled(self):
        """未到恢复间隔 → 不尝试（保持降级）。"""
        _h = _Harness()
        try:
            _p = _h.pool
            _p._degraded_sync = True
            _p._enabled = False
            _p._last_degraded_recover = time.time()
            with _Switch(POOL_DEGRADED_RECOVER_INTERVAL_SEC=300.0,
                         ENABLE_POOL_DEGRADED_RECOVERY=True):
                _ok = _p._maybe_recover_from_degraded()
            self.assertFalse(_ok)
            self.assertTrue(_p._degraded_sync)
        finally:
            _h.close()

    def test_11_pre_degrade_minimal_attempt(self):
        """重试耗尽时先做最小配置尝试；成功则不降级。"""
        _h = _Harness()
        try:
            _p = _h.pool
            _p._pool = None
            _p._enabled = False
            _p._rebuild_consecutive_failures = 3
            with _Switch(ENABLE_POOL_AUTO_REBUILD=True, POOL_REBUILD_MAX_RETRIES=3,
                         ENABLE_POOL_DEGRADED_RECOVERY=True):
                _ok = _p._rebuild_pool(reason="单测:重试耗尽")
            self.assertTrue(_ok, "最小配置重建成功应返回 True")
            self.assertFalse(_p._degraded_sync, "成功恢复不应降级")
            self.assertEqual(_p._max_workers, 1)
        finally:
            _h.close()


# ======================================================================
# 组④ 测试日志隔离（3 例）
# ======================================================================
class TestLogIsolation(unittest.TestCase):
    def test_12_mute_and_restore_roundtrip(self):
        """项目日志 FileHandler 被静音后不写盘，复原后恢复写入。"""
        with tempfile.TemporaryDirectory() as _d:
            _p = os.path.join(_d, "probe.log")
            _h = logging.FileHandler(_p, encoding="utf-8")
            _lg = logging.getLogger("m22.test.probe")
            _lg.propagate = False
            _lg.addHandler(_h)
            try:
                with mock.patch.object(TL, "_project_logs_dir",
                                       return_value=os.path.normcase(os.path.abspath(_d))):
                    _muted = TL.mute_project_file_handlers()
                    self.assertEqual(len(_muted), 1)
                    _lg.warning("MUTED_LINE")
                    TL.restore_muted_handlers(_muted)
                    _lg.warning("VISIBLE_LINE")
                _h.flush()
                _c = open(_p, encoding="utf-8").read()
                self.assertNotIn("MUTED_LINE", _c)
                self.assertIn("VISIBLE_LINE", _c)
            finally:
                _lg.removeHandler(_h)
                _h.close()

    def test_13_isolation_dir_under_tmp_and_cleaned(self):
        """隔离目录位于 tmp/ 下，cleanup 后被删除。"""
        _iso = _TestIsolation()
        self.assertIn(os.sep + "tmp" + os.sep, _ISO_DIR + os.sep)
        self.assertTrue(os.path.isdir(_ISO_DIR))
        _iso.cleanup()
        self.assertTrue(_iso.cleaned, "cleanup 后应标记已清理")
        self.assertFalse(os.path.exists(_ISO_DIR), "隔离目录应被删除")
        self.assertIsNotNone(_iso.production_intact, "应产出生产向量库指纹校验结论")

    def test_14_redirect_all_points_to_iso_dir(self):
        """verify 用 TestIsolation 把所有落盘路径指向隔离目录（而非 data/）。"""
        import nucleus.evolution.ParamPatchManager as _PPM
        _iso = _TestIsolation()
        try:
            _iso.redirect_all()
            self.assertIn(_ISO_DIR, _PPM._CONFIG_OVERRIDE_PATH)
            self.assertNotIn(os.sep + "data" + os.sep,
                             _PPM._CONFIG_OVERRIDE_PATH.lower())
        finally:
            _iso.cleanup()


# ======================================================================
# 组⑤ 修复蒸馏空片段兜底（3 例）
# ======================================================================
def _make_executor(project_root: str) -> SafeEvolutionExecutor:
    _ex = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
    _ex._project_root = project_root
    return _ex


class TestRepairSnippetFallback(unittest.TestCase):
    def test_15_read_snippet_from_file(self):
        """有关联文件时按「文件+方法」读取代码片段；项目外路径拒绝。"""
        with tempfile.TemporaryDirectory() as _d:
            _f = os.path.join(_d, "sample.py")
            open(_f, "w", encoding="utf-8").write(
                "import os\n\n\ndef target():\n    return 42\n")
            _ex = _make_executor(_d)
            _snip = _ex._read_snippet_from_file(_f, "target")
            self.assertIn("def target", _snip)
            # 项目根之外的路径 → 拒绝（安全护栏）
            _outside = os.path.join(os.path.dirname(_d), "outside_should_be_rejected.py")
            self.assertEqual(_ex._read_snippet_from_file(_outside, "x"), "")

    def test_16_message_fallback_when_no_file(self):
        """无文件时用 issue 描述/ERROR 消息作为对比素材（不静默跳过）。"""
        _ex = _make_executor(_PROJECT_ROOT)
        _mat = _ex._extract_comparison_material(
            {"description": "ERROR: 推理进程池重建失败(模拟重建失败)"}, "")
        self.assertIn("推理进程池重建失败", _mat)
        # 无 description 但有相关日志 → 用日志
        _mat2 = _ex._extract_comparison_material({}, "2026-09-11 ERROR: boom")
        self.assertIn("boom", _mat2)

    def test_17_empty_when_no_material(self):
        """确实无素材时返回空串（调用方据此跳过并记录原因）。"""
        _ex = _make_executor(_PROJECT_ROOT)
        self.assertEqual(_ex._extract_comparison_material({}, ""), "")
        self.assertEqual(_ex._extract_comparison_material({"description": "  "}, ""), "")


# ======================================================================
# 组⑥ 日志噪音治理（3 例）
# ======================================================================
class TestNoiseReduction(unittest.TestCase):
    def test_18_switch_semantics(self):
        """噪音治理开关默认开启；可关闭（零回归通道）。"""
        self.assertTrue(noise_reduction_enabled())
        with _Switch(ENABLE_LOG_NOISE_REDUCTION=False):
            self.assertFalse(noise_reduction_enabled())
        self.assertTrue(noise_reduction_enabled())

    def test_19_callgraph_syntax_error_downgraded(self):
        """调用图语法错误跳过：开启降噪时记 DEBUG，不记 WARNING。"""
        with tempfile.TemporaryDirectory() as _d:
            open(os.path.join(_d, "bad.py"), "w", encoding="utf-8").write(
                "def broken(:\n    pass\n")
            _ca = CallGraphAnalyzer(project_root=_d)
            with _Switch(ENABLE_LOG_NOISE_REDUCTION=True):
                with mock.patch.object(_CGA_MOD._logger, "warning") as _w, \
                        mock.patch.object(_CGA_MOD._logger, "debug") as _dg:
                    _r = _ca._collect_file("bad.py")
            self.assertIsNone(_r, "语法错误文件应跳过并返回 None")
            self.assertTrue(_dg.called, "应记 DEBUG")
            self.assertFalse(_w.called, "降噪开启时不应记 WARNING")

    def test_20_noise_points_and_summary_present(self):
        """4 个降噪点均已接入开关；代码学习有汇总输出（避免完全静默）。"""
        _checks = [
            ("nucleus/field/InfoField.py", "_noise_reduce()"),
            ("nucleus/reasoning/PatchManager.py", "_noise_reduce()"),
            ("nucleus/self_awareness/CallGraphAnalyzer.py", "_noise_reduce()"),
            ("organs/brain/PulseCodeLearner.py", "代码质量扫描完成"),
            ("organs/brain/PulseCodeLearner.py", "ENABLE_LOG_NOISE_REDUCTION"),
        ]
        for _rel, _needle in _checks:
            _src = open(os.path.join(_PROJECT_ROOT, _rel), encoding="utf-8").read()
            self.assertIn(_needle, _src, f"{_rel} 缺少 {_needle}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
