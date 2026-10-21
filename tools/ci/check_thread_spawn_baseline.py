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
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))  # <repo>/tools/ci/<script> -> 3 levels
BASELINE_PATH = os.path.join(REPO_ROOT, "tools", "ci", "baselines", "thread_spawn_baseline.json")
SELF_REL = os.path.relpath(os.path.abspath(__file__), REPO_ROOT).replace("\\", "/")
def _log(level: str, msg: str) -> None:
    """★340刀5：本脚本原本**没有**日志器（`_log` 这个名字在本文件里不存在）。

    为什么要自建：cw2 门禁要求"静默 except 必须留痕"，而本脚本不能引框架 logger
    （CI 件要保持零框架依赖）。补留痕时若直接写 `_log(...)` 就是 **F821/NameError**
    —— 这个坑由 ruff `--select F` 抓出（不是靠肉眼）。
    """
    sys.stderr.write("[thread_spawn_baseline][%s] %s\n" % (level, msg))


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


def _git_show_baseline():
    """读 HEAD 里的基线（用于"重锚是否被署名"自检）；不可得 ⇒ None（不阻断）。"""
    try:
        import subprocess
        _rel = os.path.relpath(BASELINE_PATH, REPO_ROOT).replace("\\", "/")
        _r = subprocess.run(["git", "show", "HEAD:" + _rel],
                            capture_output=True, text=True, timeout=30,
                            cwd=REPO_ROOT, encoding="utf-8", errors="replace")
        if _r.returncode == 0 and (_r.stdout or "").strip():
            return json.loads(_r.stdout)
    except Exception as _e:
        # ★340刀5：不静默。读不到 HEAD 基线 ⇒ 按"首次锚定"处理，但必须说清楚，
        #   否则"自检被跳过"与"自检通过"在输出上长得一样（铁律156）。
        _log("warning", "HEAD 基线读取失败（按首次锚定处理，跳过署名自检）: "
                        "%s: %s" % (type(_e).__name__, _e))
    return None


def selfcheck_reanchor_sign(cur):
    """★340刀5：把"重锚必带当日署名 + 归因之和 == delta"从**纪律**升级为**机器可校验**。

    为什么需要：基线文件里 `anchored_by=172刀0/anchored_at=2026-10-08` 是**首次锚定**
    的署名，后续每次重锚都改数字却不改署名 ⇒ 读者无法从文件本身知道"这 64/45 是谁在
    什么时候锚的"（烛微 340 前置挂账 16/28）。本自检让"数字变了但没署名"直接 FAIL。

    Returns: ``(bool, 说明)``
    """
    _head = _git_show_baseline()
    if _head is None:
        return True, "HEAD 无此基线（首次锚定），跳过署名自检"
    _dt = int(cur.get("total_occurrences") or 0) - int(_head.get("total_occurrences") or 0)
    _df = int(cur.get("file_count") or 0) - int(_head.get("file_count") or 0)
    if _dt == 0 and _df == 0:
        return True, "与 HEAD 一致（%+d/%+d），无需重锚" % (_dt, _df)
    _today = time.strftime("%Y-%m-%d")
    _at = str(cur.get("reanchor_at") or "")
    if _at != _today:
        return False, ("数字已变（occurrences %+d / files %+d）但 reanchor_at=%r != 今日 %s"
                       % (_dt, _df, _at, _today))
    if not str(cur.get("reanchor_by") or ""):
        return False, "数字已变且日期对，但缺 reanchor_by 署名"
    _attr = cur.get("attribution") or []
    _sd = sum(int(a.get("delta_occurrences", 0)) for a in _attr)
    _sf = sum(int(a.get("delta_files", 0)) for a in _attr)
    if _sd != _dt or _sf != _df:
        return False, ("归因合计 %+d/%+d != delta %+d/%+d" % (_sd, _sf, _dt, _df))
    return True, ("署名/日期/归因三项自洽：%+d/%+d，by=%s at=%s"
                  % (_dt, _df, cur.get("reanchor_by"), _at))


