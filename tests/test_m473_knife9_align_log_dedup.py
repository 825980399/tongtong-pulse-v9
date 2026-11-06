# -*- coding: utf-8 -*-
"""第473批 刀9 门控单测：「安全演化执行」粒度告警去重。

病灶
----
2026-11-07 全天日志里，同一次失败必报**两条** WARNING：

    [470刀1] modified 指向别的方法名（不对齐）: id=… 期望=_m70_kal_node_count 实际=get_node_count
    [469刀2] 跳过补丁构造：粒度无法对齐（modified_other_method）file=… organ=… method=… type=…

实测 28 对 28 条，一比一重复。两条信息**互补**（470 给期望/实际方法名，
469 给 organ/type），删任何一条都丢信息 —— 所以只能让重复的那条降级。

判据设计（与日志实际行为对齐，不靠猜）
--------------------------------------
`_m470_align_strict_on()` 为假的**唯一**含义是「470刀1 的判定点不打日志」。所以：

  * how == "modified_other_method" 且 470 严格开关开 ⇒ 470 已打全 ⇒ 469 降 INFO
  * 严格开关关 ⇒ 469 是唯一记录 ⇒ 必须保持 WARNING
  * 其余档 ⇒ 470 对它不打 ⇒ 必须保持 WARNING

★本文件特意包含两类「不宽松」断言（大小写/空白、None/0），因为把开关写成
  `getattr(cfg, X, d) or d` 时 None/0 会被当成缺省 —— 这里必须显式判 False。
"""

