# -*- coding: utf-8 -*-
"""413 刀2 门控：PulseMetricsCollector 411刀1 `_module_logger` NameError 修复。

A 组 源码契约（AST/文本）—— 符号已消、调用已改成 self._log
B 组 行为          —— 真实调用不再抛 NameError，各分支返回结构正确
C 组 先红对照      —— 用 .bak_batch413 的**改前原文**切片 exec，证明它确实会 NameError
D 组 隔离/回归     —— 落盘走隔离根，不写生产 data/
"""
import ast
import io
import os
import shutil
import sys
import tempfile
import textwrap
import time
import unittest
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from nucleus.observability import memory_by_organ as MO  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_REL = "organs/core/PulseMetricsCollector.py"
_SRC = os.path.join(_ROOT, _SRC_REL.replace("/", os.sep))
_BAK = os.path.join(_ROOT, ".bak_batch413",
                    _SRC_REL.replace("/", "__") + ".bak")

_OLD_ANCHOR = '        _module_logger.debug("[411刀1] 器官内存采样: state=%s elapsed=%sms top3=%s",'
_NEW_CALL = '        self._log(LogLevel.DEBUG,'


def _read(p):
    return io.open(p, encoding="utf-8", errors="replace").read()


def _slice_method(src_text, name, next_name):
    """从源码文本切出 `def <name>(...)` 到 `def <next_name>` 之间的整段。"""
    a = src_text.find("    def %s(" % name)
    b = src_text.find("    def %s(" % next_name)
    if a < 0 or b < 0 or b <= a:
        return ""
    return src_text[a:b]


