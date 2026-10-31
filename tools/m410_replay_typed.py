# -*- coding: utf-8 -*-
"""第410批刀2 回放工具：同一条真实事件流，旧 60s vs 新分类型容差 A/B 对账。

数据源：data/stream_miner/patterns.json 的 periodicity 段（每类最近 50 个时间戳）。
做法：
    1) 把 78 个事件类型的时间戳合并成一条按时间排序的流（真实发生顺序）；
    2) 同一顺序喂给两个 StreamMiner：一个关掉刀2 开关（旧 60s），一个打开；
    3) 每 200 条触发一次 discover_patterns() + consume()（与 InfoField 生产节拍一致）；
    4) 逐类对比 predictions/hits/miss，重点看眼睛帧是否还「怎么猜都中」。

为什么能这样比：预测登记与命中判定只依赖「事件到达时刻」和「档案容差」，
两者都是纯函数；把墙钟换成回放时钟即可复现生产判定逻辑，无需重启框架。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus._silent_except import silent_exc  # noqa: E402

import config  # noqa: E402
from nucleus.genesis.StreamMiner import StreamMiner  # noqa: E402
from nucleus.world_model import periodic_profile as pp  # noqa: E402
from nucleus.world_model import typed_prediction as tp  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATTERNS = os.path.join(_ROOT, "data", "stream_miner", "patterns.json")
OUT = os.path.join(_ROOT, "data", "world_model", "replay_410.json")


class _Clock:
    """回放时钟：替换 StreamMiner 模块级 time（只影响该模块）。"""

    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now


def _load_stream():
    with open(PATTERNS, encoding="utf-8") as _f:
        _d = json.load(_f)
    _per = _d.get("periodicity") or {}
    _rows = []
    for _et, _stamps in _per.items():
        for _s in _stamps or []:
            try:
                _rows.append((float(_s), _et))
            except (TypeError, ValueError) as _exc:
                silent_exc(_exc, where="tools.m410_replay_typed._load_stream:L51", level="debug")
                continue
    _rows.sort()
    return _rows, _d


def _run(rows, typed_on: bool, clock_mod, clock):
    config.ENABLE_M410_TYPED_TOLERANCE = typed_on
    _m = StreamMiner()
    _m.enable()
    _m.set_storage_path(os.path.join(_ROOT, "tmp", "m410_replay_patterns.json"))
    for _i, (_ts, _et) in enumerate(rows, 1):
        clock.now = _ts
        _m.feed_event({"event_type": _et, "source_organ": "?", "timestamp": _ts})
        if _i % 200 == 0:
            _m.discover_patterns()
            _m.consume()
    if clock is not None:
        clock.now = clock.now + 3600.0
        _m.sweep_typed_predictions()
    del clock_mod
    return _m


def _legacy_rows(m):
    _q = m._pattern_quality or {}
    _out = {}
    for _k, _v in _q.items():
        _et = _k.split(":", 1)[-1]
        _p = int(_v.get("predictions", 0))
        _h = int(_v.get("hits", 0))
        _out[_et] = {"predictions": _p, "hits": _h,
                     "hit_rate": (round(_h / _p, 4) if _p else None)}
    return _out


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="410批刀2 回放 A/B")
    _ap.add_argument("--json", default=OUT, help="报告落盘路径")
    _a = _ap.parse_args(argv)

    _rows, _raw = _load_stream()
    if not _rows:
        print("[FAIL] patterns.json 无 periodicity 数据")
        return 2
    _span = _rows[-1][0] - _rows[0][0]
    print("[数据] %d 条事件 / %d 类 / 跨度 %.0fs (%.1f 分钟)"
          % (len(_rows), len(_raw.get("periodicity") or {}), _span, _span / 60.0))
    _prof = pp.load_profile(max_age_sec=None)
    print("[档案] events=%d generated_at=%s"
          % (len((_prof or {}).get("events") or {}), (_prof or {}).get("generated_at")))

    import nucleus.genesis.StreamMiner as _sm
    _real_time = _sm.time
    try:
        _c1 = _Clock()
        _sm.time = _c1
        _m_old = _run(_rows, False, _sm, _c1)
        _c2 = _Clock()
        _sm.time = _c2
        _m_new = _run(_rows, True, _sm, _c2)
    finally:
        _sm.time = _real_time
        config.ENABLE_M410_TYPED_TOLERANCE = True

    _old = _legacy_rows(_m_old)
    _new = _m_new.get_typed_prediction_stats()
    _new_by = _new.get("by_event") or {}

    print("\n%-28s %-9s | %6s %5s %7s | %5s %4s %4s %7s"
          % ("event_type", "预测型", "旧预测", "命中", "旧命中率", "新命中", "miss", "无覆盖", "新命中率"))
    print("-" * 104)
    _rows_out = []
    _types = list(dict.fromkeys(list(_old.keys()) + list(_new_by.keys())))
    for _et in _types:
        _o = _old.get(_et) or {}
        _n = _new_by.get(_et) or {}
        _pt = (tp.classify(_et) or "-")
        if not (_o or _n):
            continue
        print("%-28s %-9s | %6s %5s %7s | %5s %4s %4s %7s"
              % (_et, _pt, _o.get("predictions", 0), _o.get("hits", 0),
                 _o.get("hit_rate"), _n.get("hit", 0), _n.get("miss", 0),
                 _n.get("no_coverage", 0), _n.get("hit_rate")))
        _rows_out.append({"event_type": _et, "predict_type": _pt,
                          "legacy": _o, "typed": _n})

    print("\n=== 七类判据 ===")
    _eye = _old.get(pp.EV_EYES_STREAM_FRAME) or {}
    _eye_n = _new_by.get(pp.EV_EYES_STREAM_FRAME) or {}
    print("  eyes.stream_frame  旧: 时刻预测 %s 条 / 命中 %s（命中率 %s）"
          % (_eye.get("predictions", 0), _eye.get("hits", 0), _eye.get("hit_rate")))
    print("                    新: 不登记时刻预测；按分钟条数判 hit=%s miss=%s 无覆盖=%s"
          % (_eye_n.get("hit", 0), _eye_n.get("miss", 0), _eye_n.get("no_coverage", 0)))

    _tol_summary = {}
    # 回放把模块时钟拨回到历史时刻，档案缓存可能停在那时（判过期→{}）；
    # 这里显式清缓存，用真实时钟重读一次（生产里档案缓存 300s 自然过期）。
    _m_new._m410_prof = {}
    _m_new._m410_prof_at = 0.0
    for _et in pp.TARGET_EVENTS:
        _pt, _tol, _why = _m_new._m410_plan(_et)
        _tol_summary[_et] = {"predict_type": _pt, "tolerance_sec": _tol, "why": _why}
        print("  %-28s %-9s tol=%s"
              % (_et, _pt, ("%.4f" % _tol) if _tol is not None else "None(不设时刻容差)"))

    _rep = {"generated_at": time.time(), "events": len(_rows), "span_sec": _span,
            "per_type": _rows_out, "tolerance_plan": _tol_summary,
            "typed_stats": _new, "legacy_totals": {
                "predictions": sum((r["legacy"].get("predictions") or 0) for r in _rows_out),
                "hits": sum((r["legacy"].get("hits") or 0) for r in _rows_out)}}
    try:
        os.makedirs(os.path.dirname(_a.json), exist_ok=True)
        with open(_a.json, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(_rep, _f, ensure_ascii=False, indent=2)
        print("\n[落盘] %s" % _a.json)
    except OSError as _e:
        print("[WARN] 落盘失败: %s" % _e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
