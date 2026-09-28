"""第82批 T-a：tools/_framework_probe 换 psutil 主探 + wmic 兜底 + 异常显式 WARNING。

背景（总账 185.5 / D-新记工具链债务）：
  旧版只走 wmic（已被微软弃用，且易被沙箱 Program Blacklist 拦截），
  且 `except Exception: return True` 静默保守 —— 路灯沙箱里 wmic 被拦时
  verify 误判「框架在跑」而跳过重负载用例，"0 失败"表象掩盖了少跑用例。

本测试锁定新契约：
  1. psutil 主探：有 main.py python 进程 → True；无 → False（走 psutil，非 wmic）。
  2. psutil 不可用（import 失败）→ 回退 wmic，并打 WARNING。
  3. 探测异常 → 仍保守 True（写盘守卫安全契约不变），**且** logging.WARNING 必出
     （旧版静默 True 无日志，本断言对旧实现必红）。
  4. cmdline 语义：只有「最后一段以 main.py 结尾」的 python 进程才算框架，
     避免把 `python -c "...main.py..."` 这种自检进程误判为框架在跑。
"""
from __future__ import annotations

import logging

import tools._framework_probe as probe_mod
from tools._framework_probe import _framework_looks_running


# ---------- 4. cmdline 判定语义（纯函数，最关键：防自检误报） ----------

class TestCmdlineIsFramework:
    def test_exact_main_py(self):
        assert probe_mod._cmdline_is_framework(["D:/py/python.exe", "main.py"]) is True

    def test_trailing_main_py_with_path(self):
        # 框架实际启动：`D:/Program Files/Python312/python.exe main.py`
        assert probe_mod._cmdline_is_framework(
            ["D:/Program Files/Python312/python.exe", "main.py"]
        ) is True

    def test_self_invoke_containing_main_py_string_must_not_match(self):
        # 自检进程：`python -c ".... 'main.py' ...."` 字符串里出现 main.py 但不结尾
        self_cmd = (
            "import psutil; [p for p in ... if 'main.py' in cmdline]"
        )
        assert probe_mod._cmdline_is_framework(
            ["python.exe", "-c", self_cmd]
        ) is False

    def test_pytest_process_not_match(self):
        assert probe_mod._cmdline_is_framework(["python.exe", "-m", "pytest"]) is False

    def test_empty_or_none_safe(self):
        assert probe_mod._cmdline_is_framework([]) is False
        assert probe_mod._cmdline_is_framework(None) is False


# ---------- 1. psutil 主探组合 ----------

class TestPsutilPrimary:
    def test_true_when_psutil_reports_running(self, monkeypatch):
        monkeypatch.setattr(probe_mod, "_psutil_running", lambda: True)
        assert _framework_looks_running() is True

    def test_false_when_psutil_reports_not_running(self, monkeypatch):
        # psutil 扫完确认没有 → False，不回退 wmic，也不 WARNING
        monkeypatch.setattr(probe_mod, "_psutil_running", lambda: False)
        assert _framework_looks_running() is False


# ---------- 2. psutil 不可用 → wmic 兜底 ----------

class TestWmicFallback:
    def test_psutil_unavailable_falls_back_to_wmic(self, monkeypatch, caplog):
        monkeypatch.setattr(probe_mod, "_psutil_running", lambda: None)
        monkeypatch.setattr(probe_mod, "_wmic_running", lambda: True)
        caplog.set_level(logging.WARNING, logger=probe_mod.__name__)
        assert _framework_looks_running() is True
        assert any(
            "psutil" in r.getMessage() and ("wmic" in r.getMessage() or "回退" in r.getMessage())
            for r in caplog.records
        ), "psutil 不可用回退 wmic 时必须有 WARNING，不能静默"


# ---------- 3. 异常 → 保守 True 且必出 WARNING（旧版静默必红点） ----------

class TestExceptionConservative:
    def test_exception_logs_warning_and_returns_true(self, monkeypatch, caplog):
        def _boom():
            raise RuntimeError("simulated wmic blocked by sandbox blacklist")

        monkeypatch.setattr(probe_mod, "_psutil_running", lambda: None)
        monkeypatch.setattr(probe_mod, "_wmic_running", _boom)
        caplog.set_level(logging.WARNING, logger=probe_mod.__name__)

        result = _framework_looks_running()

        assert result is True, "探测失败必须保守判在跑（写盘守卫安全契约不变）"
        warns = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert warns, "探测异常必须打 WARNING，不得静默（旧版 except: return True 无日志）"
        joined = " | ".join(warns)
        assert "RuntimeError" in joined or "simulated" in joined, (
            f"WARNING 必须带异常类型/原因，便于定位是 wmic 被拦还是 psutil 缺失：{joined}"
        )
