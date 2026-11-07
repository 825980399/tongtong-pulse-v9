# -*- coding: utf-8 -*-
"""473刀16 门控单测：[340判死] severity 档拒收日志聚合（462 条 → 1 行）。

现场（2026-11-07 12:25:37 启动后）：1474 行日志里 462 行是同一句
``[340判死] severity 档拒收: ... 原因=action_closed（行动面开=False）``，
占 31% 的日志量在重复描述「行动面关着 ⇒ 判死候选全被拒」这一件事。

本刀**只改日志形态**：拒收集合与 `_rejected["severity"]` 计数一字不动。
"""
import ast
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.self_inspector import _m473_format_severity_rejects  # noqa: E402

#: 源码与 AST 只读一次（模块级 ⇒ 普通 class 也能用）
with open(os.path.join(_ROOT, "nucleus", "self_inspector.py"),
          "r", encoding="utf-8") as _fh:
    _SRC = _fh.read()
_TREE = ast.parse(_SRC)


def _rows(n: int, why: str = "action_closed", prefix: str = "f") -> list:
    return [("%s%d.py" % (prefix, i), "m%d" % i, why) for i in range(n)]


class TestAggregate(unittest.TestCase):
    """聚合纯函数的三档行为（同因 / 多因 / 截断）。"""

    def test_01_同因462条折成一组(self):
        """现场实测形态：462 条同因 ⇒ 1 组，文本含条数。"""
        _txt, _n = _m473_format_severity_rejects(_rows(462))
        self.assertEqual(_n, 1)
        self.assertIn("action_closed", _txt)
        self.assertIn("x462", _txt)

    def test_02_样本最多3个且带等(self):
        _txt, _ = _m473_format_severity_rejects(_rows(10))
        self.assertIn("f0.py::m0", _txt)
        self.assertIn("f2.py::m2", _txt)
        self.assertNotIn("f3.py::m3", _txt)   # 第 4 个开始不展示
        self.assertIn("等", _txt)

    def test_03_样本不超3个时不加等(self):
        _txt, _ = _m473_format_severity_rejects(_rows(3))
        self.assertNotIn("等", _txt)

    def test_04_多因按次数降序(self):
        """刷得最多的排前面（与刀12 同规则）。"""
        _r = _rows(5, "below_min_severity") + _rows(9, "action_closed", "g")
        _txt, _n = _m473_format_severity_rejects(_r)
        self.assertEqual(_n, 2)
        self.assertLess(_txt.index("action_closed"), _txt.index("below_min_severity"))

    def test_05_次数相同按原因名升序(self):
        _r = _rows(3, "zzz") + _rows(3, "aaa", "g")
        _txt, _ = _m473_format_severity_rejects(_r)
        self.assertLess(_txt.index("aaa"), _txt.index("zzz"))

    def test_06_超4组折成共N组(self):
        _r = []
        for _i in range(7):
            _r += _rows(_i + 1, "why%d" % _i, "p%d" % _i)
        _txt, _n = _m473_format_severity_rejects(_r)
        self.assertEqual(_n, 7)
        self.assertIn("共7组", _txt)
        self.assertNotIn("why2", _txt)      # 第 5 组起不展开（降序后是 why6→why3）

    def test_07_空输入返回空(self):
        self.assertEqual(_m473_format_severity_rejects([]), ("", 0))
        self.assertEqual(_m473_format_severity_rejects(None), ("", 0))

    def test_08_脏项跳过不崩(self):
        _r = [None, 123, "abc", ("only",), ("a.py", "m", "why"), {}, ("b.py", "m2", "why")]
        _txt, _n = _m473_format_severity_rejects(_r)
        self.assertEqual(_n, 1)
        self.assertIn("x2", _txt)          # 只有两条合法三元组

    def test_09_None字段转占位(self):
        _txt, _n = _m473_format_severity_rejects([(None, None, None)])
        self.assertEqual(_n, 1)
        self.assertIn("?::?", _txt)
        self.assertIn("? x1", _txt)


class TestWiring(unittest.TestCase):
    """AST 接线：逐条日志必须消失、聚合必须在循环外、级别仍 INFO。"""

    @staticmethod
    def _fn():
        for _n in _TREE.body:
            if isinstance(_n, ast.FunctionDef) and _n.name == "_m335_o3_intake_candidates":
                return _n
        return None

    @staticmethod
    def _agg_ctx() -> str:
        """聚合日志的**上下文**（格式串的上一行才是 logger 调用）。"""
        _ls = _SRC.split("\n")
        _ln = [i for i, L in enumerate(_ls) if "severity 档拒收 %d 条（%d 组）" in L]
        assert len(_ln) == 1, _ln
        return "\n".join(_ls[max(0, _ln[0] - 3):_ln[0] + 1])

    @classmethod
    def _candidates_src(cls) -> str:
        _fn = cls._fn()
        assert _fn is not None, "找不到 _m335_o3_intake_candidates"
        return "\n".join(_SRC.split("\n")[_fn.lineno - 1:_fn.end_lineno])

    def test_10_逐条拒收日志已删除(self):
        """原来的 `severity 档拒收: file=%s method=%s` 必须不存在（否则双打）。"""
        self.assertNotIn("severity 档拒收: file=%s", self._candidates_src())

    def test_11_聚合在循环外(self):
        _src = self._candidates_src()
        _loop = _src.index("for _it in _pool:")
        _emit = _src.index("severity 档拒收 %d 条")
        self.assertLess(_loop, _emit, "聚合日志跑进了循环里 ⇒ 又变成逐条")

    def test_12_明细容器在循环前声明(self):
        _src = self._candidates_src()
        self.assertLess(_src.index("_m473_sev_detail: list = []"),
                        _src.index("for _it in _pool:"),
                        "容器声明跑到了循环后面 ⇒ UnboundLocalError")

    def test_13_循环内仍有计数(self):
        """★判据不变：`_rejected["severity"]` 必须还在累加（只改日志不改计数）。"""
        _src = self._candidates_src()
        self.assertIn('_rejected["severity"] += 1', _src)
        self.assertIn("_m473_sev_detail.append", _src)

    def test_14_聚合仍是INFO(self):
        """★级别不变：这是配置状态不是故障，保持 INFO。"""
        _ctx = self._agg_ctx()
        self.assertIn("_module_logger.info", _ctx)
        self.assertNotIn("_module_logger.warning", _ctx)

    def test_15_聚合前有非空判断(self):
        _src = self._candidates_src()
        self.assertIn("if _m473_sev_detail:", _src)

    def test_16_聚合体有silent_exc兜底(self):
        """日志绝不允许决定返回值（铁律40）：聚合失败不能影响筛选结果。"""
        _src = self._candidates_src()
        self.assertIn("silent_exc(_e473s", _src)

    def test_17_聚合在最终return之前(self):
        _src = self._candidates_src()
        self.assertLess(_src.index("severity 档拒收 %d 条"),
                        _src.rindex("return {\"candidates\""),
                        "聚合写在 return 之后 ⇒ 永远不会执行")


if __name__ == "__main__":
    unittest.main(verbosity=2)
