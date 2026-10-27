# -*- coding: utf-8 -*-
"""第373批 刀2 · watcher 前移＋启动失败留痕（卡点二 P1）

票：`T-372-watcher前移与启动失败留痕-1`

被测契约：
  ① `main.py` 里 `config.start_config_watcher()` 必须**早于** `load_all()`
     （原在之后；卡点二现场是主线程推进到 `Thread.start()` 的 `_started.wait()` 死等）；
  ② watcher 的三条依赖面不得碰器官实例（否则前移会 AttributeError）；
  ③ `start()` 幂等：重复调用不起第二条 `config-watcher` 线程；
  ④ 启动耗时超预算必须留 WARNING（卡点二的**可观测**入口）；
  ⑤ 线程体异常退出必须留痕且把 `_config_watcher_running` 置 False（否则 restart 被假象拒绝）。
"""

import ast
import logging
import threading
from pathlib import Path

import pytest

import config as config_mod

_MAIN = "main.py"
_CONFIG = "config.py"


def _main_call_order():
    """返回 (start_config_watcher 行号, load_all 行号)。"""
    src = Path(_MAIN).read_text(encoding="utf-8")
    tree = ast.parse(src)
    hoist_ln = None
    loadall_ln = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        # config.start_config_watcher()
        if (isinstance(f, ast.Attribute) and f.attr == "start_config_watcher"):
            hoist_ln = node.lineno if hoist_ln is None else min(hoist_ln, node.lineno)
        # framework.function_loader.load_all() —— 必须限定在 function_loader 上，
        # 否则会误命中 main.py:2261 的 `_ctx_snapshot.load_all()`（无关的上下文快照）
        if isinstance(f, ast.Attribute) and f.attr == "load_all":
            _owner = f.value
            if isinstance(_owner, ast.Attribute) and _owner.attr == "function_loader":
                loadall_ln = node.lineno if loadall_ln is None else min(loadall_ln, node.lineno)
    return hoist_ln, loadall_ln


def test_01_watcher_hoisted_before_load_all():
    """调用点必须在 load_all 之前（本刀唯一的行为面改动）。"""
    hoist_ln, loadall_ln = _main_call_order()
    assert hoist_ln is not None, "main.py 里找不到 config.start_config_watcher() 调用"
    assert loadall_ln is not None, "main.py 里找不到 load_all() 调用"
    assert hoist_ln < loadall_ln, (
        f"start_config_watcher 仍在 load_all 之后（L{hoist_ln} vs L{loadall_ln}）⇒ 刀2 未生效")


def _func_src(name):
    src = Path(_CONFIG).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{_CONFIG} 里找不到函数 {name}")


@pytest.mark.parametrize("fname", ["start_config_watcher", "_watch_config_file", "_watch_config_loop"])
def test_02_no_organ_instance_dependency(fname):
    """前移前提：watcher 不得引用器官实例（否则 load_all 之前必炸）。"""
    body = _func_src(fname)
    for bad in ("framework.", ".organs", "self.framework"):
        assert bad not in body, f"{fname} 引用了 {bad!r} ⇒ 不能前移到 load_all 之前"


def _cleanup_watcher():
    config_mod._config_watcher_running = False


def test_03_start_is_idempotent():
    """重复 start_config_watcher() 不得叠线程。"""
    _cleanup_watcher()
    try:
        config_mod.start_config_watcher()
        first = config_mod._config_watcher_thread
        config_mod.start_config_watcher()
        assert config_mod._config_watcher_thread is first, "第二次调用另起了一条 watcher 线程"
        alive = [t for t in threading.enumerate() if t.name == "config-watcher"]
        assert len(alive) == 1, f"config-watcher 线程数 {len(alive)}（期望 1）"
    finally:
        _cleanup_watcher()


def test_04_start_cost_warning_reachable(monkeypatch, caplog):
    """预算耗尽分支必须可达：把预算压到 -1，任何非负耗时都超 ⇒ 必打 WARNING。

    ★铁律159（赦免/分支须可达）：只写「超预算会告警」而不断言它真能进那个分支，
    等于留了一条永不执行的死代码。

    ★为什么是 -1 而不是 0：Windows 的 `time.monotonic()` 粒度约 15.6ms，
    `Thread.start()` 可能落在同一 tick ⇒ `_dt == 0.0`，用 0 做预算会让
    `0.0 > 0.0` 为假，分支不可达（本用例首跑即撞上）。
    """
    _cleanup_watcher()
    monkeypatch.setattr(config_mod, "_WATCHER_START_BUDGET_SEC", -1.0)
    try:
        with caplog.at_level(logging.WARNING, logger="Config"):
            config_mod.start_config_watcher()
        assert any("[373刀2]" in r.message and "Thread.start()" in r.message
                   for r in caplog.records), (
            f"预算超支未留痕，caplog={[r.message for r in caplog.records]}")
    finally:
        _cleanup_watcher()


def test_05_thread_crash_leaves_trace(monkeypatch, caplog):
    """线程体抛 BaseException ⇒ 留 WARNING + running 置 False（restart 不被假象拒绝）。

    本用例锁定的是 `_watch_config_file` 的**外层兜底分支**（本刀新增），
    循环体自身行为由现有回归覆盖；把 `_watch_config_loop` 打成抛异常是
    触发该分支最直接、零副作用的方式。
    """

    def _boom():
        raise KeyboardInterrupt("simulated thread-body crash")

    monkeypatch.setattr(config_mod, "_watch_config_loop", _boom)
    monkeypatch.setattr(config_mod, "_config_watcher_running", True)
    with caplog.at_level(logging.WARNING, logger="Config"):
        config_mod._watch_config_file()
    assert config_mod._config_watcher_running is False, (
        "线程异常退出后 _config_watcher_running 仍为 True ⇒ 后续 restart 会被 if 直接 return 拒掉")
    assert any("[373刀2]" in r.message and "异常退出" in r.message
               for r in caplog.records), f"崩溃未留痕: {[r.message for r in caplog.records]}"


def test_06_startup_marker_logged(capsys):
    """启动前留痕在位（跨进程排查「卡在哪一步」的唯一线索）。"""
    _cleanup_watcher()
    try:
        config_mod.start_config_watcher()
        out = capsys.readouterr().out
        assert "[373刀2] watcher 线程启动中" in out, f"缺启动中留痕，stdout={out!r}"
        assert "thread=config-watcher" in out, f"缺线程名/耗时留痕，stdout={out!r}"
    finally:
        _cleanup_watcher()
