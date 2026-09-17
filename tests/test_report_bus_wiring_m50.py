# -*- coding: utf-8 -*-
"""第50批 T1 门控测试：P0-1 ReportBus 接入

覆盖：适配器发布 / 异常抽取 / 消费者触发 / 开关零回归 / 消费率可度量 /
     4 处生产接入点接线（源码级）。
"""
import io
import pytest
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config                                                       # noqa: E402
from nucleus.reporting import consumers as _cons                    # noqa: E402
from nucleus.reporting import publishers as _pub                    # noqa: E402
from nucleus.reporting.report_bus import (ReportBus,               # noqa: E402
                                          reset_report_bus)
from nucleus.reporting.report_envelope import SEV_P0, SEV_P1   # noqa: E402

_SRC = {}
for _rel in ("nucleus/self_awareness/SelfAwarenessEngine.py",
             "nucleus/self_awareness/DailyScheduler.py",
             "nucleus/diagnostics.py",
             "main.py"):
    _SRC[_rel] = io.open(os.path.join(_ROOT, _rel), encoding="utf-8",
                         errors="replace").read().replace("\r\n", "\n")


def _isolate_bus():
    """每个测试用**隔离总线**（沙箱目录）+ 屏蔽消费者落盘。

    ★第50批 P2-339 修复后，默认总线（生产 ``data/reports/``）在 pytest 下
    **会被写盘守卫拒写** → 为保留「发布即落盘」的语义，
    测试改用**显式注入**的沙箱目录（= 守卫放行）。
    """
    _sand = tempfile.mkdtemp(prefix="m50bus_")
    _cap = []
    _orig = _cons._append_jsonl
    _cons._append_jsonl = lambda rel, rec: (_cap.append((rel, rec)), True)[1]
    _bus = ReportBus(base_dir=os.path.join(_sand, "reports"))
    _cons.register_builtin_consumers(_bus)
    # ★发布器内部用 `get_report_bus()`（单例）→ 测试中打桩为**隔离总线**，
    #   否则会落到生产 ``data/reports/``（且 P2-339 修复后会被守卫拒写）。
    _orig_gb = _pub.get_report_bus
    _pub.get_report_bus = lambda *a, **kw: _bus
    return _bus, _cap, _orig, _sand, _orig_gb


class _Base(unittest.TestCase):
    def setUp(self):
        (self.bus, self.cap, self._orig, self._sand,
         self._orig_gb) = _isolate_bus()

    def tearDown(self):
        _pub.get_report_bus = self._orig_gb
        _cons._append_jsonl = self._orig
        shutil.rmtree(self._sand, ignore_errors=True)
        reset_report_bus()


class _CfgSwitch:
    def __init__(self, **kw):
        self._kw, self._old = kw, {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._old.items():
            if v is None:
                if hasattr(config, k):
                    delattr(config, k)
            else:
                setattr(config, k, v)
        return False


# ============================================================ 异常抽取
class TestAnomalyExtraction(unittest.TestCase):
    def test_10_health_critical_is_p0(self):
        a = _pub.health_anomalies({"overall_health": "critical", "issues": [1],
                                   "health_score": 10})
        self.assertTrue(any(x.type == "HEALTH_CRITICAL" and x.severity == SEV_P0
                            for x in a))

    def test_11_health_warning_is_p1(self):
        a = _pub.health_anomalies({"overall_health": "warning",
                                   "warnings": [1, 2, 3]})
        self.assertTrue(any(x.type == "HEALTH_WARNING" and x.severity == SEV_P1
                            for x in a))

    def test_12_health_score_low(self):
        a = _pub.health_anomalies({"overall_health": "healthy",
                                   "health_score": 30.0})
        self.assertTrue(any(x.type == "HEALTH_SCORE_LOW"
                            and x.metric_value == 30.0 for x in a))

    def test_13_health_healthy_no_alert(self):
        a = _pub.health_anomalies({"overall_health": "healthy",
                                   "health_score": 95.0})
        self.assertEqual([x for x in a if x.severity == SEV_P0], [])

    def test_14_pollution_above_threshold(self):
        a = _pub.pollution_anomalies({"pollution_rate": 0.75})
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0].suggested_action, "clean_data")

    def test_15_pollution_below_threshold(self):
        self.assertEqual(_pub.pollution_anomalies({"pollution_rate": 0.2}), [])

    def test_16_pollution_from_counts(self):
        """无 rate 时由 counts 推导。"""
        a = _pub.pollution_anomalies({"polluted": 800, "total": 1000})
        self.assertEqual(len(a), 1)
        self.assertAlmostEqual(a[0].metric_value, 0.8, places=3)

    def test_17_pollution_very_high_is_p0(self):
        a = _pub.pollution_anomalies({"pollution_rate": 0.95})
        self.assertEqual(a[0].severity, SEV_P0)

    def test_18_self_cognition_critical(self):
        a = _pub.self_cognition_anomalies({"health_level": "critical",
                                           "overall_score": 20.0})
        self.assertEqual(a[0].severity, SEV_P0)

    def test_19_non_dict_safe(self):
        for f in (_pub.health_anomalies, _pub.pollution_anomalies,
                  _pub.self_cognition_anomalies):
            self.assertEqual(f(None), [])
            self.assertEqual(f("x"), [])
            self.assertEqual(f(123), [])