def emit(current_total, current_files, attribution=None, reanchor_by=None):
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
        # ★340刀5：**只增不改写** —— `anchored_by/anchored_at` 是首次锚定的历史署名，
        #   本次重锚的署名另开两个字段（否则历史被覆盖，无法追溯"谁在什么时候挪的红线"）。
        payload["reanchor_by"] = str(reanchor_by or "unknown")
        payload["reanchor_at"] = time.strftime("%Y-%m-%d")
        _hist = []
        try:
            if os.path.exists(BASELINE_PATH):
                with open(BASELINE_PATH, "r", encoding="utf-8") as _fh:
                    _old = json.load(_fh)
                _hist = list(_old.get("reanchor_history") or [])
        except Exception as _e:
            # ★340刀5：旧基线不可读 ⇒ 历史从本次起重新累积，但**必须留痕**
            _log("warning", "旧基线 reanchor_history 不可读（历史自本次起累积）: "
                            "%s: %s" % (type(_e).__name__, _e))
            _hist = []
        _prev_attr = None
        try:
            if os.path.exists(BASELINE_PATH):
                with open(BASELINE_PATH, "r", encoding="utf-8") as _fh2:
                    _prev_attr = (json.load(_fh2) or {}).get("attribution")
        except Exception as _e:
            _log("warning", "读取上一次 attribution 失败（记 None）: %s: %s"
                 % (type(_e).__name__, _e))
        _hist.append({
            "reanchor_by": payload["reanchor_by"],
            "reanchor_at": payload["reanchor_at"],
            "from": {"total_occurrences": None, "file_count": None},
            "to": {"total_occurrences": current_total, "file_count": current_files},
            "attribution": attribution,
            # ★340刀5：`attribution` 字段的语义是"**本次**重锚的归因"
            #   （`sum == delta` 靠它成立），所以每次重锚会被覆盖；
            #   历史归因改由这里留存 ⇒ 既不破坏算术自洽，也不丢追溯。
            "prev_attribution": _prev_attr,
        })
        if _hist:
            try:
                with open(BASELINE_PATH, "r", encoding="utf-8") as _fh:
                    _old = json.load(_fh)
                _hist[-1]["from"] = {"total_occurrences": _old.get("total_occurrences"),
                                     "file_count": _old.get("file_count")}
            except Exception as _e:
                # ★340刀5：`from` 侧抓不到就留 None 并留痕（不填 0，不静默）
                _log("warning", "reanchor_history.from 取值失败（记 None，不填 0）: "
                                "%s: %s" % (type(_e).__name__, _e))
        payload["reanchor_history"] = _hist
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
    # ★340刀5：重锚署名（写进 reanchor_by；不给 ⇒ unknown 并告警）
    ap.add_argument("--by", help="重锚署名（如 '340刀2'）")
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
        if not args.by:
            print("[WARN] 未提供 --by ⇒ 重锚署名记为 unknown（340刀5：应写批次/刀号）")
        # ★340刀5：`from` 侧数字要在**写盘前**抓（emit 里会覆盖文件）
        emit(cur_total, cur_files, _attr, reanchor_by=args.by)
        return 0

    if not os.path.exists(BASELINE_PATH):
        print(f"[FAIL] baseline missing: {BASELINE_PATH} (run with --emit to anchor)")
        return 1

    with open(BASELINE_PATH, "r", encoding="utf-8") as fh:
        base = json.load(fh)

    base_total = base.get("total_occurrences")
    base_files = base.get("file_count")

    # ★340刀5：署名自检（数字被改动过 ⇒ 必须有当日署名 + 归因自洽）
    _ok, _why = selfcheck_reanchor_sign(base)
    if not _ok:
        print("[FAIL] 重锚署名自检不通过：%s" % _why)
        return 1
    print("[OK] 重锚署名自检：%s" % _why)

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
