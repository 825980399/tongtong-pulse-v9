# -*- coding: utf-8 -*-
"""433 刀4：`steady_growth` 基线只缓存一次 + `steady_window_sec` 出口。

病因（433 T0 从代码读出来的根因）：原实现**每一拍**都把 deque 里「第一个距启动
≥ ramp 的点」重新赋给 `_m427_rss_at_10min`。deque `maxlen=720`、采样 5s ⇒ 只保留
最近 60 分钟；老样本被挤出后这个"第一个点"一路后滑 ⇒ 基线越滑越高、斜率窗口从
10 分钟拉到 61.8 分钟。生产实测：`rss_at_10min=5890.2` / `rss_now=6112.5` /
61.8min ⇒ `steady_growth_mb_per_min=3.599`（假读数）。

守护四件事：
  1) 基线一旦落定就不漂移（老样本被挤出后仍取原值）；
  2) 稳态斜率等于**真实**斜率（用例里手算 1.667MB/5s = 20.004MB/min）；
  3) `steady_window_sec` 如实给出「这个速率是几分钟算出来的」；
  4) 观测面三段（report → alarm_detailed → health_snapshot）都带这个字段。

★反证（铁律 30）：`test_03` 用**独立复算**模拟旧算法，证明旧算法在同样数据上
  会给出漂移后的基线 —— 故 test_01 的"没漂移"是修复的功劳，不是数据没变化。
"""
from __future__ import annotations

import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.runtime_metrics import RuntimeMetrics

SAMPLE = 5.0          # 与生产采样间隔一致
RAMP = 600.0          # 与 config.MEMORY_RAMP_SEC 默认一致
TRUE_MB_PER_MIN = 1.667 * (60.0 / SAMPLE)      # = 20.004


def _mk(uptime_sec: float, n: int, rss0: float = 3000.0, step: float = 1.667):
    """造一个"已跑 uptime_sec、最近 n 个采样点"的 RuntimeMetrics。"""
    _m = RuntimeMetrics()
    _now = time.time()
    _m._created_at = _now - uptime_sec
    _m._history.clear()
    for _i in range(n):
        _up = uptime_sec - (n - 1 - _i) * SAMPLE
        _m._history.append({"ts": _m._created_at + _up,
                            "mem_rss_mb": rss0 + _i * step, "mem_percent": 40.0})
    return _m


def _old_baseline(pts: list, created: float) -> tuple:
    """★旧算法（433 前的实现）的独立复算：每拍重取"第一个 ≥ ramp 的点"。"""
    for _p in pts:
        if (float(_p.get("ts") or 0.0) - created) >= RAMP:
            return float(_p["mem_rss_mb"]), float(_p["ts"])
    return None, None


