# -*- coding: utf-8 -*-
"""
test_evolution_health_m58.py —— 主线第58批 T1（P1）门控单测

自主进化闭环修复：
  1) SafeEvolutionExecutor.__init__ 现在把 _project_root 提升为实例变量
     （修复 repair_with_distillation 内 self._project_root 的 AttributeError，
      该错误曾导致自主进化闭环断裂）。
  2) 新增 _log_evolution_health 方法，每轮输出 发现数/修复数/修复率/失败原因分布，
     便于持续监控闭环健康；任何异常都降级为 DEBUG，绝不阻断主流程。
  3) repair_with_distillation 在 record_evolution_round 埋点前已接线
     self._log_evolution_health(...)。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor, _module_logger  # noqa: E402

_SEE_MODULE = "nucleus.reasoning.SafeEvolutionExecutor"
# 与 SafeEvolutionExecutor.__init__ 中一致的项目根计算方式
_EXPECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(os.path.join(_PROJECT_ROOT, "nucleus", "reasoning",
                                 "SafeEvolutionExecutor.py")))))


def _src():
    with open(os.path.join(_PROJECT_ROOT, "nucleus", "reasoning",
                           "SafeEvolutionExecutor.py"), encoding="utf-8") as f:
        return f.read()


class TestProjectRootInstanceVar(unittest.TestCase):
    """核心修复：__init__ 必须把 _project_root 存为 self._project_root。"""

    def test_project_root_set_after_init(self):
        exe = SafeEvolutionExecutor()
        self.assertTrue(hasattr(exe, "_project_root"),
                        "修复后 __init__ 必须设置 self._project_root")
        self.assertIsInstance(exe._project_root, str)
        self.assertTrue(exe._project_root)
        # 必须等于项目根
        self.assertEqual(exe._project_root, _EXPECT_ROOT)

    def test_project_root_attr_present_in_source(self):
        # 源码层面确认 __init__ 有 self._project_root = 赋值（而非局部变量）
        src = _src()
        self.assertIn("self._project_root = os.path.dirname", src,
                      "__init__ 必须将 _project_root 提升为实例变量")


class TestLogEvolutionHealth(unittest.TestCase):
    """新增的闭环健康度监控方法。"""

    def _make(self):
        # 轻量构造，避免触发完整 __init__ 的重逻辑
        exe = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        return exe

    def test_reports_rate_with_distribution(self):
        exe = self._make()
        with self.assertLogs(_module_logger.name, level="INFO") as _log:
            exe._log_evolution_health(
                4, 2, {"skip_reasons": {"无可用补丁": 2, "跳过": 1}})
        _joined = "\n".join(_log.output)
        self.assertIn("[进化健康]", _joined)
        self.assertIn("发现=4", _joined)
        self.assertIn("修复=2", _joined)
        self.assertIn("修复率=50.0%", _joined)
        self.assertIn("失败分布=无可用补丁=2", _joined)

    def test_zero_found_no_division(self):
        exe = self._make()
        with self.assertLogs(_module_logger.name, level="INFO") as _log:
            exe._log_evolution_health(0, 0, {})
        _joined = "\n".join(_log.output)
        self.assertIn("修复率=0.0%", _joined)

    def test_none_extra_is_safe(self):
        # extra 为 None / 缺键 时不应抛异常（降级为 DEBUG 日志）
        exe = self._make()
        try:
            exe._log_evolution_health(3, 1, None)
        except Exception as _e:  # pragma: no cover
            self.fail("extra=None 时不应抛异常: %r" % _e)

    def test_bad_skip_reasons_shape_is_safe(self):
        # skip_reasons 为非 dict（如 list）时，失败分布应为空，不报错
        exe = self._make()
        try:
            exe._log_evolution_health(2, 1, {"skip_reasons": ["a", "b"]})
        except Exception as _e:  # pragma: no cover
            self.fail("skip_reasons 非 dict 时不应抛异常: %r" % _e)

    def test_string_found_coerced(self):
        # found 传入字符串也应安全（int(found or 0)）
        exe = self._make()
        with self.assertLogs(_module_logger.name, level="INFO") as _log:
            exe._log_evolution_health("4", 2, {})
        self.assertIn("修复率=50.0%", "\n".join(_log.output))


class TestHealthWiredIntoRepair(unittest.TestCase):
    """repair_with_distillation 已在其修复率埋点前接线健康度日志。"""

    def test_health_call_wired(self):
        src = _src()
        self.assertIn(
            "self._log_evolution_health(len(_sorted_issues[:_process_count]), _repaired,",
            src, "repair_with_distillation 必须接线 _log_evolution_health")
        # 接线点必须位于 record_evolution_round 埋点之前
        _health = src.find("self._log_evolution_health(len(_sorted_issues[:_process_count]), _repaired,")
        _record = src.find("record_evolution_round(")
        self.assertGreater(_health, 0)
        self.assertGreater(_record, _health,
                           "健康度日志必须早于 record_evolution_round 埋点")

    def test_health_call_references_result_reasons(self):
        src = _src()
        self.assertIn('"skip_reasons": _result_reasons', src,
                      "健康度调用应传入本轮失败原因分布 _result_reasons")


if __name__ == "__main__":
    unittest.main(verbosity=2)
