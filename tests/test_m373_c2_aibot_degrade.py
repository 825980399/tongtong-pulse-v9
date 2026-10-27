# -*- coding: utf-8 -*-
"""第373批 刀6 · aibot import 降级（P2）

票：`T-372-U-import-aibot-1`

背景：175刀4 把 aibot 从 requirements 移除（pip 上无此包，由部署环境单独提供），
保护只写在 main.py 调用方。而门禁 `check_requirements_declared_imports` 判的是
**import 点本身**：既没声明、import 点也没保护 ⇒ 算「未声明硬缺口」。
本刀把降级下沉到 import 点（缺包时该通道静默禁用 + WARNING 留痕，不炸启动）。

边界：不改 main.py 调用方既有保护；不恢复 requirements 声明。
"""

import builtins
import importlib
import sys
from pathlib import Path

import pytest

_MOD = "nucleus.wecom_chat_bridge"
_SRC = Path("nucleus/wecom_chat_bridge.py")


def _import_without_aibot(monkeypatch):
    """在「aibot 不可用」的环境里重新导入本模块，返回模块对象。"""
    _real = builtins.__import__

    def _boom(name, *a, **kw):
        if name == "aibot" or name.startswith("aibot."):
            raise ImportError(f"No module named {name!r}")
        return _real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _boom)
    sys.modules.pop(_MOD, None)
    try:
        return importlib.import_module(_MOD)
    finally:
        monkeypatch.undo()
        sys.modules.pop(_MOD, None)


def test_01_both_imports_guarded_in_source():
    """源码级：aibot 两条 import 都必须落在捕获 ImportError 的 try 体内。"""
    import ast

    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    _guarded = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        _catches = any(
            (isinstance(h.type, ast.Name) and h.type.id == "ImportError")
            for h in node.handlers)
        if not _catches:
            continue
        for _stmt in node.body:
            for _sub in ast.walk(_stmt):
                _guarded.add(id(_sub))
    _hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "aibot":
            _hits.append((node.module, id(node) in _guarded))
        elif isinstance(node, ast.Import):
            for _a in node.names:
                if _a.name.split(".")[0] == "aibot":
                    _hits.append((_a.name, id(node) in _guarded))
    assert len(_hits) == 2, f"aibot import 点应恰为 2 条，实得 {_hits}"
    assert all(_ok for _, _ok in _hits), f"仍有 import 点无 ImportError 降级保护: {_hits}"


def test_02_missing_package_degrades_without_raising(monkeypatch, caplog):
    """缺包 ⇒ 模块照常 import（不炸），错误对象留存，模块级 WARNING 留下。"""
    import logging

    caplog.set_level(logging.WARNING)
    mod = _import_without_aibot(monkeypatch)
    assert mod._AIBOT_IMPORT_ERROR is not None
    assert isinstance(mod._AIBOT_IMPORT_ERROR, ImportError)
    assert mod.WSClient is None and mod.WSClientOptions is None
    assert "[373刀6]" in caplog.text, "缺包时模块级 WARNING 未留痕"


def test_03_missing_package_start_returns_without_half_state(monkeypatch, caplog):
    """缺包 ⇒ start() 早退：不起线程、不注册监听、running 保持 False、WARNING 留痕。"""
    import logging

    caplog.set_level(logging.WARNING)
    mod = _import_without_aibot(monkeypatch)

    class _InfoField:
        def register_condition(self, **_kw):  # 只要被调用就说明没早退
            raise AssertionError("缺包时不该注册回复监听")

    _bridge = mod.WeComChatBridge(bot_id="b", secret="s", info_field=_InfoField())
    caplog.clear()
    assert _bridge.start() is None
    assert _bridge._running is False, "缺包时被置 running=True（会留半启动态）"
    assert _bridge._thread is None, "缺包时仍起了后台线程"
    assert "[373刀6]" in caplog.text, "start() 早退未留痕"


def test_04_missing_package_async_connect_is_noop(monkeypatch, caplog):
    """直调 `_async_connect()` 也不许变成 'NoneType' object is not callable。"""
    import asyncio
    import logging

    caplog.set_level(logging.WARNING)
    mod = _import_without_aibot(monkeypatch)
    _bridge = mod.WeComChatBridge(bot_id="b", secret="s")
    caplog.clear()
    assert asyncio.run(_bridge._async_connect()) is None
    assert _bridge._client is None
    assert "[373刀6]" in caplog.text, "建连跳过未留痕"


def test_05_real_environment_import_is_normal():
    """本机装了 aibot 时走正常分支（`_AIBOT_IMPORT_ERROR` 必须为 None）。"""
    pytest.importorskip("aibot")
    sys.modules.pop(_MOD, None)
    mod = importlib.import_module(_MOD)
    try:
        assert mod._AIBOT_IMPORT_ERROR is None
        assert callable(mod.WSClient) and callable(mod.WSClientOptions)
    finally:
        sys.modules.pop(_MOD, None)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
