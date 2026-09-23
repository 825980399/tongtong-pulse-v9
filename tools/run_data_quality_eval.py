# -*- coding: utf-8 -*-
"""LLM 调用留存数据质量评估命令行入口（主线第43批 T1 / P1-255）。

用法::

    python tools/run_data_quality_eval.py                # 评当天留存，写默认报告
    python tools/run_data_quality_eval.py --day 20260913
    python tools/run_data_quality_eval.py --dry-run      # 只评估不写报告
    python tools/run_data_quality_eval.py --all          # 不剔除测试污染（对比用）
    python tools/run_data_quality_eval.py --json         # 打印完整报告

★ 只读：不修改任何留存数据、不改变留存逻辑（L1 仅观测）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.llm.data_quality_evaluator import (      # noqa: E402
    DEFAULT_REPORT_PATH,
    SCORE_DIMENSIONS,
    channel_whitelist,
    evaluate_day,
    format_summary_line,
    save_report,
)


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="LLM 留存数据质量评估（L1 仅观测）")
    _ap.add_argument("--day", default=None, help="日期 YYYYMMDD（默认今天）")
    _ap.add_argument("--dir", default=None, help="留存目录（默认 config.LLM_TRACE_DIR）")
    _ap.add_argument("--out", default=None, help="报告输出路径")
    _ap.add_argument("--dry-run", action="store_true", help="只评估，不写报告")
    _ap.add_argument("--all", action="store_true", help="不剔除测试污染（对比口径）")
    _ap.add_argument("--json", action="store_true", help="打印完整报告 JSON")
    _args = _ap.parse_args(argv)

    print("渠道白名单（config 推导）:", sorted(channel_whitelist()) or "(空)")
    _rep = evaluate_day(_args.day, _args.dir, drop_suspect=not _args.all)
    print()
    print("源文件: %s（存在=%s）" % (_rep.get("source_file"), _rep.get("file_exists")))
    print(format_summary_line(_rep))
    print()
    print("=== 五维度 ===")
    for _k in SCORE_DIMENSIONS:
        print("  %-16s %s / %s" % (_k, _rep["dimensions"].get(_k), "—"))
    print()
    print("=== 数据纯度 ===")
    _p = _rep.get("purity", {})
    print("  总记录=%s 生产=%s 疑似测试=%s 纯度=%.1f%%"
          % (_p.get("total"), _p.get("production"), _p.get("suspect"),
             (_p.get("purity") or 0) * 100.0))
    for _s in _p.get("suspect_samples", [])[:5]:
        print("    疑似: ch=%-14s model=%-16s prompt=%r"
              % (_s.get("channel"), _s.get("model"), _s.get("prompt")))
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
    _written = save_report(_rep, _args.out)
    print()
    print("报告: %s" % (_written or "未写入（%s）" % (_args.out or DEFAULT_REPORT_PATH)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
