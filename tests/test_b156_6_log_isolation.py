# -*- coding: utf-8 -*-
"""B156-6 落盘隔离门控测试。

验证两点：
  1. ``logs/`` 隔离真正生效 —— 指向项目 ``logs/`` 的 FileHandler 被挂上丢弃过滤器
     （修复前依赖 gitignored 的 tmp.test_log_isolation，静默降级为 no-op）；
  2. ``data/reports/*.jsonl`` 在 pytest 环境下被 ``write_guard`` 拒写
     （隔离已满足，本测试固化该契约，防回归）。
"""
import importlib.util
import logging
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_conftest():
    _p = os.path.join(ROOT, "tests", "conftest.py")
    _spec = importlib.util.spec_from_file_location("b156_conftest_under_test", _p)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod


class TestLogsIsolation:
    def test_mutes_handler_pointing_at_logs_dir(self):
        _mod = _load_conftest()
        _logs_dir = os.path.join(ROOT, "logs")
        os.makedirs(_logs_dir, exist_ok=True)
        _probe = os.path.join(_logs_dir, "_b156_6_probe.log")
        _h = logging.FileHandler(_probe, encoding="utf-8")
        logging.root.addHandler(_h)
        try:
            _muted = _mod._mute_project_logs_handlers()
            # 断言：指向 logs/ 的 handler 被识别并挂上过滤器
            assert any(_hh is _h for _hh, _f in _muted), "logs/ handler 未被隔离"
            _f = dict(_muted)[_h]
            _rec = logging.LogRecord("x", 0, "x", 0, "m", None, None)
            # 过滤器应丢弃记录（隔离噪声不落生产日志）
            assert _f(_rec) is False
        finally:
            _mod._restore_muted_logs_handlers(_muted)
            logging.root.removeHandler(_h)

    def test_non_logs_handler_left_untouched(self, tmp_path):
        _mod = _load_conftest()
        _other = os.path.join(str(tmp_path), "other.log")
        _h = logging.FileHandler(_other, encoding="utf-8")
        logging.root.addHandler(_h)
        try:
            _muted = _mod._mute_project_logs_handlers()
            assert all(_hh is not _h for _hh, _f in _muted), "非 logs/ handler 被误隔离"
        finally:
            _mod._restore_muted_logs_handlers(_muted)
            logging.root.removeHandler(_h)


class TestDataReportsIsolation:
    def test_reports_jsonl_rejected_in_test_env(self):
        from nucleus.data.write_guard import reject_write

        _p = os.path.join(ROOT, "data", "reports", "alerts.jsonl")
        # pytest 环境下不得写生产 data/（含 data/reports/*.jsonl）
        assert reject_write(_p, explicit=False, component="B156-6-test") is True

    def test_explicit_injection_still_allowed(self):
        from nucleus.data.write_guard import reject_write

        _p = os.path.join(ROOT, "data", "reports", "alerts.jsonl")
        # 显式注入隔离目录 → 放行（与 call_recorder 守卫同源语义）
        assert reject_write(_p, explicit=True, component="B156-6-test") is False