import ast
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus.reasoning.SafeEvolutionExecutor import (
    _m470_align_strict_on,
    _m473_should_demote_align_log,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOD_PATH = os.path.join(_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")

_DUP_HOW = "modified_other_method"

# 470刀1 判定点**不打**日志的其余档（含空/None/未知）
_OTHER_HOWS = (
    "method_not_found",
    "no_target",
    "orig_mismatch",
    "modified_equals_original",
    "body_mismatch",
    "unknown_how",
    "",
    None,
)


def _with_strict(value):
    """把 470 严格开关临时设成指定值（None/0 也照设，不当缺省）。"""
    return mock.patch.object(config, "ENABLE_M470_ALIGN_STRICT", value)


class TestDemoteJudgement(unittest.TestCase):
    """纯函数三档判据。"""

    def test_demote_when_strict_on(self):
        """470 判定点已打全 ⇒ 469 降 INFO。"""
        with _with_strict(True):
            self.assertTrue(_m473_should_demote_align_log(_DUP_HOW))

    def test_keep_warning_when_strict_off(self):
        """470 判定点不打 ⇒ 469 是唯一记录 ⇒ 必须 WARNING。"""
        with _with_strict(False):
            self.assertFalse(_m473_should_demote_align_log(_DUP_HOW))

    def test_keep_warning_when_strict_none(self):
        """None 是显式假值，不是「缺省」—— 不能因为 None 就回默认 True。"""
        with _with_strict(None):
            self.assertFalse(_m473_should_demote_align_log(_DUP_HOW))

    def test_keep_warning_when_strict_zero(self):
        """0 同理：`or 默认值` 写法会把 0 吞成 True，这里必须 False。"""
        with _with_strict(0):
            self.assertFalse(_m473_should_demote_align_log(_DUP_HOW))

    def test_keep_warning_for_other_how(self):
        """其余档即使 470 开关开着也不降级（470 对它不打日志）。"""
        with _with_strict(True):
            for _how in _OTHER_HOWS:
                self.assertFalse(_m473_should_demote_align_log(_how),
                                 f"{_how!r} 不该降级")

    def test_no_loose_match_on_case_or_space(self):
        """不做大小写/空白宽松匹配：未知形态一律保守保持 WARNING。"""
        with _with_strict(True):
            for _bad in ("MODIFIED_OTHER_METHOD", " modified_other_method",
                         "modified_other_method ", "modified_other_methodx"):
                self.assertFalse(_m473_should_demote_align_log(_bad), _bad)


class TestStrictSwitchSemantics(unittest.TestCase):
    """`_m470_align_strict_on` 的缺省语义（刀9 判据的上游）。"""

    def test_attr_missing_defaults_true(self):
        """属性缺失 ⇒ 默认 True（470 判定点照常打日志）。"""
        _sentinel = object()
        _old = getattr(config, "ENABLE_M470_ALIGN_STRICT", _sentinel)
        try:
            if hasattr(config, "ENABLE_M470_ALIGN_STRICT"):
                delattr(config, "ENABLE_M470_ALIGN_STRICT")
            self.assertIs(_m470_align_strict_on(), True)
            self.assertTrue(_m473_should_demote_align_log(_DUP_HOW))
        finally:
            if _old is not _sentinel:
                config.ENABLE_M470_ALIGN_STRICT = _old

    def test_truthy_string_is_on(self):
        """非空字符串按真处理（配置层可能传 "1"/"true"）。"""
        with _with_strict("1"):
            self.assertIs(_m470_align_strict_on(), True)


class TestWiring(unittest.TestCase):
    """★不许「函数写了但没人用」：判据必须真的接在消费点上。"""

    @classmethod
    def setUpClass(cls):
        with open(_MOD_PATH, "r", encoding="utf-8") as _fh:
            cls.src = _fh.read()
        cls.tree = ast.parse(cls.src)

    @classmethod
    def _scan(cls):
        _defs, _calls = [], []
        for _node in ast.walk(cls.tree):
            if isinstance(_node, ast.FunctionDef) and _node.name == "_m473_should_demote_align_log":
                _defs.append(_node.lineno)
            if isinstance(_node, ast.Call) and isinstance(_node.func, ast.Name) \
                    and _node.func.id == "_m473_should_demote_align_log":
                _calls.append(_node.lineno)
        return _defs, _calls

    def test_defined_exactly_once(self):
        _defs, _ = self._scan()
        self.assertEqual(len(_defs), 1, f"应只定义一次，实际 {_defs}")

    def test_called_at_least_once_after_def(self):
        _defs, _calls = self._scan()
        self.assertTrue(_calls, "模块内没有任何调用点 ⇒ 判据没接线")
        self.assertTrue(all(_c > _defs[0] for _c in _calls),
                        f"调用点必须位于定义之后：def={_defs} call={_calls}")

    def test_call_passes_how_from_align_result(self):
        """调用参数必须是 469 对齐结果的 `how`，不是常量或别的东西。"""
        _found = False
        for _node in ast.walk(self.tree):
            if not (isinstance(_node, ast.Call) and isinstance(_node.func, ast.Name)
                    and _node.func.id == "_m473_should_demote_align_log"):
                continue
            if len(_node.args) != 1:
                continue
            _arg = _node.args[0]
            if isinstance(_arg, ast.Call) and isinstance(_arg.func, ast.Attribute) \
                    and _arg.func.attr == "get" and _arg.args \
                    and isinstance(_arg.args[0], ast.Constant) and _arg.args[0].value == "how":
                _found = True
        self.assertTrue(_found, '调用点未传 `.get("how")` ⇒ 判据吃不到真实档位')

    def test_dup_flag_comes_from_judgement(self):
        """`_m473_dup` 必须由本刀判据赋值，不能是别处变量复用。"""
        _ok = False
        for _node in ast.walk(self.tree):
            if isinstance(_node, ast.Assign) and len(_node.targets) == 1:
                _t = _node.targets[0]
                if not (isinstance(_t, ast.Name) and _t.id == "_m473_dup"):
                    continue
                _v = _node.value
                if isinstance(_v, ast.Call) and isinstance(_v.func, ast.Name) \
                        and _v.func.id == "_m473_should_demote_align_log":
                    _ok = True
        self.assertTrue(_ok, "`_m473_dup` 未由判据函数赋值")

    def test_level_is_chosen_dynamically(self):
        """日志级别必须是 info/warning 二选一，而不是写死。"""
        _oks = []
        for _node in ast.walk(self.tree):
            if not isinstance(_node, ast.Assign) or len(_node.targets) != 1:
                continue
            _t = _node.targets[0]
            if not (isinstance(_t, ast.Name) and _t.id == "_m473_logf"):
                continue
            _v = _node.value
            if not (isinstance(_v, ast.IfExp) and isinstance(_v.test, ast.Name)
                    and _v.test.id == "_m473_dup"):
                continue
            _body = _v.body.attr if isinstance(_v.body, ast.Attribute) else None
            _orel = _v.orelse.attr if isinstance(_v.orelse, ast.Attribute) else None
            if (_body, _orel) == ("info", "warning"):
                _oks.append(_node.lineno)
        self.assertTrue(_oks, "未找到 `_m473_logf = info if _m473_dup else warning` 结构")

    def test_demoted_branch_still_carries_hint(self):
        """降 INFO 后仍要指明「详细判据见上一条 470刀1」，否则等于丢线索。"""
        self.assertIn("详细判据见上一条 470刀1", self.src)


if __name__ == "__main__":
    unittest.main()