# ============================================================ 发布 + 消费者
class TestPublishAndConsume(_Base):
    def test_20_health_p0_alert(self):
        r = _pub.publish_health({"overall_health": "critical",
                                 "issues": [1], "health_score": 5})
        self.assertIsNotNone(r)
        self.assertIn("health_anomaly_consumer", r["consumers"])
        self.assertTrue(any("alert:" in a for a in r["actions"]))
        self.assertTrue(any(x[1].get("needs_human") for x in self.cap))

    def test_21_pollution_clean_suggestion(self):
        r = _pub.publish_pollution({"total": 100, "polluted": 80,
                                    "pollution_rate": 0.8})
        self.assertIn("pollution_anomaly_consumer", r["consumers"])
        self.assertIn("clean_suggestion", r["actions"])
        _todo = [x for x in self.cap if x[1].get("kind") == "serp_clean_suggestion"]
        self.assertTrue(_todo)
        self.assertTrue(_todo[0][1]["needs_downtime"])
        self.assertFalse(_todo[0][1]["auto_executed"], "★消费者不得自动清洗")

    def test_22_low_pollution_no_alarm(self):
        r = _pub.publish_pollution({"total": 1000, "polluted": 10,
                                    "pollution_rate": 0.01})
        self.assertEqual(r["consumers"], [])
        self.assertEqual(self.cap, [])

    def test_23_self_cognition_published(self):
        r = _pub.publish_self_cognition("x" * 500,
                                        summary={"overall_score": 90.0,
                                                 "health_level": "healthy"})
        self.assertIsNotNone(r)
        self.assertEqual(r["report_type"], "self_cognition")
        self.assertTrue(r["persisted"])

    def test_24_self_cognition_retrievable_from_bus(self):
        r = _pub.publish_self_cognition("正文" * 50, summary={})
        env = self.bus.get(r["report_id"])
        self.assertIsNotNone(env, "★报告必须可从总线取回（不再『没人读』）")
        self.assertGreater(env.content.get("text_length"), 0)

    def test_25_patch_quality_zero_rate_is_p0(self):
        r = _pub.publish_patch_quality({"real_fix_rate": 0.0})
        self.assertEqual(r["max_severity"], SEV_P0)

    def test_26_data_quality_low(self):
        r = _pub.publish_data_quality({"score": 0.3})
        self.assertEqual(r["max_severity"], SEV_P1)

    def test_27_generic(self):
        r = _pub.publish_generic("runtime", "probe", content={"a": 1})
        self.assertEqual(r["report_type"], "runtime")

    def test_28_consumption_rate_measurable(self):
        """★P0-1 核心度量：消费率必须可算且 > 0。"""
        _pub.publish_health({"overall_health": "critical", "issues": [1]})
        _pub.publish_pollution({"pollution_rate": 0.9})
        _pub.publish_self_cognition("x")
        st = self.bus.get_stats()
        self.assertGreater(st["total"], 0)
        self.assertGreater(st["consumed"], 0)
        self.assertGreater(st["consumption_rate"], 0.0)


