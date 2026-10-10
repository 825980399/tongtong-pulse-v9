# -*- coding: utf-8 -*-
"""tools/patch_template_helper —— 补丁脚本模板工具（主线第56批 T3/P2-390）

把"写补丁脚本"反复踩的 8 个坑固化成可复用 API，避免重复踩坑：

  坑1  多行 ``from x import (`` 的开头行被当插入点 → 语法错误
        → find_first_top_import_line 跳过整段多行 import（括号平衡）
  坑2  插在 ``from __future__`` 之前 → 语法错误
        → 安全插入点跳过 __future__
  坑3  插在 ``sys.path.insert()`` 之后 → E402
        → 安全插入点跳过 sys.path.insert()
  坑4  CRLF 文件用 ``\\n`` 模板 → anchor 失配 + 引入裸 LF
        → detect_eol + 所有模板统一用文件实际行尾
  坑5  config.py 追加块用 LF → 多 8 个裸 LF（与坑4 同源）
        → detect_eol 统一行尾
  坑6  ``eol`` 变量 bytes/str 混用 → TypeError
        → eol_str / eol_bytes 严格分离（bytes/str 分离）
  坑7  跨盘 os.path.relpath → ValueError（重复 2 次）
        → safe_relpath(f, root) 复用 nucleus.data.path_utils（基准=扫描根）
  坑8  补丁模板把 return _out 错写成 return _v → 返回类型错误但"能跑"
        → assert_structure 结构断言

核心 API
--------
* detect_eol(path) -> str                      行尾探测（坑4/5）
* eol_bytes(eol_str) -> bytes                  bytes/str 分离（坑6）
* find_first_top_import_line(text) -> int      安全插入点（坑1/2/3）
* balance_paren(lines, start) -> int           多行 import 结束行（坑1）
* safe_relpath(f, root) -> str                 跨盘安全（坑7，复用）
* assert_structure(value, expected)            结构断言（坑8）
* apply_patches(path, pairs, ...) -> dict      整体安全应用（坑4/8 + 幂等）

使用示例见本文件 ``__main__``（dry-run 演示，不改动任何生产文件）。
"""
from __future__ import annotations

import io
import os

from nucleus.data.path_utils import safe_relpath as _safe_relpath

__all__ = [
    "detect_eol", "eol_bytes", "find_first_top_import_line", "balance_paren",
    "safe_relpath", "assert_structure", "apply_patches",
]


# --------------------------------------------------------------------------
# 坑4/5：行尾探测（bytes/str 严格分离，见坑6）
# --------------------------------------------------------------------------
def detect_eol(path: str) -> str:
    """★检测文件行尾：返回 ``'\\r\\n'``（CRLF）或 ``'\\n'``（LF）。

    判定：CRLF 行数（= ``b'\\r\\n'`` 计数）过半即视为 CRLF，否则 LF。
    避免把 CRLF 文件误判为 LF 导致模板 anchor 失配、引入裸 LF（坑4/5）。
    """
    b = open(path, "rb").read()
    _crlf = b.count(b"\r\n")
    _lf = b.count(b"\n")
    return "\r\n" if _crlf * 2 >= _lf else "\n"


def eol_bytes(eol_str: str) -> bytes:
    """★坑6：bytes/str 分离。把行尾 str 转成 bytes，禁止混用。

    读盘用 bytes（``open(..., 'rb')``），写盘模板拼接用 str（``open(..., newline='')``），
    二者不可直接相加——此函数显式转换并校验类型，混用时尽早 TypeError。
    """
    if not isinstance(eol_str, str):
        raise TypeError(f"eol_str 必须是 str，收到 {type(eol_str).__name__}")
    _b = eol_str.encode("utf-8")
    if _b not in (b"\r\n", b"\n"):
        raise ValueError(f"非法行尾: {eol_str!r}")
    return _b


# --------------------------------------------------------------------------
# 坑1/2/3：安全插入点查找
# --------------------------------------------------------------------------
_FUTURE = "from __future__ import"
_SYSPATH_INSERT = "sys.path.insert("


def balance_paren(lines, start: int) -> int:
    """★坑1：给定多行 ``from x import (`` 的**开头行**下标 start，返回该语句结束行下标。

    通过括号平衡（统计未闭合 ``(`` 与 ``)``）定位结束行，避免把开头行当作插入点
    导致语法错误。返回最后一个属于该 import 语句的行下标。
    """
    _depth = 0
    _i = start
    while _i < len(lines):
        _line = lines[_i]
        _depth += _line.count("(") - _line.count(")")
        if _depth <= 0 and _i > start:
            return _i
        _i += 1
    return len(lines) - 1


def find_first_top_import_line(text: str) -> int:
    """★坑1/2/3：找到第一个**顶层** import 语句的行下标（在其**之前/之后**插入）。

    规则：
      * 跳过 ``from __future__ import``（坑2，必须在最前）
      * 跳过 ``sys.path.insert(...)``（坑3，其后插入会 E402）
      * 若首个顶层 import 是多行 ``from x import (``，用 balance_paren 跳到结束行**之后**
      * 找不到 import → 返回末尾（追加到文件尾）

    返回值含义：单行为该 import 行下标（在其前插入即可）；多行 import 为结束行**之后**
    的下标（模块级代码插在 import 块之后，仍属顶层，零 E402）。
    """
    _lines = text.splitlines()
    _n = len(_lines)
    _i = 0
    while _i < _n:
        _s = _lines[_i].strip()
        if _s.startswith(_FUTURE):  # 坑2：跳过 __future__ 整段
            _i += 1
            continue
        if _s.startswith(_SYSPATH_INSERT) or _s == "sys.path.insert":  # 坑3
            _i += 1
            continue
        if _s.startswith("import ") or _s.startswith("from "):
            if "(" in _s and ")" not in _s:  # 多行 import → 跳到结束行之后
                return balance_paren(_lines, _i) + 1
            return _i
        _i += 1
    return _n


