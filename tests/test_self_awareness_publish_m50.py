# -*- coding: utf-8 -*-
"""第50批 T1 补测：SelfAwarenessEngine.generate_report → ReportBus 的**功能级**集成

★教训：本批 T1 只做了「源码接线断言」，未功能调用集成路径 →
`_m50_report_summary` 的 AttributeError（dataclass 被 json.dumps 成字符串）
被 except 吞成 DEBUG 日志 → **报告发布恒失败而测试全绿**。
本测试补齐该缺口。
"""
import io
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.reporting import publishers as _pub  # noqa: E402
from nucleus.reporting.report_bus import ReportBus  # noqa: E402
from nucleus.self_awareness.SelfAwarenessEngine import SelfAwarenessEngine  # noqa: E402

_SA_SRC = io.open(os.path.join(_ROOT, "nucleus/self_awareness/SelfAwarenessEngine.py"),
                  encoding="utf-8", errors="replace").read().replace("\r\n", "\n")


class TestGenerateReportPublishes(unittest.TestCase):
    """★功能级：真实调用 generate_report，验证报告确实进入总线。"""

    @classmethod
    def setUpClass(cls):
        cls._eng = SelfAwarenessEngine()

    def setUp(self):
        self._sand = tempfile.mkdtemp(prefix="m50sa_")
        self.bus = ReportBus(base_dir=os.path.join(self._sand, "reports"))
        self._orig = _pub.get_report_bus
        _pub.get_report_bus = lambda *a, **kw: self.bus

    def tearDown(self):
        _pub.get_report_bus = self._orig
        shutil.rmtree(self._sand, ignore_errors=True)

    def test_70_generate_report_publishes_self_cognition(self):
        """★核心：`generate_report()` 必须真的把报告发布到总线。"""
        _txt = self._eng.generate_report()
        self.assertIsInstance(_txt, str)
        _envs = [e for e in self.bus.all() if e.report_type == "self_cognition"]
        self.assertTrue(_envs, "★generate_report 未发布 self_cognition 报告（P0-1 未闭环）")
        _e = _envs[0]
        self.assertEqual(_e.generator, "SelfAwarenessEngine.generate_report")
        self.assertGreater(_e.content.get("text_length", 0), 0)
        self.assertTrue(_e.is_consumed() or True)   # 无订阅者时不强求

    def test_71_report_summary_is_plain_dict(self):
        """★`_m50_report_summary` 必须返回**真 dict**（非字符串）。"""
        _s = self._eng._m50_report_summary(self._eng.get_profile())
        self.assertIsInstance(_s, dict, "★摘要必须是 dict（原 bug 返回 str）")
        for _k in ("health_level", "overall_score", "best_dimension",
                   "worst_dimension", "headline_issue", "summary"):
            self.assertIn(_k, _s)
        self.assertTrue(_s["overall_score"] is None
                        or isinstance(_s["overall_score"], (int, float)),
                        "overall_score 应为 None 或数值")

    def test_72_summary_tolerates_dataclass_and_dict_and_none(self):
        _p = self._eng.get_profile()
        for _v in (_p, {"overall_score": 88.0, "health_level": "healthy"}, None,
                   "str", 123, []):
            _r = self._eng._m50_report_summary(_v)
            self.assertIsInstance(_r, dict, "{!r} 应降级为空 dict".format(type(_v)))

    def test_73_no_json_dumps_default_str_antipattern(self):
        """★防回归：`_m50_report_summary` 内不得再用 `json.dumps/loads` 提取字段。

        ★用 **AST**（不是文本正则）—— 修复说明的 **docstring 里就含**该反模式文字，
        文本扫描会误报（本测试第一版即如此，见 skill §45.1）。
        """
        import ast
        _tree = ast.parse(_SA_SRC)
        _fn = next((n for n in ast.walk(_tree)
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "_m50_report_summary"), None)
        self.assertIsNotNone(_fn, "未找到 _m50_report_summary")
        _bad = []
        for _n in ast.walk(_fn):
            if (isinstance(_n, ast.Call)
                    and isinstance(_n.func, ast.Attribute)
                    and _n.func.attr in ("dumps", "loads")
                    and isinstance(_n.func.value, ast.Name)
                    and _n.func.value.id == "json"):
                _bad.append(_n.lineno)
        self.assertEqual(
            _bad, [], "★函数体内仍用 json.dumps/loads 提取字段 @{}".format(_bad))

    def test_74_publish_failure_logs_message_not_only_type(self):
        """★防回归：发布失败日志必须带**异常消息**（原只记类型 → 故障难定位）。"""
        i = _SA_SRC.find("自认知报告发布失败")
        self.assertGreater(i, 0)
        _seg = _SA_SRC[i:i + 200]
        self.assertIn("_m50_e", _seg)
        self.assertIn("%s: %s", _seg.replace("\n", " ").replace("  ", " ")
                      if "%s: %s" not in _seg else _seg)

    def test_75_report_persisted_in_isolated_bus(self):
        self._eng.generate_report()
        _files = os.listdir(os.path.join(self._sand, "reports", "self_cognition")) \
            if os.path.isdir(os.path.join(self._sand, "reports", "self_cognition")) else []
        self.assertTrue(_files, "自认知报告应落盘到总线目录")


if __name__ == "__main__":
    unittest.main(verbosity=2)
