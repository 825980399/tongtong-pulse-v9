# -*- coding: utf-8 -*-
"""169批 C4（T-报告时限-1）：报告停留时限读数 + 逾期清单（强制落盘）。

背景（T0 实测）
--------------
* ``docs/路灯与星轨对话/交付报告/待分析`` 75 件 **全部** mtime 超 2 天；
* ``docs/分析报告`` 92 件中 80 件超 2 天；
* 全仓无任何 dwell / 逾期清单机制 → 时限口径缺失。

本刀只做
--------
1. **停留时限读数**：扫描汇报件目录，算出每件的停留天数，与限时限比对；
2. **逾期清单**：超时限者进清单；
3. **汇报件强制落盘断言**：清单必须真的写盘（可解析、含条目），否则报错。

★不做：任何删除/移动/清理动作（清理归停窗段，见任务书）。

用法::

    python tools/report_dwell_audit.py                 # 默认 2 天，写 tmp/
    python tools/report_dwell_audit.py --limit-days 3 --out <path>
    python tools/report_dwell_audit.py --strict        # 有逾期 -> exit 1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

#: 默认扫描目录（相对项目根）
DEFAULT_DIRS = (
    "docs/路灯与星轨对话/交付报告/待分析",
    "docs/分析报告",
)
#: 默认停留时限（天）
DEFAULT_LIMIT_DAYS = 2.0
#: 默认落盘位置（相对项目根；可用 --out 覆盖）
DEFAULT_OUT = os.path.join("tmp", "report_dwell_overdue.json")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
# ★第169批 C2：统一归一入口（内委托 safe_relpath，跨盘降级绝对路径 + 分隔符归一）
from nucleus._silent_except import silent_exc  # noqa: E402
from nucleus.data.path_utils import normalize_relpath  # noqa: E402


def _m169_dwell_enabled() -> bool:
    """C4 灰度开关（内联默认，不写 config.py）；关闭 → 不扫描、不落盘。"""
    try:
        sys.path.insert(0, _PROJECT_ROOT)
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_REPORT_DWELL_AUDIT", True))
    except Exception as _e:
        silent_exc(_e, where="tools.report_dwell_audit::_m169_dwell_enabled")
        return True


def _limit_days(limit_days: float | None = None) -> float:
    """时限：显式传参 > config.REPORT_DWELL_LIMIT_DAYS > 默认 2 天。"""
    if limit_days is not None:
        return float(limit_days)
    try:
        sys.path.insert(0, _PROJECT_ROOT)
        import config as _cfg
        return float(getattr(_cfg, "REPORT_DWELL_LIMIT_DAYS",
                             DEFAULT_LIMIT_DAYS))
    except Exception as _e:
        silent_exc(_e, where="tools.report_dwell_audit::_limit_days")
        return DEFAULT_LIMIT_DAYS


def scan_dwell(dirs=None, limit_days: float | None = None,
               now: float | None = None, root: str | None = None) -> dict:
    """扫描汇报件目录，产出停留时限读数与逾期清单。

    Args:
        dirs: 相对项目根的目录列表；``None`` 用 :data:`DEFAULT_DIRS`。
        limit_days: 停留时限（天）；``None`` 走 :func:`_limit_days`。
        now: 当前时间戳（注入以便测试）；``None`` 用 ``time.time()``。
        root: 项目根；``None`` 用本文件推导。

    Returns:
        ``{"limit_days", "scanned_dirs", "total", "overdue_count",
           "overdue": [{"path", "age_days", "mtime", "size"}]}``
    """
    _root = root or _PROJECT_ROOT
    _dirs = list(dirs) if dirs is not None else list(DEFAULT_DIRS)
    _lim = _limit_days(limit_days)
    _now = time.time() if now is None else float(now)
    _lim_sec = _lim * 86400.0

    _total = 0
    _overdue: list[dict] = []
    for _d in _dirs:
        _abs = _d if os.path.isabs(_d) else os.path.join(_root, _d)
        if not os.path.isdir(_abs):
            continue
        for _fn in sorted(os.listdir(_abs)):
            _p = os.path.join(_abs, _fn)
            if not os.path.isfile(_p):
                continue
            _total += 1
            try:
                _mt = os.path.getmtime(_p)
                _sz = os.path.getsize(_p)
            except OSError:
                continue
            _age = _now - _mt
            if _age > _lim_sec:
                _overdue.append({
                    "path": normalize_relpath(_p, _root),
                    "age_days": round(_age / 86400.0, 3),
                    "mtime": _mt,
                    "size": _sz,
                })
    _overdue.sort(key=lambda x: -x["age_days"])
    return {
        "limit_days": _lim,
        "scanned_dirs": list(_dirs),
        "total": _total,
        "overdue_count": len(_overdue),
        "overdue": _overdue,
    }


def write_overdue(result: dict, out: str | None = None,
                  root: str | None = None) -> str:
    """★汇报件强制落盘：把逾期清单写盘并**断言真的落盘**。

    Raises:
        RuntimeError: 落盘失败或写出的文件不可解析（强制断言，不静默）。
    """
    _root = root or _PROJECT_ROOT
    _out = out or DEFAULT_OUT
    if not os.path.isabs(_out):
        _out = os.path.join(_root, _out)
    _d = os.path.dirname(_out)
    if _d:
        os.makedirs(_d, exist_ok=True)
    with open(_out, "w", encoding="utf-8") as _f:
        json.dump(result, _f, ensure_ascii=False, indent=2)
    # ★强制落盘断言
    if not os.path.isfile(_out):
        raise RuntimeError("逾期清单未落盘: %s" % _out)
    with open(_out, encoding="utf-8") as _f:
        _back = json.loads(_f.read())
    if _back.get("overdue_count") != result.get("overdue_count"):
        raise RuntimeError("落盘内容与内存结果不一致（逾期清单被截断）")
    return _out


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="报告停留时限审计（只读取数，不删文件）")
    _ap.add_argument("--limit-days", type=float, default=None,
                     help="停留时限（天），默认读 config 或 2")
    _ap.add_argument("--out", default=None, help="逾期清单落盘路径")
    _ap.add_argument("--dirs", nargs="*", default=None,
                     help="扫描目录（相对项目根），默认两处汇报件目录")
    _ap.add_argument("--strict", action="store_true",
                     help="存在逾期项时以 exit 1 结束（供 CI 门禁用）")
    args = _ap.parse_args(argv)

    if not _m169_dwell_enabled():
        print("[report_dwell_audit] ENABLE_REPORT_DWELL_AUDIT=False -> 跳过")
        return 0

    _res = scan_dwell(dirs=args.dirs, limit_days=args.limit_days)
    try:
        _out = write_overdue(_res, args.out)
    except RuntimeError as _e:
        print("[report_dwell_audit] 落盘断言失败: %s" % _e)
        return 2

    print("[report_dwell_audit] 时限=%.2f天 扫描=%d 逾期=%d -> %s"
          % (_res["limit_days"], _res["total"], _res["overdue_count"], _out))
    for _it in _res["overdue"][:10]:
        print("  逾期 %.1f天  %s" % (_it["age_days"], _it["path"]))
    if _res["overdue_count"] > 10:
        print("  ...（其余 %d 件见清单）" % (_res["overdue_count"] - 10))
    if args.strict and _res["overdue_count"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
