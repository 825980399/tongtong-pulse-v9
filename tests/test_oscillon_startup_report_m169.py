# -*- coding: utf-8 -*-
"""169批 C3（T-Oscillon配置-1）门控测试。

锁定：启动报告打印 **实际生效值** `cython_effective=<True/False>` + fallback 原因。

口径要点：effective = 模块级可用性（导入成功才算生效），**不是**开关值本身
—— 开关开但模块未编译时必须报 False + 降级原因（这正是原实现的盲区）。
"""
import importlib.util
import os
import sys
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import nucleus.field.OscillonField as of


class _Rec:
    """假日志器：捕获 info/warning 文案。"""

    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, msg, *a, **k):
        self.infos.append(str(msg))

    def warning(self, msg, *a, **k):
        self.warnings.append(str(msg))

    def debug(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


class TestCythonStatus(unittest.TestCase):
    """① cython_status：实际生效值 + 降级原因。"""

    def test_10_has_keys(self):
        _st = of.cython_status()
        self.assertEqual(sorted(_st.keys()), ["effective", "reason", "switch"])
        self.assertIsInstance(_st["effective"], bool)
        self.assertIsInstance(_st["switch"], bool)
        self.assertTrue(_st["reason"])

    def test_11_switch_off(self):
        """开关关闭 → 生效 False，原因点明「配置关闭」。"""
        with mock.patch.object(of, "_cython_extensions_enabled",
                               return_value=False), \
                mock.patch.object(of, "_oscillon_cy_available", False):
            _st = of.cython_status()
        self.assertFalse(_st["effective"])
        self.assertFalse(_st["switch"])
        self.assertIn("配置关闭", _st["reason"])

    def test_12_switch_on_and_loaded(self):
        """开关开 + 导入成功 → 生效 True。"""
        with mock.patch.object(of, "_cython_extensions_enabled",
                               return_value=True), \
                mock.patch.object(of, "_oscillon_cy_available", True):
            _st = of.cython_status()
        self.assertTrue(_st["effective"])
        self.assertIn("导入成功", _st["reason"])

    def test_13_switch_on_but_import_failed(self):
        """★关键：开关开但模块未编译 → 生效 False + 降级原因带具体错误。"""
        with mock.patch.object(of, "_cython_extensions_enabled",
                               return_value=True), \
                mock.patch.object(of, "_oscillon_cy_available", False), \
                mock.patch.object(of, "_oscillon_cy_import_error",
                                  "ImportError: No module named '_oscillon_cy'"):
            _st = of.cython_status()
        self.assertTrue(_st["switch"])
        self.assertFalse(_st["effective"], "开关开但未编译必须报 False")
        self.assertIn("降级", _st["reason"])
        self.assertIn("_oscillon_cy", _st["reason"])


class TestStartupReport(unittest.TestCase):
    """② 启动报告行：cython_effective=... fallback_reason=..."""

    def _reload_capture(self):
        """以独立模块名重装载，捕获模块级启动日志。"""
        _rec = _Rec()
        _src = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "nucleus", "field", "OscillonField.py")
        with mock.patch("nucleus.logger.get_module_logger", return_value=_rec):
            _spec = importlib.util.spec_from_file_location(
                "of_m169_{}".format(uuid.uuid4().hex), _src)
            _mod = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
        return _rec

    def test_20_startup_line_emitted(self):
        _rec = self._reload_capture()
        _hits = [x for x in _rec.infos if "cython_effective=" in x]
        self.assertTrue(_hits, "未打印启动报告生效值: {}".format(_rec.infos))
        self.assertIn("fallback_reason=", _hits[0])

    def test_21_switch_off_no_startup_line(self):
        """灰度开关关闭 → 不打印启动报告行（零回归）。"""
        with mock.patch.object(config, "ENABLE_OSCILLON_STARTUP_REPORT",
                               False, create=True):
            self.assertFalse(of._m169_startup_report_on())
            _rec = self._reload_capture()
        _hits = [x for x in _rec.infos if "cython_effective=" in x]
        self.assertEqual(_hits, [], "开关关闭时不得打印: {}".format(_rec.infos))


if __name__ == "__main__":
    unittest.main()
