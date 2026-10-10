# -*- coding: utf-8 -*-
"""补丁历史去重工具（主线第43批 T4 / P1-280 / P1-279）。

用法::

    python tools/dedup_patch_history.py --dry-run   # 只报告，不写盘
    python tools/dedup_patch_history.py             # 备份 + 去重 + 修复 detail + 报告

产出：
  · ``data/patches/patch_history.json.bak_m43_dedup``   —— 去重前的完整备份
  · ``data/patches/patch_history_duplicates.json.bak``  —— **被移除的重复记录**（不删除）
  · ``data/patches/dedup_report.json``                  —— 去重报告

★ 幂等：已无重复时跳过写盘。★ 只删「重复记录」到备份文件，**从不直接丢弃数据**。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.evolution.patch_dedup import (  # noqa: E402
    dedup_history,
    dedup_stats,
    fix_details_in_history,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST = os.path.join(_ROOT, "data", "patches", "patch_history.json")
BAK_ALL = HIST + ".bak_m43_dedup"
BAK_DUP = os.path.join(_ROOT, "data", "patches", "patch_history_duplicates.json.bak")
REPORT = os.path.join(_ROOT, "data", "patches", "dedup_report.json")


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="补丁历史去重（P1-280）")
    _ap.add_argument("--dry-run", action="store_true", help="只报告不写盘")
    _ap.add_argument("--history", default=HIST)
    _ap.add_argument("--json", action="store_true")
    _args = _ap.parse_args(argv)

    if not os.path.isfile(_args.history):
        print("历史文件不存在: %s" % _args.history)
        return 1
    with io.open(_args.history, encoding="utf-8", errors="replace") as _f:
        _hist = json.loads(_f.read())
    if not isinstance(_hist, list):
        print("历史文件格式异常（非 list）")
        return 1

    _st = dedup_stats(_hist)
    print("=== 去重统计 ===")
    for _k, _v in _st.items():
        print("  %-22s %s" % (_k, str(_v)[:160]))

    _kept, _removed = dedup_history(_hist)
    _fixed, _ids = fix_details_in_history(_kept)
    print()
    print("detail 一致性修复（P1-279）: %d 条 %s" % (_fixed, _ids[:8]))

    if _st["duplicate_id_count"] == 0 and _fixed == 0:
        print()
        print("（已无重复、detail 已一致 → 无需写盘）")
        return 0

    if _args.dry_run:
        print()
        print("（--dry-run：未写盘）")
        return 0

    # ① 完整备份（含重复）
    if not os.path.isfile(BAK_ALL):
        shutil.copy2(_args.history, BAK_ALL)
        print("完整备份: %s" % BAK_ALL)
    else:
        print("完整备份已存在: %s" % BAK_ALL)
    # ② 被移除记录单独备份（追加保留历史）
    _prev = []
    if os.path.isfile(BAK_DUP):
        try:
            with io.open(BAK_DUP, encoding="utf-8") as _f:
                _prev = json.loads(_f.read())
            if not isinstance(_prev, list):
                _prev = []
        except (OSError, ValueError):
            _prev = []
    with io.open(BAK_DUP, "w", encoding="utf-8") as _f:
        _f.write(json.dumps(_prev + _removed, ensure_ascii=False, indent=2))
    print("重复记录备份: %s（本次 %d 条，累计 %d 条）"
          % (BAK_DUP, len(_removed), len(_prev) + len(_removed)))
    # ③ 写回去重后历史
    with io.open(_args.history, "w", encoding="utf-8") as _f:
        _f.write(json.dumps(_kept, ensure_ascii=False, indent=2))
    # ④ 报告
    _rep = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "history": _args.history,
        "dedup": _st,
        "note": "保留规则：优先 applied=True；同真同假取时间最新者",
        "detail_fixed": _fixed,
        "detail_fixed_ids": _ids,
        "backup_full": BAK_ALL,
        "backup_removed": BAK_DUP,
    }
    with io.open(REPORT, "w", encoding="utf-8") as _f:
        _f.write(json.dumps(_rep, ensure_ascii=False, indent=2))
    print("报告: %s" % REPORT)
    print("去重后条数: %d（原 %d）" % (len(_kept), _st["before"]))

    if _args.json:
        print()
        print(json.dumps(_rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
