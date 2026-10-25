#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`lock_with_emit` 检测器假阳性修正的回归用例（367 后续 · A2 核真伪）。

## 背景（实测）

池里 2 条 `lock_with_emit`（medium）：`PulseMotivationCycle._emit_motivation_signals:514` /
`_emit_pressure_constraints:523`。逐行看：

- `_emit_motivation_signals`：`★355刀7：发射移出锁外` 注释明写，emit 在 with 块**之后**；
- `_emit_pressure_constraints`：`with self._lock:` 只包两行赋值，emit 在块**之外**。

⇒ 两条都是**假阳性**。根因是原实现用 `body.find('self._lock') < body.find('self._emit')`
做**纯字符串位置比较**，不看缩进与块归属 ⇒ 锁块结束后的发射必然被报。

★为什么必须修：这类条目会让代理/人照着"修"**正确的代码**，越修越坏。
"""
from __future__ import annotations

import io
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from nucleus.self_inspector import SelfInspector  # noqa: E402

_MC = os.path.join(_ROOT, "organs", "core", "PulseMotivationCycle.py")


@pytest.fixture(scope="module")
def si():
    return SelfInspector()


def _check(si, body):
    iss = []
    si._check_lock_with_emit(body, "t.py", "o", "f", 100, iss)
    return iss


class TestPrecision:
    def test_01_emit_outside_lock_not_reported(self, si):
        """★核心：`with` 块**结束之后**的发射不得被报（这就是原 bug）。"""
        body = "def f(self):\n    with self._lock:\n        x = 1\n    self._emit('e', {})\n"
        assert _check(si, body) == []

    def test_02_emit_inside_lock_reported(self, si):
        """真阳性必须仍能抓到（收紧判据不能把召回一起砍掉）。"""
        body = "def f(self):\n    with self._lock:\n        self._emit('e', {})\n"
        r = _check(si, body)
        assert len(r) == 1 and r[0]["type"] == "lock_with_emit"

    def test_03_no_lock_not_reported(self, si):
        assert _check(si, "def f(self):\n    self._emit('e', {})\n") == []

    def test_04_lock_without_emit_not_reported(self, si):
        assert _check(si, "def f(self):\n    with self._lock:\n        x = 1\n") == []

    def test_05_nested_with_inside_lock_reported(self, si):
        """嵌套 with 里的发射也要抓到（按行区间判，不按第一处字样）。"""
        body = ("def f(self):\n    with self._lock:\n        with x:\n"
                "            self._emit('e', {})\n")
        assert len(_check(si, body)) == 1

    def test_06_acquire_form_reported(self, si):
        """`with self._lock.acquire():` 形态同样要抓到。"""
        body = "def f(self):\n    with self._lock.acquire():\n        self._emit('e', {})\n"
        assert len(_check(si, body)) == 1

    def test_07_parse_failure_not_reported(self, si):
        """解析不了 ⇒ **不报**（不猜），且不得抛异常。"""
        assert _check(si, "def f(self):\n    with self._lock:\n  bad indent\n") == []

    def test_08_line_number_points_to_emit(self, si):
        """行号必须指向 emit 那一行（不是 with 那一行）。"""
        body = "def f(self):\n    with self._lock:\n        x = 1\n        self._emit('e', {})\n"
        r = _check(si, body)
        assert len(r) == 1
        assert r[0]["line"] == 100 + 3, "应为 start_line + emit 行序 - 1"

    def test_09_only_one_issue_per_method(self, si):
        """同一方法多处锁内发射只报一条（防同方法刷屏）。"""
        body = ("def f(self):\n    with self._lock:\n        self._emit('a', {})\n"
                "        self._emit('b', {})\n")
        assert len(_check(si, body)) == 1


class TestRealWorldRegression:
    def _method_body(self, meth):
        src = io.open(_MC, encoding="utf-8").read()
        m = re.search(r"\n    def %s\(.*?(?=\n    def )" % re.escape(meth), src, re.S)
        assert m, "未找到方法 %s" % meth
        return m.group(0)

    def test_10_emit_motivation_signals_no_longer_reported(self, si):
        """355 刀7 已把发射移出锁外 ⇒ 本方法不得再被报。"""
        body = self._method_body("_emit_motivation_signals")
        assert "355刀7" in body, "355刀7 的锁外修复注释不在了？先查代码"
        assert _check(si, body) == [], "假阳性回来了"

    def test_11_emit_pressure_constraints_no_longer_reported(self, si):
        """with 只包赋值、emit 在块外 ⇒ 不得再被报。"""
        assert _check(si, self._method_body("_emit_pressure_constraints")) == []


class TestDescriptionWording:
    def test_12_description_says_in_block(self, si):
        """文案改为「位于 with self._lock 块内」（原来写"方法内同时存在"）。

        ★不同事实不同措辞：原来那句描述的是"同时存在"，是**弱证据**；
        现在报的是"块归属"，是**强证据**，文案必须跟着变。
        """
        iss = _check(si, "def f(self):\n    with self._lock:\n        self._emit('e', {})\n")
        assert "块内" in iss[0]["description"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
