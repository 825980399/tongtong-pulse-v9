# -*- coding: utf-8 -*-
"""主线第12批 T1：浏览器保活线程安全（方案A + 属主线程模式）—— 门控单测。

覆盖：
  - 属主线程模式：预热提交到 Playwright 专用池（创建线程名含 Playwright）
  - 保活信号：touch 在池线程执行、due 复位、重复信号去重
  - 灰度开关存在性与默认值
  - 无浏览器时不提交（零开销）
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg  # noqa: E402
from organs.motor.PulseController import PulseController  # noqa: E402


class _SilentLog:
    def __call__(self, *a, **k):
        return None


def _bare_controller():
    c = PulseController.__new__(PulseController)
    c._log = _SilentLog()
    c._playwright_lock = threading.Lock()
    c._playwright_executor = None
    c._keepalive_due = False
    c._keepalive_lock = threading.Lock()
    c._headless_browser = None
    c._headless_page = None
    c._headless_active = False
    return c


def test_playwright_executor_singleton_single_worker():
    c = _bare_controller()
    e1 = c._get_playwright_executor()
    e2 = c._get_playwright_executor()
    assert e1 is e2
    assert e1._max_workers == 1


def test_prewarm_runs_in_playwright_pool_thread():
    """属主线程模式：创建浏览器的线程应是 Playwright 专用池线程。"""
    c = _bare_controller()
    seen = {}

    def _fake_ensure():
        seen["thread"] = threading.current_thread().name

    c._get_playwright_executor().submit(_fake_ensure)
    time.sleep(0.3)
    assert str(seen.get("thread", "")).startswith("Playwright")


def test_keepalive_signal_runs_touch_in_pool_thread():
    c = _bare_controller()
    c._headless_browser = object()
    c._headless_page = object()
    touched = {}

    def _fake_touch():
        touched["thread"] = threading.current_thread().name

    c._keepalive_touch = _fake_touch
    c._signal_keepalive()
    time.sleep(0.4)
    assert str(touched.get("thread", "")).startswith("Playwright")
    assert c._keepalive_due is False


def test_keepalive_signal_dedup():
    """due 已置位时再次 _signal_keepalive 不重复提交（锁语义，确定性验证）。"""
    c = _bare_controller()
    # 手动置 due=True（模拟"上一拍尚未被消费"），再发信号应被跳过
    c._keepalive_due = True
    cnt = {"n": 0}

    def _counting_touch():
        cnt["n"] += 1

    c._keepalive_touch = _counting_touch
    c._signal_keepalive()   # due 已置位 → 直接 return，不提交
    time.sleep(0.2)
    assert cnt["n"] == 0
    assert c._keepalive_due is True


def test_keepalive_signal_no_browser_no_submit():
    c = _bare_controller()
    c._signal_keepalive()
    time.sleep(0.2)
    # 无浏览器时 _check_keepalive 直接返回，不应有副作用
    assert c._keepalive_due is False


def test_switches_exist_and_default_true():
    assert hasattr(cfg, "HEADLESS_BROWSER")
    hb = cfg.HEADLESS_BROWSER
    assert hb.get("headless_keepalive_signal_mode", None) is True
    assert hb.get("headless_owner_thread_mode", None) is True


def test_controller_mode_methods_present():
    c = _bare_controller()
    assert hasattr(c, "_keepalive_signal_mode")
    assert hasattr(c, "_owner_thread_mode")
    assert hasattr(c, "_check_keepalive")
    assert hasattr(c, "_signal_keepalive")
