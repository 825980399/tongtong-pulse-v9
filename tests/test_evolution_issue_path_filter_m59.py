# -*- coding: utf-8 -*-
"""主线第59批 T2 门控：问题发现器路径过滤（排除标准库/项目外路径）。

覆盖：
  - 标准库路径过滤（/Lib/、site-packages/）
  - 项目内路径保留（绝对项目路径 + 相对路径）
  - 可配置排除模式（config.EVOLUTION_ISSUE_PATH_EXCLUDE_PATTERNS）
  - 集成：repair_with_distillation 入口过滤并记录 DEBUG 计数
"""
import logging
import os
import sys
import unittest.mock as mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.reasoning.SafeEvolutionExecutor import (  # noqa: E402
    SafeEvolutionExecutor,
    _issue_file_out_of_scope,
)


def test_stdlib_lib_path_filtered():
    """Python 标准库（/Lib/）路径应被过滤。"""
    _cases = [
        "C:/Python312/Lib/ssl.py",
        "C:\\Python312\\Lib\\socket.py",
        "/usr/lib/python3.9/http/client.py",
    ]
    for _c in _cases:
        assert _issue_file_out_of_scope(_c, ROOT) is True, "should filter stdlib path: " + _c


def test_site_packages_path_filtered():
    """第三方包（site-packages/）路径应被过滤。"""
    _cases = [
        "D:/xinrenlei/tongtong-pulse-v9/.venv/Lib/site-packages/requests/api.py",
        "/home/user/venv/lib/python3.11/site-packages/numpy/core/__init__.py",
    ]
    for _c in _cases:
        assert _issue_file_out_of_scope(_c, ROOT) is True, "should filter site-packages path: " + _c


def test_project_abs_path_kept():
    """项目内绝对路径应保留。"""
    _p = os.path.join(ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
    assert _issue_file_out_of_scope(_p, ROOT) is False


def test_relative_path_kept():
    """相对路径（项目内）应保留，即使含标准库帧字面量。"""
    assert _issue_file_out_of_scope("nucleus/foo.py", ROOT) is False
    assert _issue_file_out_of_scope("Lib/ssl.py", ROOT) is False


def test_empty_path_not_filtered():
    """空路径不过度过滤（保留，交由其它逻辑处理）。"""
    assert _issue_file_out_of_scope("", ROOT) is False
    assert _issue_file_out_of_scope(None, ROOT) is False  # type: ignore[arg-type]


def test_configurable_exclude_patterns(monkeypatch):
    """排除模式可配置：追加自定义模式后，命中即被过滤。"""
    import config

    monkeypatch.setattr(
        config,
        "EVOLUTION_ISSUE_PATH_EXCLUDE_PATTERNS",
        ["/Lib/", "site-packages/", "/custom/vendor/"],
    )
    assert _issue_file_out_of_scope("/custom/vendor/lib/foo.py", ROOT) is True
    # 不在模式内、且位于项目外的绝对路径仍按 project_root 判定过滤
    assert _issue_file_out_of_scope("C:/other_project/mod.py", ROOT) is True
    # 项目内路径始终保留
    assert _issue_file_out_of_scope(os.path.join(ROOT, "x.py"), ROOT) is False


def test_repair_with_distillation_filters_stdlib(caplog):
    """集成：repair_with_distillation 入口过滤标准库问题并记录 DEBUG 计数。"""
    import config

    _orig = getattr(config, "EVOLUTION_ISSUE_PATH_EXCLUDE_PATTERNS", None)
    try:
        config.EVOLUTION_ISSUE_PATH_EXCLUDE_PATTERNS = ["/Lib/", "site-packages/"]
        _ex = SafeEvolutionExecutor()
        _issues = [
            {"type": "print_instead_of_log", "file": "C:/Python312/Lib/ssl.py"},
            {"type": "bare_except", "file": "/usr/lib/python3.9/socket.py"},
        ]
        _fake_hub = mock.MagicMock()
        with caplog.at_level(logging.DEBUG, logger="SafeEvolutionExecutor"):
            with mock.patch(
                "nucleus.mnemosyne.verification_learning_hub.get_verification_learning_hub",
                return_value=_fake_hub,
            ):
                _res = _ex.repair_with_distillation(_issues)
        assert isinstance(_res, dict)
        assert "过滤2个" in caplog.text
    finally:
        if _orig is None:
            config.EVOLUTION_ISSUE_PATH_EXCLUDE_PATTERNS = ["/Lib/", "site-packages/"]
        else:
            config.EVOLUTION_ISSUE_PATH_EXCLUDE_PATTERNS = _orig
