# -*- coding: utf-8 -*-
"""LLM 调用模式分析命令行入口（主线第43批 T2 / P0-250）。

用法::

    python tools/run_call_pattern_analysis.py            # 分析当天留存并写报告
    python tools/run_call_pattern_analysis.py --all      # 含测试污染（对比口径）
    python tools/run_call_pattern_analysis.py --dry-run  # 只分析不写报告
    python tools/run_call_pattern_analysis.py --json

★ 只读：不修改任何留存数据、不改变任何调用逻辑（L1 仅观测）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.llm.call_pattern_analyzer import (      # noqa: E402
    DEFAULT_REPORT_PATH, analyze_day, format_summary_line, save_report,
)


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="LLM 调用模式分析（L1 仅观测）")
    _ap.add_argument("--day", default=None, help="日期 YYYYMMDD（默认今天）")
    _ap.add_argument("--dir", default=None, help="留存目录")
    _ap.add_argument("--out", default=None, help="报告输出路径")
    _ap.add_argument("--all", action="store_true", help="包含测试污染记录")
    _ap.add_argument("--dry-run", action="store_true", help="不写报告")
    _ap.add_argument("--json", action="store_true", help="打印完整 JSON")
    _args = _ap.parse_args(argv)

    _rep = analyze_day(_args.day, _args.dir, include_suspect=_args.all)
    print("源文件: %s（存在=%s）" % (_rep.get("source_file"), _rep.get("file_exists")))
    print(format_summary_line(_rep))
    print()
    print("=== 分布 ===")
    for _k, _v in (_rep.get("distribution") or {}).items():
        print("  %-10s %s" % (_k, _v))
    print("  覆盖率    %s" % _rep.get("coverage"))
    print()
    print("=== 可优化模式（%d 类）===" % len(_rep.get("patterns", [])))
    for _p in _rep.get("patterns", []):
        print("  [%s] %s" % (_p.get("name"), _p.get("label")))
        for _k in ("count", "redundant", "no_reason", "coverage"):
            if _k in _p:
                print("      %-10s %s" % (_k, _p[_k]))
        for _t in (_p.get("top") or [])[:3]:
            print("      top: %s" % _t)
    print()
    print("=== 建议（按收益）===")
    for _i, _r in enumerate(_rep.get("recommendations", []), 1):
        print("  %d. %s" % (_i, _r.get("action")))
        print("     收益: %s" % _r.get("expected_gain"))
        print("     风险: %s" % _r.get("risk"))
    print()
    print("=== 结论 ===")
    for _f in _rep.get("findings", []):
        print("  - %s" % _f)

    if _args.json:
        print()
        print(json.dumps(_rep, ensure_ascii=False, indent=2))
    if _args.dry_run:
        print()
        print("（--dry-run：未写报告）")
        return 0
    _w = save_report(_rep, _args.out)
    print()
    print("报告: %s" % (_w or "未写入（%s）" % (_args.out or DEFAULT_REPORT_PATH)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