class TestA_SourceContract(unittest.TestCase):
    """A 组：源码里 `_module_logger` 已彻底消失，且替换成类内既有日志方法。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(_SRC)

    def test_A1_no_module_logger_symbol(self):
        """A1：本文件不得再**引用** `_module_logger` 这一未定义符号。

        ★用 AST 取 `ast.Name`，注释/docstring 里提到该名字不算（铁律：文本扫描
          会被自己写的说明注释命中）。
        """
        tree = ast.parse(self.src)
        _lines = [n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Name) and n.id == "_module_logger"]
        self.assertEqual(_lines, [], "★ _module_logger 仍被引用 @L%s" % _lines)

    def test_A2_replaced_with_self_log(self):
        """A2：411刀1 的采样日志已改为类内 `self._log(LogLevel.DEBUG, ...)`。"""
        self.assertIn(_NEW_CALL, self.src)
        self.assertNotIn(_OLD_ANCHOR, self.src)

    def test_A3_loglevel_imported(self):
        """A3：`LogLevel` 必须已在本文件 import（否则新写法自己就 F821）。"""
        tree = ast.parse(self.src)
        _names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                for a in n.names:
                    _names.add(a.asname or a.name)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    _names.add((a.asname or a.name).split(".")[0])
        self.assertIn("LogLevel", _names)

    def test_A4_return_out_after_log(self):
        """A4：日志必须在 `return _out` **之前** —— 否则丢了返回值的坑还在。"""
        blk = _slice_method(self.src, "_m411_memory_by_organ", "_m411_mbo_last_reading")
        self.assertTrue(blk, "★ 切片失败：方法边界变了")
        _i_log = blk.rfind("self._log(")
        _i_ret = blk.rfind("return _out")
        self.assertGreater(_i_log, 0)
        self.assertGreater(_i_ret, _i_log, "★ 日志在 return 之后，改法无效")

    def test_A5_top3_none_safe(self):
        """A5：`top3` 取 None 时不得 TypeError（旧版 `_out['top3']` 会炸）。"""
        self.assertIn('(_out.get("top3") or [])', self.src)


class TestB_Behavior(unittest.TestCase):
    """B 组：真实调用不抛异常、各分支结构正确。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m413k2_")
        MO.set_data_dir(self._tmp)
        self._logs = []

    def tearDown(self):
        MO.set_data_dir(None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _mk(self):
        """轻量实例：只挂被测方法需要的属性。"""
        from organs.core.PulseMetricsCollector import PulseMetricsCollector as C
        o = C.__new__(C)

        def _log(level, message, error_code=None):
            self._logs.append(str(message))

        o._log = _log
        return o

    def test_B1_no_nameerror(self):
        """B1：★核心 —— 真实采样拍不再抛 NameError。"""
        o = self._mk()
        try:
            r = o._m411_memory_by_organ(1024.0)
        except NameError as e:
            self.fail("★ 仍抛 NameError：%s" % e)
        self.assertIsInstance(r, dict)

    def test_B2_state_is_known(self):
        """B2：返回结构含合法 state。"""
        o = self._mk()
        r = o._m411_memory_by_organ(1024.0)
        self.assertIn(r.get("state"),
                      ("ok", "throttled", "disabled", "unavailable", "empty"))

    def test_B3_log_emitted(self):
        """B3：采样成功时打了 [411刀1] 日志（证明走到日志那行且没炸）。"""
        o = self._mk()
        r = o._m411_memory_by_organ(1024.0)
        if r.get("state") == "ok":
            self.assertTrue(any("[411刀1]" in x for x in self._logs),
                            "★ 没有 [411刀1] 日志：%r" % (self._logs,))

    def test_B4_no_nameerror_word_in_log(self):
        """B4：日志里不得出现 NameError 字样。"""
        o = self._mk()
        o._m411_memory_by_organ(1024.0)
        self.assertFalse(any("NameError" in x for x in self._logs))

    def test_B5_throttle_returns_cache(self):
        """B5：节流未到点时返回上次读数（不重新采样）。"""
        o = self._mk()
        r1 = o._m411_memory_by_organ(1024.0)
        r2 = o._m411_memory_by_organ(1024.0)
        self.assertEqual(r2.get("ts"), r1.get("ts"), "★ 第二次应返回缓存读数")
        self.assertEqual(len(self._logs), 1, "★ 节流拍不应再打日志")

    def test_B6_disabled_switch(self):
        """B6：开关关闭 ⇒ state=disabled 且不采样。"""
        _old = getattr(config, "ENABLE_M411_MEMORY_BY_ORGAN", True)
        config.ENABLE_M411_MEMORY_BY_ORGAN = False
        try:
            o = self._mk()
            r = o._m411_memory_by_organ(1024.0)
            self.assertEqual(r.get("state"), "disabled")
            self.assertEqual(self._logs, [])
        finally:
            config.ENABLE_M411_MEMORY_BY_ORGAN = _old

    def test_B7_returns_out_on_sample(self):
        """B7：采样拍必须**返回** _out（旧版在此处炸掉，返回 None）。"""
        o = self._mk()
        r = o._m411_memory_by_organ(1024.0)
        if r.get("state") != "throttled":
            self.assertIsNotNone(r.get("state"))
            self.assertIn("rss_separable", r)
            self.assertFalse(r["rss_separable"])


class TestC_RedProof(unittest.TestCase):
    """C 组：用 .bak_batch413 的改前原文复算 —— 证明旧实现确实 NameError。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m413k2c_")
        MO.set_data_dir(self._tmp)

    def tearDown(self):
        MO.set_data_dir(None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_C1_backup_exists(self):
        self.assertTrue(os.path.isfile(_BAK), "★ 缺 .bak_batch413 基线，先跑 tmp/m413_backup.py")

    def test_C2_old_source_has_the_bug(self):
        """C2：改前原文含 `_module_logger.debug(` 且全文无该符号定义。"""
        old = _read(_BAK)
        self.assertIn("_module_logger.debug(", old)
        _defs = [ln for ln in old.split("\n")
                 if ln.lstrip().startswith("_module_logger")
                 and "=" in ln and "debug" not in ln]
        self.assertEqual(_defs, [], "★ 改前若有定义，则不是「未定义」型缺陷")

    def test_C3_old_slice_raises_nameerror(self):
        """C3：★先红 —— exec 改前真实切片并调用，必须抛 NameError。

        旧切片在新进程语义下照原样执行（只有 `_log` 由替身提供），
        若它不抛 NameError，说明缺陷定性错了。
        """
        old = _read(_BAK)
        blk = _slice_method(old, "_m411_memory_by_organ", "_m411_mbo_last_reading")
        self.assertTrue(blk, "★ 旧文件切片失败")
        code = textwrap.dedent(blk).replace("\r\n", "\n")
        self.assertIn("_module_logger.debug(", code)

        from nucleus._silent_except import silent_exc
        ns = {"time": time, "Any": Any, "silent_exc": silent_exc}

        class _Fake:
            _m411_mbo_last = 0.0

            def _log(self, *a, **k):
                pass

        exec(compile(code, "<bak413>", "exec"), ns)  # noqa: S102 - 仅 exec 本仓备份源码
        fn = ns["_m411_memory_by_organ"]
        with self.assertRaises(NameError) as cm:
            fn(_Fake(), 1024.0)
        self.assertIn("_module_logger", str(cm.exception))

    def test_C4_new_slice_does_not_raise(self):
        """C4：后绿 —— 当前源码的同一段切片 exec 后**不**抛 NameError。"""
        cur = _read(_SRC)
        blk = _slice_method(cur, "_m411_memory_by_organ", "_m411_mbo_last_reading")
        self.assertTrue(blk)
        code = textwrap.dedent(blk).replace("\r\n", "\n")
        _tree = ast.parse(code)
        self.assertEqual(
            [n.lineno for n in ast.walk(_tree)
             if isinstance(n, ast.Name) and n.id == "_module_logger"], [],
            "★ 当前切片仍引用 _module_logger")

        from nucleus._silent_except import silent_exc
        from nucleus.const import LogLevel
        ns = {"time": time, "Any": Any, "silent_exc": silent_exc, "LogLevel": LogLevel}

        class _Fake:
            _m411_mbo_last = 0.0

            def __init__(self):
                self.calls = []

            def _log(self, *a, **k):
                self.calls.append(a)

        exec(compile(code, "<cur413>", "exec"), ns)  # noqa: S102
        f = _Fake()
        fn = ns["_m411_memory_by_organ"]
        r = fn(f, 1024.0)
        self.assertIsInstance(r, dict)
        self.assertTrue(f.calls, "★ 当前切片没有走 _log 分支")


class TestD_Isolation(unittest.TestCase):
    """D 组：落盘走隔离根，绝不写生产 data/。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m413k2d_")
        MO.set_data_dir(self._tmp)

    def tearDown(self):
        MO.set_data_dir(None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_D1_data_root_is_tmp(self):
        self.assertEqual(os.path.abspath(MO.data_root()), os.path.abspath(self._tmp))

    def test_D2_record_written_into_tmp(self):
        """D2：采样产物的落点必须在隔离根内。"""
        from organs.core.PulseMetricsCollector import PulseMetricsCollector as C
        o = C.__new__(C)
        o._log = lambda *a, **k: None
        r = o._m411_memory_by_organ(1024.0)
        _p = str(r.get("path") or "")
        if _p:
            self.assertTrue(os.path.abspath(_p).startswith(os.path.abspath(self._tmp)),
                            "★ 落盘跑出隔离根：%s" % _p)

    def test_D3_no_write_to_prod_monitor(self):
        """D3：跑完之后生产 data/monitor 不应出现本测试新建的文件。"""
        prod = os.path.join(_ROOT, "data", "monitor")
        before = set(os.listdir(prod)) if os.path.isdir(prod) else set()
        from organs.core.PulseMetricsCollector import PulseMetricsCollector as C
        o = C.__new__(C)
        o._log = lambda *a, **k: None
        o._m411_memory_by_organ(1024.0)
        after = set(os.listdir(prod)) if os.path.isdir(prod) else set()
        self.assertEqual(sorted(after - before), [], "★ 写了生产 data/monitor")


if __name__ == "__main__":
    unittest.main(verbosity=2)
