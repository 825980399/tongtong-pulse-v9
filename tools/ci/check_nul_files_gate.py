#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""保留设备名文件门禁（防回归）：禁止保留 Windows 设备名文件被纳入版本库。

背景
----
Windows 保留设备名（NUL / CON / PRN / AUX / COM1-9 / LPT1-9）若作为文件名提交，
在 NTFS 上会导致无法读写该路径（"文件名保留字"），进而让依赖该路径的工具/脚本
在 Windows CI 上直接崩坏。本门禁用 `git ls-files` 仅扫**已跟踪**文件：

设计要点（不阻断构建）
----------------------
* **仅扫描 `git ls-files`（已跟踪文件）**：任何被跟踪的文件若 basename（大小写不敏感、
  去扩展名后）命中保留名 → FAIL（exit 1）。
* **忽略/未跟踪的保留名文件只发 stderr 警告、不阻断**：例如仓库根已存在被 `.gitignore`
  排除的 `NUL` 文件，本门**绝不会**因它而 FAIL，避免误伤既有资产。
* `.git/` 内部路径一律跳过（git 内部对象，非用户资产）。

核心谓词
--------
`_is_reserved_name(basename)`：去扩展名后（大小写不敏感）命中保留名集合即 True。

用法
----
  python tools/ci/check_nul_files_gate.py          # 默认扫描（pre-commit 调用）
  python tools/ci/check_nul_files_gate.py --selftest
"""
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

#: Windows 保留设备名（小写，大小写不敏感比对）
_RESERVED = (
    {"nul", "con", "prn", "aux"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def _is_reserved_name(basename: str) -> bool:
    """basename 去扩展名后（小写）命中保留名集合 → True。"""
    stem, _ = os.path.splitext(basename)
    return stem.lower() in _RESERVED


def _tracked_files():
    """已跟踪文件相对路径列表（git ls-files）。非 git 环境返回空。"""
    r = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        return []
    return [ln.strip() for ln in r.stdout.split("\n") if ln.strip()]


def _others(ignored: bool):
    """未跟踪文件列表；ignored=True 取被忽略项，否则取未忽略项。"""
    cmd = ["git", "ls-files", "--others"]
    if ignored:
        cmd.append("--ignored")
    cmd.append("--exclude-standard")
    r = subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        return []
    return [ln.strip() for ln in r.stdout.split("\n") if ln.strip()]


def _in_git(p: str) -> bool:
    return p.startswith(".git/") or "/.git/" in p


def check():
    fails = []
    warns = []
    for p in _tracked_files():
        if _in_git(p):
            continue
        if _is_reserved_name(os.path.basename(p)):
            fails.append(p)
    # 忽略/未跟踪的保留名文件：仅警告，不阻断
    for p in _others(True) + _others(False):
        if _in_git(p):
            continue
        if _is_reserved_name(os.path.basename(p)):
            warns.append(p)

    for p in warns:
        sys.stderr.write("[NUL-gate] ⚠ 保留设备名文件（忽略/未跟踪，仅警告不阻断）: %s\n" % p)
    if fails:
        for p in fails:
            sys.stderr.write("[NUL-gate] ❌ 跟踪文件中存在保留设备名: %s\n" % p)
        sys.stderr.write("[NUL-gate] 结论：FAIL（保留设备名不得入库）\n")
        return 1
    sys.stderr.write("[NUL-gate] ✅ PASS（跟踪文件无保留设备名）\n")
    return 0


def selftest():
    # 核心谓词自证
    assert _is_reserved_name("NUL") is True
    assert _is_reserved_name("com1") is True
    assert _is_reserved_name("normal.py") is False
    # 豁免：.git 路径内即便 basename 命中保留名也跳过
    fake_tracked = ["src/app.py", ".git/refs/NUL", "docs/readme.md"]
    detected = []
    for p in fake_tracked:
        if _in_git(p):
            continue
        if _is_reserved_name(os.path.basename(p)):
            detected.append(p)
    assert all(not _in_git(p) for p in detected), "豁免：.git 路径应被跳过"
    assert not any("NUL" in os.path.basename(p) for p in detected), "豁免：.git/NUL 不应命中"
    print("[selftest] nul-files 自证通过")
    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv
    if "--selftest" in argv:
        return selftest()
    return check()


if __name__ == "__main__":
    sys.exit(main())