# --------------------------------------------------------------------------
# 坑7：跨盘安全 relpath（复用 nucleus.data.path_utils，避免重造）
# --------------------------------------------------------------------------
def safe_relpath(f: str, root: str) -> str:
    """★坑7：跨盘安全 relpath（复用 ``nucleus.data.path_utils.safe_relpath``）。

    ``f`` 相对到**扫描根 root**（不是 ``_PROJ``），跨盘时降级为绝对路径，绝不抛
    ``ValueError``。
    """
    return _safe_relpath(f, root)


# --------------------------------------------------------------------------
# 坑8：结构断言
# --------------------------------------------------------------------------
def assert_structure(value, expected) -> None:
    """★坑8：改返回值/结构时必须加结构断言（避免"能跑但结果全错"）。

    * expected 为类型/类型元组 → 校验 isinstance
    * expected 为 callable（谓词）  → 校验其返回真
    * 其余                          → 按 ``==`` 比较
    断言失败抛 ``AssertionError``（带可读信息）。
    """
    if isinstance(expected, type):
        if not isinstance(value, expected):
            raise AssertionError(
                f"结构断言失败：期望类型 {expected.__name__}，"
                f"实际 {type(value).__name__}: {value!r}")
        return
    if isinstance(expected, tuple) and expected and all(
            isinstance(t, type) for t in expected):
        if not isinstance(value, expected):
            raise AssertionError(
                f"结构断言失败：期望类型之一 {expected}，"
                f"实际 {type(value).__name__}: {value!r}")
        return
    if callable(expected) and not isinstance(expected, type):
        if not expected(value):
            raise AssertionError(f"结构断言失败：谓词不满足，实际 {value!r}")
        return
    if value != expected:
        raise AssertionError(f"结构断言失败：期望 {expected!r}，实际 {value!r}")


# --------------------------------------------------------------------------
# 整体安全应用（坑4/8 + 唯一锚点 + 幂等 + all-or-nothing）
# --------------------------------------------------------------------------
def apply_patches(path: str, pairs, idempotency_tag: str | None = None,
                  dry_run: bool = False) -> dict:
    """★把一组 ``(old, new)`` 补丁应用到 path，全程安全：

      * 行尾探测（坑4/5）：用文件实际行尾重建锚点，避免 anchor 失配/裸 LF
      * 唯一锚点：每个 old 必须全库唯一（count==1），否则整体不写盘（防部分落盘）
      * 幂等：含 idempotency_tag 则整体跳过（不重复应用）
      * all-or-nothing：先全部校验通过，再一次性写盘
      * 结构断言由调用方在构造 new 时保证（坑8）

    pairs: list[(old:str, new:str)]，old/new 用 ``'\\n'`` 书写，内部按文件行尾重建。
    Returns: dict {"applied", "eol", "pairs", "skipped"/"dry_run"}
    """
    _eol = detect_eol(path)
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        _t = f.read()
    if idempotency_tag and idempotency_tag in _t:
        return {"applied": False, "eol": _eol, "pairs": 0, "skipped": True}
    for _old, _new in pairs:
        _old_e = _old.replace("\n", _eol)
        _n = _t.count(_old_e)
        if _n != 1:
            raise AssertionError(
                f"anchor 失配 {path!r}: 命中 {_n} 处（必须唯一）\n--- {_old!r}")
    if dry_run:
        return {"applied": False, "eol": _eol, "pairs": len(pairs),
                "dry_run": True}
    _out = _t
    for _old, _new in pairs:
        _old_e = _old.replace("\n", _eol)
        _new_e = _new.replace("\n", _eol)
        _out = _out.replace(_old_e, _new_e, 1)
    if idempotency_tag:
        if _out.endswith(_eol):
            _out = _out + idempotency_tag
        else:
            _out = _out + _eol + idempotency_tag
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(_out)
    return {"applied": True, "eol": _eol, "pairs": len(pairs)}


if __name__ == "__main__":  # pragma: no cover - 自演示（dry-run，不碰生产文件）
    import tempfile
    _d = tempfile.mkdtemp(prefix="m56_helper_demo_")
    _demo = os.path.join(_d, "demo_mod.py")
    with io.open(_demo, "w", encoding="utf-8", newline="") as f:
        f.write("from __future__ import annotations\n"
                "import os\n"
                "sys.path.insert(0, 'x')\n"
                "\n"
                "def foo():\n"
                "    return 1\n")
    print("detect_eol =>", repr(detect_eol(_demo)))
    _ins = find_first_top_import_line(
        io.open(_demo, "r", encoding="utf-8", newline="").read())
    print("insertion line index =>", _ins, "(应在 import os 之后)")
    print("safe_relpath cross-drive =>",
          repr(safe_relpath("C:/a/b.py", "D:/root")))
    print("apply_patches dry_run =>",
          apply_patches(_demo, [("def foo():\n", "def foo():\n    pass\n")],
                        idempotency_tag="# M56_DEMO", dry_run=True))
    shutil_rm = True
    try:
        import shutil
        shutil.rmtree(_d, ignore_errors=True)
    except Exception:
        shutil_rm = False
    print("demo cleaned:", shutil_rm)
