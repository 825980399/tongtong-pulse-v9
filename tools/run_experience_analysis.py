# -*- coding: utf-8 -*-
"""经验库分析命令行入口（主线第42批 T3 / P1-265）。

用法::

    python tools/run_experience_analysis.py                # 污染分析（默认）
    python tools/run_experience_analysis.py --observe      # 语义检索 vs 现有检索器对比
    python tools/run_experience_analysis.py --out X.json   # 指定报告路径
    python tools/run_experience_analysis.py --json         # 打印完整报告

★ 只读：不修改经验库、不替换任何现有检索逻辑（L1 仅观测）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.mnemosyne.experience_retriever import (  # noqa: E402
    DEFAULT_POLLUTION_REPORT_PATH,
    ExperienceRetriever,
    analyze_pollution,
    save_pollution_report,
)


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="经验库污染分析与语义检索观测（L1）")
    _ap.add_argument("--observe", action="store_true", help="做语义检索对比观测")
    _ap.add_argument("--sample", type=int, default=3, help="观测样本数")
    _ap.add_argument("--out", default=None, help="污染报告输出路径")
    _ap.add_argument("--json", action="store_true", help="打印完整 JSON")
    _args = _ap.parse_args(argv)

    _rep = analyze_pollution()
    print("=== 经验库污染分析 ===")
    for _k in ("status", "total", "polluted", "clean", "pollution_rate",
               "text_coverage", "generated_at"):
        if _k in _rep:
            print("  %-22s %s" % (_k, _rep[_k]))
    print("  by_reason             {}".format(_rep.get("by_reason")))
    print("  by_quality_flag       {}".format(_rep.get("by_quality_flag")))
    print("  is_summarized         {}".format(_rep.get("is_summarized")))
    for _f in _rep.get("findings", []):
        print("  - {}".format(_f))

    _written = save_pollution_report(_rep, _args.out)
    print()
    print("污染报告: %s" % (_written or "未写入"))

    if _args.observe:
        print()
        print("=== 语义检索 vs 现有检索器（L1 观测）===")
        _rtr = ExperienceRetriever()
        if not _rtr.available():
            print("  编码器不可用（未加载完成）→ 跳过")
        else:
            _s = _rtr.observe_daily(sample=_args.sample)
            for _k, _v in _s.items():
                print("  %-24s %s" % (_k, _v))

    if _args.json:
        print()
        print(json.dumps(_rep, ensure_ascii=False, indent=2))

    print()
    print("默认报告路径: {}".format(DEFAULT_POLLUTION_REPORT_PATH))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
