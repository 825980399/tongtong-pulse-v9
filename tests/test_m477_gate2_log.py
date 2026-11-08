# -*- coding: utf-8 -*-
"""477 门控单测：`_m91_gate2_parse` 的日志严谨性（判据正常否定 ≠ 静默异常）。

守三条：
① 语法失败是**正常返回值**，不得抛；valid 仍返回 (True, "", 0)；
② 连续多次失败只在窗口打开时落一条（返回 True），其余静默（返回 False）；
③ 源码不得再出现陈旧的硬编码行号 `L535`；真正的非语法异常仍保留 WARNING。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("PULSE_FRAMEWORK", "1")

from nucleus.reasoning.SafeEvolutionExecutor import (
    _M477_GATE2_WINDOW,
    _m91_gate2_parse,
    _m477_note_gate2_fail,
)


def _see_src() -> str:
    with open(os.path.join(ROOT, "nucleus", "reasoning",
                           "SafeEvolutionExecutor.py"),
              "r", encoding="utf-8", errors="replace") as _f:
        return _f.read()


class TestGate2ParseBehavior:
    def test_valid_returns_true(self):
        assert _m91_gate2_parse("x = 1") == (True, "", 0)

    def test_invalid_returns_false_with_line(self):
        _ok, _err, _line = _m91_gate2_parse("def f(:\n    pass")
        assert _ok is False
        assert "SyntaxError" in _err
        assert _line >= 1, "应带回错误行号（用于精确定位修复）"

    def test_no_raise_on_garbage(self):
        # 均为确定非法（注意 `    return 1` 其实合法：dedent 后被包进函数体）
        for _bad in ("def f(:", "class:", "x = )", "if True"):
            _ok, _err, _line = _m91_gate2_parse(_bad)
            assert _ok is False and isinstance(_err, str), _bad


class TestAggregationWindow:
    def _syntax_exc(self) -> SyntaxError:
        """直接构造 SyntaxError（避免 try/except，杜绝 cw2 静默 handler）。"""
        return SyntaxError("invalid syntax", ("<llm-patch>", 1, 1, "x = )"))

    def test_only_first_opens_window(self):
        _M477_GATE2_WINDOW["ts"] = 0.0
        _M477_GATE2_WINDOW["count"] = 0
        _M477_GATE2_WINDOW["by_type"] = {}
        _e = self._syntax_exc()
        assert _m477_note_gate2_fail(_e) is True, "首次应打开窗口"
        assert _m477_note_gate2_fail(_e) is False, "窗口内后续应静默"
        assert _m477_note_gate2_fail(_e) is False, "窗口内后续应静默"

    def test_count_resets_after_window(self):
        _M477_GATE2_WINDOW["ts"] = 0.0
        _M477_GATE2_WINDOW["count"] = 0
        _M477_GATE2_WINDOW["by_type"] = {}
        _e = self._syntax_exc()
        _m477_note_gate2_fail(_e)
        assert _M477_GATE2_WINDOW["count"] == 0, "开窗后计数应清零"

    def test_by_type_distribution(self):
        _M477_GATE2_WINDOW["ts"] = 0.0
        _M477_GATE2_WINDOW["count"] = 0
        _M477_GATE2_WINDOW["by_type"] = {}
        _e = self._syntax_exc()
        _m477_note_gate2_fail(_e)   # 开窗并清零
        _m477_note_gate2_fail(_e)   # 窗口内累计
        assert _M477_GATE2_WINDOW["by_type"].get("SyntaxError") == 1


class TestSourceAntiRegression:
    def test_stale_lineno_gone(self):
        _s = _see_src()
        assert "_m91_gate2_parse L535" not in _s, "陈旧硬编码行号必须清除"
        assert "_m91_gate2_parse L537" not in _s

    def test_syntax_branch_uses_stable_where(self):
        _s = _see_src()
        assert 'where="SafeEvolutionExecutor::_m91_gate2_parse(syntax)"' in _s

    def test_unexpected_keeps_warning(self):
        _s = _see_src()
        assert 'where="SafeEvolutionExecutor::_m91_gate2_parse(unexpected)"' in _s
        _i = _s.index('_m91_gate2_parse(unexpected)')
        assert 'level="warning"' in _s[_i:_i + 200], "真·意外异常仍应 WARNING"

    def test_helper_present(self):
        assert "def _m477_note_gate2_fail" in _see_src()
