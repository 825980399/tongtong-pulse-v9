# -*- coding: utf-8 -*-
"""audit_utils —— 审计/扫描脚本的统一排除规则（主线第12批 T2/P2-82）。

背景：
    多批审计脚本各自硬编码排除目录（如 `.bak_mainline8`/`.bak_mainline9`），
    新批次备份（`.bak_mainline10`+）出现后未同步 → 备份镜像的全库代码被当作
    源文件扫描 → 审计结果失真（第11批实测：零引用候选从 25 误降为 0）。
    星轨裁决（2026-09-10 22:40）：抽取共用模块，所有审计脚本引用同一份规则。

设计：
    - 备份目录一律 **前缀匹配**（`.bak`），不再逐批次硬编码 —— 新增批次零维护。
    - 同时覆盖 `.bak_mainline<N>` / `.bak_batch<N>` / `foo.py.bak_*` 等历史命名。
    - 纯函数、无副作用、不依赖项目其他模块，可被 tools/ 与 tmp/ 脚本独立引用。
"""

from __future__ import annotations
from nucleus.data.exclude_dirs import COMMON_SCAN_EXCLUDED  # ★第55批 T4（统一排除清单）

import os
import re
from nucleus.data.path_utils import safe_relpath  # ★第49批 T4


# ---------------------------------------------------------------------------
# 目录级排除（按“叶子目录名”匹配）
# ---------------------------------------------------------------------------

#: 备份目录前缀：任何以该串开头的目录名（含文件）一律视为备份快照
BACKUP_PREFIXES: tuple[str, ...] = (".bak",)

#: 备份目录的正则补充（覆盖历史命名，如 bak_mainline9、code_backups）
BACKUP_DIR_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"^\.bak"),                 # .bak_mainline8 / .bak_batch14
    re.compile(r"^bak_"),                  # 无点前缀的历史写法
)

#: 与业务无关的目录（缓存 / 运行时 / 巨量数据），审计一律排除


def _m55_unified_excludes() -> bool:
    """★第55批 T4 灰度开关：关掉可回退到第55批前的各自定义清单。"""
    try:
        import config as _m55_cfg

        return bool(getattr(_m55_cfg, "ENABLE_EXCLUDE_DIRS_UNIFIED", True))
    except Exception:
        return True

_M55_LEGACY_COMMON_EXCLUDE_DIRS: frozenset[str] = frozenset({
    ".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".idea", ".vscode", ".mpy-workbench", ".workbuddy",
    "logs", "node_modules", "code_backups", "corrupted",
})

#: ★第55批 T4：统一清单（基础项来自 exclude_dirs，新增目录只需改一处）
COMMON_EXCLUDE_DIRS: frozenset[str] = (
    COMMON_SCAN_EXCLUDED if _m55_unified_excludes() else _M55_LEGACY_COMMON_EXCLUDE_DIRS
)

#: 顶层数据/运行时目录（体积巨大且非源码，脚本大多需要排除）
COMMON_EXCLUDE_TOP: frozenset[str] = frozenset({
    "data", "logs", "node_modules", "models", "hardware",
    "knowledge", "experience", "stream",
})

#: 备份文件名后缀正则（如 `config.py.bak_mainline7`）
BACKUP_FILE_RE = re.compile(r"\.bak[_\-]?\w*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 判定函数
# ---------------------------------------------------------------------------

def is_backup_name(name: str) -> bool:
    """目录名或文件名是否属于备份快照（前缀 + 正则双保险）。"""
    if not name:
        return False
    if name.startswith(BACKUP_PREFIXES):
        return True
    return any(p.search(name) for p in BACKUP_DIR_PATTERNS)


def should_skip_dir(name: str, extra: frozenset[str] | None = None) -> bool:
    """目录是否应跳过（备份 + 通用排除 + 调用方自定义）。"""
    if is_backup_name(name):
        return True
    if name in COMMON_EXCLUDE_DIRS:
        return True
    return bool(extra and name in extra)


def should_skip_top(name: str) -> bool:
    """顶层目录是否应跳过（数据/运行时巨量目录）。"""
    return name in COMMON_EXCLUDE_TOP


def is_backup_path(path: str) -> bool:
    """完整路径中任一组成部分属备份目录/文件即判定为备份路径。"""
    if not path:
        return False
    norm = str(path).replace("\\", "/")
    parts = [p for p in norm.split("/") if p]
    for p in parts:
        if is_backup_name(p):
            return True
    return bool(BACKUP_FILE_RE.search(parts[-1])) if parts else False


def filter_backup_files(file_list) -> list:
    """从文件/路径列表中剔除备份路径（保持原顺序）。"""
    return [f for f in file_list if not is_backup_path(f)]


def iter_source_py(root: str, extra_exclude_dirs: frozenset[str] | None = None,
                   include_tmp: bool = False, include_tests: bool = True):
    """遍历源码 .py 文件（已排除备份/缓存/数据目录）。

    参数：
        root: 仓库根目录
        extra_exclude_dirs: 调用方额外的目录排除集合
        include_tmp: 是否包含 tmp/（多数审计脚本应排除，默认 False）
        include_tests: 是否包含 tests/（默认 True）

    产出：绝对路径字符串
    """
    _extra = set(extra_exclude_dirs or ())
    if not include_tmp:
        _extra.add("tmp")
    for dp, dn, fn in os.walk(root):
        # ★主线第49批 T4（P2-320）：`root` 是**可被外部传入**的参数
        #   （如沙箱测试传 C: 临时目录，而项目在 D: 盘）→ 原 `os.path.relpath`
        #   会抛 ValueError。改用跨盘安全版；**同盘行为与原实现完全一致**。
        rel = safe_relpath(dp, root).replace("\\", "/")
        parts = [] if rel == "." else rel.split("/")
        if parts and should_skip_top(parts[0]):
            dn[:] = []
            continue
        dn[:] = [d for d in dn if not should_skip_dir(d, frozenset(_extra))]
        for f in fn:
            if not f.endswith(".py"):
                continue
            if is_backup_path(f):
                continue
            yield os.path.join(dp, f)
# _m49_t4_au_body_done
# _m49_t4_au_imp_done
