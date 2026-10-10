# -*- coding: utf-8 -*-
"""cleanup_reports.py —— 自认知报告磁盘清理工具（★主线第51批 T5，P2-357）

背景
----
`data/reports/` 的磁盘文件此前无回收机制（第50批实测 142 个，全为测试期产物）。
本工具提供**人工可控**的清理入口（自动回收由 `ReportBus._prune_disk` 负责）。

安全设计（★归档而非删除）
------------------------
* 默认 ``--dry-run``：**只报告不动作**；
* ``--yes`` 执行时，把目标文件 **move 到** ``data/reports/_archive_<label>/``
  （同盘 rename，**不删除任何内容**，可完整回滚）；
* 归档目录名以 ``_`` 开头 → 被 ``ReportBus._prune_disk`` 自动跳过。

用法
----
    python tools/cleanup_reports.py                     # 预览（默认 dry-run）
    python tools/cleanup_reports.py --yes --label m51   # 归档
    python tools/cleanup_reports.py --keep 50           # 每类型保留最新 50 份
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import time

from nucleus.data.path_utils import (
    safe_relpath as _safe_relpath,  # ★第55批 T3（跨盘安全，同盘行为与 os.path.relpath 一致）
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(ROOT, "data", "reports")


def _iter_reports(base: str):
    """产出 ``(type, path, mtime, size)``；跳过以 ``_`` 开头的归档目录。"""
    if not os.path.isdir(base):
        return
    for _t in sorted(os.listdir(base)):
        _tdir = os.path.join(base, _t)
        if not os.path.isdir(_tdir) or _t.startswith("_"):
            continue
        for _fn in sorted(os.listdir(_tdir)):
            if not _fn.endswith(".json"):
                continue
            _fp = os.path.join(_tdir, _fn)
            if os.path.isfile(_fp):
                yield _t, _fp, os.path.getmtime(_fp), os.path.getsize(_fp)


def build_plan(base: str = REPORTS_DIR, keep: int = 0,
               before: float | None = None) -> dict:
    """构造清理预案（**只读**）。

    Args:
        base: 报告根目录。
        keep: 每个类型**保留最新** N 份（0 = 不按数量保留）。
        before: 只处理 mtime **早于**该时刻的文件（None = 全部）。

    Returns:
        ``{"targets": [...], "by_type": {...}, "total": int, "bytes": int}``
    """
    _by_type: dict[str, list] = {}
    for _t, _fp, _mt, _sz in _iter_reports(base):
        _by_type.setdefault(_t, []).append((_mt, _fp, _sz))
    _targets = []
    _stat: dict[str, int] = {}
    for _t, _items in _by_type.items():
        _items.sort()                       # 旧 → 新
        _keepset = set()
        if keep > 0:
            _keepset = {p for _m, p, _s in _items[-keep:]}
        for _mt, _fp, _sz in _items:
            if _fp in _keepset:
                continue
            if before is not None and _mt >= before:
                continue
            _targets.append({"type": _t, "path": _fp, "mtime": _mt, "size": _sz})
            _stat[_t] = _stat.get(_t, 0) + 1
    return {"targets": _targets, "by_type": _stat,
            "total": len(_targets),
            "bytes": sum(x["size"] for x in _targets)}


def apply_plan(plan: dict, base: str = REPORTS_DIR,
               label: str = "test") -> dict:
    """执行归档：把目标文件 **move 到** ``_archive_<label>/``（不删除）。

    ★同盘 rename → 不触发沙箱删除配额，且完整可回滚。
    """
    _dest = os.path.join(base, "_archive_%s" % label)
    _out = {"moved": 0, "errors": [], "dest": _dest}
    for _x in plan.get("targets", []):
        try:
            _rel = _safe_relpath(_x["path"], base)
            _to = os.path.join(_dest, _rel)
            os.makedirs(os.path.dirname(_to), exist_ok=True)
            shutil.move(_x["path"], _to)
            _out["moved"] += 1
        except (OSError, shutil.Error) as _e:
            _out["errors"].append("%s: %s" % (_x["path"], _e))
    return _out


def restore_archive(label: str = "test", base: str = REPORTS_DIR) -> dict:
    """从归档目录**回滚**（把文件移回原位）。"""
    _src = os.path.join(base, "_archive_%s" % label)
    _out = {"restored": 0, "errors": [], "src": _src}
    if not os.path.isdir(_src):
        _out["errors"].append("归档目录不存在: %s" % _src)
        return _out
    for _dp, _dns, _fns in os.walk(_src):
        for _fn in _fns:
            _fp = os.path.join(_dp, _fn)
            _rel = _safe_relpath(_fp, _src)
            _to = os.path.join(base, _rel)
            try:
                os.makedirs(os.path.dirname(_to), exist_ok=True)
                shutil.move(_fp, _to)
                _out["restored"] += 1
            except (OSError, shutil.Error) as _e:
                _out["errors"].append("%s: %s" % (_fp, _e))
    return _out


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(
        description="自认知报告磁盘清理（默认 dry-run；执行时归档不删除）")
    _ap.add_argument("--yes", action="store_true", help="确认执行（默认只预览）")
    _ap.add_argument("--keep", type=int, default=0,
                     help="每个类型保留最新 N 份（0=不按数量保留）")
    _ap.add_argument("--before", default="", help="只处理早于该时刻（YYYY-MM-DD HH:MM）")
    _ap.add_argument("--label", default="test", help="归档目录后缀")
    _ap.add_argument("--restore", action="store_true", help="从归档回滚")
    _ap.add_argument("--report", default="", help="输出 JSON 报告路径")
    _ns = _ap.parse_args(argv)

    if _ns.restore:
        _r = restore_archive(_ns.label)
        print("[回滚] 恢复 %d 份，错误 %d" % (_r["restored"], len(_r["errors"])))
        for _e in _r["errors"][:5]:
            print("   ", _e)
        return 0

    _before = None
    if _ns.before:
        try:
            _before = time.mktime(time.strptime(_ns.before, "%Y-%m-%d %H:%M"))
        except ValueError as _e:
            print("[ERR] --before 格式应为 'YYYY-MM-DD HH:MM': %s" % _e)
            return 2

    _plan = build_plan(REPORTS_DIR, keep=_ns.keep, before=_before)
    print("=== 报告磁盘清理预案（%s）===" % ("执行" if _ns.yes else "预览 dry-run"))
    print("  报告根目录: %s" % REPORTS_DIR)
    print("  待处理文件: %d 份 / %.1f KB" % (_plan["total"], _plan["bytes"] / 1024))
    print("  按类型分布: %s" % _plan["by_type"])
    _disk = sum(1 for _ in _iter_reports(REPORTS_DIR))
    print("  当前磁盘总数: %d 份" % _disk)
    print()

    if not _ns.yes:
        print("（预览模式：未做任何改动。加 --yes 执行归档）")
        if _ns.report:
            io.open(_ns.report, "w", encoding="utf-8").write(
                json.dumps({"mode": "dry_run", "plan": _plan},
                           ensure_ascii=False, indent=2))
        return 0

    _res = apply_plan(_plan, label=_ns.label)
    print("[归档] 移入 %s：%d 份，错误 %d" % (_res["dest"], _res["moved"],
                                              len(_res["errors"])))
    for _e in _res["errors"][:5]:
        print("   ", _e)
    _after = sum(1 for _ in _iter_reports(REPORTS_DIR))
    print("  归档后活动报告数: %d 份" % _after)
    if _ns.report:
        io.open(_ns.report, "w", encoding="utf-8").write(json.dumps(
            {"mode": "apply", "label": _ns.label, "plan": _plan,
             "result": _res, "after_count": _after},
            ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
