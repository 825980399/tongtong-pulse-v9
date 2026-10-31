"""第410批刀1 工具：生成 / 查看七类事件周期档案。

用法：
    python tools/m410_build_profile.py --build      # 生成并落盘
    python tools/m410_build_profile.py --stats      # 只看现有档案
    python tools/m410_build_profile.py --dry-run    # 只计算不写盘（默认）
    python tools/m410_build_profile.py --root tmp/x # 隔离根（测试用）
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.world_model import periodic_profile as pp  # noqa: E402


def _f(v, nd: int = 4) -> str:
    return "--" if v is None else ("%.*f" % (nd, float(v)))


def cmd_stats(argv: list[str]) -> int:
    if argv and argv[0]:
        pp.set_data_dir(argv[0])
    _s = pp.summarize()
    print("=" * 78)
    print("[刀1] 周期档案 %s" % ("存在" if _s["exists"] else "不存在"))
    print("  version=%s generated_at=%s total=%d ok=%d complete=%s"
          % (_s["version"] or "-", _s["generated_at"] or "-",
             _s["total"], _s["ok"], _s["complete"]))
    print("  by_predict_type=%s" % (_s["by_predict_type"] or {}))
    if _s["missing"]:
        print("  missing=%s" % _s["missing"])
    if _s["insufficient"]:
        print("  insufficient=%s" % _s["insufficient"])
    _p = pp.load_profile()
    _ev = (_p.get("events") or {}) if _p else {}
    if _ev:
        print()
        print("  %-30s %-9s %12s %12s %6s %12s"
              % ("event", "type", "median_sec", "p90_sec", "n", "tol_sec"))
        for _et in pp.TARGET_EVENTS:
            _e = _ev.get(_et) or {}
            print("  %-30s %-9s %12s %12s %6s %12s"
                  % (_et, _e.get("predict_type", "-"),
                     _f(_e.get("median_interval_sec")), _f(_e.get("p90_interval_sec")),
                     _e.get("sample_count", 0), _f(_e.get("tolerance_sec"))))
    print("=" * 78)
    return 0


def cmd_build(argv: list[str], write: bool) -> int:
    _root = argv[0] if argv and argv[0] else None
    if _root:
        pp.set_data_dir(_root)
    if not write:
        _r = pp.build_profile()
    else:
        _r = pp.build()
    print("[刀1] %s  version=%s complete=%s written=%s skipped=%s"
          % ("已落盘" if _r.get("written") else "只计算",
             _r["version"], _r["complete"], _r.get("written"), _r.get("skipped") or "-"))
    print("  source=%s (saved_at=%s)" % (_r["source"], _r.get("source_saved_at")))
    if _r.get("path"):
        print("  path=%s" % _r["path"])
    print()
    print("  %-30s %-9s %12s %12s %6s %12s"
          % ("event", "type", "median_sec", "p90_sec", "n", "tol_sec"))
    for _et in pp.TARGET_EVENTS:
        _e = _r["events"][_et]
        print("  %-30s %-9s %12s %12s %6s %12s"
              % (_et, _e["predict_type"],
                 _f(_e["median_interval_sec"]), _f(_e["p90_interval_sec"]),
                 _e["sample_count"], _f(_e["tolerance_sec"])))
    print()
    # 验收判据（任务书刀1）
    _hb = _r["events"][pp.EV_HEART_BEAT]["median_interval_sec"]
    _hb_ok = _hb is not None and 11.0 <= float(_hb) <= 16.0
    print("[验收] 七类齐全      : %s (%d/%d)"
          % ("PASS" if _r["complete"] else "FAIL",
             len(pp.TARGET_EVENTS) - len(_r["missing"]), len(pp.TARGET_EVENTS)))
    print("[验收] %s≈13s: %s (median=%s)"
          % (pp.EV_HEART_BEAT, "PASS" if _hb_ok else "FAIL", _f(_hb)))
    _dis = _r["events"][pp.EV_DIGEST_KNOWLEDGE]
    print("[信息] %s median=%s p90=%s（burst 型方差大，符合预期）"
          % (pp.EV_DIGEST_KNOWLEDGE, _f(_dis["median_interval_sec"]),
             _f(_dis["p90_interval_sec"])))
    if not write:
        print("\n（dry-run：未写盘。加 --build 落盘）")
    return 0


def main(argv: list[str] | None = None) -> int:
    _argv = list(sys.argv[1:] if argv is None else argv)
    _ap = argparse.ArgumentParser(description="第410批刀1 周期档案工具")
    _g = _ap.add_mutually_exclusive_group()
    _g.add_argument("--build", action="store_true", help="生成并落盘")
    _g.add_argument("--stats", action="store_true", help="只看现有档案")
    _g.add_argument("--dry-run", action="store_true", help="只计算不写盘（默认）")
    _ap.add_argument("--root", default=None, help="隔离的数据根（测试用）")
    _a = _ap.parse_args(_argv)
    _rest = [_a.root] if _a.root else []
    if _a.stats:
        return cmd_stats(_rest)
    if _a.build:
        return cmd_build(_rest, write=True)
    return cmd_build(_rest, write=False)


if __name__ == "__main__":
    sys.exit(main())
