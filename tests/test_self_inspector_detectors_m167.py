# -*- coding: utf-8 -*-
"""第167批 C2｜T-检测器-1 偏差交付验证测试

★背景（T0偏差核实）：
  任务书 C2 落点称 `nucleus/review/CodeReviewEngine.py`「注册键缺 sql_injection／
  cross_module_singleton_call（现 0 命中）」并要求在 CodeReviewEngine 补两检测器。
  实测：该两检测器早在 commit 8d9b95f（第102~116批）即已实现于
  `nucleus/self_inspector.py`：
    - `_check_sql_injection`        (self_inspector.py:4020)
    - `_check_cross_module_singleton_call` (self_inspector.py:4083)
  并已接入全量检测分发循环（self_inspector.py:3599 / :3603），元数据注册于 :2679 / :2694。
  CodeReviewEngine 仅是 ruff/pyright 包装层，非模式检测器引擎；在此重复实现会制造
  死代码/重复检测器，违背项目「禁止重复/死代码」铁律。故 C2 实质（两检测器存在且可用）
  早已落地，本批不再重复实现，仅补「正/反/豁免三例」验证测试作为交付证据。

本测试直接驱动 self_inspector 既有检测器，覆盖正例必报 / 反例必忽略 / 豁免例必忽略。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.self_inspector import SelfInspector  # noqa: E402


class TestSqlInjectionDetector(unittest.TestCase):
    """验证既有 sql_injection 检测器（self_inspector.py:4020）。"""

    def setUp(self):
        self.si = SelfInspector()
        self.fp = "demo_module.py"

    def _run(self, body):
        issues = []
        self.si._check_sql_injection(body, self.fp, "Demo", "demo_method", 1, issues)
        return issues

    def test_positive_fstring_interpolation(self):
        """正例：f-string 把变量拼进 SQL ⇒ 必报 1 条 sql_injection。"""
        body = (
            "def query(uid):\n"
            "    sql = f\"SELECT * FROM users WHERE id = {uid}\"\n"
            "    return db.execute(sql)\n"
        )
        issues = self._run(body)
        self.assertEqual(len(issues), 1, "f-string SQL 拼接应被检出")
        self.assertEqual(issues[0]["type"], "sql_injection")

    def test_negative_no_sql(self):
        """反例：与 SQL 无关的代码 ⇒ 0 条。"""
        body = (
            "def add(a, b):\n"
            "    return a + b\n"
        )
        self.assertEqual(self._run(body), [])

    def test_exempt_parameterized_query(self):
        """豁免例：参数化查询（%s 占位，无 {..} 插值）⇒ 不报 SQL 注入。"""
        body = (
            "def query(uid):\n"
            "    return db.execute(\"SELECT * FROM users WHERE id = %s\", (uid,))\n"
        )
        self.assertEqual(self._run(body), [])


class TestCrossModuleSingletonDetector(unittest.TestCase):
    """验证既有 cross_module_singleton_call 检测器（self_inspector.py:4083）。"""

    def setUp(self):
        self.si = SelfInspector()
        self.fp = "demo_module.py"

    def _run(self, body):
        issues = []
        self.si._check_cross_module_singleton_call(
            body, self.fp, "Demo", "demo_method", 1, issues)
        return issues

    def test_positive_get_singleton_call(self):
        """正例：get_xxx().method() 跨模块单例直调 ⇒ 必报 1 条。"""
        body = (
            "def do():\n"
            "    return get_registry().lookup(\"x\")\n"
        )
        issues = self._run(body)
        self.assertEqual(len(issues), 1, "get_ 单例直调应被检出")
        self.assertEqual(issues[0]["type"], "cross_module_singleton_call")

    def test_negative_no_get_singleton(self):
        """反例：普通对象方法调用（非 get_ 前缀）⇒ 0 条。"""
        body = (
            "def do():\n"
            "    return obj.lookup(\"x\")\n"
        )
        self.assertEqual(self._run(body), [])

    def test_exempt_non_get_factory_call(self):
        """豁免例：非 get_ 前缀的工厂调用（factory().send()）不属跨模块单例直调 ⇒ 0 条。

        证明检测器作用域精确收敛于 `get_` 单例模式，不会误伤普通工厂调用。
        """
        body = (
            "def do():\n"
            "    return factory().send(\"x\")\n"
        )
        self.assertEqual(self._run(body), [])


if __name__ == "__main__":
    unittest.main()
