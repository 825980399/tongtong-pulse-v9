# -*- coding: utf-8 -*-
"""
test_self_awareness_m20.py —— 主线第20批 门控单测（PHASE18 阶段二：EventTap 运行时整合）

覆盖：
  1. EventTap 整合接口 integrate_event_tap（5 例）
  2. 器官活跃度排名 analyze_organ_activity（4 例）
  3. 报告增强（动静结合段落）（3 例）
  4. 脚本开关 --include/--no-include/--reset/--event-tap-window（3 例）
  5. Profile 新字段序列化往返（附加 2 例）

隔离：全部产物落 tmp/_sa20_tests/，不触碰 data/；EventTap 全部用 mock，不依赖真实事件总线。
"""

import contextlib
import io
import json
import os
import shutil
import sys
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from nucleus.self_awareness.SelfAwarenessEngine import (  # noqa: E402
    SelfAwarenessEngine,
    SelfAwarenessProfile,
)

_SCRATCH = os.path.join(_PROJECT_ROOT, "tmp", "_sa20_tests")
_SCRIPT_DIR = os.path.join(_PROJECT_ROOT, "tools")


class _Switch:
    """临时改 config 开关（退出恢复原值）。"""

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


_DEFAULT_STATS = {
    "enabled": True,
    "started": True,
    "total": 1000,
    "distinct_names": 4,
    "by_name": {
        "cortex.dialog.start": 500,
        "liver.memory.write": 300,
        "stomach.digest.end": 150,
        "rare.event": 50,
    },
    "by_source": {
        "PulseCortex": 500,
        "PulseLiver": 300,
        "PulseStomach": 150,
        "PulseRare": 45,
        "PulseSkin": 5,
    },
    "by_priority": {"LOW": 900, "NORMAL": 100},
    "interval": {"min": 0.01, "max": 12.5, "avg": 1.2, "samples": 999},
    "recent_size": 100,
}


class _MockTap:
    """EventTap 替身（只实现本批用到的两个接口）。"""

    def __init__(self, stats=None, raise_on_stats=False):
        self._stats = dict(stats) if stats is not None else dict(_DEFAULT_STATS)
        self._raise = raise_on_stats
        self.reset_called = 0

    def get_stats(self):
        if self._raise:
            raise RuntimeError("模拟 get_stats 失败")
        return dict(self._stats)

    def reset_stats(self):
        self.reset_called += 1
        self._stats = {k: (0 if isinstance(v, int) else {})
                       for k, v in self._stats.items()}


# ======================================================================
# 组 1：EventTap 整合接口（5 例）
# ======================================================================
class TestIntegrateEventTap(unittest.TestCase):
    def setUp(self):
        self.e = SelfAwarenessEngine()

    def test_config_default_enabled(self):
        self.assertIs(getattr(config, "ENABLE_EVENT_TAP_INTEGRATION", None), True)

    def test_normal_integration_fields(self):
        """正常整合：任务书要求的字段必须齐全且口径正确。"""
        r = self.e.integrate_event_tap(_MockTap())
        for _k in ("total_events", "distinct_event_names", "active_sources",
                   "top_event_names", "top_sources", "by_priority", "interval",
                   "tap_enabled", "tap_started"):
            self.assertIn(_k, r, "缺少字段 {}".format(_k))
        self.assertEqual(r["total_events"], 1000)
        self.assertEqual(r["distinct_event_names"], 4)
        self.assertEqual(r["active_sources"], 5)
        # Top 排序：降序
        self.assertEqual(r["top_event_names"][0]["name"], "cortex.dialog.start")
        self.assertEqual(r["top_sources"][0]["source"], "PulseCortex")
        self.assertEqual(r["interval"]["samples"], 999)
        self.assertTrue(r["tap_enabled"] and r["tap_started"])

    def test_tap_disabled_still_returns_shape(self):
        """EventTap 未启用（enabled=False）→ 仍返回结构，tap_enabled=False。"""
        _stats = dict(_DEFAULT_STATS)
        _stats.update({"enabled": False, "started": False, "total": 0,
                       "distinct_names": 0, "by_name": {}, "by_source": {},
                       "by_priority": {}})
        r = self.e.integrate_event_tap(_MockTap(_stats))
        self.assertFalse(r["tap_enabled"])
        self.assertFalse(r["tap_started"])
        self.assertEqual(r["total_events"], 0)
        self.assertEqual(r["top_sources"], [])

    def test_stats_exception_returns_empty(self):
        """取数异常 → 空 dict（不抛出）。"""
        self.assertEqual(self.e.integrate_event_tap(_MockTap(raise_on_stats=True)), {})

    def test_switch_off_returns_disabled(self):
        """开关关闭 → {'disabled': True}。"""
        with _Switch(ENABLE_EVENT_TAP_INTEGRATION=False):
            self.assertEqual(self.e.integrate_event_tap(_MockTap()),
                             {"disabled": True})

    def test_empty_stats(self):
        """空统计（total=0 且无分布）→ 数值归零、列表为空。"""
        _stats = {"enabled": True, "started": True, "total": 0,
                  "distinct_names": 0, "by_name": {}, "by_source": {},
                  "by_priority": {}, "interval": {"min": None, "max": 0,
                                                  "avg": 0, "samples": 0}}
        r = self.e.integrate_event_tap(_MockTap(_stats))
        self.assertEqual(r["total_events"], 0)
        self.assertEqual(r["active_sources"], 0)
        self.assertEqual(r["top_event_names"], [])
        self.assertIsNone(r["interval"]["min"])


