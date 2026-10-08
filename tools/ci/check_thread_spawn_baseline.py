#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
172刀0 · 线程计数口径统一 · 门禁件（CI-ONLY，零生产代码面改动）

钉死"生产 spawn 点"口径并断言一致性：
    指标 = 仓库内所有 .py 文件中 `threading.Thread(` 出现次数（不重复计数文件）
    排除目录：tests/（测试，非生产）、.bak*（全部批次备份，gitignored）、
              data/（运行时输出，本批纪律零写盘 data）、tmp/、.git、__pycache__、各缓存目录
    排除方式：路径不以 tests/ 开头；跳过上述目录

门禁行为：
    默认：读取基线 JSON，断言 当前计数 == 基线（occurrences + file_count 双向一致）；
          不一致则打印差异并 exit(1)。
    --emit：将当前计数写入基线 JSON（首次锚定或重锚时使用）。

历史口径冲突（已解决）：170C11 报 49/37、170 任务书 60/20+、172 规划窗实测 121/67，
    三套并存导致验收不可判定。本件以"生产源码"精确排除集锁定唯一可复现口径。
"""
import argparse
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))  # <repo>/tools/ci/<script> -> 3 levels
BASELINE_PATH = os.path.join(REPO_ROOT, "tools", "ci", "baselines", "thread_spawn_baseline.json")
SELF_REL = os.path.relpath(os.path.abspath(__file__), REPO_ROOT).replace("\\", "/")
PATTERN = re.compile(r"threading\.Thread\(")
SKIP_DIRS = {".git", "__pycache__", ".ruff_cache", ".pytest_cache",
             "tmp", "node_modules", ".venv", "venv", "data"}


def scan():
    total = 0
    file_set = set()
    per_file = []
    for root, dirs, fns in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".bak")]
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, REPO_ROOT).replace("\\", "/")
            if rel.startswith("tests/"):
                continue
            if rel == SELF_REL:  # exclude self-reference (literal pattern in docstring/code)
                continue
            with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                txt = fh.read()
            n = len(PATTERN.findall(txt))
            if n:
                total += n
                file_set.add(rel)
                per_file.append([rel, n])
    return total, len(file_set), sorted(per_file)


def emit(current_total, current_files):
    payload = {
        "metric": "production_thread_spawn_points",
        "pattern": "threading.Thread(",
        "caliber": ("occurrences of 'threading.Thread(' in .py files under repo root, "
                    "EXCLUDING paths starting with tests/, and excluding directories: "
                    ".bak* (all batch backups), data/ (runtime), tmp/, .git, __pycache__, caches"),
        "total_occurrences": current_total,
        "file_count": current_files,
        "anchored_by": "172刀0",
        "anchored_at": "2026-10-08",
        "note": "resolves historical ambiguity 49/37 vs 60/20+ vs 121/67; locked to production-source caliber",
    }
    os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
    with open(BASELINE_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"[EMIT] baseline written -> {BASELINE_PATH}")
    print(f"[EMIT] total_occurrences={current_total} file_count={current_files}")


def main():
    ap = argparse.ArgumentParser(description="thread spawn baseline gate (172刀0)")
    ap.add_argument("--emit", action="store_true", help="write current count as baseline")
    args = ap.parse_args()

    cur_total, cur_files, _ = scan()

    if args.emit:
        emit(cur_total, cur_files)
        return 0

    if not os.path.exists(BASELINE_PATH):
        print(f"[FAIL] baseline missing: {BASELINE_PATH} (run with --emit to anchor)")
        return 1

    with open(BASELINE_PATH, "r", encoding="utf-8") as fh:
        base = json.load(fh)

    base_total = base.get("total_occurrences")
    base_files = base.get("file_count")

    if cur_total == base_total and cur_files == base_files:
        print(f"[PASS] thread spawn caliber stable: occurrences={cur_total} files={cur_files}")
        return 0

    print("[FAIL] thread spawn caliber drifted:")
    print(f"       baseline occurrences={base_total} files={base_files}")
    print(f"       current   occurrences={cur_total} files={cur_files}")
    print(f"       delta     occurrences={cur_total - base_total} files={cur_files - base_files}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
