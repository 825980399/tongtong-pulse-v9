# -*- coding: utf-8 -*-
"""第300批刀6：62 条「不可验证」补丁逐条归因——验证器缺 vs 补丁无效。

★唯一判据源（复用既有，不自立）：``patch_quality_evaluator.evaluate_history``
  的 ``label``，其 unverifiable 判据见 patch_quality_evaluator.py:315-320——
  ``baseline==0`` 使 ``real_effectiveness`` 为 None（0/0 无意义）→ 不可判定。

归因二分（本工具只做归类，不改判据、不改数据）：
  * **验证器缺** —— ``baseline == 0``：文件级基线错误**未采集到**，无参照物可比，
    效果不可判定 ⇒ **框架侧采集/判据缺陷**，不能据此说补丁无效。
  * **补丁无效** —— ``baseline > 0`` 且 ``effectiveness <= 0``：有基线可参照，
    但修复后错误未下降（``post >= baseline``）⇒ **补丁侧确实没修好**。

用法（纯只读，不落盘、不改任何文件）：
  python tools/classify_unverifiable_300.py [--json]

退出码：0=完成；2=读取/评估失败。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_PATH = os.path.join(ROOT, "data", "patches", "patch_history.json")


def _num(v: Any) -> float:
    try:
        if isinstance(v, bool) or v is None:
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def classify(items: list[dict]) -> dict[str, Any]:
    """对 unverifiable 条目做「验证器缺 vs 补丁无效」二分归因。"""
    _unv = [x for x in items if isinstance(x, dict) and x.get("label") == "unverifiable"]
    _verifier_missing: list[dict] = []
    _patch_invalid: list[dict] = []
    _other: list[dict] = []
    for _p in _unv:
        _b = _num(_p.get("baseline_errors"))
        _post = _num(_p.get("post_apply_errors"))
        _row = {
            "id": _p.get("id"),
            "issue_type": _p.get("issue_type"),
            "risk_level": _p.get("risk_level"),
            "file": _p.get("file"),
            "method": _p.get("method"),
            "baseline_errors": _p.get("baseline_errors"),
            "post_apply_errors": _p.get("post_apply_errors"),
        }
        if _b <= 0:
            # baseline==0（或未采集）→ 0/0 不可判定 ⇒ 验证器缺
            _row["reason"] = "baseline=0（文件级基线未采集）→ 0/0 不可判定"
            _verifier_missing.append(_row)
        elif _post >= _b:
            # 有基线但修复后错误未下降 ⇒ 补丁无效
            _row["reason"] = f"baseline={_b:g} 但 post={_post:g} 未下降 → 补丁无效"
            _patch_invalid.append(_row)
        else:
            _row["reason"] = "其它（有基线且已下降，不应落 unverifiable）"
            _other.append(_row)
    return {
        "total_unverifiable": len(_unv),
        "verifier_missing": _verifier_missing,
        "patch_invalid": _patch_invalid,
        "other": _other,
        "counts": {
            "verifier_missing": len(_verifier_missing),
            "patch_invalid": len(_patch_invalid),
            "other": len(_other),
        },
    }


def main() -> int:
    _ap = argparse.ArgumentParser(description="300批刀6：不可验证补丁归因（只读）")
    _ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    _args = _ap.parse_args()
    try:
        from nucleus.evolution.patch_quality_evaluator import evaluate_history
        _hist = json.load(open(HISTORY_PATH, encoding="utf-8"))
        _res = evaluate_history(_hist)
        _items = [x for x in (_res.get("patches") or []) if isinstance(x, dict)]
    except Exception as e:
        print(f"[归因] 读取/评估失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    _labels = Counter(x.get("label") for x in _items)
    _cls = classify(_items)
    if _args.json:
        print(json.dumps({"labels": dict(_labels), "classification": _cls},
                         ensure_ascii=False, indent=2))
        return 0
    print("=== 300批刀6：不可验证补丁归因（只读）===")
    print(f"全账 label 分布: {dict(_labels)}")
    print(f"unverifiable 总数 = {_cls['total_unverifiable']}")
    print(f"  验证器缺(baseline=0, 0/0不可判定) = {_cls['counts']['verifier_missing']}")
    print(f"  补丁无效(有基线但错误未下降)       = {_cls['counts']['patch_invalid']}")
    print(f"  其它                               = {_cls['counts']['other']}")
    if _cls["patch_invalid"]:
        print("--- 补丁无效明细 ---")
        for _r in _cls["patch_invalid"]:
            print(f"  {_r['id']} {_r['file']}:{_r['method']} — {_r['reason']}")
    if _cls["other"]:
        print("--- 其它明细 ---")
        for _r in _cls["other"]:
            print(f"  {_r['id']} {_r['file']}:{_r['method']} — {_r['reason']}")
    print("★结论口径：unverifiable 是「文件级 baseline=0 无参照物」判据缺陷的下游后果，")
    print("  属验证器侧采集缺失，**不等于补丁无效**。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
