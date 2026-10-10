#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""经验库清洗工具（★写操作，独立于只读分析器）—— 主线第50批 T2（P0-3）

为什么独立成工具
----------------
``tools/serp_pollution_analyzer.py`` 的 docstring 声明**硬约束**
「只读，绝不写生产数据；即便误传 ``--apply`` 也不提供写能力」。
本批**不破坏**该约束 —— 清洗能力放在本工具，写与读**物理分离**。

策略（任务书 §T2 建议：标记，不删除）
------------------------------------
给污染记录打标：``is_cleaned=False`` / ``pollution_risk="high"`` /
``cleanup_reason`` / ``cleanup_batch`` / ``cleanup_at``。
★**一条不删**；``polluted`` 字段原样保留 → 可 ``rollback`` 精确回退。

L3 检索闸门由 ``nucleus/data/experience_cleanup.py`` 提供，
``ExperiencePool`` 的 4 个查询方法已接入（开关
``ENABLE_EXPERIENCE_CLEANUP_FILTER``）。

用法::

    python tools/experience_cleanup.py plan                 # 只读：预告将标记什么
    python tools/experience_cleanup.py apply --yes          # 备份 + 标记 + 验证
    python tools/experience_cleanup.py verify               # 只读：清洗后核验
    python tools/experience_cleanup.py rollback --yes       # 从备份整体回滚
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.data import experience_cleanup as _ec  # noqa: E402

DEFAULT_POOL = os.path.join(ROOT, "data", "experience", "experience_pool.json")
DEFAULT_BACKUP = DEFAULT_POOL + ".backup_20260914_pre_cleanup"


def _load(path: str):
    return json.load(io.open(path, encoding="utf-8"))


def _recs(doc):
    if isinstance(doc, dict) and "experiences" in doc:
        return doc["experiences"]
    return doc if isinstance(doc, list) else []


# ---------------------------------------------------------------- plan / verify
def cmd_plan(ns) -> int:
    doc = _load(ns.pool)
    recs = _recs(doc)
    s = _ec.stats(recs)
    r = _ec.mark_polluted(recs, batch_no=ns.batch, dry_run=True)
    print("=" * 70)
    print("清洗预案（只读，不修改任何数据）")
    print("=" * 70)
    print("  经验库: %s" % ns.pool)
    print("  总记录 %d  污染 %d (%.1f%%)  干净 %d"
          % (s["total"], s["polluted"], s["polluted_rate"] * 100,
             s["total"] - s["polluted"]))
    print("  待标记: %d 条（已标记跳过 %d 条）" % (r["marked"], r["already_marked"]))
    print("  分类:", r["by_class"])
    print("  ★预计清洗后可检索: %d 条 (%.1f%%)"
          % (s["total"] - s["polluted"], (1 - s["polluted_rate"]) * 100))
    print("  ★策略: 标记不删除；备份路径 %s" % ns.backup)
    return 0


def cmd_verify(ns) -> int:
    doc = _load(ns.pool)
    recs = _recs(doc)
    s = _ec.stats(recs)
    print("=" * 70)
    print("清洗后核验（只读）")
    print("=" * 70)
    for k, v in s.items():
        print("  %-22s %s" % (k, v))
    _clean = [e for e in recs if isinstance(e, dict) and not e.get("polluted")]
    _clean_ok = [e for e in _clean if _ec.is_retrievable(e)]
    print("  干净记录: %d  其中仍可检索: %d" % (len(_clean), len(_clean_ok)))
    print("  ★干净记录可检索率: %.2f%%"
          % (100.0 * len(_clean_ok) / max(1, len(_clean))))
    print("  ★可检索总数: %d / %d (%.1f%%)"
          % (s["retrievable"], s["total"], s["retrievable_rate"] * 100))
    if ns.report:
        _d = os.path.dirname(os.path.abspath(ns.report))
        if _d:
            os.makedirs(_d, exist_ok=True)
        io.open(ns.report, "w", encoding="utf-8").write(
            json.dumps({"cleanup": s, "clean_records": len(_clean),
                        "clean_retrievable": len(_clean_ok)}, ensure_ascii=False, indent=2))
        print("  报告已写出 → %s" % ns.report)
    return 0


# ---------------------------------------------------------------- apply
def cmd_apply(ns) -> int:
    if not ns.yes:
        print("★这是写操作。确认请加 --yes")
        return 2
    print("=" * 70)
    print("执行清洗（标记策略；含备份 + sha256 校验）")
    print("=" * 70)
    _t0 = time.time()
    _r = _ec.cleanup_file(ns.pool, batch_no=ns.batch, backup=ns.backup,
                          dry_run=False)
    _b, _a = _r["stats_before"], _r["stats_after"]
    print("  备份: %s" % _r.get("backup"))
    print("  sha256 前 : %s" % str(_r.get("sha256_before"))[:32])
    print("  sha256 备份: %s  一致=%s" % (str(_r.get("sha256_backup"))[:32],
                                          _r.get("backup_matches_source")))
    print("  sha256 后 : %s" % str(_r.get("sha256_after"))[:32])
    print()
    print("  清洗前: 总 %d  污染 %d (%.1f%%)  可检索 %d"
          % (_b["total"], _b["polluted"], _b["polluted_rate"] * 100, _b["retrievable"]))
    print("  清洗后: 总 %d  污染 %d (%.1f%%)  可检索 %d  ★一条未删"
          % (_a["total"], _a["polluted"], _a["polluted_rate"] * 100, _a["retrievable"]))
    print("  标记 %d 条  分类: %s" % (_r["mark"]["marked"], _r["mark"]["by_class"]))
    print("  耗时 %.2fs" % (time.time() - _t0))
    print()
    print("  回滚方式: python tools/experience_cleanup.py rollback --yes")
    return 0


# ---------------------------------------------------------------- rollback
def cmd_rollback(ns) -> int:
    if not ns.yes:
        print("★这是写操作。确认请加 --yes")
        return 2
    if not os.path.isfile(ns.backup):
        print("★备份不存在，无法回滚: %s" % ns.backup)
        return 3
    _r = _ec.rollback_file(ns.pool, ns.backup)
    print("  已回滚: %s" % _r)
    return 0


def main(argv: list[str] | None = None) -> int:
    _ap = argparse.ArgumentParser(description="经验库清洗（标记策略，可回滚）")
    _sub = _ap.add_subparsers(dest="cmd", required=True)
    for _name, _help in (("plan", "只读：打印清洗预案"),
                         ("verify", "只读：清洗后核验"),
                         ("apply", "写：备份 + 标记 + 验证"),
                         ("rollback", "写：从备份整体回滚")):
        _p = _sub.add_parser(_name, help=_help)
        _p.add_argument("--pool", default=DEFAULT_POOL)
        _p.add_argument("--backup", default=DEFAULT_BACKUP)
        _p.add_argument("--batch", type=int, default=50)
        _p.add_argument("--report", default="")
        _p.add_argument("--yes", action="store_true")
    _ns = _ap.parse_args(argv)
    return {"plan": cmd_plan, "verify": cmd_verify,
            "apply": cmd_apply, "rollback": cmd_rollback}[_ns.cmd](_ns)


if __name__ == "__main__":
    raise SystemExit(main())
