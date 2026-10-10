#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""补丁语义拆分回填工具（主线第47批 T1，P0-2）

把 ``no_regression`` / ``problem_fixed`` / ``verification_granularity`` /
``effectiveness`` 四个字段回填到既有补丁历史，并输出**真实修复率 vs 旧声称率**的对比报告。

用法::

    python tools/backfill_patch_verification_split.py --dry-run              # 只统计
    python tools/backfill_patch_verification_split.py                        # 执行（自动备份）
    python tools/backfill_patch_verification_split.py --report <out.json>    # 另存报告
    python tools/backfill_patch_verification_split.py --no-backup            # 不备份（不推荐）

★安全约束：
  * 默认**先备份**再写盘，备份带 sha256 校验
  * 与第44批 ``nucleus/data/write_guard.py`` 协同：测试环境下拒绝写生产数据
  * ``--dry-run`` 为默认安全态，绝不写盘
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import time
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.data.path_utils import safe_relpath as _safe_relpath  # noqa: E402
from nucleus.evolution.patch_verification_split import (  # noqa: E402
    F_EFFECTIVENESS,
    F_GRANULARITY,
    F_NO_REGRESSION,
    F_PROBLEM_FIXED,
    backfill,
    display_label,
    real_fix_rate,
)

DEFAULT_TARGET = os.path.join("data", "patches", "patch_history.json")
ARCHIVE_ROOT = os.path.join("data", "_archive")


def _sha256(path: str) -> str:
    _h = hashlib.sha256()
    with open(path, "rb") as _f:
        for _c in iter(lambda: _f.read(65536), b""):
            _h.update(_c)
    return _h.hexdigest()


def _load(path: str) -> tuple[Any, list[dict]]:
    """返回 (文档对象, 补丁列表)。"""
    if not os.path.isfile(path):
        raise SystemExit("[回填] 目标不存在: {}".format(path))
    _doc = json.load(io.open(path, encoding="utf-8"))
    if isinstance(_doc, list):
        return _doc, _doc
    if isinstance(_doc, dict):
        for _k in ("patches", "records", "items", "data"):
            if isinstance(_doc.get(_k), list):
                return _doc, _doc[_k]
    raise SystemExit("[回填] 无法识别结构: {}".format(path))


