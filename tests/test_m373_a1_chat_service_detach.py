# -*- coding: utf-8 -*-
"""第373批 刀1 · ChatService 剥离主装配路径（卡点一 · 342刀5 回归 P0）

票：`T-372-卡点一ChatService剥离主线程-1`

被测契约（改前 → 改后）：
  改前：`ChatService.start()` 末尾同步调 `self._chat_loop()`，而 `_chat_loop()` 里的
        `input()` 无超时 ⇒ 装配线程被 stdin 永久钉死（现场 6h23m）。
  改后：`start()` 只做「注册监听器 + 打横幅 + 起 daemon 循环线程」，随后**立即返回**。

本文件故意**不 mock 掉 `input` 的阻塞本质**：`_chat_loop` 被替换成「永久等待」的假循环，
用来模拟 `input()` 卡住。若 `start()` 退回同步驻留语义，本文件会在 join 超时处转红
（这是本文件存在的唯一理由——它不是形状断言，是阻塞语义断言）。
"""

import threading
import time

import pytest

from functions.chat.chat_service import FUNCTION_META, ChatService
from functions.function_loader import FunctionLoader

_BLOCK_SECONDS = 30.0
_JOIN_TIMEOUT = 3.0


class _FakeFramework:
    """`FunctionLoader` 只需 `.logger.log`（仅异常路径用到）。"""

    class _Logger:
        @staticmethod
        def log(*_a, **_kw):
            return None

    logger = _Logger()


def _hold_loop_factory(hold: threading.Event):
    """返回一个「永远阻塞」的假 `_chat_loop`，模拟 `input()` 钉死。"""

    def _blocked_loop(self):
        hold.wait(_BLOCK_SECONDS)

    return _blocked_loop


def test_01_start_returns_immediately(monkeypatch):
    """start() 必须立即返回：假循环阻塞 30s，start() 耗时须 < 3s。"""
    hold = threading.Event()
    monkeypatch.setattr(ChatService, "_chat_loop", _hold_loop_factory(hold))
    c = ChatService()
    try:
        t0 = time.time()
        c.start()
        elapsed = time.time() - t0
        assert elapsed < _JOIN_TIMEOUT, (
            f"start() 耗时 {elapsed:.2f}s ⇒ 仍在同步驻留 _chat_loop()（卡点一复发）")
        th = c._chat_loop_thread
        assert th is not None, "start() 未记录循环线程句柄"
        assert th.is_alive(), "循环线程未真正起来"
        assert th.daemon is True, "循环线程必须是 daemon，否则进程退不出去"
        assert th.name == "chat-service-loop", f"线程名应为 chat-service-loop，实际 {th.name}"
        assert th is not threading.main_thread(), "循环线程跑在主线程上（未剥离）"
    finally:
        hold.set()
        c.stop()


def test_02_function_loader_assembly_not_pinned(monkeypatch):
    """装配级回归：`FunctionLoader._run_module` 跑过 start() 即返回，不等 stdin。"""
    hold = threading.Event()
    monkeypatch.setattr(ChatService, "_chat_loop", _hold_loop_factory(hold))
    c = ChatService()
    loader = FunctionLoader(_FakeFramework())
    try:
        t0 = time.time()
        loader._run_module(c, "对话交互")
        elapsed = time.time() - t0
        assert elapsed < _JOIN_TIMEOUT, (
            f"装配点被钉死 {elapsed:.2f}s ⇒ 主线程未进主循环（卡点一现场 6h23m）")
    finally:
        hold.set()
        c.stop()


def test_03_thread_mode_matches_reality():
    """thread_mode 须据实：start() 不驻留了，就不能再声明 blocking。"""
    assert FUNCTION_META["thread_mode"] != "blocking", (
        "FUNCTION_META.thread_mode 仍为 blocking，与 start() 实际语义不符（名实不符）")
    assert FUNCTION_META["thread_mode"] == "background"


def test_04_stop_does_not_hang(monkeypatch):
    """stop() 不得因循环线程卡在 input() 而挂住。"""
    hold = threading.Event()
    monkeypatch.setattr(ChatService, "_chat_loop", _hold_loop_factory(hold))
    c = ChatService()
    c.start()
    try:
        t0 = time.time()
        c.stop()
        elapsed = time.time() - t0
        assert elapsed < _JOIN_TIMEOUT, f"stop() 耗时 {elapsed:.2f}s ⇒ 引入了新的同步阻塞点"
        assert c._running is False
    finally:
        hold.set()


def test_05_double_start_no_extra_thread(monkeypatch):
    """重复 start() 不得叠线程（两条线程抢同一 stdin 会吞输入）。"""
    hold = threading.Event()
    monkeypatch.setattr(ChatService, "_chat_loop", _hold_loop_factory(hold))
    c = ChatService()
    try:
        c.start()
        first = c._chat_loop_thread
        c.start()
        assert c._chat_loop_thread is first, "第二次 start() 另起了一条循环线程"
        alive = [t for t in threading.enumerate() if t.name == "chat-service-loop"]
        assert len(alive) == 1, f"chat-service-loop 线程数 {len(alive)}（期望 1）"
    finally:
        hold.set()
        c.stop()


def test_06_eof_leaves_loop_without_raising(monkeypatch):
    """无控制台场景 stdin 立即 EOF：留痕后退出循环，不炸、不吞异常。"""

    def _eof_input(*_a, **_kw):
        raise EOFError("stdin closed")

    monkeypatch.setattr("builtins.input", _eof_input)
    c = ChatService()
    c._running = True
    err = []

    def _run():
        try:
            c._chat_loop()
        except BaseException as e:  # 此处就是要抓「炸没炸」
            err.append(e)

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(_JOIN_TIMEOUT)
    assert not th.is_alive(), "EOF 后 _chat_loop 未退出（应为 break）"
    assert not err, f"_chat_loop 在 EOF 场景抛出异常：{err!r}"


def test_07_start_logs_detach_marker(monkeypatch):
    """留痕可回查：start() 须打出 `[373刀1] action=detached`。"""
    hold = threading.Event()
    monkeypatch.setattr(ChatService, "_chat_loop", _hold_loop_factory(hold))
    logs = []

    class _Logger:
        def log(self, level, msg, *a, **kw):
            logs.append(msg)

    c = ChatService()
    c._logger = _Logger()
    try:
        c.start()
        assert any("action=detached" in m for m in logs), f"缺 detached 留痕，实得 {logs}"
    finally:
        hold.set()
        c.stop()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
