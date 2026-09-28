# -*- coding: utf-8 -*-
"""批次代码备份（``.bak_batchN``）—— 主线第50批 T4（P2-334）

问题（P2-334）
-------------
历次批次的备份脚本用**裸目录名**集合做剪枝::

    SKIP = {".git", "__pycache__", "data", "logs", "tmp", ...}
    dns[:] = [x for x in dns if x not in SKIP]

`os.walk` 会在**任何层级**匹配 `data` / `tmp` —— 于是
``nucleus/data/``（第48批新增的 `exclude_dirs.py` / `path_utils.py` 所在目录）
被误跳过 → 这些改动文件**无基线**（第49批实测 3 个文件：`exclude_dirs.py`、
`test_isolation.py`、`path_utils.py`），改动行数无法准确统计。

修复
----
1. **相对路径前缀匹配**（本模块 `SKIP_PATH_PREFIXES`）：
   ``data/`` / ``logs/`` 只在**项目根层**跳过（纯数据、体量大）。
2. **目录名匹配**只用于「缓存 / 副本」类，**明确不含** ``data`` / ``tmp``。
3. **``tmp/`` 纳入备份**：``tmp/test_isolation.py`` 是真实被测代码，
   第22批曾因整目录被删而**永久丢失**（无 git 历史、无副本、无备份）。
   只复制 ``.py``，体量约 1 MB，可接受。

用法::

    python tools/batch_backup.py 50                 # 生成 .bak_batch50
    python tools/batch_backup.py 50 --dry-run       # 只统计不落盘
    python tools/batch_backup.py plan               # 只列出将被备份的文件
"""

from __future__ import annotations
from nucleus.data.exclude_dirs import BACKUP_SCAN_EXCLUDED  # ★第55批 T4（统一排除清单）


from nucleus.data.path_utils import safe_relpath as _safe_relpath  # ★第55批 T3（跨盘安全，同盘行为与 os.path.relpath 一致）
import argparse
import os
import shutil
import sys

__all__ = [
    "SKIP_PATH_PREFIXES", "SKIP_DIR_NAMES", "BACKUP_DIR_PREFIXES",
    "KNOWN_SKIPPED_PY_FILES",
    "should_skip_dir", "iter_py_files", "backup", "plan",
]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: ★P2-334 修复点：**相对路径前缀**（只在项目根层命中）
#: ★P2-208 基线声明：以下目录为**运行时数据目录**，非源码，刻意不纳入备份基线：
#:   - ``data/``：运行时知识库/经验库/补丁/指标等JSON与缓存；
#:     已知例外：``data/evolution/tests/`` 下有6个.py测试文件（约1.5KB/个），
#:     属进化引擎配套测试，建议后续迁移至 ``tests/`` 目录统一管理。
#:   - ``logs/``：运行日志，滚动覆盖，无基线价值。
#: 如需纳入 ``data/**/*.py``，应先将源码类文件迁出 data/，避免混入运行时数据。
SKIP_PATH_PREFIXES: tuple[str, ...] = ("data/", "logs/")

#: ★P2-208：已知被跳过的.py文件清单（便于审计，不改变跳过行为）
KNOWN_SKIPPED_PY_FILES: tuple[str, ...] = (
    "data/evolution/tests/loop_verify_test.py",
    "data/evolution/tests/test_p0_compat.py",
    "data/evolution/tests/test_p0_roundtrip.py",
    "data/evolution/tests/test_p0_test.py",
    "data/evolution/tests/test_smoke_health2_test.py",
    "data/evolution/tests/test_smoke_health_test.py",
)

#: 缓存 / 副本类目录名（**任意层级**按名跳过）
#: ★刻意**不含** ``data`` / ``tmp`` —— 这正是 P2-334 的缺陷所在。


def _m55_unified_excludes() -> bool:
    """★第55批 T4 灰度开关：关掉可回退到第55批前的各自定义清单。"""
    try:
        import config as _m55_cfg

        return bool(getattr(_m55_cfg, "ENABLE_EXCLUDE_DIRS_UNIFIED", True))
    except Exception:
        return True

_M55_LEGACY_SKIP_DIR_NAMES: frozenset[str] = frozenset({
    ".git", ".svn", ".hg",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".ipynb_checkpoints", "htmlcov", ".tox",
    "node_modules", ".venv", "venv",
    "code_backups", "site-packages",
    # 备份/隔离落点（均为副本，非独立源码）
    ".tmp_backup", ".bak_tmp", ".pytest_tmp", ".release-tmp",
})

#: ★第55批 T4：统一清单（基础项来自 exclude_dirs，新增目录只需改一处）
SKIP_DIR_NAMES: frozenset[str] = (
    BACKUP_SCAN_EXCLUDED if _m55_unified_excludes() else _M55_LEGACY_SKIP_DIR_NAMES
)

#: 备份目录前缀（``.bak_batch44`` / ``.bak_tmp`` / ...）
BACKUP_DIR_PREFIXES: tuple[str, ...] = (".bak",)


