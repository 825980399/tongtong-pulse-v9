# -*- coding: utf-8 -*-
"""模型自更新器命令行入口（主线第43批 T3 / P1-256）。

用法::

    python tools/run_model_self_update.py --check        # 只检查是否有更新（默认）
    python tools/run_model_self_update.py --incremental --quality 86.5
    python tools/run_model_self_update.py --retrain --quality 87.0
    python tools/run_model_self_update.py --rollback
    python tools/run_model_self_update.py --stats --json

★ 仅框架：不执行任何实际模型训练（L1 观测）。
★ 铁律：样本先过 training_guard（内在模型产出永不入训练集）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.llm.model_self_updater import (  # noqa: E402
    DEFAULT_BASE_DIR,
    ModelSelfUpdater,
    training_guard,
    updater_enabled,
)


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="模型自更新器（仅框架）")
    _ap.add_argument("--check", action="store_true", help="检查是否有更新（默认）")
    _ap.add_argument("--incremental", action="store_true", help="执行一次增量更新（登记版本）")
    _ap.add_argument("--retrain", action="store_true", help="产出全量重训**计划**（不训练）")
    _ap.add_argument("--rollback", action="store_true", help="回滚到上一版本")
    _ap.add_argument("--quality", type=float, default=None, help="候选版本质量评分")
    _ap.add_argument("--samples", default=None, help="样本 JSON 文件（先过 training_guard）")
    _ap.add_argument("--stats", action="store_true", help="打印状态")
    _ap.add_argument("--json", action="store_true", help="打印完整 JSON")
    _args = _ap.parse_args(argv)

    _u = ModelSelfUpdater()
    print("启用=%s  版本目录=%s  可写=%s" % (
        updater_enabled(), _u.base_dir(), _u._writable()))
    print("（默认目录: %s）" % DEFAULT_BASE_DIR)

    _samples = None
    if _args.samples and os.path.isfile(_args.samples):
        with open(_args.samples, encoding="utf-8") as _f:
            _samples = json.load(_f)
        _kept = training_guard(_samples)
        print("★training_guard: 输入 %d → 保留 %d（剔除 %d 条内在模型产出）"
              % (len(_samples), len(_kept), len(_samples) - len(_kept)))
        _samples = _kept

    _out = None
    if _args.rollback:
        _out = _u.rollback()
    elif _args.retrain:
        _out = _u.full_retrain(quality_score=_args.quality, samples=_samples)
    elif _args.incremental:
        _out = _u.incremental_update(quality_score=_args.quality, samples=_samples)
    else:
        _out = _u.check_for_updates()

    print()
    print("=== 结果 ===")
    for _k, _v in (_out or {}).items():
        if _k == "plan":
            print("  plan:")
            for _p in _v:
                print("     %s" % _p)
        else:
            print("  %-24s %s" % (_k, _v))

    if _args.stats or _args.json:
        print()
        print("=== stats ===")
        print(json.dumps(_u.stats(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