def _backup(path: str, archive_root: str) -> dict:
    """整文件归档备份（带 sha256）。"""
    _ts = time.strftime("%Y%m%d_%H%M%S")
    _d = os.path.join(archive_root, "patch_split", _ts)
    os.makedirs(_d, exist_ok=True)
    _dst = os.path.join(_d, os.path.basename(path))
    shutil.copy2(path, _dst)
    _h = _sha256(path)
    with io.open(os.path.join(_d, "MANIFEST.json"), "w", encoding="utf-8") as _f:
        _f.write(json.dumps({
            # ★第48批 T2（P2-320）：--target / archive_root 可传任意盘路径
            "source": _safe_relpath(path, ROOT),
            "backup": _safe_relpath(_dst, ROOT),
            "sha256": _h, "created_at": time.time(),
            "created_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False, indent=2))
    return {"dir": _safe_relpath(_d, ROOT),
            "backup": _safe_relpath(_dst, ROOT), "sha256": _h}


def build_report(path: str, apply: bool = False,
                 do_backup: bool = True,
                 archive_root: str = ARCHIVE_ROOT) -> dict:
    _doc, _ps = _load(path)
    _before_sha = _sha256(path)
    _rep = backfill(_ps, apply=False)          # 先 dry-run 统计（不改数据）
    _rep["dry_run"] = True

    _samples = []
    for _p in _ps[:5]:
        if not isinstance(_p, dict):
            continue
        _samples.append({
            "id": str(_p.get("id"))[:24],
            "file": str(_p.get("file", ""))[:44],
            "baseline_errors": _p.get("baseline_errors"),
            "post_apply_errors": _p.get("post_apply_errors"),
            "verification.passed": (_p.get("verification") or {}).get("passed")
            if isinstance(_p.get("verification"), dict) else None,
            "no_regression": _p.get(F_NO_REGRESSION),
            "problem_fixed": _p.get(F_PROBLEM_FIXED),
            "granularity": _p.get(F_GRANULARITY),
            "effectiveness": _p.get(F_EFFECTIVENESS),
            "label": display_label(_p),
        })

    if apply:
        _bk = _backup(path, archive_root) if do_backup else None
        # ★测试环境保护（与第44批 write_guard 协同）
        try:
            from nucleus.data import write_guard as _wg
            if not _wg.is_production_data_path(os.path.abspath(path)):
                return {**_rep, "applied": False,
                        "error": "目标不在生产 data/ 路径下，拒绝写入"}
        except Exception as _e:
            # ★第51批 T4（P2-355）：原为裸 ``pass``（静默吞异常）→ 显式留痕。
            #   本守卫限制"只允许写生产 data/ 路径"；故障时按 fail-open 继续，
            #   但**必须可观测** —— 否则"守卫是否生效"无从判断（第50批 P2-339 教训）。
            import sys as _sys
            _sys.stderr.write(
                "[backfill] 写盘守卫不可用（按 fail-open 继续）: {}: {}\n".format(type(_e).__name__, _e))
        _rep2 = backfill(_ps, apply=True)
        with io.open(path, "w", encoding="utf-8") as _f:
            _f.write(json.dumps(_doc, ensure_ascii=False, indent=2))
        _rep.update(_rep2)
        _rep.update({"applied": True, "backup": _bk,
                     "sha256_before": _before_sha,
                     "sha256_after": _sha256(path),
                     "dry_run": False})

    _rep["samples"] = _samples
    _rep["target"] = _safe_relpath(path, ROOT)
    _rep["real_fix_rate_fn"] = real_fix_rate(_ps)
    return _rep


def main() -> int:
    _ap = argparse.ArgumentParser(description="补丁语义拆分回填（P0-2）")
    _ap.add_argument("--target", default=DEFAULT_TARGET)
    _ap.add_argument("--apply", action="store_true", help="执行写入（默认 dry-run）")
    _ap.add_argument("--no-backup", action="store_true")
    _ap.add_argument("--report", default="", help="JSON 报告输出路径")
    _ns = _ap.parse_args()

    _r = build_report(_ns.target, apply=_ns.apply,
                      do_backup=not _ns.no_backup)

    print("=" * 70)
    print("补丁语义拆分回填" + ("（DRY-RUN）" if not _ns.apply else "（已执行）"))
    print("=" * 70)
    print("目标: %s   总数 %d" % (_r["target"], _r["total"]))
    print()
    print("[1] no_regression（没弄坏）")
    print("    true=%d  false=%d  none=%d"
          % (_r["no_regression"]["true"], _r["no_regression"]["false"],
             _r["no_regression"]["none"]))
    print()
    print("[2] problem_fixed（修好了）")
    print("    true=%d  false=%d  none=%d"
          % (_r["problem_fixed"]["true"], _r["problem_fixed"]["false"],
             _r["problem_fixed"]["none"]))
    print()
    print("[3] 验证粒度分布:", _r["granularity_dist"])
    print()
    print("[4] ★修复率对比")
    print("    旧声称修复率 (verified)   : %.2f%%" % (_r["old_claimed_rate"] * 100))
    print("    真实修复率 (problem_fixed): %.2f%%" % ((_r.get("real_fix_rate") or 0.0) * 100))
    print("    可判定率                  : %.2f%%" % (_r["verifiable_rate"] * 100))
    print("    effectiveness 均值: %s（%d 条可算）"
          % (_r["effectiveness_mean"], _r["effectiveness_count"]))
    print()
    print("[5] 样本")
    for _s in _r["samples"]:
        print("    %-24s base=%-5s after=%-5s → %s"
              % (_s["id"], _s["baseline_errors"], _s["post_apply_errors"],
                 _s["label"]))
    if _ns.apply:
        print()
        print("[6] 已写入；备份:", _r.get("backup", {}).get("dir", "(未备份)"))
        print("    sha256 {} → {}".format(_r.get("sha256_before", "")[:16], _r.get("sha256_after", "")[:16]))
    else:
        print()
        print("[6] dry-run：未写盘。加 --apply 执行。")

    if _ns.report:
        _d = os.path.dirname(os.path.abspath(_ns.report))
        if _d and not os.path.isdir(_d):
            os.makedirs(_d, exist_ok=True)
        with io.open(_ns.report, "w", encoding="utf-8") as _f:
            _f.write(json.dumps(_r, ensure_ascii=False, indent=2))
        print()
        print("报告已写出 →", _ns.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# _m51_t4_a