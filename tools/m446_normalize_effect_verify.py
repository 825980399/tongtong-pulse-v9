# -*- coding: utf-8 -*-
"""446 刀2 迁移工具：把历史效果验证台账按**严格口径**重判。

背景（任务书 §刀2 第三条）
    `data/evolution/effect_verify.json` 实测 41 条 verdict：
        passed=True  & problem_gone=True   20 条
        passed=True  & problem_gone=None  **20 条**   ← 「没测出问题是否消失」被记成「验证有效」
        passed=None  & problem_gone=None    1 条
    新口径（`ENABLE_M446_EFFECT_STRICT`）在 `verify()` 里已拦住新增；本工具负责把
    **存量 20 条**降级为 `passed=None`，并把原值留在 `passed_raw` +
    `legacy_self_certified=True`（审计能看出它曾被判 True、被谁改的）。

纪律
    * 默认 `--dry-run`：只读、只打统计，一个字节都不写。
    * `--apply` 才写；写前备份到 `.bak_batch446/effect_verify.json.bak_<时间戳>`，写完读回校验。
    * 走 `EvolutionEffectVerifier.save()`（原子 tmp+replace），不自己 open('w') 写台账 ——
      本批刚判过非原子写，不能自己犯。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc
from nucleus.evolution.EvolutionEffectVerifier import (
    EvolutionEffectVerifier,
    normalize_strict_passed,
)

BACKUP_DIR = os.path.join(_ROOT, ".bak_batch446")


def _stats(verdicts: dict) -> dict:
    _c = {"passed_true_pg_true": 0, "passed_true_pg_none": 0,
          "passed_none": 0, "passed_false": 0, "other": 0}
    for _v in verdicts.values():
        if not isinstance(_v, dict):
            _c["other"] += 1
            continue
        _p = _v.get("passed")
        _pg = ((_v.get("dims") or {}).get("problem_gone"))
        if _p is True and _pg is True:
            _c["passed_true_pg_true"] += 1
        elif _p is True:
            _c["passed_true_pg_none"] += 1
        elif _p is None:
            _c["passed_none"] += 1
        elif _p is False:
            _c["passed_false"] += 1
        else:
            _c["other"] += 1
    _c["total"] = len(verdicts)
    return _c


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="446 刀2 台账严格口径归一")
    _ap.add_argument("--apply", action="store_true", help="真的写盘（默认只读）")
    _ap.add_argument("--base-dir", default="", help="台账目录（默认 data/evolution）")
    _args = _ap.parse_args(argv)

    _base = _args.base_dir or os.path.join(_ROOT, "data", "evolution")
    _v = EvolutionEffectVerifier(base_dir=_base)
    _verdicts = getattr(_v, "_verdicts", None)
    if not isinstance(_verdicts, dict):
        print("FAIL: _verdicts 不是 dict，拒绝继续")
        return 1

    _before = _stats(_verdicts)
    print("BEFORE:", json.dumps(_before, ensure_ascii=False))

    _res = normalize_strict_passed(_verdicts)
    _after = _stats(_verdicts)
    print("AFTER :", json.dumps(_after, ensure_ascii=False))
    print("RESULT:", json.dumps(_res, ensure_ascii=False))

    if not _args.apply:
        print("DRY-RUN：未写盘（加 --apply 才写）")
        return 0
    if not _res.get("normalized"):
        print("无需归一，未写盘")
        return 0

    _src = _v._save_path
    os.makedirs(BACKUP_DIR, exist_ok=True)
    _bak = os.path.join(BACKUP_DIR,
                        f"effect_verify.json.bak_{time.strftime('%Y%m%d_%H%M%S')}")
    try:
        if os.path.exists(_src):
            shutil.copy2(_src, _bak)
            print("备份:", _bak)
    except OSError as _e:
        silent_exc(_e, where="tools/m446_normalize_effect_verify.py::备份", level="warning")
        print("FAIL: 备份失败，不写盘")
        return 1

    _v.save()
    try:
        with open(_src, "r", encoding="utf-8") as _f:
            _back = json.load(_f)
        _check = _stats(_back.get("verdicts") or {})
        print("READBACK:", json.dumps(_check, ensure_ascii=False))
        if _check["passed_true_pg_none"] != 0:
            print("FAIL: 回读仍有 passed=True/problem_gone=None")
            return 1
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="tools/m446_normalize_effect_verify.py::回读", level="warning")
        print("FAIL: 回读失败")
        return 1
    print(f"OK: 已归一 {_res['normalized']} 条，回读校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
