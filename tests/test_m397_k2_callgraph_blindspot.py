# -*- coding: utf-8 -*-
"""397 刀2 验收：调用图 42 文件盲区修复。

票面：
  · `CALL_GRAPH_EXCLUDED` 按**裸目录名**匹配 ⇒ `nucleus/data/` 6 个真实文件被连带剪掉；
  · 用调用图判"零调用"会得到假 0（"没采到"被读成"没人调用"）；
  · 验收：覆盖 367→409、`nucleus/data/` 6 个文件在图里有节点、排除集不再误伤深层同名目录。

★T0 核实修正（不许照抄票面数字就交差）：
  本机改**之前**实测覆盖 **394**（不是 367）。409 − 394 = 15 的构成是：
    nucleus/data 6 + hardware 7 + config.py 1 + pulse_doctor.py 1 = 15
  也就是说，只改"排除匹配方式"只能把 6 个 data 文件捞回来（394→400），**到不了 409**；
  另外 9 个是**扫描范围**问题（`_DEFAULT_SCAN_DIRS` / `config.CALL_GRAPH_SCAN_DIRS`
  根本没列 hardware / config.py / pulse_doctor.py）。本刀两处一起改才凑齐 42 文件这个目标。

★改 `_DEFAULT_SCAN_DIRS` 不够：`config.py::CALL_GRAPH_SCAN_DIRS` 才是运行期生效值
  （`_scan_dirs()` 优先读 config）—— 这是本项目反复踩过的"改了默认槽位没改生效值"。
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from nucleus.data import exclude_dirs as _ed  # noqa: E402

# ★`nucleus.self_awareness` 包的 `__init__` 把同名**类**重导出到包属性上，
#   于是 `import ...CallGraphAnalyzer as x` / `from ... import CallGraphAnalyzer`
#   拿到的都是类，不是模块（本项目踩过一次）。要模块里的私有函数只能走 sys.modules。
import nucleus.self_awareness.CallGraphAnalyzer  # noqa: E402,F401

_cga = sys.modules["nucleus.self_awareness.CallGraphAnalyzer"]
assert hasattr(_cga, "_iter_py_files"), "拿到的不是 CallGraphAnalyzer 模块"

_EXPECT_TOTAL = 409
_DATA_FILES = [
    "nucleus/data/DataAccessLayer.py",
    "nucleus/data/call_graph_guard.py",
    "nucleus/data/exclude_dirs.py",
    "nucleus/data/experience_cleanup.py",
    "nucleus/data/path_utils.py",
    "nucleus/data/write_guard.py",
]


def _files() -> list[str]:
    return _cga._iter_py_files(_cga._project_root(), _cga._scan_dirs())


# ---------------------------------------------------------------- ① 顶层判据语义
@pytest.mark.parametrize("rel,expect", [
    ("data", True),
    ("nucleus/data", False),
    ("tests", True),
    ("tests/unit", True),
    ("tmp", True),
    ("docs", True),
    ("nucleus", False),
    ("organs/body", False),
    ("hardware", False),          # ★397：hardware 已纳入扫描范围，顶层也不能排它
    ("hardware/robot_body", False),
    ("", False),
])
def test_01_top_level_matching(rel, expect):
    """只匹配顶层那一段：深层同名目录不再被误伤。"""
    assert _ed.is_call_graph_excluded(rel) is expect, rel


def test_02_deep_same_name_dir_not_pruned():
    """★核心回归：裸名 `data` 不再把 `nucleus/data` 剪掉。"""
    assert "data" in _ed.CALL_GRAPH_EXCLUDED, "票面前提：裸名集里确实有 data"
    assert _ed.is_call_graph_excluded("data") is True
    assert _ed.is_call_graph_excluded("nucleus/data") is False, "深层同名目录被误伤了"


def test_03_windows_sep_and_leading_slash_tolerated():
    """路径分隔符/前导斜杠两种写法都要判对（Windows 上 os.sep 是反斜杠）。"""
    assert _ed.is_call_graph_excluded("nucleus\\data") is False
    assert _ed.is_call_graph_excluded("/data") is True
    assert _ed.is_call_graph_excluded("/nucleus/data") is False


# ---------------------------------------------------------------- ② 覆盖范围
def test_04_total_is_409():
    """票面验收：调用图覆盖 409 个 .py。"""
    _f = _files()
    assert len(_f) == _EXPECT_TOTAL, f"覆盖 {len(_f)} ≠ {_EXPECT_TOTAL}"


@pytest.mark.parametrize("rel", _DATA_FILES)
def test_05_nucleus_data_in_graph(rel):
    """票面验收：`nucleus/data/` 6 个文件在图里有节点。"""
    assert rel in _files(), f"{rel} 仍不在调用图扫描范围内"


def test_06_hardware_and_root_scripts_in_graph():
    """另外 9 个盲区（扫描范围问题，不是排除集问题）。"""
    _f = _files()
    for _rel in ("hardware/hw_abstraction.py", "hardware/robot_body/robot_body_driver.py",
                 "config.py", "pulse_doctor.py", "main.py"):
        assert _rel in _f, f"{_rel} 不在扫描范围内"


# ---------------------------------------------------------------- ③ 生效值同步
def test_07_config_scan_dirs_matches_default():
    """★防回退：`config.CALL_GRAPH_SCAN_DIRS`（运行期生效值）必须与默认槽位同步。

    只改 `_DEFAULT_SCAN_DIRS` 不改 config ⇒ 生产上根本不生效（本批实测过一次）。
    """
    import config as _cfg

    _cfg_dirs = list(getattr(_cfg, "CALL_GRAPH_SCAN_DIRS") or [])
    assert _cfg_dirs, "config 未定义 CALL_GRAPH_SCAN_DIRS"
    assert set(_cfg_dirs) == set(_cga._DEFAULT_SCAN_DIRS), (
        f"config 生效值 {sorted(_cfg_dirs)} ≠ 默认槽位 {sorted(_cga._DEFAULT_SCAN_DIRS)}")


def test_08_scan_dirs_covers_blindspot():
    _cfg_dirs = _cga._scan_dirs()
    for _d in ("hardware", "config.py", "pulse_doctor.py"):
        assert _d in _cfg_dirs, f"{_d} 未进运行期扫描范围"


# ---------------------------------------------------------------- ④ 旧语义未破
def test_09_bare_name_set_untouched():
    """裸名集合本身不动：`test_tool_wiring_m49` 的子集断言与其它两个扫描通道依赖它。"""
    assert _ed.SOURCE_SCAN_DIRS <= _ed.CALL_GRAPH_EXCLUDED
    for _n in ("tests", "tmp", "logs", "data", "docs", "hardware"):
        assert _n in _ed.CALL_GRAPH_EXCLUDED


def test_10_backup_dirs_still_pruned():
    """.bak* / 隐藏目录仍必须被剪（否则备份副本会把引用统计自我膨胀）。"""
    _f = _files()
    assert not any(x.startswith(".bak") for x in _f), "备份目录进了图"
    assert not any("/__pycache__/" in x or x.startswith("__pycache__") for x in _f), "缓存目录进了图"
    assert not any(x.startswith("tests/") for x in _f), "tests 进了图"
    assert not any(x.startswith("tmp/") for x in _f), "tmp 进了图"


def test_11_excluded_toplevel_drops_hardware():
    """顶层判据集合必须去掉 hardware（否则 hardware/robot_body 6 个又被整段剪掉）。"""
    assert "hardware" in _ed.CALL_GRAPH_EXCLUDED
    assert "hardware" not in _ed.CALL_GRAPH_EXCLUDED_TOPLEVEL
