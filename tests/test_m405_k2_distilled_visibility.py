# -*- coding: utf-8 -*-
"""第405批 刀2 判据：distilled 异常可见化（只定位不修复）。

本刀的验收要害是**三条语义必须可分辨**：
  · step=1（`_exp.record`）抛  → 打 WARNING **并上抛**，计数不 +1（契约不变）
  · step=2（`_persist_state`）抛 → 打 WARNING **但自吞**（它本来就有 try/except）
  · 两步都通过             → 日志里能看到「两步全通过」的 DEBUG

判据不锁日志措辞，只锁：有没有 WARNING、里面能不能读出
「哪一步 / 什么异常类型 / 哪个文件」。措辞改动不该顶红，语义改动必须顶红。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from nucleus._silent_except import silent_exc
from nucleus.reasoning import ReasoningExperienceIndexer as _mod
from nucleus.reasoning.ReasoningExperienceIndexer import (
    ReasoningExperienceIndexer,
    _exc_site,
)


class _BoomExp:
    """替身经验池：record() 必抛（模拟生产里 step=1 那个堵点）。"""

    def __init__(self, exc: BaseException | None = None) -> None:
        self._file_path = "data/context/reasoning_experience.json"
        self.calls: list = []
        self._exc = exc or ValueError("模拟 record 内部炸了")

    def record(self, *a, **kw):
        self.calls.append((a, kw))
        raise self._exc


class _OkExp:
    """替身经验池：record() 正常返回。"""

    def __init__(self) -> None:
        self._file_path = "data/context/reasoning_experience.json"
        self.calls: list = []

    def record(self, *a, **kw):
        self.calls.append((a, kw))
        return True


class _Base(unittest.TestCase):
    def _mk(self, exp):
        """造一个隔离的 indexer：不碰生产 state 文件、不碰真实 metrics。"""
        _e = ReasoningExperienceIndexer(reasoning_exp=exp, node_pool=None)
        _e._double_write_enabled = False      # 关掉双写：隔绝 metrics / L2 副作用
        _e._remediation_count = 0
        _e._base_remediation = 0
        return _e


class TestExcSite(unittest.TestCase):
    """辅助函数：异常位置必须能取到 文件:行号 in 函数。"""

    def test_01_extracts_file_line_and_func(self):
        def _inner_raiser():
            raise RuntimeError("boom")

        # ★不用 assertRaises：unittest 会在 __exit__ 里清掉 __traceback__
        #   （防引用环），那样 _exc_site 只能拿到 <no-tb>，判据就失效了。
        #   显式 except + silent_exc 留痕，同时保住 traceback。
        _site = ""
        try:
            _inner_raiser()
        except RuntimeError as e:
            silent_exc(e, where="test_m405_k2::test_01_extracts", level="debug")
            _site = _exc_site(e)
        self.assertIn("test_m405_k2", _site, "没取到文件名: %s" % _site)
        self.assertIn("_inner_raiser", _site, "没取到函数名: %s" % _site)
        self.assertIn(":", _site, "没取到行号: %s" % _site)

    def test_02_no_traceback_is_survivable(self):
        """没有 traceback 的异常也要能返回字符串，绝不二次抛。"""
        self.assertIsInstance(_exc_site(ValueError("no tb")), str)


class TestStep1Visible(_Base):
    """step=1：_exp.record 抛 → WARNING + 上抛 + 计数不 +1。"""

    def test_10_warning_logged_with_step_and_type(self):
        _e = self._mk(_BoomExp(ValueError("模拟 step1 失败")))
        with self.assertLogs(_mod.logger, level="WARNING") as _cm:
            with self.assertRaises(ValueError):
                _e.record_remediation_success("问题", "答案")
        _txt = "\n".join(_cm.output)
        self.assertIn("step=1", _txt, "WARNING 里没标出断在哪一步")
        self.assertIn("ValueError", _txt, "WARNING 里没有异常类型")
        self.assertIn("reasoning_experience.json", _txt,
                      "WARNING 里没有经验文件名")

    def test_11_exception_still_propagates(self):
        """★契约不变：仍然原样上抛（调用方既有异常边界不受影响）。"""
        _e = self._mk(_BoomExp(KeyError("上游约定要能接住")))
        with self.assertLogs(_mod.logger, level="WARNING"):
            with self.assertRaises(KeyError):
                _e.record_remediation_success("问题", "答案")

    def test_12_counter_untouched_after_step1_failure(self):
        """★只定位不修复：step=1 抛后计数仍是 0（distilled 应当照样是 0）。"""
        _e = self._mk(_BoomExp(RuntimeError("x")))
        with self.assertLogs(_mod.logger, level="WARNING"):
            with self.assertRaises(RuntimeError):
                _e.record_remediation_success("问题", "答案")
        self.assertEqual(_e._remediation_count, 0,
                         "step=1 失败后计数被 +1 了 —— 这已经从'定位'变成'修复'")

    def test_13_no_warning_when_step1_ok(self):
        """step=1 正常时不得有 WARNING（防误报把真问题淹掉）。"""
        _e = self._mk(_OkExp())
        _e._persist_state = lambda: None      # 只隔离 step=2
        with self.assertNoLogs(_mod.logger, level="WARNING"):
            _e.record_remediation_success("问题", "答案")
        self.assertEqual(_e._remediation_count, 1)


class TestStep2Visible(_Base):
    """step=2：_persist_state 内部抛 → WARNING（自吞，不外抛）。"""

    def test_20_persist_failure_warns_but_swallowed(self):
        _e = self._mk(_OkExp())
        # 用一个不可能建的路径让 makedirs/写盘抛（Windows/POSIX 都成立）
        _bad = os.path.join("Z:", "__m405_no_such_drive__", "state.json") \
            if os.name == "nt" else "/proc/__m405__/state.json"
        with mock.patch.object(_mod, "_STATE_PATH", _bad):
            with self.assertLogs(_mod.logger, level="WARNING") as _cm:
                # ★不得抛出：_persist_state 自带 try/except，异常必须自吞
                _e.record_remediation_success("问题", "答案")
        _txt = "\n".join(_cm.output)
        self.assertIn("step=2", _txt, "WARNING 里没标出是持久化这一步")
        self.assertIn("_persist_state", _txt)
        # 计数照旧递增：这一步失败不影响内存计数（原语义）
        self.assertEqual(_e._remediation_count, 1,
                         "持久化失败把内存计数也回退了（改变了原语义）")

    def test_21_warning_contains_target_file(self):
        """WARNING 必须能看出写的是哪个文件（任务书要求"带文件名"）。"""
        _e = self._mk(_OkExp())
        _bad = os.path.join("Z:", "__m405_no_such_drive__", "state.json") \
            if os.name == "nt" else "/proc/__m405__/state.json"
        with mock.patch.object(_mod, "_STATE_PATH", _bad):
            with self.assertLogs(_mod.logger, level="WARNING") as _cm:
                _e.record_remediation_success("问题", "答案")
        self.assertIn("state.json", "\n".join(_cm.output))


class TestBothStepsLogged(_Base):
    """两步全通过时，DEBUG 必须能把「走完了」说清楚。"""

    def test_30_both_steps_debug_visible(self):
        _e = self._mk(_OkExp())
        _dir = tempfile.mkdtemp(prefix="m405_k2_")
        _st = os.path.join(_dir, "state.json")
        try:
            with mock.patch.object(_mod, "_STATE_PATH", _st):
                with self.assertLogs(_mod.logger, level="DEBUG") as _cm:
                    _e.record_remediation_success("问题", "答案")
            _txt = "\n".join(_cm.output)
            for _must in ("step=1 before", "step=1 after",
                          "step=2 before", "step=2 after"):
                self.assertIn(_must, _txt, "缺 DEBUG 观测点: %s" % _must)
            # 持久化真的写下去了
            with open(_st, encoding="utf-8") as f:
                _s = json.load(f)
            self.assertEqual(_s.get("remediation_count"), 1)
        finally:
            _purge(_dir)

    def test_31_empty_answer_takes_delegated_path(self):
        """空答案仍走 delegated 早返回，不碰 _exp.record（原分支不得被改坏）。"""
        _exp = _BoomExp()          # 若误入 step=1 会立刻抛
        _e = self._mk(_exp)
        _e._double_write_enabled = False
        _e.record_remediation_success("问题", "")     # 不抛 = 没走 step=1
        self.assertEqual(_exp.calls, [], "空答案错误地进入了蒸馏路径")
        self.assertEqual(_e._remediation_count, 0)


def _purge(path: str) -> None:
    """清空并删除临时目录（测试清理；每个 except 都留痕，符合项目铁律）。"""
    try:
        if not os.path.isdir(path):
            return
        for _n in os.listdir(path):
            try:
                os.remove(os.path.join(path, _n))
            except OSError as _e:
                silent_exc(_e, where="test_m405_k2::_purge/rm", level="debug")
        os.rmdir(path)
    except OSError as _e:
        silent_exc(_e, where="test_m405_k2::_purge", level="debug")


if __name__ == "__main__":
    unittest.main(verbosity=2)
