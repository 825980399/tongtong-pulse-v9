# -*- coding: utf-8 -*-
"""第104批 T-104c（D169）：ERROR -> error_snapshots 桥接 + 5051 存活感知。

离线单测（不依赖框架运行）。验证：
1. pulse.* 的 ERROR/CRITICAL 日志自动进入 error_snapshots（面板/HTTP 可见）。
2. WARNING/DEBUG 不进入（级别过滤）。
3. install_error_capture 幂等。
4. HealthUIServer 看门狗在 serve 线程意外死亡时记录 5051 故障。
"""
import logging
import time

import pytest

from nucleus.runtime_metrics import (
    ErrorCaptureHandler,
    get_runtime_metrics,
    install_error_capture,
)


def _snap_count():
    return len(get_runtime_metrics().get_snapshot()["error_snapshots"])


def test_error_capture_handler_bridges_error_to_snapshots():
    """★D169 核心：logger.ERROR 不再"全盲"，自动进 error_snapshots。"""
    install_error_capture("pulse")
    lg = logging.getLogger("pulse.test_t104c_err")
    before = _snap_count()
    lg.error("单元测试注入的 ERROR 现场：%s", "fault-abc-123")
    # 聚合线程立即消费队列，稍候即可见
    time.sleep(0.3)
    after = get_runtime_metrics().get_snapshot()["error_snapshots"]
    assert len(after) > before, "ERROR 应进入 error_snapshots"
    assert any("fault-abc-123" in s["error"] for s in after), "应包含注入的 ERROR 文本"


def test_error_capture_handler_ignores_warning():
    """级别过滤：WARNING 不应进 error_snapshots（避免刷屏）。"""
    install_error_capture("pulse")
    lg = logging.getLogger("pulse.test_t104c_warn")
    before = _snap_count()
    lg.warning("仅告警，不应被捕获：%s", "warn-only")
    time.sleep(0.2)
    after = _snap_count()
    assert after == before, "WARNING 不应进入 error_snapshots"


def test_error_capture_handler_captures_critical():
    install_error_capture("pulse")
    lg = logging.getLogger("pulse.test_t104c_crit")
    before = _snap_count()
    lg.critical("致命错误现场：%s", "crit-xyz")
    time.sleep(0.3)
    after = get_runtime_metrics().get_snapshot()["error_snapshots"]
    assert len(after) > before
    assert any("crit-xyz" in s["error"] for s in after)


def test_install_error_capture_idempotent():
    """重复安装应幂等，不会挂多个 handler。"""
    install_error_capture("pulse")
    second = install_error_capture("pulse")
    assert second is False
    # 确认 pulse logger 上仅一个 ErrorCaptureHandler
    _lg = logging.getLogger("pulse")
    _n = sum(1 for h in _lg.handlers if isinstance(h, ErrorCaptureHandler))
    assert _n == 1, f"pulse logger 上 ErrorCaptureHandler 数应为1，实为{_n}"


def test_health_ui_watchdog_detects_5051_death():
    """★D169 子项：5051 监控面板 serve 线程意外死亡 -> 记入 error_snapshots（治理零感知）。"""
    import functions.health_ui as hui

    class _StubServer:
        def serve_forever(self):
            return  # 立即返回 -> serve 线程随即死亡

        def shutdown(self):
            pass

    _orig = hui.ThreadingHTTPServer
    hui.ThreadingHTTPServer = lambda *a, **k: _StubServer()
    _orig_sleep = time.sleep
    time.sleep = lambda x: None  # 加速看门狗巡检
    srv = None
    try:
        srv = hui.HealthUIServer(port=5051)
        srv.start()  # serve 线程立即死；看门狗线程应快速感知
        if srv._watchdog is not None:
            srv._watchdog.join(timeout=5)
        snaps = get_runtime_metrics().get_snapshot()["error_snapshots"]
        assert any("5051" in s["error"] for s in snaps), "看门狗应记录 5051 服务线程死亡"
    finally:
        time.sleep = _orig_sleep
        hui.ThreadingHTTPServer = _orig
        if srv is not None:
            try:
                srv.stop()
            except Exception:
                pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
