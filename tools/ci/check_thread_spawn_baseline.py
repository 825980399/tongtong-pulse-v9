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

★337批刀11 · 口径**定案**（此前 3/9/13 三套并存）：
    * **门禁口径 = `threading.Thread(` 字面**（即本件 PATTERN，唯一进基线的口径）；
    * 宽口径（`ThreadPoolExecutor(` + `.submit(`）**单列统计、不入基线** ——
      池化提交是有界复用，自起线程是不可控常驻增量，基线只该管后者；
    * `PulseController 13 处` **作废**：烛微 337 前置实测只有 3（门禁口径）／9（宽口径），
      328 移交的 13 复现不出（转引未验的老账，认账见 337 前置§五-㊃）。

★337批刀11 · `--emit` **禁止裸重锚**：delta != 0 时必须给 `--attr <json>`，
  且归因分项之和必须**严格等于** delta（算术自洽，不是"写了就算"）。
  为什么：基线门禁的全部价值 = "红线只能被有记录地挪动"；允许裸 `--emit`
  等于允许任何一次提交把自己违规的那 1 次 spawn 变成新常态。
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


def emit(current_total, current_files, attribution=None):
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
    if attribution:
        payload["attribution"] = attribution
        payload["reanchor_note"] = (
            "★337刀11 起：重锚必须带归因，且归因分项之和 == delta（脚本强制）")
    os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
    with open(BASELINE_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"[EMIT] baseline written -> {BASELINE_PATH}")
    print(f"[EMIT] total_occurrences={current_total} file_count={current_files}")


def main():
    ap = argparse.ArgumentParser(description="thread spawn baseline gate (172刀0)")
    ap.add_argument("--emit", action="store_true", help="write current count as baseline")
    # ★337批刀11：重锚归因（delta != 0 时必填）
    ap.add_argument("--attr", help="重锚归因 JSON 文件路径（delta != 0 时必填）")
    args = ap.parse_args()

    cur_total, cur_files, per_file = scan()

    if args.emit:
        _attr = None
        if args.attr:
            with open(args.attr, "r", encoding="utf-8") as fh:
                _attr = json.load(fh)
        # ★337批刀11：**拒绝裸重锚** —— delta 非 0 却无归因，直接不放行
        if os.path.exists(BASELINE_PATH):
            with open(BASELINE_PATH, "r", encoding="utf-8") as fh:
                _old = json.load(fh)
            _dt = cur_total - int(_old.get("total_occurrences") or 0)
            _df = cur_files - int(_old.get("file_count") or 0)
            if _dt or _df:
                if not _attr:
                    print("[FAIL] 拒绝裸重锚：delta occurrences=%+d files=%+d "
                          "但未提供 --attr 归因（337刀11 起强制）" % (_dt, _df))
                    return 1
                _sd = sum(int(a.get("delta_occurrences", 0)) for a in _attr)
                _sf = sum(int(a.get("delta_files", 0)) for a in _attr)
                if _sd != _dt or _sf != _df:
                    print("[FAIL] 归因与 delta 不符：归因合计 %+d/%+d vs delta %+d/%+d"
                          % (_sd, _sf, _dt, _df))
                    return 1
                print("[OK] 归因算术自洽：%+d 次 / %+d 文件（%d 条）"
                      % (_dt, _df, len(_attr)))
        emit(cur_total, cur_files, _attr)
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
