# -*- coding: utf-8 -*-
"""主线第38批 T1+T2 门控：Profile 综合评分/最严重问题 + evolution_health 维度。

覆盖：
  T1  overall_score 加权平均 / 排除 unavailable / <2 维度为 None /
      health_level 四档 / top_issues 排序与上限 / severity 归一化 /
      to_dict·from_dict·merge 同步 / 开关关闭零回归 / 报告段落
  T2  evolution_health 分数与 issues（成功率/回滚率/稳定性）/
      无记录 → unavailable / 开关关闭 / ★只读（不触发 verify/rollback）
"""
import os
import sys

# ★注意：sys.path.insert(...) 是调用语句（不关闭 ruff import 区）；
#   `_ROOT = ...` 这类赋值会关闭 → 其后所有 import 报 E402。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

import config
from nucleus.self_awareness.SelfAwarenessEngine import (
    SelfAwarenessEngine,
    SelfAwarenessProfile,
    _collect_top_issues,
    _health_level,
    _severity_rank,
    compute_overall_score,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Switch:
    def __init__(self, **kw):
        self.kw = kw
        self.old = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(config, k)
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(config, k, v)
        return False


def _mk(**dims):
    """构造画像：dims 形如 code_health={...}。"""
    p = SelfAwarenessProfile()
    for k, v in dims.items():
        setattr(p, k, v)
    return p


# ======================================================================
class TestOverallScore(unittest.TestCase):
    """T1：综合评分计算口径。"""

    def test_01_weighted_average_equal(self):
        p = _mk(code_health={"score": 80.0}, knowledge_health={"score": 60.0})
        r = compute_overall_score(p)
        self.assertEqual(r["overall_score"], 70.0)
        self.assertFalse(r["weighted"])

    def test_02_excludes_unavailable(self):
        p = _mk(code_health={"score": 90.0},
                knowledge_health={"score": 70.0},
                runtime_health={"status": "unavailable", "score": 0.0})
        r = compute_overall_score(p)
        self.assertEqual(r["overall_score"], 80.0)
        self.assertNotIn("runtime_health", r["available_dimensions"])

    def test_03_excludes_no_data_flag(self):
        p = _mk(code_health={"score": 88.0}, call_graph_health={"score": 72.0},
                knowledge_health={"no_data": True, "score": 0.0})
        r = compute_overall_score(p)
        self.assertEqual(r["overall_score"], 80.0)

    def test_04_less_than_two_dims_is_none(self):
        p = _mk(code_health={"score": 88.0})
        r = compute_overall_score(p)
        self.assertIsNone(r["overall_score"])
        self.assertEqual(r["available_dimensions"], ["code_health"])

    def test_05_empty_profile_is_none(self):
        self.assertIsNone(compute_overall_score(SelfAwarenessProfile())["overall_score"])

    def test_06_custom_weights(self):
        p = _mk(code_health={"score": 100.0}, knowledge_health={"score": 0.0})
        with _Switch(SELF_AWARENESS_DIMENSION_WEIGHTS={"code_health": 3.0,
                                                       "knowledge_health": 1.0}):
            r = compute_overall_score(p)
        self.assertEqual(r["overall_score"], 75.0)
        self.assertTrue(r["weighted"])

    def test_07_score_of_fallback_keys(self):
        p = _mk(code_health={"total_score": 64.0},
                knowledge_health={"health_score": 36.0})
        self.assertEqual(compute_overall_score(p)["overall_score"], 50.0)


class TestHealthLevel(unittest.TestCase):
    """T1：健康等级分级（任务书 §T1.3）。"""

    def test_10_boundaries(self):
        self.assertEqual(_health_level(80), "healthy")
        self.assertEqual(_health_level(100), "healthy")
        self.assertEqual(_health_level(79.99), "moderate")
        self.assertEqual(_health_level(60), "moderate")
        self.assertEqual(_health_level(59.99), "concerning")
        self.assertEqual(_health_level(40), "concerning")
        self.assertEqual(_health_level(39.99), "critical")
        self.assertEqual(_health_level(0), "critical")

    def test_11_none_is_unknown(self):
        self.assertEqual(_health_level(None), "unknown")
        self.assertEqual(_health_level("x"), "unknown")
        self.assertEqual(_health_level(True), "unknown")


class TestSeverityRank(unittest.TestCase):
    """T1：各维度 severity 口径归一化。"""

    def test_20_normalization(self):
        """★第39批 T4（P2-241）：rank 语义由 **3 档**细化为**统一 5 级**
        （critical=0 / high=1 / medium=2 / low=3 / info=4）。

        第38批此处按 3 档断言（high/severe 同组、info 归入 low）——
        T4 拆分出独立的 `critical` 与 `info` 后**必须同步**
        （断言守当前设计契约，见 skill §33.4）。
        """
        for s in ("critical", "fatal", "severe", "error", "blocker", "P0"):
            self.assertEqual(_severity_rank(s), 0, s)
        for s in ("high", "major", "P1"):
            self.assertEqual(_severity_rank(s), 1, s)
        for s in ("medium", "moderate", "warning", "warn", "P2"):
            self.assertEqual(_severity_rank(s), 2, s)
        for s in ("low", "minor", "trivial", "P3"):
            self.assertEqual(_severity_rank(s), 3, s)
        # 未知 / info 一律归最低档（保守：不夸大严重度）
        for s in ("info", "notice", "weird", "", None):
            self.assertEqual(_severity_rank(s), 4, s)


class TestTopIssues(unittest.TestCase):
    """T1：跨维度 issues 收集与排序。"""

    def test_30_sorted_by_severity(self):
        p = _mk(code_health={"by_severity": {"warning": 9}},
                knowledge_health={"top_issues": [
                    {"severity": "medium", "detail": "盲区 2 个"},
                    {"severity": "high", "detail": "冲突 3 处"}]},
                fake_loops={"critical": [{"module": "a", "class": "B",
                                          "score": 0}]})
        ti = _collect_top_issues(p, 5)
        self.assertEqual(ti[0]["severity"], "high")
        # 排序严格非降
        ranks = [_severity_rank(x["severity"]) for x in ti]
        self.assertEqual(ranks, sorted(ranks))

    def test_31_limit_and_fields(self):
        p = _mk(knowledge_health={"top_issues": [
            {"severity": "high", "detail": "d%d" % i} for i in range(10)]})
        ti = _collect_top_issues(p, 5)
        self.assertEqual(len(ti), 5)
        for x in ti:
            for k in ("dimension", "dimension_label", "severity",
                      "description", "suggestion"):
                self.assertIn(k, x)

    def test_32_fake_loop_severity_mapping(self):
        p = _mk(fake_loops={"critical": [
            {"module": "m1", "class": "C1", "score": 0},
            {"module": "m2", "class": "C2", "score": 60}]})
        ti = _collect_top_issues(p, 5)
        self.assertEqual(ti[0]["severity"], "high")     # score 0 → high
        self.assertEqual(ti[1]["severity"], "medium")   # score 60 → medium

    def test_33_empty_returns_empty(self):
        self.assertEqual(_collect_top_issues(SelfAwarenessProfile(), 5), [])


class TestProfileFieldSync(unittest.TestCase):
    """T1：新字段的序列化 / 合并 / 报告。"""

    def test_40_defaults(self):
        p = SelfAwarenessProfile()
        self.assertIsNone(p.overall_score)
        self.assertEqual(p.health_level, "unknown")
        self.assertEqual(p.top_issues, [])

    def test_41_roundtrip(self):
        p = SelfAwarenessProfile()
        p.overall_score = 73.5
        p.health_level = "moderate"
        p.top_issues = [{"dimension": "code_health", "severity": "high",
                         "description": "x", "suggestion": "y"}]
        q = SelfAwarenessProfile.from_dict(p.to_dict())
        self.assertEqual(q.overall_score, 73.5)
        self.assertEqual(q.health_level, "moderate")
        self.assertEqual(len(q.top_issues), 1)
        self.assertEqual(q.top_issues[0]["severity"], "high")

    def test_42_from_dict_tolerant(self):
        q = SelfAwarenessProfile.from_dict({"overall_score": "bad",
                                           "health_level": None,
                                           "top_issues": "nope"})
        self.assertIsNone(q.overall_score)
        self.assertEqual(q.health_level, "unknown")
        self.assertEqual(q.top_issues, [])

    def test_43_merge_semantics(self):
        a = SelfAwarenessProfile()
        b = SelfAwarenessProfile()
        b.overall_score = 66.0
        b.health_level = "moderate"
        b.top_issues = [{"severity": "high", "description": "z"}]
        a.merge(b)
        self.assertEqual(a.overall_score, 66.0)
        self.assertEqual(a.health_level, "moderate")
        self.assertEqual(len(a.top_issues), 1)
        # 空值不覆盖
        c = SelfAwarenessProfile()
        a.merge(c)
        self.assertEqual(a.overall_score, 66.0)

    def test_44_run_all_applies_overall(self):
        e = SelfAwarenessEngine()
        e.register_analyzer("a", lambda _x: {"score": 90.0}, "code_health")
        e.register_analyzer("b", lambda _x: {"score": 70.0}, "knowledge_health")
        p = e.run_all_analyses()
        self.assertEqual(p.overall_score, 80.0)
        self.assertEqual(p.health_level, "healthy")
        # 两个维度均未产出 issues → 无「最严重问题」（空列表是正确结果）
        self.assertEqual(p.top_issues, [])

    def test_44b_run_all_collects_issues(self):
        e = SelfAwarenessEngine()
        e.register_analyzer(
            "a", lambda _x: {"score": 60.0,
                             "issues": [{"severity": "high", "description": "x"}]},
            "code_health")
        e.register_analyzer("b", lambda _x: {"score": 70.0}, "knowledge_health")
        p = e.run_all_analyses()
        self.assertEqual(p.overall_score, 65.0)
        self.assertEqual(len(p.top_issues), 1)
        self.assertEqual(p.top_issues[0]["severity"], "high")

    def test_45_switch_off_zero_change(self):
        with _Switch(ENABLE_SELF_AWARENESS_OVERALL_SCORE=False):
            e = SelfAwarenessEngine()
            e.register_analyzer("a", lambda _x: {"score": 90.0}, "code_health")
            e.register_analyzer("b", lambda _x: {"score": 70.0}, "knowledge_health")
            p = e.run_all_analyses()
        self.assertIsNone(p.overall_score)
        self.assertEqual(p.health_level, "unknown")
        self.assertEqual(p.top_issues, [])

    def test_46_report_section(self):
        p = _mk(code_health={"score": 95.0}, knowledge_health={"score": 45.0})
        from nucleus.self_awareness.SelfAwarenessEngine import _apply_overall
        _apply_overall(p)
        txt = "\n".join(SelfAwarenessEngine._report_overall_section(p))
        self.assertIn("【综合评分】70.00 / 100", txt)
        self.assertIn("中等", txt)
        self.assertIn("【最严重问题】", txt)

    def test_47_report_section_unavailable(self):
        txt = "\n".join(
            SelfAwarenessEngine._report_overall_section(SelfAwarenessProfile()))
        self.assertIn("不可用", txt)
        self.assertIn("【最严重问题】无", txt)


# ======================================================================
class _FakePatchManager:
    def __init__(self, history):
        self._h = history

    def load_json(self, path, default):
        return self._h

    def get_history_file(self):
        return "<mem>"


class _FakeExecutor:
    """★只读接口假件；调用任何「会改状态」的方法即断言失败。"""

    def __init__(self, applied=4, verified=2, rolled=1, pending=1,
                 crash=0, history=None):
        self._patches = applied
        self._crash = crash
        if history is None:
            history = []
            for _ in range(verified):
                history.append({"applied": True, "runtime_verified": True,
                                "effectiveness": 1.0})
            for _ in range(rolled):
                history.append({"applied": True, "rolled_back": True,
                                "effectiveness": -1.0})
            for _ in range(pending):
                history.append({"applied": True, "needs_runtime_verify": True})
        self._patch_manager = _FakePatchManager(history)

    def get_stats(self):
        return {"total_patches_generated": self._patches}

    def get_crash_stats(self):
        return {"total": self._crash, "repair": {"count": self._crash}}

    def get_strategy_learning_stats(self):
        return {"status": "ok", "strategies": {"s1": {}, "s2": {}}}

    def verify_applied_patches(self):       # pragma: no cover
        raise AssertionError("integrate_evolution_health 不得调用会改状态的接口")

    def rollback_patch(self, *a, **k):      # pragma: no cover
        raise AssertionError("不得回滚")

    def _auto_apply_patch(self, *a, **k):   # pragma: no cover
        raise AssertionError("不得应用补丁")


class TestEvolutionHealth(unittest.TestCase):
    """T2：evolution_health 维度。"""

    def test_50_score_and_issues(self):
        e = SelfAwarenessEngine()
        r = e.integrate_evolution_health(executor=_FakeExecutor())
        self.assertEqual(r["status"], "ok")
        # 成功率 2/4=50% ; 回滚率 1/4=25% ; 崩溃 0 → 稳定性 100
        self.assertEqual(r["success_rate"], 50.0)
        self.assertEqual(r["rollback_rate"], 25.0)
        self.assertEqual(r["stability"], 100.0)
        # 50*0.4 + 75*0.3 + 100*0.3 = 20 + 22.5 + 30 = 72.5
        self.assertEqual(r["score"], 72.5)
        kinds = {i["type"] for i in r["issues"]}
        self.assertIn("low_success_rate", kinds)
        self.assertIn("high_rollback_rate", kinds)

    def test_51_healthy_no_high_issues(self):
        e = SelfAwarenessEngine()
        r = e.integrate_evolution_health(
            executor=_FakeExecutor(applied=10, verified=9, rolled=0, pending=1))
        self.assertEqual(r["status"], "ok")
        self.assertNotIn("high",
                         [i["severity"] for i in r["issues"]])
        self.assertGreater(r["score"], 80.0)

    def test_52_crash_issue(self):
        e = SelfAwarenessEngine()
        r = e.integrate_evolution_health(
            executor=_FakeExecutor(applied=4, verified=4, rolled=0, pending=0,
                                   crash=1))
        kinds = {i["type"] for i in r["issues"]}
        self.assertIn("evolution_crash", kinds)

    def test_53_no_record_unavailable(self):
        e = SelfAwarenessEngine()
        r = e.integrate_evolution_health(
            executor=_FakeExecutor(applied=0, history=[]))
        self.assertEqual(r["status"], "unavailable")
        self.assertIsNone(r["score"])
        self.assertEqual(r["issues"][0]["type"], "no_record")
        self.assertEqual(r["issues"][0]["severity"], "info")

    def test_54_switch_off(self):
        with _Switch(ENABLE_EVOLUTION_HEALTH_INTEGRATION=False):
            e = SelfAwarenessEngine()
            self.assertEqual(
                e.integrate_evolution_health(executor=_FakeExecutor()), {})

    def test_55_readonly_no_state_changing_calls(self):
        """★只读红线：假件对 verify/rollback/apply 均抛断言，能跑通即证明未调用。"""
        e = SelfAwarenessEngine()
        ex = _FakeExecutor()
        r = e.integrate_evolution_health(executor=ex)
        self.assertEqual(r["status"], "ok")

    def test_56_none_executor_singleton_unavailable(self):
        """单例不可用（返回 None）→ 空 dict，且不抛异常。"""
        from unittest import mock
        e = SelfAwarenessEngine()
        with mock.patch(
                "nucleus.reasoning.SafeEvolutionExecutor"
                ".get_safe_evolution_executor", return_value=None):
            self.assertEqual(e.integrate_evolution_health(executor=None), {})

    def test_57_unavailable_excluded_from_overall(self):
        """无记录的 evolution_health 带 status=unavailable → 不参与综合评分。"""
        p = SelfAwarenessProfile()
        p.code_health = {"score": 90.0}
        p.knowledge_health = {"score": 70.0}
        p.evolution_health = {"status": "unavailable", "score": None}
        r = compute_overall_score(p)
        self.assertEqual(r["overall_score"], 80.0)
        self.assertNotIn("evolution_health", r["available_dimensions"])

    def test_58_registered_in_main(self):
        """★T2 接线：main.py 必须注册 evolution_health 分析器（生产调用点）。"""
        with open(os.path.join(_ROOT, "main.py"), encoding="utf-8") as f:
            _src = f.read()
        self.assertIn('"evolution_health", lambda _e: _e.integrate_evolution_health()',
                      _src.replace("\r\n", "\n"))


if __name__ == "__main__":
    unittest.main()
