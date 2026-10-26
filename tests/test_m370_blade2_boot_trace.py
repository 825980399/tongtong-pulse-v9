# -*- coding: utf-8 -*-
"""370 刀2 定向单测：L0 后台自检**超时/心跳留痕**补强（不动同步语义）。

★被测面：`organs/core/PulseSystemManager.py::_run_boot_checks` 与 `_on_system_boot`。
★注入口径：薄壳借用被测方法，检查函数以 lambda 注入；**不改真实器官、不落生产目录**。
★并发语义零改动是本刀红线：④ 专门盯 `start()` 立即返回 pending 且不阻塞调用方。
"""
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.const import LogLevel  # noqa: E402
from organs.core import PulseSystemManager as _pm_mod  # noqa: E402
from organs.core.PulseSystemManager import PulseSystemManager  # noqa: E402


class _Harness:
    """薄壳：只借用被测方法，检查项以可注入 lambda 替代（避免真实 subprocess）。"""

    _run_boot_checks = PulseSystemManager._run_boot_checks
    _on_system_boot = PulseSystemManager._on_system_boot

    def __init__(self, svc_ret=None, hw_ret=None, svc_delay=0.0,
                 hw_delay=0.0, hw_exc=None):
        self.logs = []
        self._running = False
        self._required_dirs: list = []
        self._scheduled = 0
        self._svc_ret = svc_ret if svc_ret is not None else {"ollama": "running"}
        self._hw_ret = hw_ret if hw_ret is not None else {"memory_ok": True}
        self._svc_delay = svc_delay
        self._hw_delay = hw_delay
        self._hw_exc = hw_exc
        self._last_boot_services = None
        self._last_boot_hardware = None

    # ---- 可注入检查项 ----
    def _check_and_repair_services(self):
        if self._svc_delay:
            time.sleep(self._svc_delay)
        return self._svc_ret

    def _check_hardware(self):
        if self._hw_delay:
            time.sleep(self._hw_delay)
        if self._hw_exc is not None:
            raise self._hw_exc
        return self._hw_ret

    def _schedule_health_check(self):
        self._scheduled += 1

    def _log(self, level, msg):
        self.logs.append((level, msg))

    def has(self, level, needle):
        return any(lv == level and needle in str(m) for lv, m in self.logs)


# ------------------------------------------------------------------ 用例
def test_01_trace_fields_complete():
    """① 后台自检起点／完成／分段耗时留痕字段齐备。"""
    h = _Harness(svc_delay=0.01, hw_delay=0.01)
    h._run_boot_checks()

    assert h.has(LogLevel.INFO, "后台自检开始"), "缺起点留痕"
    assert h.has(LogLevel.INFO, "后台自检完成"), "缺完成留痕"
    assert h.has(LogLevel.INFO, "分段"), "完成日志缺分段耗时字段"

    meta = h._last_boot_check_meta
    for k in ("state", "started_ts", "finished_ts", "total_s",
              "budget_s", "segments", "services_n", "memory_ok", "over_budget"):
        assert k in meta, "留痕 meta 缺字段 %s" % k
    assert meta["state"] == "ok"
    assert set(meta["segments"]) == {"services", "hardware"}, "分段耗时须两段齐备"
    assert meta["services_n"] == 1 and meta["memory_ok"] is True
    # ★口径对齐：结果字段与既有 _last_boot_services/_last_boot_hardware 一致
    assert h._last_boot_services == {"ollama": "running"}
    assert h._last_boot_hardware == {"memory_ok": True}


def test_02_over_budget_warns(monkeypatch):
    """② 总耗时超 L0 预算 ⇒ WARNING 留痕，且**不中断**后台线程（后续仍跑完）。"""
    monkeypatch.setattr(_pm_mod, "_boot_check_budget_s", lambda: 0.01)
    h = _Harness(svc_delay=0.03, hw_delay=0.03)
    h._run_boot_checks()

    assert h.has(LogLevel.WARNING, "超预算"), "超预算未告警"
    assert h._last_boot_check_meta["over_budget"] is True
    assert h._last_boot_check_meta["total_s"] > 0.01
    # ★不中断：超预算后硬件段仍执行完（结果字段照旧落）
    assert h._last_boot_hardware == {"memory_ok": True}, "超预算不得中断后台自检"
    assert h.has(LogLevel.INFO, "后台自检完成"), "超预算后仍须完成并留痕"


