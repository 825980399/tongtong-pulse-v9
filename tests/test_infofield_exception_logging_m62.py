# -*- coding: utf-8 -*-
"""主线第62批 T2 专项：InfoField.reload_l2_config 不得静默吞异常。

背景：
  ``nucleus/field/InfoField.py`` 的 ``reload_l2_config``（第57批 T4 引入）中，
  设置 L2 线程池 ``_max_workers`` 时用了 ``except Exception: pass``，
  静默吞掉异常 —— 违反「核心 7 文件不得有静默异常」门禁，
  导致 ``tests/test_robustness_m7.py::test_core_files_no_silent_except_pass`` 失败。

本批修复：改为 ``except Exception as _e:`` + ``_module_logger.warning(...)``
（级别 warning 而非 error：配置设置失败不影响主流程）。

验收：
  1. 核心文件无 `except ...: pass`
  2. 异常发生时**有日志输出**，且包含异常类型与 workers 值
  3. 异常不得中断流程 —— 仍返回 _cfg（保留原 return 语义）
"""
import io
import os
import re
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import nucleus.field.InfoField as IF  # noqa: E402

_SRC = io.open(os.path.join(_PROJECT_ROOT, "nucleus", "field", "InfoField.py"),
               encoding="utf-8").read()

# 与 tests/test_robustness_m7.py 保持一致的静默异常检测
_SILENT_SINGLE = re.compile(r"except\s+[A-Za-z_][A-Za-z0-9_.]*\s*:\s*pass\s*$")
_SILENT_MULTI = re.compile(r"except\s+[^\n:]+:\s*\n\s*pass\s*(\n|$)")


class _BadPool:
    """设置 ``_max_workers`` 时抛异常的假线程池。"""

    def __init__(self):
        object.__setattr__(self, "_max_workers", 1)

    def __setattr__(self, key, value):
        if key == "_max_workers":
            raise RuntimeError("boom-m62")
        object.__setattr__(self, key, value)


class _GoodPool:
    """正常线程池（可写入 _max_workers）。"""

    def __init__(self):
        self._max_workers = 1


def _mk_field():
    """轻量实例（绕 __init__），只喂 reload_l2_config 需要的路径属性。"""
    _f = IF.InfoField.__new__(IF.InfoField)
    _f._layer_pools = {}
    _f._layer_queue_limits = {}
    return _f


class TestNoSilentExcept(unittest.TestCase):
    """① 静态：核心门禁守卫。"""

    def test_01_no_silent_except_pass(self):
        self.assertEqual(len(_SILENT_SINGLE.findall(_SRC)), 0,
                         "仍存在单行 except ...: pass")
        self.assertEqual(len(_SILENT_MULTI.findall(_SRC)), 0,
                         "仍存在多行 except ...: pass")

    def test_02_exception_log_signature(self):
        self.assertIn("type={type(_e).__name__}", _SRC)

    def test_03_uses_module_logger(self):
        """复用文件已有的 module-level logger，而非运行时动态取 logger。"""
        self.assertIn("_module_logger", _SRC)
        self.assertIn("_module_logger.warning", _SRC)


class TestExceptionIsLogged(unittest.TestCase):
    """② 运行时：异常必须被记录，且不中断流程。"""

    def test_10_warning_emitted_on_failure(self):
        _f = _mk_field()
        _f._layer_pools[IF._PULSE_LAYER_L2] = _BadPool()
        with self.assertLogs(IF._module_logger, level="WARNING") as _cm:
            _f.reload_l2_config()
        _joined = "\n".join(_cm.output)
        self.assertIn("max_workers", _joined, "日志应点名失败的字段")
        self.assertIn("boom-m62", _joined, "日志应带上异常信息")
        self.assertIn("RuntimeError", _joined, "日志应带上异常类型")

    def test_11_still_returns_cfg_on_failure(self):
        """异常不得中断流程 —— 仍返回 _cfg（保留原 return 语义）。"""
        _f = _mk_field()
        _f._layer_pools[IF._PULSE_LAYER_L2] = _BadPool()
        with self.assertLogs(IF._module_logger, level="WARNING"):
            _cfg = _f.reload_l2_config()
        self.assertIsInstance(_cfg, dict)
        self.assertIn("workers", _cfg)

    def test_12_success_path_sets_workers(self):
        """正常路径：max_workers 被写入，且不产生 warning。"""
        _f = _mk_field()
        _pool = _GoodPool()
        _f._layer_pools[IF._PULSE_LAYER_L2] = _pool
        _cfg = _f.reload_l2_config()
        self.assertEqual(_pool._max_workers, _cfg["workers"])

    def test_13_no_pool_is_fine(self):
        """没有 L2 线程池时不应抛异常（原逻辑分支保留）。"""
        _f = _mk_field()
        _cfg = _f.reload_l2_config()
        self.assertIsInstance(_cfg, dict)


if __name__ == "__main__":
    unittest.main()