def _norm(rel_dir: str) -> str:
    return (rel_dir or "").replace("\\", "/").strip("/")


def should_skip_dir(rel_dir: str) -> bool:
    """按**相对路径**判断该目录是否应跳过。

    Args:
        rel_dir: 相对项目根的目录路径，如 ``"nucleus/data"`` / ``"data"`` / ``"tmp"``。
                 （不是裸目录名 —— 传裸名会失去层级信息。）
    """
    rel = _norm(rel_dir)
    if not rel or rel == ".":
        return False
    # ① 根层纯数据目录：相对路径前缀匹配（★P2-334）
    for _p in SKIP_PATH_PREFIXES:
        if rel == _p.rstrip("/") or rel.startswith(_p):
            return True
    _name = rel.rsplit("/", 1)[-1]
    # ② 缓存/副本类：任意层级按名
    if _name in SKIP_DIR_NAMES:
        return True
    # ③ 备份目录前缀
    if _name.startswith(BACKUP_DIR_PREFIXES):
        return True
    return False


def iter_py_files(root: str = ROOT):
    """遍历应备份的 ``.py`` 文件，返回**相对路径**（POSIX 分隔符）。"""
    root = os.path.abspath(root)
    for dp, dns, fns in os.walk(root):
        rel_dir = _safe_relpath(dp, root).replace("\\", "/")
        dns[:] = [d for d in dns if not should_skip_dir(
            d if rel_dir in (".", "") else "%s/%s" % (rel_dir, d))]
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            rel = _safe_relpath(os.path.join(dp, fn), root).replace("\\", "/")
            yield rel


def plan(root: str = ROOT) -> dict:
    """列出将被备份的文件（只读）。"""
    files = sorted(iter_py_files(root))
    _by_top: dict[str, int] = {}
    for f in files:
        _by_top[f.split("/", 1)[0]] = _by_top.get(f.split("/", 1)[0], 0) + 1
    return {
        "root": os.path.abspath(root),
        "count": len(files),
        "by_top": dict(sorted(_by_top.items(), key=lambda kv: -kv[1])),
        # ★自检：这两处必须为 0
        "skipped_nucleus_data": sum(1 for f in files if f.startswith("nucleus/data/")),
        "skipped_root_tmp": sum(1 for f in files if f.startswith("tmp/")),
        "files": files,
    }


def backup(batch_no: int, root: str = ROOT, dry_run: bool = False) -> dict:
    """生成 ``.bak_batchN``（保持相对路径）。"""
    root = os.path.abspath(root)
    files = sorted(iter_py_files(root))
    dst_root = os.path.join(root, ".bak_batch%d" % batch_no)
    copied = 0
    errors: list[str] = []
    if not dry_run:
        os.makedirs(dst_root, exist_ok=True)
    for rel in files:
        src = os.path.join(root, rel.replace("/", os.sep))
        dst = os.path.join(dst_root, rel.replace("/", os.sep))
        try:
            if not dry_run:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
            copied += 1
        except Exception as _e:                      # 单个文件失败不中断整批
            errors.append("%s: %s: %s" % (rel, type(_e).__name__, _e))
    return {
        "batch": batch_no,
        "dst": dst_root,
        "dry_run": dry_run,
        "copied": copied,
        "errors": errors,
        "nucleus_data_included": sum(1 for f in files if f.startswith("nucleus/data/")),
        "tmp_included": sum(1 for f in files if f.startswith("tmp/")),
    }


def main(argv: list[str] | None = None) -> int:
    _ap = argparse.ArgumentParser(description="批次代码备份（P2-334 修复版）")
    _ap.add_argument("batch", help="批次号（生成 .bak_batchN）或 'plan'")
    _ap.add_argument("--root", default=ROOT)
    _ap.add_argument("--dry-run", action="store_true")
    _ns = _ap.parse_args(argv)

    if _ns.batch == "plan":
        _p = plan(_ns.root)
        print("将被备份的 .py 文件数: %d" % _p["count"])
        print("根层分布（Top）:")
        for k, v in list(_p["by_top"].items())[:12]:
            print("   %-24s %4d" % (k, v))
        print("★ nucleus/data/ 纳入: %d 个（P2-334 修复点，期望 > 0）"
              % _p["skipped_nucleus_data"])
        print("★ tmp/ 纳入:          %d 个（期望 > 0）" % _p["skipped_root_tmp"])
        return 0

    _r = backup(int(_ns.batch), _ns.root, dry_run=_ns.dry_run)
    print("[%s] .bak_batch%d → %s" % ("DRY" if _r["dry_run"] else "OK",
                                      _r["batch"], _r["dst"]))
    print("  已复制: %d" % _r["copied"])
    print("  nucleus/data/ 纳入: %d    tmp/ 纳入: %d"
          % (_r["nucleus_data_included"], _r["tmp_included"]))
    for _e in _r["errors"][:10]:
        print("  [ERR] %s" % _e)
    return 1 if _r["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