# ============================================================ 开关零回归
class TestSwitch(_Base):
    def test_30_switch_off_all_noop(self):
        with _CfgSwitch(ENABLE_REPORT_BUS=False):
            self.assertIsNone(_pub.publish_health({"overall_health": "critical"}))
            self.assertIsNone(_pub.publish_pollution({"pollution_rate": 0.9}))
            self.assertIsNone(_pub.publish_self_cognition("x"))
            self.assertIsNone(_pub.publish_patch_quality({"real_fix_rate": 0.0}))
            self.assertIsNone(_pub.publish_data_quality({"score": 0.1}))
            self.assertEqual(self.cap, [], "关闭时不得写入任何告警/待办")

    def test_31_switch_default_on(self):
        self.assertIs(getattr(config, "ENABLE_REPORT_BUS", None), True)

    def test_32_thresholds_configurable(self):
        for k in ("REPORT_BUS_HEALTH_SCORE_WARN", "REPORT_BUS_POLLUTION_WARN",
                  "REPORT_BUS_PATCH_FIX_RATE_WARN", "REPORT_BUS_DATA_QUALITY_WARN"):
            self.assertIsInstance(getattr(config, k, None), (int, float), k)

    def test_33_publish_never_raises_on_bad_input(self):
        """★硬性约束：发布不得抛异常。"""
        for _bad in (None, "x", 123, [], {"a": object()}):
            _pub.publish_health(_bad)
            _pub.publish_pollution(_bad)
            _pub.publish_self_cognition("", summary=_bad)
            _pub.publish_patch_quality(_bad)
            _pub.publish_data_quality(_bad)


# ============================================================ 生产接线
class TestWiring(_Base):
    def test_40_self_awareness_publishes(self):
        s = _SRC["nucleus/self_awareness/SelfAwarenessEngine.py"]
        i = s.find("def generate_report")
        self.assertGreater(i, 0)
        seg = s[i:i + 4000]
        self.assertIn("publish_self_cognition", seg,
                      "★generate_report 必须发布（P0-1 头号修复点）")

    def test_41_daily_scheduler_publishes_four(self):
        s = _SRC["nucleus/self_awareness/DailyScheduler.py"]
        for fn in ("publish_pollution", "publish_patch_quality",
                   "publish_data_quality", "publish_generic"):
            self.assertIn(fn, s, "run_once 应发布 %s" % fn)

    def test_42_diagnostics_publishes_health(self):
        s = _SRC["nucleus/diagnostics.py"]
        i = s.find("def get_health_summary")
        self.assertGreater(i, 0)
        self.assertIn("publish_health", s[i:i + 2500])

    def test_43_main_registers_consumers(self):
        s = _SRC["main.py"]
        self.assertIn("register_builtin_consumers", s,
                      "★启动时必须注册消费者（否则报告无人消费）")

    def test_44_at_least_three_production_call_sites(self):
        """★任务书要求 ≥2 处实际调用（本批 4 处）。"""
        n = 0
        for _rel, _s in _SRC.items():
            n += sum(_s.count(x) for x in
                     ("publish_health", "publish_pollution",
                      "publish_self_cognition", "publish_patch_quality",
                      "publish_data_quality", "publish_generic"))
        self.assertGreaterEqual(n, 4, "接入点不足")

    def test_45_publishers_exported(self):
        import nucleus.reporting as _r
        for n in ("publish_health", "publish_pollution",
                  "publish_self_cognition", "publishers",
                  "TYPE_HEALTH", "TYPE_POLLUTION", "TYPE_SELF_COGNITION"):
            self.assertTrue(hasattr(_r, n), n)

    def test_46_calls_are_guarded(self):
        """★每处发布调用都必须包在 try 中（失败不得影响既有链路）。"""
        s = _SRC["nucleus/self_awareness/DailyScheduler.py"]
        for fn in ("publish_pollution", "publish_patch_quality",
                   "publish_data_quality"):
            i = s.find(fn)
            self.assertGreater(i, 0)
            self.assertIn("try:", s[max(0, i - 700):i], "%s 未包 try" % fn)


# ============================================================ 契约
class TestContract(unittest.TestCase):
    def test_50_publishers_never_import_heavy_modules(self):
        """适配器不应在 import 期引入重型框架模块（可安全导入）。"""
        src = io.open(os.path.join(_ROOT, "nucleus/reporting/publishers.py"),
                      encoding="utf-8").read()
        top = src.split("__all__")[0]
        for bad in ("import main", "from main", "import config\n"):
            self.assertNotIn(bad, top)

    def test_51_no_bare_except_pass(self):
        import re
        src = io.open(os.path.join(_ROOT, "nucleus/reporting/publishers.py"),
                      encoding="utf-8").read()
        self.assertEqual(
            len(re.findall(r"except\s+[^\n:]+:\s*\n\s*pass\s*(\n|$)", src)), 0)