# ======================================================================
# 组 2：器官活跃度排名（4 例）
# ======================================================================
class TestOrganActivity(unittest.TestCase):
    def setUp(self):
        self.e = SelfAwarenessEngine()

    def test_ranking_sorted_with_percentage(self):
        r = self.e.analyze_organ_activity({"by_source": _DEFAULT_STATS["by_source"]})
        self.assertEqual(r["total_organs"], 5)
        self.assertEqual(r["total_events"], 1000)
        self.assertEqual(r["ranking"][0]["organ"], "PulseCortex")
        self.assertEqual(r["ranking"][0]["percentage"], 50.0)
        # 降序
        _counts = [x["count"] for x in r["ranking"]]
        self.assertEqual(_counts, sorted(_counts, reverse=True))
        self.assertEqual(r["concentration_ratio"], 0.5)

    def test_silent_organs_detected(self):
        """占比 < 1% 判为沉默器官（PulseSkin 5/1000=0.5%）。"""
        r = self.e.analyze_organ_activity({"by_source": _DEFAULT_STATS["by_source"]})
        _silent = [x["organ"] for x in r["silent_organs"]]
        self.assertIn("PulseSkin", _silent)
        self.assertNotIn("PulseCortex", _silent)

    def test_overactive_organs_detected(self):
        """占比 > 20% 判为过热器官。"""
        r = self.e.analyze_organ_activity({"by_source": _DEFAULT_STATS["by_source"]})
        _over = [x["organ"] for x in r["overactive_organs"]]
        self.assertIn("PulseCortex", _over)     # 50%
        self.assertIn("PulseLiver", _over)      # 30%
        self.assertNotIn("PulseStomach", _over)  # 15%

    def test_empty_and_missing_source(self):
        for _bad in ({}, None, {"no_by_source": 1}, {"by_source": {}}):
            r = self.e.analyze_organ_activity(_bad)
            self.assertTrue(r.get("no_data"), "输入 {!r} 应判 no_data".format(_bad))

    def test_thresholds_configurable(self):
        r = self.e.analyze_organ_activity(
            {"by_source": _DEFAULT_STATS["by_source"]},
            silent_threshold=0.10, overactive_threshold=0.40)
        self.assertEqual([x["organ"] for x in r["overactive_organs"]],
                         ["PulseCortex"])
        # 150/1000=15% > 10% 不算沉默；仅 PulseRare(4.5%) 与 PulseSkin(0.5%)
        self.assertEqual(len(r["silent_organs"]), 2)

    def test_top_sources_list_fallback(self):
        """兼容 T1 输出（top_sources list）作为输入。"""
        _rt = self.e.integrate_event_tap(_MockTap())
        r = self.e.analyze_organ_activity(_rt)
        self.assertFalse(r.get("no_data"))
        self.assertEqual(r["total_organs"], 5)


# ======================================================================
# 组 3：报告增强（3 例）
# ======================================================================
class TestReportSections(unittest.TestCase):
    def setUp(self):
        self.e = SelfAwarenessEngine()

    def _profile_with_runtime(self):
        _rt = self.e.integrate_event_tap(_MockTap())
        _oa = self.e.analyze_organ_activity(_rt)
        _p = SelfAwarenessProfile(timestamp="2026-09-11T15:00:00")
        _p.runtime_events = _rt
        _p.organ_activity = _oa
        _p.summary = "第20批测试"
        self.e.set_profile(_p)
        return self.e.generate_report()

    def test_report_has_runtime_section(self):
        _rep = self._profile_with_runtime()
        self.assertIn("【运行时事件统计】", _rep)
        self.assertIn("总事件数: 1000", _rep)
        self.assertIn("Top5 事件名", _rep)
        self.assertIn("Top5 来源器官", _rep)

    def test_report_has_organ_activity_section(self):
        _rep = self._profile_with_runtime()
        self.assertIn("【器官活跃度分析】", _rep)
        self.assertIn("参与器官: 5 个", _rep)
        self.assertIn("沉默器官", _rep)
        self.assertIn("集中度", _rep)

    def test_report_no_data_message(self):
        """无数据时显示提示，不报错。"""
        _sec = SelfAwarenessEngine._report_runtime_sections(SelfAwarenessProfile())
        _joined = "\n".join(_sec)
        self.assertIn("运行时数据不可用", _joined)
        # disabled 也走同一提示
        _p = SelfAwarenessProfile()
        _p.runtime_events = {"disabled": True}
        self.assertIn("运行时数据不可用",
                      "\n".join(SelfAwarenessEngine._report_runtime_sections(_p)))

    def test_existing_sections_not_removed(self):
        """红线：不得删除现有报告段落。"""
        _rep = self._profile_with_runtime()
        self.assertIn("曈曈 PulseNet · 自我认知画像报告", _rep)
        self.assertIn("【总结】", _rep)
        self.assertIn("—— 报告结束 ——", _rep)