class TestBaselineStability:
    def test_01_baseline_cached_once_no_drift(self):
        """跑满 60 分钟后 deque 已挤出 ramp 后首个样本 ⇒ 基线必须**不漂移**。"""
        _m = _mk(3600.0, 720)
        _r1 = _m.get_memory_growth_report(window_sec=300.0)
        _base1 = _m._m427_rss_at_10min
        _ts1 = _m._m427_rss_at_10min_ts
        assert _base1 is not None, "第 1 次就该落定基线"
        assert _ts1 is not None, "基线的采样时刻必须与值同源存下"

        # 再喂 200 拍（= 1000s），老样本被挤出 200 个
        _now = time.time()
        for _i in range(200):
            _m._history.append({"ts": _now + (_i + 1) * SAMPLE,
                                "mem_rss_mb": 3000.0 + (720 + _i) * 1.667,
                                "mem_percent": 40.0})
        _r2 = _m.get_memory_growth_report(window_sec=300.0)

        assert _m._m427_rss_at_10min == _base1, "基线被重新取了（原 bug 复发）"
        assert _m._m427_rss_at_10min_ts == _ts1
        assert _r2["rss_at_10min"] == _r1["rss_at_10min"]

    def test_02_steady_rate_equals_true_slope(self):
        """稳态斜率必须等于真实斜率（1.667MB/5s ⇒ 20.004MB/min）。"""
        _m = _mk(1800.0, 361)
        _r = _m.get_memory_growth_report(window_sec=300.0)
        assert _r["steady_growth_mb_per_min"] == pytest.approx(TRUE_MB_PER_MIN, abs=0.05)
        assert _r["growth_mb_per_min"] == pytest.approx(TRUE_MB_PER_MIN, abs=0.05)

    def test_03_old_algorithm_would_drift(self):
        """★反证：同一份数据交给旧算法，基线会漂到后面那个点上去。"""
        _m = _mk(3600.0, 720)
        _r1 = _m.get_memory_growth_report(window_sec=300.0)
        _new_base = _m._m427_rss_at_10min

        _now = time.time()
        for _i in range(200):
            _m._history.append({"ts": _now + (_i + 1) * SAMPLE,
                                "mem_rss_mb": 3000.0 + (720 + _i) * 1.667,
                                "mem_percent": 40.0})
        _pts = [p for p in list(_m._history) if p.get("mem_rss_mb")]
        _old_base, _old_ts = _old_baseline(_pts, _m._created_at)

        assert _old_base is not None
        assert _old_base > _new_base + 100, (
            f"反证失效：旧算法本应取到明显更高的基线（old={_old_base} new={_new_base}）")
        assert _old_ts > _m._m427_rss_at_10min_ts, (
            "旧算法取到的基线时刻本应更晚（这就是窗口被拉长的原因）")

    def test_04_ramp_phase_has_no_baseline(self):
        """启动 5 分钟：还没到 ramp 边界 ⇒ 基线必须是 None（不得拿 ramp 内的点充数）。"""
        _m = _mk(300.0, 61)
        _r = _m.get_memory_growth_report(window_sec=300.0)
        assert _r["phase"] == "ramp"
        assert _r["rss_at_10min"] is None
        assert _r["steady_growth_mb_per_min"] is None
        assert _r["steady_window_sec"] is None
        assert _m._m427_rss_at_10min is None

    def test_05_late_first_call_refuses_to_cache(self):
        """老进程首次调用时 deque 里已无 ramp 后首个样本 ⇒ 宁可不缓存也不冒充。"""
        _m = _mk(7200.0, 720)          # 已跑 2 小时，deque 只覆盖最后 60 分钟
        _r = _m.get_memory_growth_report(window_sec=300.0)
        assert _m._m427_rss_at_10min is None, "不得把晚到的样本当作 10 分钟基线"
        assert _r["rss_at_10min"] is None
        assert _r["steady_growth_mb_per_min"] is None


class TestObservability:
    def test_06_steady_window_sec_matches_base_ts(self):
        _m = _mk(1800.0, 361)
        _r = _m.get_memory_growth_report(window_sec=300.0)
        _expect = float(_m._history[-1]["ts"]) - float(_m._m427_rss_at_10min_ts)
        assert _r["steady_window_sec"] == pytest.approx(_expect, abs=0.2)
        # 基线在 ramp 边界（600s）⇒ 窗口应 ≈ 1800-600 = 1200s
        assert _r["steady_window_sec"] == pytest.approx(1200.0, abs=SAMPLE * 2)

    def test_07_alarm_detailed_carries_field(self):
        _m = _mk(1800.0, 361)
        _a = _m.check_memory_alarm_detailed()
        assert "steady_window_sec" in _a, "告警链返回值缺 steady_window_sec"
        assert _a["steady_window_sec"] == pytest.approx(1200.0, abs=SAMPLE * 2)

    def test_08_snapshot_chain_declares_field(self):
        """health_snapshot 链路两个组装点都必须透传该字段。"""
        _p = os.path.join(ROOT, "organs", "core", "PulseMetricsCollector.py")
        with open(_p, "r", encoding="utf-8", newline="") as _f:
            _code = "\n".join(_ln for _ln in _f.read().replace("\r\n", "\n").split("\n")
                              if not _ln.lstrip().startswith("#"))
        assert _code.count('"steady_window_sec": _al.get("steady_window_sec")') == 2, (
            "PulseMetricsCollector 的 growth_alarm 与 memory 块各需一处透传")

    def test_09_report_default_has_field(self):
        """冷启动（样本不足）也必须给出该键，值为 None（不是缺键）。"""
        _m = _mk(10.0, 1)
        _r = _m.get_memory_growth_report()
        assert "steady_window_sec" in _r and _r["steady_window_sec"] is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
