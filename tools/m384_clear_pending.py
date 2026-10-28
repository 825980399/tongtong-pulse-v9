# -*- coding: utf-8 -*-
"""384 刀2：pending_patches.json 清零（停机窗专用）。

## 为什么需要工具而不是直接手改

`data/patches/pending_patches.json` 是**生产数据**。铁律 198：普通脚本写生产 data/
必须显式确认 + 可回滚。所以本脚本：
  · 默认 **dry-run**（只打印，不写）
  · `--apply` 才写，且**先备份**到 `.bak_384/`
  · 备份后核对 sha256，不一致就中止
  · 写前再核对 `patch_history.json` 未被触碰

## ★结构修正（票面口径 vs 实测）

票面写「清零 pending 数组（保留文件结构）」「pending 2 条 + runtime_verified 8 条」。
实测：文件是**顶层 list**（不是含 `pending` 数组的 dict），共 **11** 条，
`status` 分布 = pending **3** + runtime_verified 8。

所以本脚本的"清零"= **删掉 `status == "pending"` 的条目**，保留 runtime_verified
（它们代表已跑过运行验证、只是等人工确认的补丁，不是 pending 队列）。
文件结构（顶层 list）不变。

用法：
    python tools/m384_clear_pending.py                    # dry-run
    python tools/m384_clear_pending.py --apply             # 真清（自动备份）
    python tools/m384_clear_pending.py --apply --allow-prod   # 生产目录显式确认
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402

PENDING = os.path.join(_ROOT, "data", "patches", "pending_patches.json")
HISTORY = os.path.join(_ROOT, "data", "patches", "patch_history.json")
BAK_DIR = os.path.join(_ROOT, ".bak_384")

#: 要清掉的状态。**只清 pending**：runtime_verified 是「已验证、等人工确认」，
#: 与「无人推进的 pending」不是一回事，一起清会把可确认项也丢掉。
CLEAR_STATUSES = ("pending",)


def _sha256(path: str) -> str:
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
    except OSError as _e:
        silent_exc(_e, where="m384_clear_pending._sha256", level="debug")
        return ""


def _load(path: str):
    with open(path, "r", encoding="utf-8") as _f:
        return json.load(_f)


def survey(rows: list) -> dict:
    """统计各状态条数（不同事实不同措辞，铁律166）。"""
    _by = {}
    for _r in rows:
        if not isinstance(_r, dict):
            _by["<非dict>"] = _by.get("<非dict>", 0) + 1
            continue
        _st = str(_r.get("status") or "<无status>")
        _by[_st] = _by.get(_st, 0) + 1
    return _by


def split_rows(rows: list, clear_statuses=CLEAR_STATUSES) -> tuple:
    """返回 (保留, 清除)。非 dict 一律保留（宁可留，不可误删）。"""
    _keep, _drop = [], []
    for _r in rows:
        if not isinstance(_r, dict):
            _keep.append(_r)
            continue
        if str(_r.get("status") or "") in clear_statuses:
            _drop.append(_r)
        else:
            _keep.append(_r)
    return _keep, _drop


def _brief(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "status": row.get("status"),
        "file": row.get("file"),
        "method": row.get("method"),
        "issue_type": row.get("issue_type"),
        "risk_level": row.get("risk_level"),
        "trust_score": row.get("trust_score"),
        "dedup_key": row.get("dedup_key"),
        "created_at": row.get("created_at") or row.get("timestamp"),
    }


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="384刀2 pending 清零（默认 dry-run）")
    _ap.add_argument("--apply", action="store_true", help="真写入（先自动备份）")
    _ap.add_argument("--allow-prod", action="store_true",
                     help="确认这是生产 data/ 目录（--apply 时必须叠加以防误跑）")
    _ap.add_argument("--json", default="", help="把清单报告写到该路径")
    _args = _ap.parse_args(argv)

    if not os.path.isfile(PENDING):
        print("[384刀2] 找不到 %s" % PENDING)
        return 1

    _rows = _load(PENDING)
    if not isinstance(_rows, list):
        print("[384刀2] 结构异常：顶层应为 list，实际 %s ⇒ 中止（不猜结构）"
              % type(_rows).__name__)
        return 1

    _before_hist = _sha256(HISTORY)
    _keep, _drop = split_rows(_rows)
    _report = {
        "pending_file": PENDING,
        "structure": "top-level list",
        "total_before": len(_rows),
        "by_status_before": survey(_rows),
        "cleared": [_brief(_r) for _r in _drop],
        "cleared_count": len(_drop),
        "kept": [_brief(_r) for _r in _keep],
        "kept_count": len(_keep),
        "by_status_after": survey(_keep),
        "history_sha256_before": _before_hist,
        "applied": False,
    }

    print("=" * 74)
    print("384刀2 pending_patches.json 清零（%s）" % ("APPLY" if _args.apply else "DRY-RUN"))
    print("=" * 74)
    print("结构：顶层 list｜总数 %d" % len(_rows))
    print("按状态：" + " / ".join("%s=%d" % kv for kv in sorted(_report["by_status_before"].items())))
    print("待清（status ∈ %s）：%d 条" % (list(CLEAR_STATUSES), len(_drop)))
    for _r in _drop:
        print("  - %-24s %-14s %-34s %s" % (str(_r.get("id"))[:24],
                                            str(_r.get("status")),
                                            str(_r.get("file"))[:34],
                                            str(_r.get("method"))[:22]))
    print("保留：%d 条（%s）" % (len(_keep),
                              " / ".join("%s=%d" % kv for kv in sorted(_report["by_status_after"].items()))))
    print("patch_history.json sha256[:16] = %s（写前）" % _before_hist)

    if _args.json:
        _out = _args.json if os.path.isabs(_args.json) else os.path.join(_ROOT, _args.json)
        os.makedirs(os.path.dirname(_out) or ".", exist_ok=True)
        with open(_out, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(_report, _f, ensure_ascii=False, indent=1)
        print("清单已写入 %s" % _out)

    if not _args.apply:
        print("\n[dry-run] 未写任何文件。加 --apply --allow-prod 才落盘。")
        return 0

    if not _args.allow_prod:
        print("\n[拒绝] --apply 必须叠加 --allow-prod（确认这是生产 data/ 目录）。")
        return 2

    # ① 备份
    os.makedirs(BAK_DIR, exist_ok=True)
    _bak = os.path.join(BAK_DIR, "pending_patches_before_384.json")
    shutil.copy2(PENDING, _bak)
    _src_sha, _bak_sha = _sha256(PENDING), _sha256(_bak)
    if not _src_sha or _src_sha != _bak_sha:
        print("[中止] 备份校验失败: src=%s bak=%s" % (_src_sha, _bak_sha))
        return 3
    print("[备份] %s（sha256[:16]=%s 已核对）" % (_bak, _bak_sha))

    # ② 写（保持顶层 list 结构；缩进与原文件一致用 indent=2）
    _tmp = PENDING + ".tmp384"
    with open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
        json.dump(_keep, _f, ensure_ascii=False, indent=2)
    os.replace(_tmp, PENDING)

    # ③ 复核
    _after = _load(PENDING)
    _n_pending = sum(1 for _r in _after
                     if isinstance(_r, dict) and str(_r.get("status") or "") == "pending")
    _hist_after = _sha256(HISTORY)
    print("[写后] 条数 %d → %d｜pending 余 %d 条" % (len(_rows), len(_after), _n_pending))
    print("[写后] patch_history.json sha256[:16] = %s（%s）"
          % (_hist_after, "未变 ✓" if _hist_after == _before_hist else "**变了，需排查**"))
    return 0 if (_n_pending == 0 and _hist_after == _before_hist) else 4


if __name__ == "__main__":
    sys.exit(main())