def test_03_exception_falls_into_fields_with_error():
    """③ 异常分支：失败态落字段＋ERROR 留痕（含分段位置），不吞异常。"""
    h = _Harness(svc_delay=0.01, hw_exc=RuntimeError("模拟硬件检测炸了"))
    h._run_boot_checks()

    meta = h._last_boot_check_meta
    assert meta["state"] == "failed", "异常未落失败态"
    assert meta["failed_at"] == "hardware", "未记录分段位置（实际 %r）" % meta["failed_at"]
    assert meta["error_type"] == "RuntimeError"
    assert "模拟硬件检测炸了" in (meta["error_msg"] or "")

    assert h.has(LogLevel.ERROR, "后台自检异常"), "异常未 ERROR 留痕"
    assert h.has(LogLevel.ERROR, "hardware"), "ERROR 留痕缺分段位置"
    # ★失败态空值口径：服务段结果保留、硬件段落 error 说明，而不是留 None 让人读成"没跑"
    assert h._last_boot_services == {"ollama": "running"}, "已跑完的分段结果不得被清空"
    assert isinstance(h._last_boot_hardware, dict), "失败态不得留 None"
    assert "error" in h._last_boot_hardware, "失败态硬件字段缺 error 说明"


def test_04_start_returns_pending_without_blocking(tmp_path):
    """④ ★红线：`_on_system_boot` 仍立即返回 pending，重任务不阻塞调用方。"""
    d = tmp_path / "req"
    h = _Harness(svc_delay=0.30, hw_delay=0.30)  # 后台合计 ~0.6s
    h._required_dirs = [str(d)]

    t0 = time.time()
    ret = h._on_system_boot({})
    cost = time.time() - t0

    assert ret == {"boot": "started", "services": "pending",
                   "hardware": "pending"}, "start() 返回口径被改动：%r" % ret
    assert cost < 0.25, "start() 被后台重任务阻塞（耗时 %.3fs）" % cost
    assert os.path.isdir(str(d)), "轻量建目录仍须同步完成"
    assert h._scheduled == 1, "周期健康检查接线不得丢"

    # 后台线程随后把结果填上（并发语义仍在）
    # ★等「整轮跑完」而不是「首个结果字段非 None」——后者会在硬件段未结束时提前退出
    deadline = time.time() + 5
    while time.time() < deadline and (
            getattr(h, "_last_boot_check_meta", {}).get("state") == "running"):
        time.sleep(0.02)
    assert h._last_boot_services == {"ollama": "running"}, "后台线程未回填结果"
    assert h._last_boot_hardware == {"memory_ok": True}, "后台线程未回填硬件结果"
    assert h._last_boot_check_meta["state"] == "ok"


def test_05_results_not_regressed():
    """⑤ 既有服务检查／硬件检测结果口径不回归（多服务项 / 内存告警两种形态）。"""
    svc = {"ollama": "running", "neo4j": "stopped", "influxdb": "running"}
    h = _Harness(svc_ret=svc, hw_ret={"memory_ok": False, "mem_pct": 91.2})
    h._run_boot_checks()

    assert h._last_boot_services == svc
    assert h._last_boot_hardware == {"memory_ok": False, "mem_pct": 91.2}
    assert h._last_boot_check_meta["services_n"] == 3
    assert h._last_boot_check_meta["memory_ok"] is False
    assert h.has(LogLevel.INFO, "内存告警"), "完成日志的告警措辞口径须保留"
    assert not h.has(LogLevel.WARNING, "超预算"), "未超预算不得误报"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
