# -*- coding: utf-8 -*-
"""编码卫生晨检工具（T-115a 新增）。

扫描仓库内全部 .py，输出两类问题：
  1) BOM 文件   —— 首 3 字节 == EF BB BF（会令 ast.parse 用 utf-8 读入后炸，触发幽灵题）
  2) 不可解析文件 —— 用 utf-8-sig 读入后仍 ast.parse 失败（语法/编码损坏）

设计目标：300 文件 < 1s。纯只读，不改动任何文件。
退出码：发现问题返回 1，干净返回 0（供晨检脚本串联）。

用法：
    python tools/check_encoding_hygiene.py
    python tools/check_encoding_hygiene.py --root . --quiet
"""
import argparse
import ast
import os
import sys
import time

SKIP_DIRS = {
    ".git", ".bak_batch", "backups", "tmp", "__pycache__", ".venv", "venv",
    "node_modules", ".mypy_cache", ".ruff_cache",
}


def iter_py(root):
    for dirpath, dirnames, filenames in os.walk(root):
        # 原地剪枝，避免进入备份/临时目录
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".bak_batch")]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def main():
    ap = argparse.ArgumentParser(description="编码卫生晨检：BOM + 可解析性")
    ap.add_argument("--root", default=".", help="扫描根目录（默认当前目录）")
    ap.add_argument("--quiet", action="store_true", help="仅输出问题行")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    if not args.quiet:
        print(f"[check_encoding_hygiene] 扫描 {root}")

    t0 = time.time()
    bom_files = []
    broken_files = []
    total = 0
    for path in iter_py(root):
        total += 1
        try:
            with open(path, "rb") as f:
                head = f.read(3)
            if head == b"\xef\xbb\xbf":
                bom_files.append(path)
            # 可解析性：用 utf-8-sig 读入（自动吞 BOM），仍失败才是真问题
            with open(path, "r", encoding="utf-8-sig") as f:
                src = f.read()
            ast.parse(src, filename=path)
        except SyntaxError as e:
            broken_files.append((path, f"SyntaxError: {e.msg} (line {e.lineno})"))
        except Exception as e:  # noqa: BLE001
            broken_files.append((path, f"{type(e).__name__}: {e}"))

    dt = time.time() - t0

    if not args.quiet:
        print(f"[check_encoding_hygiene] 共扫描 {total} 个 .py，耗时 {dt*1000:.1f} ms")
    if bom_files:
        print(f"[BOM] 发现 {len(bom_files)} 个带 UTF-8 BOM 的文件：")
        for p in bom_files:
            print(f"  BOM  {p}")
    if broken_files:
        print(f"[PARSE] 发现 {len(broken_files)} 个不可解析文件：")
        for p, why in broken_files:
            print(f"  FAIL {p}  -> {why}")

    if not bom_files and not broken_files:
        print("[check_encoding_hygiene] OK：无 BOM、全部可解析")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
