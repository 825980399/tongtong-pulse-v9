# -*- coding: utf-8 -*-
"""473刀17 门控单测：[339域] 域外 / 白名单外 拒收日志聚合（54 条 → 1 行）。

现场（2026-11-07 12:25:37 启动后 ~12:50）：
  `[339域] 白名单外 ⇒ 不入池: file=… method=…` 54 条，
  集中在 utils/pulse_tracer.py(12) / somatics/reflex.py(12) / somatics/body_state.py(12)
  + 其余 6 个文件各 3 条 —— 与刀16 的 severity 拒收同型。

复用刀16 的纯函数 `_m473_format_severity_rejects`（入参同为 (file, method, why)）。
本刀**只改日志形态**：`_rejected["domain"]` / `_rejected["whitelist"]` 计数一字不动。
"""
import ast
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

with open(os.path.join(_ROOT, "nucleus", "self_inspector.py"),
          "r", encoding="utf-8") as _fh:
    _SRC = _fh.read()
_TREE = ast.parse(_SRC)
_LINES = _SRC.split("\n")


def _fn_src(name: str) -> str:
    for _n in _TREE.body:
        if isinstance(_n, ast.FunctionDef) and _n.name == name:
            return "\n".join(_LINES[_n.lineno - 1:_n.end_lineno])
    raise AssertionError(f"找不到函数 {name}")


class TestWiring(unittest.TestCase):
    """AST 接线：逐条日志消失、容器在循环前、聚合在循环外。"""

    @classmethod
    def setUpClass(cls):
        cls._src = _fn_src("_m335_o3_intake_candidates")

    def test_01_域外逐条日志已删除(self):
        self.assertNotIn("判据域外 ⇒ 不入池: file=%s", self._src)

    def test_02_白名单逐条日志已删除(self):
        self.assertNotIn("白名单外 ⇒ 不入池: file=%s", self._src)

    def test_03_两个容器在循环前声明(self):
        _loop = self._src.index("for _it in _pool:")
        for _bag in ("_m473_dom_detail: list = []", "_m473_wl_detail: list = []"):
            self.assertIn(_bag, self._src)
            self.assertLess(self._src.index(_bag), _loop,
                            f"{_bag} 声明跑到了循环后面 ⇒ UnboundLocalError")

    def test_04_循环内仍累加计数(self):
        """★判据不变：两个计数器必须还在（只改日志不改计数）。"""
        self.assertIn('_rejected["domain"] += 1', self._src)
        self.assertIn('_rejected["whitelist"] += 1', self._src)
        self.assertIn("_m473_dom_detail.append", self._src)
        self.assertIn("_m473_wl_detail.append", self._src)

    def test_05_聚合在循环外(self):
        _loop = self._src.index("for _it in _pool:")
        _emit = self._src.index("for _m473_tag, _m473_bag in")
        self.assertLess(_loop, _emit, "聚合跑进了循环里 ⇒ 又变成逐条")

    def test_06_聚合在最终return之前(self):
        self.assertLess(self._src.index("for _m473_tag, _m473_bag in"),
                        self._src.rindex('return {"candidates"'),
                        "聚合写在 return 之后 ⇒ 永远不会执行")

    def test_07_两个标签都在(self):
        self.assertIn('"[339域] 判据域外 ⇒ 不入池"', self._src)
        self.assertIn('"[339域] 白名单外 ⇒ 不入池"', self._src)

    def test_08_聚合仍是INFO(self):
        """★级别不变：这是筛选规则不是故障，保持 INFO。"""
        _i = self._src.index("for _m473_tag, _m473_bag in")
        _blk = self._src[_i:_i + 700]
        self.assertIn("_module_logger.info", _blk)
        self.assertNotIn("_module_logger.warning", _blk)

    def test_09_有非空判断(self):
        self.assertIn("if not _m473_bag:", self._src)

    def test_10_聚合体有silent_exc兜底(self):
        """日志绝不允许决定返回值（铁律40）。"""
        self.assertIn("silent_exc(_e473d", self._src)

    def test_11_复用刀16的纯函数而非另写一份(self):
        """★不复制第二套聚合实现（防两处规则漂移）。"""
        self.assertIn("_m473_format_severity_rejects(_m473_bag)", self._src)
        self.assertEqual(_SRC.count("def _m473_format_severity_rejects"), 1)

    def test_12_原因文本带上下文(self):
        """why 字段必须自解释，不能只写 'domain'。"""
        self.assertIn("判据域外（tests/ 或 test_ 前缀）", self._src)
        self.assertIn("白名单外（仅放 nucleus/ tools/ organs/ config.py main.py scripts/）", self._src)


class TestAggregateReuse(unittest.TestCase):
    """确认复用路径产出的文本形态可用（不重复测纯函数本身，只测 339域 场景）。"""

    def test_13_单组54条形态(self):
        from nucleus.self_inspector import _m473_format_severity_rejects as _f
        _why = "白名单外（仅放 nucleus/ tools/ organs/ config.py main.py scripts/）"
        _rows = [(f"f{_i}.py", f"m{_i}", _why) for _i in range(54)]
        _txt, _n = _f(_rows)
        self.assertEqual(_n, 1)
        self.assertIn("x54", _txt)
        self.assertIn("f0.py::m0", _txt)
        self.assertIn("等", _txt)

    def test_14_多文件仍是单组单原因(self):
        """现场形态：3 个文件各 12 条 + 6 个各 3 条 ⇒ 原因相同 ⇒ 1 组。"""
        from nucleus.self_inspector import _m473_format_severity_rejects as _f
        _why = "白名单外（仅放 nucleus/ tools/ organs/ config.py main.py scripts/）"
        _rows = []
        for _f_ in ("utils/pulse_tracer.py", "somatics/reflex.py", "somatics/body_state.py"):
            _rows += [(_f_, f"m{_i}", _why) for _i in range(12)]
        _txt, _n = _f(_rows)
        self.assertEqual(_n, 1)
        self.assertIn("x36", _txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
