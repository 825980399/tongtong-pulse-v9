# -*- coding: utf-8 -*-
"""主线第10批 任务1（P2-77）：控制器常驻浏览器断开修复验证。

验证点：
1. 断开原因分类 `_classify_browser_error`（超时/崩溃/网络错误/未知）。
2. 保活 `_keepalive_touch`：正常触活不置空；失败时记录原因并主动标记浏览器失效。
3. 重建路径 `_ensure_headless_browser`：3次确认断开后记录原因日志并自动重建成功。
4. 配置化定期重启默认关闭（headless_restart_interval_seconds=0），不改变原有行为。
"""
from unittest.mock import MagicMock

import organs.motor.PulseController as pc_module
from nucleus.const import LogLevel
from organs.motor.PulseController import PulseController


def _minimal_engine():
    eng = PulseController.__new__(PulseController)
    eng.is_running = True
    eng._log_calls = []
    eng._log = lambda lvl, msg: eng._log_calls.append((lvl, msg))
    eng._browser_rebuild_cooldown_until = 0.0
    eng._browser_rebuild_failures = 0
    eng._headless_active = True
    eng._headless_last_used = 0.0
    eng._headless_browser = None
    eng._headless_page = None
    eng._playwright = None
    eng._get_playless = None
    eng._get_headless_config = lambda k, d=None: d
    eng._current_load_level = lambda: "normal"
    eng._get_playwright_executor = lambda: MagicMock(submit=lambda f: f())
    return eng


def test_classify_browser_error_categories():
    eng = _minimal_engine()

    assert eng._classify_browser_error(Exception("Timeout 30000ms exceeded")) == "超时"
    assert eng._classify_browser_error(Exception("net::ERR_CONNECTION_CLOSED")) == "网络错误"
    assert eng._classify_browser_error(Exception("Target page closed")) == "崩溃/连接断开"
    assert eng._classify_browser_error(Exception("Execution context was destroyed")) == "崩溃/连接断开"
    assert eng._classify_browser_error(Exception("something weird")) == "未知"


def test_keepalive_touch_success_no_null():
    eng = _minimal_engine()
    page = MagicMock(name="page")
    eng._headless_browser = "browser"
    eng._headless_page = page
    eng._keepalive_touch()
    # 正常触活：更新 last_used，不置空浏览器
    assert eng._headless_browser == "browser"
    assert eng._headless_page is page
    assert page.goto.called
    assert not any(l[0] == LogLevel.WARNING for l in eng._log_calls)


def test_keepalive_touch_failure_logs_and_marks_dead():
    eng = _minimal_engine()
    page = MagicMock(name="page")
    page.goto.side_effect = Exception("Target page closed")
    eng._headless_browser = "browser"
    eng._headless_page = page
    eng._keepalive_touch()
    # 严重失效：主动标记浏览器为失效，便于下次使用重建
    assert eng._headless_browser is None
    assert eng._headless_page is None
    # 记录断开原因日志
    warns = [m for lvl, m in eng._log_calls if lvl == LogLevel.WARNING]
    assert warns, "保活失败应记录 WARNING"
    assert "崩溃/连接断开" in warns[0]


def test_ensure_headless_browser_rebuilds_after_disconnect(monkeypatch):
    eng = _minimal_engine()
    # 模拟一个已存在但已断开的浏览器（title() 连续抛错）
    dead_page = MagicMock(name="dead_page")
    dead_page.title.side_effect = Exception("Target page closed")
    eng._headless_browser = "old_browser"
    eng._headless_page = dead_page

    # 伪造 playwright 启动链路，使重建成功
    fake_pw = MagicMock(name="playwright")
    browser = MagicMock(name="browser")
    ctx = MagicMock(name="ctx")
    new_page = MagicMock(name="new_page")
    fake_pw.start.return_value = fake_pw
    fake_pw.chromium.launch.return_value = browser
    browser.new_context.return_value = ctx
    ctx.new_page.return_value = new_page

    monkeypatch.setattr(pc_module, "PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(pc_module, "sync_playwright", lambda: fake_pw)

    ok = eng._ensure_headless_browser()
    assert ok is True, "断开后应能自动重建并返回可用浏览器"
    assert eng._headless_page is new_page
    # 断开原因日志应包含 '原因='
    warns = [m for lvl, m in eng._log_calls if lvl == LogLevel.WARNING]
    assert any("原因=" in m for m in warns), "应记录断开原因（超时/崩溃/网络）"


def test_restart_interval_default_disabled():
    # 默认配置为 0（禁用），不改变原有常驻行为
    from config import HEADLESS_BROWSER
    assert HEADLESS_BROWSER.get("headless_restart_interval_seconds", 0) == 0