# ======================================================================
# 组 4：脚本开关（3 例）
# ======================================================================
class TestScriptSwitches(unittest.TestCase):
    def _run(self, argv, tap):
        """在进程内跑脚本 main()，patch EventTap 单例和 run_all_analyses，返回 (rc, stdout)。

        ★P2-243优化：mock run_all_analyses 返回空profile，跳过全量代码扫描（原单类191s→<1s）。
        仍验证：命令行开关解析、EventTap重置、分析器注册列表、报告生成落盘。
        """
        _buf = io.StringIO()
        if _SCRIPT_DIR not in sys.path:
            sys.path.insert(0, _SCRIPT_DIR)
        import run_self_awareness_analysis as _script
        _empty_profile = SelfAwarenessProfile()
        with mock.patch("nucleus.events.EventTap.get_event_tap",
                        return_value=tap), \
                mock.patch.object(SelfAwarenessEngine, "run_all_analyses",
                                  return_value=_empty_profile), \
                contextlib.redirect_stdout(_buf):
            _rc = _script.main(argv)
        return _rc, _buf.getvalue()

    def _base_argv(self, extra):
        return (["--skip-code-review", "--out-dir", os.path.join("tmp", "_sa20_tests"), *list(extra)])

    def test_include_event_tap_off(self):
        """--no-include-event-tap → 不注册 event_tap 分析器。"""
        _rc, _out = self._run(self._base_argv(["--no-include-event-tap"]),
                              _MockTap())
        self.assertEqual(_rc, 0)
        _line = [x for x in _out.splitlines() if "已注册分析器" in x]
        self.assertTrue(_line)
        self.assertNotIn("event_tap", _line[0])

    def test_reset_event_tap(self):
        """--reset-event-tap → 调 reset_stats 并打印 WARNING。"""
        _tap = _MockTap()
        _rc, _out = self._run(self._base_argv(["--reset-event-tap"]), _tap)
        self.assertEqual(_rc, 0)
        self.assertEqual(_tap.reset_called, 1, "必须恰好重置一次")
        self.assertIn("将重置 EventTap 统计", _out)
        self.assertIn("统计已重置", _out)

    def test_event_tap_window_reserved(self):
        """--event-tap-window N（>0）→ 提示未实现，但仍正常完成。"""
        _rc, _out = self._run(self._base_argv(["--event-tap-window", "60"]),
                              _MockTap())
        self.assertEqual(_rc, 0)
        self.assertIn("时间窗口过滤暂未实现", _out)
        # window=0 不应出现提示
        _rc2, _out2 = self._run(self._base_argv(["--event-tap-window", "0"]),
                                _MockTap())
        self.assertEqual(_rc2, 0)
        self.assertNotIn("时间窗口过滤暂未实现", _out2)


# ======================================================================
# 组 5：Profile 新字段序列化（附加 2 例）
# ======================================================================
class TestProfileFields(unittest.TestCase):
    def test_to_dict_contains_new_fields(self):
        _p = SelfAwarenessProfile()
        _d = _p.to_dict()
        self.assertIn("runtime_events", _d)
        self.assertIn("organ_activity", _d)

    def test_roundtrip_preserves_new_fields(self):
        _p = SelfAwarenessProfile()
        _p.runtime_events = {"total_events": 7}
        _p.organ_activity = {"no_data": False, "total_organs": 2}
        _d = _p.to_dict()
        _p2 = SelfAwarenessProfile.from_dict(_d)
        self.assertEqual(_p2.runtime_events, {"total_events": 7})
        self.assertEqual(_p2.organ_activity, {"no_data": False, "total_organs": 2})
        # 旧版 JSON（缺字段）仍可解析
        _old = {"timestamp": "x", "code_health": {}}
        self.assertEqual(SelfAwarenessProfile.from_dict(_old).runtime_events, {})
        # 落盘-读回（JSON 顶层可见）
        _path = os.path.join(_SCRATCH, "profile_roundtrip.json")
        os.makedirs(_SCRATCH, exist_ok=True)
        with open(_path, "w", encoding="utf-8") as f:
            json.dump(_d, f, ensure_ascii=False)
        with open(_path, encoding="utf-8") as f:
            _back = json.load(f)
        self.assertIn("runtime_events", _back)
        self.assertIn("organ_activity", _back)


def teardown_module():
    # ★第22批 T3：pytest 只识别下划线命名模块级夹具，驼峰 tearDownModule
    #   不会被调用 → 临时目录清理长期失效并污染 tmp/ 与全库 F 口径。
    """★模块级收尾：删除 scratch 目录（保证 tmp/ 干净）。"""
    shutil.rmtree(_SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
