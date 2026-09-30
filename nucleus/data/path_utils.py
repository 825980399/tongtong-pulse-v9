# -*- coding: utf-8 -*-
"""路径安全工具（主线第48批 T2，P2-320）

问题
----
`os.path.relpath(path, start)` 在 **Windows 跨盘符**（如 `path` 在 `C:`、
`start` 在 `D:`）时抛::

    ValueError: path is on mount 'C:', start on mount 'D:'

本项目在 `D:` 盘，而 pytest 的临时目录（`tempfile.gettempdir()`）、
用户目录常在 `C:` 盘 → 任何"把沙箱路径相对化到项目根"的写法都可能炸。

第46/47批已踩中 3 次：
  1. `tools/tmp_backup.snapshot()` 的 `"tmp_dir": os.path.relpath(tmp_dir, ROOT)`
  2. 同文件的 `scan_test_dependencies()`
  3. `tools/tmp_backup.migrate_legacy()`

（另有 `.bak_batch46/` 里 19 处 noqa 误报、`shutil.move` 跨盘触发沙箱删除配额
 等**同类跨盘**问题。）

解法
----
统一提供 :func:`safe_relpath`：跨盘时**降级为绝对路径**，其余行为与
`os.path.relpath` **完全一致** —— 只增加容错，不改变既有语义。

用法::

    from nucleus.data.path_utils import safe_relpath
    _rel = safe_relpath(some_path, ROOT)      # 跨盘时返回绝对路径
"""

from __future__ import annotations

import os
from nucleus._silent_except import silent_exc

__all__ = ["safe_relpath", "safe_commonpath", "same_drive", "drive_of"]


def drive_of(path: str) -> str:
    """返回路径的盘符（``D:``）/ UNC 前缀；无盘符时返回空串。

    ★用 ``splitdrive`` 而非取 ``[:2]`` —— 后者对 UNC（``\\\\server\\share``）
      与 POSIX 路径会给出错误结果。
    """
    try:
        return os.path.splitdrive(os.path.abspath(path))[0]
    except (TypeError, ValueError, OSError) as e:
        silent_exc(e, where="nucleus.data.path_utils::drive_of L48")
        return ""


def same_drive(path_a: str, path_b: str) -> bool:
    """两个路径是否在同一盘符（POSIX 上恒为 True，因为盘符为空串）。"""
    return drive_of(path_a) == drive_of(path_b)


def safe_relpath(path: str, start: str | None = None) -> str:
    """``os.path.relpath`` 的跨盘安全版本。

    跨盘（或任何 ``ValueError``）时**返回绝对路径**，绝不抛异常。

    Args:
        path:  目标路径。
        start: 基准路径；``None`` 时等价于 ``os.path.relpath(path)``（用 cwd）。

    Returns:
        相对路径；跨盘时返回 ``os.path.abspath(path)``。
    """
    try:
        _abs = os.path.abspath(path)
    except (TypeError, ValueError, OSError):
        return str(path)
    try:
        if start is None:
            return os.path.relpath(_abs)
        return os.path.relpath(_abs, os.path.abspath(start))
    except ValueError:
        # ★Windows 跨盘 —— 降级为绝对路径（信息不丢失，只是更长）
        return _abs
    except (TypeError, OSError):
        return _abs


def safe_commonpath(paths) -> str:
    """``os.path.commonpath`` 的跨盘安全版本；跨盘时返回空串。"""
    try:
        return os.path.commonpath([os.path.abspath(p) for p in paths])
    except (ValueError, TypeError, OSError) as e:
        silent_exc(e, where="nucleus.data.path_utils::safe_commonpath L88")
        return ""
