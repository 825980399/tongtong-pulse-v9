# -*- coding: utf-8 -*-
"""399 刀2 验收：`hardware/hw_abstraction.py` 空壳删除。

票面：「10 行纯转发空壳 …… 确认零调用后直接删 …… 调用图覆盖数不变（本来就没在图里）」。

★票面半句不成立：它**在**扫描范围内（`_files()` 里有它的文件条目），只是**没有函数节点**
  （整个文件只有一个 docstring，零 `def`）。所以删掉它，覆盖数会 411 → 410。
  判据 `>= 409` 仍过，但具名清单必须跟着减一项 —— 这条是本刀最容易漏的连带。

判据：
  * 文件真没了，且 `import` 也真失败了（不是"文件删了、.pyc 还能导入"）；
  * 全仓 .py 里零引用（AST 级，不认字符串/注释）；
  * `hardware/` 仍是可用的命名空间包（`robot_body/` 5 个模块能导入）；
  * 调用图仍覆盖 `hardware/`（不能因为删一个空壳把整个目录摘出扫描范围）；
  * 398 孤儿清单里该行必须记为「已删除」（审计链不能断）。
"""
from __future__ import annotations

import ast
import importlib
import io
import os
import sys

import pytest

from nucleus._silent_except import silent_exc

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_GONE_REL = "hardware/hw_abstraction.py"
_GONE_MOD = "hw_abstraction"
_MANIFEST = os.path.join(
    _ROOT, "docs", "路灯与星轨对话", "分析报告", "路灯_398批_孤儿处置清单.md")
_SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "build", "tmp", ".workbuddy"}


def _iter_py():
    for _dp, _dns, _fns in os.walk(_ROOT):
        _dns[:] = [d for d in _dns if d not in _SKIP_DIRS and not d.startswith(".bak")]
        for _fn in _fns:
            if _fn.endswith(".py"):
                yield os.path.join(_dp, _fn)


def _rel(p: str) -> str:
    return os.path.relpath(p, _ROOT).replace("\\", "/")


# ---------------------------------------------------------------- ① 删除生效
def test_01_file_is_gone():
    assert not os.path.isfile(os.path.join(_ROOT, _GONE_REL)), (
        f"{_GONE_REL} 还在 —— 本刀就是要删它")


def test_02_module_is_no_longer_importable():
    """★光删 .py 不够：要确认 `import` 真的失败。

    `__pycache__/hw_abstraction.cpython-3xx.pyc` 会留在磁盘上（不进 git），
    它单独存在时 Python 3 不会拿它当模块 —— 但这条判据把"不会"变成"已验证"。
    """
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(_GONE_MOD)


# ---------------------------------------------------------------- ② 零引用（AST）
def test_03_no_import_reference_anywhere():
    """全仓 .py 里不许再有 `import hw_abstraction` / `from hardware import hw_abstraction`。"""
    _hits = []
    for _p in _iter_py():
        try:
            _t = ast.parse(io.open(_p, encoding="utf-8-sig", errors="replace").read())
        except SyntaxError as _e:
            silent_exc(_e, where="m399_k2::test_03 parse", level="debug")
            continue        # 与扫描器同口径：解析不了的 py 交给 cw2 的 parse_err 门
        for _n in ast.walk(_t):
            if isinstance(_n, ast.Import):
                if any(a.name.split(".")[-1] == _GONE_MOD for a in _n.names):
                    _hits.append((_rel(_p), _n.lineno))
            elif isinstance(_n, ast.ImportFrom):
                _mod = (_n.module or "")
                if _mod.split(".")[-1] == _GONE_MOD:
                    _hits.append((_rel(_p), _n.lineno))
                elif _mod.split(".")[0] == "hardware" and any(
                        a.name == _GONE_MOD for a in _n.names):
                    _hits.append((_rel(_p), _n.lineno))
    assert not _hits, f"仍有人在 import 它（删除会断链）：{_hits[:8]}"


# ---------------------------------------------------------------- ③ hardware 包仍可用
def test_04_hardware_namespace_package_still_works():
    """`hardware/` 没有 `__init__.py`（命名空间包），删一个子模块不影响其余。"""
    for _m in ("hardware.robot_body.robot_body_driver",
               "hardware.robot_body.config_body",
               "hardware.robot_body.tcp_client"):
        _mod = importlib.import_module(_m)
        assert getattr(_mod, "__name__", "") == _m
    assert os.path.isdir(os.path.join(_ROOT, "hardware", "robot_body")), "robot_body/ 没了"


# ---------------------------------------------------------------- ④ 调用图仍覆盖 hardware
def test_05_callgraph_still_covers_hardware():
    """★删文件不能让整个 `hardware/` 掉出扫描范围（票面担心的正是这个方向的反面）。"""
    import nucleus.self_awareness.CallGraphAnalyzer  # noqa: F401

    _cga = sys.modules["nucleus.self_awareness.CallGraphAnalyzer"]
    _f = _cga._iter_py_files(_cga._project_root(), _cga._scan_dirs())
    assert "hardware" in _cga._scan_dirs(), "hardware 掉出扫描目录了"
    assert not [x for x in _f if x == _GONE_REL], "已删文件还在图里（缓存没刷？）"
    _hw = [x for x in _f if x.startswith("hardware/")]
    assert _hw, "hardware/ 下一条都不在图里 —— 扫描范围出问题了"
    assert "hardware/robot_body/robot_body_driver.py" in _f
    # 覆盖数是 411→410 的实数，判据仍用下限（铁律：别钉 == ，新增一个正常文件就红）
    assert len(_f) >= 409, f"覆盖数掉到 {len(_f)}，低于 409 下限"


# ---------------------------------------------------------------- ⑤ 审计链
def test_06_manifest_records_the_deletion():
    """398 的处置清单必须记着它被 399 删了 —— 否则下次有人照着清单找文件会找不到理由。"""
    _s = io.open(_MANIFEST, encoding="utf-8", errors="replace").read()
    assert _GONE_MOD in _s, "清单里连名字都没了（审计链断了）"
    assert "399" in _s and "已删除" in _s, "清单里没有 399 批的删除追记"
    assert "13 个" in _s, "A 类件数没有从 14 改成 13"