# ============================================================ P2-339 假防护
@pytest.mark.production_data
class TestWriteGuardNotBypassed(_Base):
    """★P2-339：守卫调用**参数名写错**曾被裸 ``except: pass`` 吞掉 → 假防护。

    原状：`guard_write(p, explicit_base_dir=True)`（无此关键字）→ TypeError
    → 被吞 → **守卫从未生效** → pytest 期间报告写入生产 `data/reports/`。
    """

    def test_60_no_wrong_keyword_in_source(self):
        """★AST 精确检测：不得把 ``explicit_base_dir`` 当**关键字**传给守卫。

        （用 AST 而非正则 —— 正则会误匹配修复说明注释里的同名文字。）
        """
        import ast
        for _rel in ("nucleus/reporting/report_bus.py",
                     "nucleus/reporting/consumers.py"):
            _s = io.open(os.path.join(_ROOT, _rel), encoding="utf-8").read()
            _bad = []
            for _n in ast.walk(ast.parse(_s)):
                if isinstance(_n, ast.Call):
                    for _kw in _n.keywords:
                        if _kw.arg == "explicit_base_dir":
                            _bad.append(_n.lineno)
            self.assertEqual(
                _bad, [], "%s 仍把 explicit_base_dir 当关键字传参 @%s" % (_rel, _bad))

    def test_61_guard_signature_matches_call(self):
        """守卫参数名必须是 `explicit`（调用方与签名一致）。"""
        import inspect
        from nucleus.data.write_guard import guard_write
        _p = inspect.signature(guard_write).parameters
        self.assertIn("explicit", _p)
        self.assertNotIn("explicit_base_dir", _p)
        try:
            guard_write("", explicit=False, component="t")
        except TypeError as _e:
            self.fail("参数名不匹配会抛 TypeError: %s" % _e)

    def test_62_default_bus_is_not_explicit(self):
        """默认总线（生产 data/reports）应判为**未显式注入**。"""
        _b = ReportBus()
        self.assertFalse(_b._explicit_base_dir)

    def test_63_injected_bus_is_explicit(self):
        self.assertTrue(self.bus._explicit_base_dir)

    def test_64_production_bus_withheld_under_pytest(self):
        """★核心：pytest 下默认总线不得写生产 `data/reports/`。"""
        _b = ReportBus()
        _r = _b.publish_simple("runtime", "guard_probe", content={"t": 1})
        self.assertFalse(_r["persisted"], "★pytest 下不得写入生产 data/reports/")
        self.assertIn("write_guard", _b._last_error)

    def test_65_injected_bus_persists(self):
        """零回归：显式注入的沙箱总线必须仍能落盘。"""
        _r = self.bus.publish_simple("runtime", "guard_probe", content={"t": 2})
        self.assertTrue(_r["persisted"])

    def test_66_consumers_respect_guard(self):
        """★consumers 落点固定为项目 data/reports → pytest 下应拒写。

        ★本测试必须用**真实**落盘函数（`_Base` 会打桩它）。
        """
        _real = self._orig
        _ap = os.path.join(_ROOT, "data", "reports", "alerts.jsonl")
        _before = os.path.getsize(_ap) if os.path.isfile(_ap) else 0
        _ok = _real("data/reports/alerts.jsonl", {"probe": True})
        _after = os.path.getsize(_ap) if os.path.isfile(_ap) else 0
        self.assertFalse(_ok, "★pytest 下不得追加生产 alerts.jsonl")
        self.assertEqual(_before, _after)

    def test_67_no_bare_except_pass_in_guard_calls(self):
        import re
        for _rel in ("nucleus/reporting/report_bus.py",
                     "nucleus/reporting/consumers.py"):
            _s = io.open(os.path.join(_ROOT, _rel), encoding="utf-8").read()
            self.assertEqual(
                len(re.findall(r"except\s+[^\n:]+:\s*\n\s*pass\s*(\n|$)", _s)),
                0, "%s 仍有裸 except: pass（会再次吞掉守卫异常）" % _rel)

    def test_68_guard_signature_accepts_real_component(self):
        """守卫可被真实组件名调用（签名与调用方契约一致）。"""
        from nucleus.data.write_guard import guard_write
        import tempfile
        _d = tempfile.mkdtemp(prefix="m50gw_")
        try:
            self.assertTrue(guard_write(os.path.join(_d, "x.json"),
                                        explicit=True, component="ReportBus"))
        finally:
            shutil.rmtree(_d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
