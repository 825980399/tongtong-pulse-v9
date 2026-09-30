# -*- coding: utf-8 -*-
"""统一「应排除目录」配置（主线第47批 T4，P2-310）

背景
----
第46批连续三次踩中同一类问题：**新建的副本/归档/临时目录被全库扫描类测试计入**：

1. ``.tmp_backup/``（第46批新建的 tmp 快照）→ ``test_entry_probe_m35`` 把备份副本里的
   历史 noqa 当成源码死 noqa，**误报 19 处**
2. ``.pytest_tmp/``（第46批新建的测试隔离目录）→ 同类风险
3. ``.release-tmp/``（发布源码副本）→ 被 ``scan_orphan_event_m10`` 计入，
   每个 Event 常量在副本里"自我引用" → **零引用恒为 0，审计彻底失效**（24→0）

根因：**每个测试各自维护排除列表，新增目录时必然遗漏**。

解法
----
本模块提供**唯一权威来源**，所有全库扫描类测试/工具一律引用它。

★与第44批 ``write_guard.py`` 同目录 —— 二者同属「目录/数据治理」语义。
"""

from __future__ import annotations

import os
from collections.abc import Iterator

#: 版本/进度控制类目录（原有，各测试已在用）
VCS_DIRS = frozenset({
    ".git", ".svn", ".hg",
})

#: Python / 工具缓存
CACHE_DIRS = frozenset({
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "node_modules", ".venv", "venv", "htmlcov", ".tox",
})

#: ★源码副本 / 归档 / 备份 —— 第46批事故的核心
#:   这些目录里的 .py 是**别处的副本**，计入扫描会让引用统计自我膨胀、
#:   noqa 审计把历史遗留当成当前源码。
COPY_DIRS = frozenset({
    ".release-tmp",          # 发布源码副本（★零引用审计失效的元凶）
    ".tmp_backup",           # 第46批 tmp 快照（旧落点）
    ".bak_tmp",              # 第47批 tmp 快照（新落点）
    ".pytest_tmp",           # 测试隔离目录（第46批起）
    "code_backups",
})

#: 数据/输出目录（通常不含源码，但扫描时也应跳过以提速）
DATA_DIRS = frozenset({
    "data", "logs", "tmp",
})

#: 备份目录的**前缀**规则（``.bak_batch44`` / ``.bak_batch47`` / ...）
BACKUP_PREFIXES = (".bak",)

#: 默认全集
DEFAULT_EXCLUDED = VCS_DIRS | CACHE_DIRS | COPY_DIRS

# =============================================================================
# ★主线第49批 T5（P2-310）：源码静态分析类模块的共享排除集
#
#   背景：CallGraphAnalyzer / FakeLoopDetector / ProductionConsumptionMatcher
#   各自定义 `_EXCLUDE_DIRS`（三套集合**并不相同**）→ 新增目录时必然遗漏。
#
#   ★设计：**集中定义、逐一保持各模块原有语义**。
#     若直接合并成一个集合，CallGraphAnalyzer 会多排/少排
#     目录 → **扫描结果变化 → 既有测试变红**。故保留三套具名集合。
# =============================================================================

#: 源码通道共同集（FakeLoopDetector / ProductionConsumptionMatcher 原有集合）
SOURCE_SCAN_DIRS = frozenset({
    "tests", "tmp", "logs", "data", "models", "venv",
    "node_modules", ".git", ".pytest_cache", "__pycache__",
})

#: CallGraphAnalyzer 原有集合（= 共同集 + 下列 4 项）
CALL_GRAPH_EXCLUDED = SOURCE_SCAN_DIRS | frozenset({
    "test", ".venv", "docs", "hardware",
})

#: ★磁盘枚举通道排除（**不含 data/logs** —— 它们正是目标数据目录）
DISK_SCAN_EXCLUDED = frozenset({
    "tmp", "__pycache__", "node_modules", ".git", ".pytest_cache",
    ".ruff_cache", "venv", "models", "hardware", "code_backups",
})


def is_excluded(dirname: str, include_data: bool = True) -> bool:
    """该目录名是否应被全库扫描排除。

    Args:
        dirname:      目录**名**（不是路径）。
        include_data: 是否一并排除 ``data`` / ``logs`` / ``tmp``。
    """
    if not dirname:
        return False
    if dirname in DEFAULT_EXCLUDED:
        return True
    if include_data and dirname in DATA_DIRS:
        return True
    for _p in BACKUP_PREFIXES:
        if dirname.startswith(_p):
            return True
    return False


