# -*- coding: utf-8 -*-
"""主线第60批 T1+T3 门控：问题发现路径过滤（备份目录）与 self_inspector 审计。

覆盖：
  - T1：SafeEvolutionExecutor._issue_filter_reason / _issue_file_out_of_scope
       按路径「段前缀 .bak」判定备份目录（.bak_batchN / .bak_tmp / .bak_mainlineN），
       且不误伤文件名含 .bak 后缀的普通文件（foo.py.bak）。
  - T1：repair_with_distillation 入口集成过滤备份目录问题并记录计数。
  - T3：self_inspector._issue_file_in_backup_dir 段前缀判定（扫描排除共用）。
"""
import os
import sys
import unittest.mock as mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.reasoning.SafeEvolutionExecutor import (  # noqa: E402
    SafeEvolutionExecutor,
    _issue_file_out_of_scope,
    _issue_filter_reason,
)
from nucleus.self_inspector import _issue_file_in_backup_dir  # noqa: E402


# ---------- T1：_issue_filter_reason 备份目录段前缀判定 ----------

def test_filter_reason_backup_segment_prefixes():
    """各类 .bak* 段前缀路径应被识别为 backup。"""
    _cases = [
        ".bak_batch59/organs/body/foo.py",
        ".bak_tmp/nucleus/reasoning/x.py",
        ".bak_mainline3/organs/brain/y.py",
        "data/.bak_20260915/foo.py",
        ".bak_batch44/utils/z.py",
    ]
    for _c in _cases:
        assert _issue_filter_reason(_c, ROOT) == "backup", "should be backup: " + _c


def test_filter_reason_not_backup_for_bak_suffix_filename():
    """文件名含 .bak 后缀但非独立段（foo.py.bak）不应被误判为备份目录。"""
    _cases = [
        "organs/body/foo.py.bak",
        "data/backup.py.bak",
        "nucleus/reasoning/SafeEvolutionExecutor.py.bak",
    ]
    for _c in _cases:
        assert _issue_filter_reason(_c, ROOT) == "", "should be kept: " + _c


def test_filter_reason_stdlib_and_external():
    """标准库（Windows /Lib/）返回 stdlib；项目外绝对路径返回 external。"""
    assert _issue_filter_reason("C:/Python312/Lib/ssl.py", ROOT) == "stdlib"
    # 说明：模式 /Lib/ 大小写敏感且面向 Windows 部署；Unix 绝对路径
    # /usr/lib/... 不含 /Lib/ 子串，又属项目外绝对路径 → 归类为 external（仍被过滤）。
    assert _issue_filter_reason("/usr/lib/python3.9/socket.py", ROOT) == "external"
    # 项目外绝对路径（盘符形式，且不以 ROOT 开头）
    _ext = "D:/other_project/mod.py"
    assert _issue_filter_reason(_ext, ROOT) == "external"
    # 项目内绝对路径保留
    _proj = os.path.join(ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
    assert _issue_filter_reason(_proj, ROOT) == ""


def test_filter_reason_relative_kept():
    """相对路径（项目内）一律保留，即使字面含标准库/备份字样。"""
    assert _issue_filter_reason("nucleus/foo.py", ROOT) == ""
    assert _issue_filter_reason("Lib/ssl.py", ROOT) == ""
    assert _issue_filter_reason("", ROOT) == ""
    assert _issue_filter_reason(None, ROOT) == ""  # type: ignore[arg-type]


def test_issue_file_out_of_scope_returns_bool():
    """回归：委托函数保持 bool 语义（既有 59 测试契约不变）。"""
    assert _issue_file_out_of_scope(".bak_batch59/organs/body/foo.py", ROOT) is True
    assert _issue_file_out_of_scope("organs/body/foo.py.bak", ROOT) is False
    assert _issue_file_out_of_scope("nucleus/foo.py", ROOT) is False
    assert _issue_file_out_of_scope("C:/Python312/Lib/ssl.py", ROOT) is True


# ---------- T3：self_inspector 备份目录判定（扫描排除共用） ----------

def test_self_inspector_backup_dir_true():
    """self_inspector 仅按段前缀 .bak 判定为真。"""
    _cases = [
        ".bak_batch59/organs/body/foo.py",
        ".bak_tmp/foo.py",
        "x/.bak_mainline3/y.py",
    ]
    for _c in _cases:
        assert _issue_file_in_backup_dir(_c) is True, "should be backup: " + _c


def test_self_inspector_backup_dir_false():
    """文件名 .bak 后缀、普通项目路径不应命中。"""
    _cases = [
        "organs/body/foo.py.bak",
        "nucleus/reasoning/SafeEvolutionExecutor.py.bak",
        "organs/body/foo.py",
        "data/knowledge/cold/evol_level=L1/part-0.parquet",
        "",
    ]
    for _c in _cases:
        assert _issue_file_in_backup_dir(_c) is False, "should not be backup: " + repr(_c)


# ---------- T1：repair_with_distillation 入口集成过滤 ----------

def test_repair_filters_backup_dir_issues(caplog):
    """集成：repair_with_distillation 入口过滤备份目录问题并记录 DEBUG 计数。"""
    import logging

    _fake_hub = mock.MagicMock()
    _ex = SafeEvolutionExecutor()
    _issues = [
        {"type": "print_instead_of_log", "file": ".bak_batch59/organs/body/x.py"},
        {"type": "bare_except", "file": ".bak_tmp/nucleus/reasoning/y.py"},
    ]
    with caplog.at_level(logging.DEBUG, logger="SafeEvolutionExecutor"):
        with mock.patch(
            "nucleus.mnemosyne.verification_learning_hub.get_verification_learning_hub",
            return_value=_fake_hub,
        ):
            with mock.patch.object(_ex, "_call_llm_for_repair", return_value=""):
                _res = _ex.repair_with_distillation(_issues)
    assert isinstance(_res, dict)
    assert "过滤2个" in caplog.text
    assert "备份目录2个" in caplog.text
    assert _res.get("skipped", 0) >= 2
