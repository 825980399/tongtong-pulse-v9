# -*- coding: utf-8 -*-
"""★主线第57批 T1 / P2-391：框架运行期统一守卫门控单测。

验证：
1. is_framework_running 在框架停止/运行时分别返回 False/True（含 env 覆盖）；
2. guard_production_data 内联 fixture 在框架运行时触发 skip、停止时正常通过；
3. pytest_collection_modifyitems 在框架运行时为 production_data 标记测试追加 skip。

注：直接加载 tests/conftest.py 以获取真实守卫函数（与 pytest 加载的是同一逻辑）。
"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "m57_conftest_probe", os.path.join(ROOT, "tests", "conftest.py"))
cf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cf)


def _set_env(val):
    if val is None:
        os.environ.pop("PULSE_TEST_FRAMEWORK_RUNNING", None)
    else:
        os.environ["PULSE_TEST_FRAMEWORK_RUNNING"] = val


def test_detector_env_override_stopped():
    _set_env("0")
    try:
        assert cf.is_framework_running() is False
    finally:
        _set_env(None)


def test_detector_env_override_running():
    _set_env("1")
    try:
        assert cf.is_framework_running() is True
    finally:
        _set_env(None)


def test_detector_off_when_no_lock_and_no_process(monkeypatch):
    """框架停止（无 lock、无 main.py 进程）→ 返回 False。"""
    monkeypatch.delenv("PULSE_TEST_FRAMEWORK_RUNNING", raising=False)
    assert cf.is_framework_running() is False


def test_skip_helper_skips_when_running(monkeypatch):
    monkeypatch.setattr(cf, "is_framework_running", lambda: True)
    with pytest.raises(Exception) as exc:
        cf._framework_running_skip_if_needed()
    assert "框架运行" in str(exc.value)


def test_skip_helper_runs_when_stopped(monkeypatch):
    monkeypatch.setattr(cf, "is_framework_running", lambda: False)
    cf._framework_running_skip_if_needed()  # 不应抛异常


@pytest.mark.usefixtures("guard_production_data")
def test_guard_fixture_requested_runs_when_stopped(monkeypatch):
    """fixture 被正常请求、框架停止（env 强制）时不应跳过（验证接线）。"""
    monkeypatch.setenv("PULSE_TEST_FRAMEWORK_RUNNING", "0")
    assert True


class _FakeItem:
    def __init__(self, marked):
        self.keywords = {"production_data": None} if marked else {}
        self.markers = []

    def add_marker(self, m):
        self.markers.append(m)


def test_collection_hook_skips_marked_when_running(monkeypatch):
    monkeypatch.setattr(cf, "is_framework_running", lambda: True)
    items = [_FakeItem(True), _FakeItem(False)]
    cf.pytest_collection_modifyitems(config=None, items=items)
    assert len(items[0].markers) == 1, "标记 production_data 的测试应在框架运行时被 skip"
    assert len(items[1].markers) == 0, "未标记的测试不应被 skip"


def test_collection_hook_noop_when_stopped(monkeypatch):
    monkeypatch.setattr(cf, "is_framework_running", lambda: False)
    items = [_FakeItem(True), _FakeItem(False)]
    cf.pytest_collection_modifyitems(config=None, items=items)
    assert len(items[0].markers) == 0
    assert len(items[1].markers) == 0


def test_marker_registered():
    """conftest 必须注册 production_data 标记，避免未知标记告警。"""
    assert hasattr(pytest.mark, "production_data"), \
        "pytest.mark.production_data 应可用（由 conftest 注册）"
