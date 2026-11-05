# -*- coding: utf-8 -*-
"""459 刀3：稳态内存观察工具的单测（合成时序，不碰生产数据）。"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.xingrui import memory_steady_report as MSR


def _series(*, start_mb=1000.0, rate_mb_per_min=0.0, minutes=180, step_min=10):
    """合成 ``(ts, rss_mb, uptime_sec, gen)`` 时序。"""
    t0 = 1793890000.0
    out = []
    for i in range(0, minutes + 1, step_min):
        out.append((t0 + i * 60.0, start_mb + rate_mb_per_min * i,
                    i * 60.0, "pid-test@20261105"))
    return out


# ---------------------------------------------------------------- 斜率
def test_slope_recovers_known_rate():
    pts = _series(rate_mb_per_min=8.0, minutes=180)
    s, n, d = MSR.slope(pts, 4 * 3600.0, pts[-1][0])
    assert n == 19
    assert abs(s - 8.0) < 1e-6, s
    assert abs(d - 8.0 * 180) < 1e-6


def test_slope_needs_two_points():
    pts = _series(minutes=0)
    s, n, _d = MSR.slope(pts, 3600.0, pts[-1][0])
    assert s is None and n == 1


# ---------------------------------------------------------------- 判定
def test_verdict_warmup_under_30min():
    r = MSR.analyse(_series(rate_mb_per_min=60.0, minutes=16, step_min=4))
    assert r["verdict"] == "预热期"
    assert "<30min" in r["why"]


def test_verdict_steady_leak():
    # 3 小时恒定 +8MB/min，uptime 3h ⇒ 1h/2h 都 >5 且不回落
    r = MSR.analyse(_series(rate_mb_per_min=8.0, minutes=180))
    assert r["verdict"] == "稳态泄漏", r["why"]
    assert "连续 2 小时" in r["why"]


def test_verdict_warmup_tail():
    # 前 10 分钟陡升（+20MB/min），第 10 分钟后完全走平
    pts = [(1793890000.0 + i * 60.0,
            1000.0 + (20.0 * i if i <= 10 else 200.0),
            i * 60.0, "g")
           for i in range(0, 181, 5)]
    r = MSR.analyse(pts)
    assert r["verdict"] == "预热期尾巴", r["why"]


def test_verdict_normal_fluctuation():
    r = MSR.analyse(_series(rate_mb_per_min=0.5, minutes=180))
    assert r["verdict"] == "正常波动", r["why"]


# ---------------------------------------------------------------- 数据源
def test_load_jsonl_with_flexible_keys(tmp_path):
    p = tmp_path / "runtime_metrics.jsonl"
    rows = [
        {"ts": 1793890000.0, "mem_rss_mb": 1000.0, "uptime_sec": 0, "proc_gen": "g1"},
        {"ts": 1793890600.0, "mem_rss_mb": 1010.0, "uptime_sec": 600, "proc_gen": "g1"},
        {"ts": 1793891200.0, "rss_gb": 1.0, "uptime_sec": 1200, "proc_gen": "g1"},
    ]
    with open(p, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows))
    pts, src = MSR.load_series(str(p))
    assert len(pts) == 3 and src.startswith("jsonl")
    assert pts[1][1] == 1010.0
    assert abs(pts[2][1] - 1024.0) < 1e-6, "rss_gb 要 ×1024 转 MB"


def test_load_ignores_bad_lines(tmp_path):
    p = tmp_path / "bad.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write("not json\n")
        f.write(json.dumps({"ts": 1793890000.0, "rss_mb": 1.0}) + "\n")
    pts, _ = MSR.load_series(str(p))
    assert len(pts) == 1


def test_real_log_fallback_returns_current_gen():
    """兜底真的能从 logs/pulse.log* 取到当前活体进程的时序（只读）。"""
    pts, src = MSR.load_series()
    assert pts, "日志兜底不可用"
    assert src.startswith("logs:"), src
    assert "gen=" in src
    assert all(len(x) == 4 for x in pts)


# ---------------------------------------------------------------- 报告
def test_render_contains_table_and_verdict():
    r = MSR.analyse(_series(rate_mb_per_min=0.5, minutes=180))
    md = MSR.render(r, "logs:[T-107a] 资源采样", 4.0)
    assert "# 稳态内存观察报告" in md
    assert "| 1h |" in md and "| 24h |" in md
    assert r["verdict"] in md
    assert "只读内存时序" in md


def test_main_no_write_does_not_create_report(tmp_path, capsys):
    p = tmp_path / "m.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps({"ts": 1793890000.0 + i * 60.0,
                                     "rss_mb": 1000.0 + i,
                                     "uptime_sec": i * 60.0, "proc_gen": "g"})
                        + "\n" for i in range(0, 181, 30)))
    out = str(tmp_path / "never.md")
    rc = MSR.main(["--hours", "4", "--data", str(p), "--out", out, "--no-write"])
    assert rc == 0
    assert not os.path.exists(out)
    assert "判定" in capsys.readouterr().out


def test_main_writes_report_to_requested_path(tmp_path):
    p = tmp_path / "m.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps({"ts": 1793890000.0 + i * 60.0,
                                     "rss_mb": 1000.0 + i,
                                     "uptime_sec": i * 60.0, "proc_gen": "g"})
                        + "\n" for i in range(0, 181, 30)))
    out = str(tmp_path / "memory_steady_test.md")
    assert MSR.main(["--hours", "4", "--data", str(p), "--out", out]) == 0
    assert os.path.isfile(out)
    with open(out, encoding="utf-8") as _f:
        assert "稳态内存观察报告" in _f.read()
