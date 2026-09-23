# -*- coding: utf-8 -*-
"""补丁质量评估器命令行入口（主线第42批 T2 / P0-250）。

用法::

    python tools/run_patch_quality_eval.py                 # 评生产历史，写默认报告
    python tools/run_patch_quality_eval.py --dry-run       # 只评估不写报告
    python tools/run_patch_quality_eval.py --history X.json --out Y.json
    python tools/run_patch_quality_eval.py --json          # 打印完整报告 JSON

★ 只读：本工具不修改补丁历史、不改变任何进化决策（L1 仅观测）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.evolution.patch_quality_evaluator import (      # noqa: E402
    DEFAULT_HISTORY_PATH,
    DEFAULT_REPORT_PATH,
    evaluate_history,
    format_summary_line,
    load_history,
    save_report,
)


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="补丁质量评估（L1 仅观测）")
    _ap.add_argument("--history", default=None, help="补丁历史 JSON 路径")
    _ap.add_argument("--out", default=None, help="报告输出路径")
    _ap.add_argument("--dry-run", action="store_true", help="只评估，不写报告")
    _ap.add_argument("--json", action="store_true", help="打印完整报告 JSON")
    _ap.add_argument("--top", type=int, default=10, help="打印评分最低的 N 条")
    _args = _ap.parse_args(argv)

    _hist = load_history(_args.history)
    _ev = evaluate_history(_hist)
    _ev["history_path"] = _args.history or DEFAULT_HISTORY_PATH

    print("历史文件: %s" % _ev["history_path"])
    print("记录条数: %d" % _ev["summary"].get("history_records", 0))
    print()
    print(format_summary_line(_ev["summary"]))
    print()
    print("=== 结论 ===")
    for _f in _ev["findings"]:
        print("  - %s" % _f)
    print()
    print("=== 评分最低 %d 条 ===" % _args.top)
    for _x in _ev["patches"][:_args.top]:
        print("  %-13s score=%-6s claimed=%-6s real=%-6s %s.%s"
              % (_x["label"], _x["score"], _x["claimed_effectiveness"],
                 _x["real_effectiveness"],
                 os.path.basename(str(_x["file"])), _x["method"]))

    if _args.json:
        print()
        print(json.dumps(_ev, ensure_ascii=False, indent=2))

    if _args.dry_run:
        print()
        print("（--dry-run：未写报告）")
        return 0

    _out = _args.out or DEFAULT_REPORT_PATH
    _written = save_report(_ev, _args.out)
    print()
    print("报告: %s" % (_written if _written else "未写入（%s）" % _out))
    return 0 if _written or _args.out else 0


if __name__ == "__main__":
    raise SystemExit(main())
