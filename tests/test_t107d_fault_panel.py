# -*- coding: utf-8 -*-
"""往期批次 相关任务（Dxxx）：故障面板补充验证（离线单测，不依赖框架运行）。

复用往期批次 相关任务 的 ErrorCaptureHandler 机制，针对「真实故障经由器官 logger
（pulse.* 命名空间）注入 ERROR 后能否进 error_snapshots、并被 5051 面板数据通道读取」
做回归锁定，确保故障面板对运行时 ERROR 不再全盲。
"""
import time
import logging

import pytest

from nucleus.runtime_metrics import (
    get_runtime_metrics,
    install_error_capture,
    ErrorCaptureHandler,
)


def _snap_count():
    return len(get_runtime_metrics().get_snapshot()["error_snapshots"])


def test_organ_logger_error_reaches_panel_snapshots():
    """★相关任务：器官 logger（pulse.* 命名空间）的 ERROR 自动进 error_snapshots（面板可见）。"""
    install_error_capture("pulse")
    # 与器官 logger 同命名空间（BasePulseOrgan._log 走 pulse.*）
    lg = logging.getLogger("pulse.t107d_organ")
    before = _snap_count()
    lg.error("T-107d 注入故障：%s", "organ-fault-107d")
    time.sleep(0.3)
    after = get_runtime_metrics().get_snapshot()["error_snapshots"]
    assert len(after) > before, "器官 ERROR 应进入 error_snapshots"
    assert any("organ-fault-107d" in s["error"] for s in after), "应包含注入的 ERROR 文本"


def test_error_snapshots_exposed_to_health_panel():
    """★相关任务：error_snapshots 是 5051 面板数据源，须可直接读取（面板不盲）。"""
    install_error_capture("pulse")
    lg = logging.getLogger("pulse.t107d_panel")
    before = _snap_count()
    lg.error("T-107d 面板数据源校验：%s", "panel-src-107d")
    time.sleep(0.3)
    errs = get_runtime_metrics().get_snapshot().get("error_snapshots", [])
    assert len(errs) > before
    assert any("panel-src-107d" in e["error"] for e in errs)
    # 5051 面板/HTTP 读取的就是 runtime_metrics 的 error_snapshots（health_ui 已消费）


def test_error_capture_still_idempotent():
    """★相关任务：回归——重复安装不应挂多个 handler（防回潮）。"""
    install_error_capture("pulse")
    second = install_error_capture("pulse")
    assert second is False
    _lg = logging.getLogger("pulse")
    _n = sum(1 for h in _lg.handlers if isinstance(h, ErrorCaptureHandler))
    assert _n == 1, f"pulse logger 上 ErrorCaptureHandler 数应为1，实为{_n}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