def prune(dirs: list[str], include_data: bool = True) -> list[str]:
    """用于 ``os.walk`` 的原地剪枝：``dns[:] = prune(dns)``。"""
    return [d for d in dirs if not is_excluded(d, include_data)]


def iter_python_files(root: str, include_data: bool = True,
                      extra_skip: frozenset[str] | set[str] | None = None,
                      ) -> Iterator[str]:
    """遍历 ``root`` 下的全部 ``.py`` 文件（**已排除**副本/缓存/备份目录）。

    Args:
        root:         扫描根。
        include_data: 是否跳过 ``data`` / ``logs`` / ``tmp``。
        extra_skip:   额外排除的目录名（调用方特有）。
    """
    _extra = set(extra_skip or ())
    for _dp, _dns, _fns in os.walk(root):
        _dns[:] = [d for d in _dns
                   if not is_excluded(d, include_data) and d not in _extra]
        for _f in _fns:
            if _f.endswith(".py"):
                yield os.path.join(_dp, _f)


def describe() -> dict[str, list[str]]:
    """返回当前排除清单（供测试与文档核对）。"""
    return {
        "vcs": sorted(VCS_DIRS),
        "cache": sorted(CACHE_DIRS),
        "copy": sorted(COPY_DIRS),
        "data": sorted(DATA_DIRS),
        "backup_prefixes": list(BACKUP_PREFIXES),
    }
# =============================================================
# ★主线第55批 T4：tools/ 审计类工具的共享排除集
#
#   背景（与第49批 T5 同源）：tools/ 下 5 个脚本**各自定义**排除清单，
#   → 「新增目录时必然遗漏」（第46批连续三次踩坑的根因）。
#
#   ★设计原则（铁律 47：意图 > 字面）：
#     1. 基础项（VCS / 缓存 / 副本）集中到此处，各工具只保留**特有项**
#     2. 只做「原集合 ⊆ 新集合」的替换 —— **不缩小**任何既有排除范围
#     3. 新增项均为缓存 / 副本 / VCS 类，不排除真实源码目录
#     4. 各工具带灰度开关 ENABLE_EXCLUDE_DIRS_UNIFIED（关 = 回退第55批前行为）
#
#   等价性由 tests/test_exclude_dirs_unified_m55.py 守（原集合逐项 ⊆ 新集合）
# =============================================================

#: 工作区元数据 / IDE 配置类（check_code_limits 与 audit_utils 共有）
WORKSPACE_META_DIRS = frozenset({
    ".idea", ".vscode", ".mpy-workbench", ".workbuddy", "corrupted",
})

#: 审计类共用的「巨量 / 非源码」目录（含 qica 等项目专属目录）
AUDIT_BULK_DIRS = frozenset({
    "models", "hardware", "knowledge", "experience", "stream", "qica",
})

#: ① 全量代码审计（tools/check_code_limits.py）
AUDIT_SCAN_EXCLUDED = DEFAULT_EXCLUDED | DATA_DIRS | WORKSPACE_META_DIRS | AUDIT_BULK_DIRS

#: ② 备份快照（tools/batch_backup.py）：副本 / 缓存 + 依赖目录
#:    ★刻意**不含** data / tmp —— 见 batch_backup 顶部注释（P2-334）
BACKUP_SCAN_EXCLUDED = DEFAULT_EXCLUDED | frozenset({
    ".ipynb_checkpoints", "site-packages",
})

#: ③ 打包交付（tools/package_full_project.py）：不含 data / logs（只保留骨架）
PACKAGE_EXCLUDED = CACHE_DIRS | VCS_DIRS | frozenset({".idea", ".vscode"})

#: ④ 质量审计（tools/quality_audit.py）：比 AUDIT_SCAN_EXCLUDED 更窄，避免漏报
QUALITY_AUDIT_EXCLUDED = VCS_DIRS | CACHE_DIRS | frozenset({
    ".workbuddy", "tmp", "site-packages",
})

#: ⑤ 通用审计工具（tools/audit_utils.py）：VCS / 缓存 / 副本 + 工作区元数据 + logs
COMMON_SCAN_EXCLUDED = DEFAULT_EXCLUDED | WORKSPACE_META_DIRS | frozenset({"logs"})

# _m49_t5_exc_done
