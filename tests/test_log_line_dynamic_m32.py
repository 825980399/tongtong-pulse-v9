# -*- coding: utf-8 -*-
"""主线第32批 门控测试：logger 文案硬编码行号修复（T3 / P2-190）。

背景（本批 T0 实测）：
  5 个生产文件共 18 条形如
      `self._log(LogLevel.DEBUG, "[主线N批] 静默异常已记录: <路径>:<硬编码行号>")`
  的日志，行号**硬编码** → 代码一改即失真（实测 `parallel_scheduler.py` 的文案
  写 `:355` 而实际在 `:363`；`PulseStomach.py` 的文案写 `:314/:732` 而实际在 `:327/:856`）。

修复：统一改用 `nucleus.logger.exc_location()` —— 在 except 块内取**异常抛出点**行号，
无异常时取调用处行号；绝不硬编码。
"""
import os
import re
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from nucleus.logger import exc_location  # noqa: E402

_FILES = [
    "base/BasePulseOrgan.py",
    "organs/senses/PulseVisualCortex.py",
    "organs/body/PulseStomach.py",
    "organs/motor/PulseController.py",
    "nucleus/parallel_scheduler.py",
]


class TestExcLocation(unittest.TestCase):
    """`exc_location()` 的行为契约。"""

    def test_01_returns_path_and_line(self):
        _loc = exc_location()
        self.assertIsInstance(_loc, str)
        _m = re.match(r"^(.+):(\d+)$", _loc)
        self.assertIsNotNone(_m, f"格式应为 '<路径>:<行号>'，实际 {_loc!r}")
        self.assertGreater(int(_m.group(2)), 0)

    def test_02_relative_to_project_root(self):
        _loc = exc_location()
        self.assertFalse(_loc.startswith(".."), f"应为项目内相对路径，实际 {_loc}")
        self.assertNotIn(":\\", _loc, "路径应统一为正斜杠")

    def test_03_exception_point_not_call_site(self):
        """★核心：在 except 内调用 → 取**异常抛出点**行号，而非调用行。"""
        try:
            _before = exc_location()          # 行 A（无异常 → 调用处行号）
            _call_line = int(_before.rsplit(":", 1)[1])
            raise ValueError("boom")          # 行 B（异常抛出点）
        except ValueError:
            _after = exc_location()
        _raise_line = int(_after.rsplit(":", 1)[1])
        self.assertGreater(_raise_line, _call_line,
                           "异常位置应指向 raise 那一行（晚于调用行）")

    def test_04_tracks_real_line_dynamically(self):
        """★动态性实证：两处不同位置的异常给出**不同**行号。"""

        def _raise_at(n):
            try:
                if n:
                    raise RuntimeError("x")
                raise RuntimeError("y")
            except RuntimeError:
                return int(exc_location().rsplit(":", 1)[1])

        self.assertNotEqual(_raise_at(True), _raise_at(False),
                            "不同代码位置的异常必须给出不同行号（原硬编码做不到）")

    def test_05_never_raises(self):
        """任何情况下不得抛出（它本身用于异常路径）。"""
        for _ in range(3):
            self.assertTrue(exc_location())


class TestNoHardcodedLineLeft(unittest.TestCase):
    """★源码层：18 处文案已全部改为动态，且无残留硬编码。"""

    _PAT = re.compile(r'静默异常已记录: [^"\']*?:\d+')

    def test_10_no_hardcoded_left(self):
        for _rel in _FILES:
            _t = open(os.path.join(_PROJECT_ROOT, _rel), encoding="utf-8").read()
            _left = self._PAT.findall(_t)
            self.assertEqual(_left, [], f"{_rel} 仍有硬编码行号: {_left}")

    def test_11_all_use_exc_location(self):
        _total = 0
        for _rel in _FILES:
            _t = open(os.path.join(_PROJECT_ROOT, _rel), encoding="utf-8").read()
            _n = _t.count("静默异常已记录: {exc_location()}")
            self.assertGreater(_n, 0, f"{_rel} 未使用 exc_location()")
            _total += _n
        self.assertGreaterEqual(_total, 18, "5 个文件合计应 >= 18 处")

    def test_12_imported_in_each_file(self):
        for _rel in _FILES:
            _t = open(os.path.join(_PROJECT_ROOT, _rel), encoding="utf-8").read()
            self.assertIn("exc_location", _t, f"{_rel} 未导入/未使用 exc_location")
            self.assertTrue(
                re.search(r"from nucleus\.logger import .*\bexc_location\b", _t),
                f"{_rel} 缺少 `from nucleus.logger import exc_location`")

    def test_13_message_text_preserved(self):
        """★不得改动文案其余部分（既有测试断言依赖该子串）。"""
        for _rel in _FILES:
            _t = open(os.path.join(_PROJECT_ROOT, _rel), encoding="utf-8").read()
            self.assertIn("[主线10批] 静默异常已记录", _t,
                          f"{_rel} 文案前缀被改动")


if __name__ == "__main__":
    unittest.main(verbosity=2)
