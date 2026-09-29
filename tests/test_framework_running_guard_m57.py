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
    """框架停止（无 lock、无 main.py 进程）→ 返回 False。

    ★第89批 相关任务 同步：原用例直接调 is_framework_running()，隐含依赖
    「本机没有 main.py 进程」这一**环境前提** —— 修复后进程探测能正确识别
    在跑的框架（实测框架在线时该前提不成立 → 恒失败）。
    改为**注入**两个信号（无 lock + 进程探测为假），断言强度不变（仍 `is False`），
    且不再受环境干扰。
    """
    monkeypatch.delenv("PULSE_TEST_FRAMEWORK_RUNNING", raising=False)
    monkeypatch.setattr(cf, "_framework_lock_present", lambda: False)
    monkeypatch.setattr(cf, "_detect_framework_process", lambda: False)
    assert cf.is_framework_running() is False


def test_detector_on_when_process_detected(monkeypatch):
    """★第89批 相关任务：无 lock 但进程探测命中 → True（进程分支接线有效）。"""
    monkeypatch.delenv("PULSE_TEST_FRAMEWORK_RUNNING", raising=False)
    monkeypatch.setattr(cf, "_framework_lock_present", lambda: False)
    monkeypatch.setattr(cf, "_detect_framework_process", lambda: True)
    assert cf.is_framework_running() is True


class TestCmdlineMatcher:
    """★第89批 相关任务：命令行匹配语义（修复的核心判据）。"""

    def test_matches_main_py_argument(self):
        assert cf._cmdline_is_framework_main(
            ["D:\\Program Files\\Python312\\python.exe", "main.py"]) is True

    def test_matches_absolute_main_py(self):
        assert cf._cmdline_is_framework_main(
            ["python.exe", "C:\\work\\project\\main.py"]) is True

    def test_does_not_match_mentioning_process(self):
        """命令行**正文里提到** main.py（如 -c 脚本）不得命中。"""
        assert cf._cmdline_is_framework_main(
            ["python.exe", "-c", "open('main.py').read()"]) is False

    def test_does_not_match_other_entry(self):
        assert cf._cmdline_is_framework_main(
            ["python.exe", "-m", "pytest", "tests/"]) is False
        assert cf._cmdline_is_framework_main([]) is False
        assert cf._cmdline_is_framework_main(None) is False


def test_psutil_tier_detects_running_framework():
    """★第89批 相关任务 端到端：框架在线时 psutil 分支必须能识别（离线环境 skip）。"""
    _cl = cf._iter_main_py_cmdlines()
    if _cl is None:
        import pytest as _pt
        _pt.skip("psutil 不可用，跳过真实进程探测")
    if not _cl:
        import pytest as _pt
        _pt.skip("本机框架未运行（离线环境），跳过真实进程探测")
    assert any("main.py" in _c for _c in _cl)


def test_skip_helper_skips_when_running(monkeypatch):
    """框架运行时应抛出 skip。

    ★第89批 相关任务 顺带修：`pytest.skip` 抛出的 Skipped 继承自 **BaseException**，
    原用例写 `pytest.raises(Exception)` 根本捕获不到 —— 该用例此前**一直是 skip**
    （断言从未真正执行）。改用公开的 `pytest.skip.Exception` 精确捕获。
    """
    monkeypatch.setattr(cf, "is_framework_running", lambda: True)
    with pytest.raises(pytest.skip.Exception) as exc:
        cf._framework_running_skip_if_needed()
    assert "框架运行" in str(exc.value)


def test_skip_helper_runs_when_stopped(monkeypatch):
    monkeypatch.setattr(cf, "is_framework_running", lambda: False)
    cf._framework_running_skip_if_needed()  # 不应抛异常


@pytest.fixture(autouse=True)
def _m89_force_framework_stopped(monkeypatch):
    """★第89批 相关任务：本模块默认把「框架是否在跑」强制判为**停止**（env=0）。

    动机：修复进程探测后，真实在线框架会被正确识别，于是 `production_data`
    相关用例会在 **fixture 建立阶段** 就被 skip —— 本模块「停止态」的断言便不再执行。
    env 覆盖必须早于 `guard_production_data` 建立，故用 autouse
    （同一 scope 下 autouse fixture 先于显式请求的 fixture 实例化）。
    低层探测函数（`_cmdline_is_framework_main` / `_iter_main_py_cmdlines`）
    是直接调用、不读 env，因此不受本 fixture 影响（真实探测仍被单独覆盖）。
    """
    monkeypatch.setenv("PULSE_TEST_FRAMEWORK_RUNNING", "0")


@pytest.mark.usefixtures("guard_production_data")
def test_guard_fixture_requested_runs_when_stopped():
    """fixture 被正常请求、框架停止（env 强制，见上方 autouse）时不应跳过（验证接线）。"""
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
