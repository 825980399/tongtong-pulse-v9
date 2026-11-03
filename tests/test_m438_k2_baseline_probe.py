# -*- coding: utf-8 -*-
"""438 刀2 单测：基线探针（窗口覆盖判据 / 绝不填 0 / 入队写入 / 幂等 / 总闸）。

★基准说明（铁律 30）：期望值不是拿被测函数的输出算的，而是**先把日志写死、
再按行数手点出来的**（合成日志见各用例）。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
import nucleus.evolution.baseline_probe as bp

# 合成日志：含 a.py 的错误行**恰好 3 条**（2 ERROR + 1 CRITICAL）；
# INFO/WARNING 各 1 条不算；Traceback 续行（File "a.py"）本身不是错误级也不算。
_LINES = [
    "2026-11-03 10:00:00 [框架] INFO: 启动完成",
    "2026-11-03 10:00:05 [框架] ERROR: a.py 第 3 行炸了",
    "2026-11-03 10:01:00 [框架] INFO: a.py 正常跑着",
    "2026-11-03 10:02:00 [框架] ERROR: a.py 又炸了",
    "2026-11-03 10:03:00 [框架] CRITICAL: a.py 彻底挂了",
    "2026-11-03 10:04:00 [框架] WARNING: a.py 只是警告",
    "2026-11-03 10:05:00 Traceback (most recent call last):",
    '  File "a.py", line 1',
    "2026-11-03 10:06:00 [框架] DEBUG: 无关行",
]


def _ts(s: str) -> float:
    return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))


def _mk(tmp_path, lines=None, name="pulse.log") -> str:
    p = str(tmp_path / name)
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines if lines is not None else _LINES) + "\n")
    return p


# ---------------------------------------------------------------- ① 计数口径
def test_scan_counts_only_error_lines(tmp_path):
    """窗口全覆盖时，a.py 的错误行数 = 3（手点：2 ERROR + 1 CRITICAL）。"""
    _p = _mk(tmp_path)
    _r = bp.scan_window("organs/brain/a.py", "m", since=_ts("2026-11-03 10:00:00"),
                        until=_ts("2026-11-03 10:06:00"), path=_p, mode="file")
    assert _r["covered"] is True, _r["why"]
    assert _r["count"] == 3, f"计数={_r['count']}（应 3）"


def test_scan_respects_window(tmp_path):
    """窗口收窄到 10:02~10:04 ⇒ 只剩 2 条错误。"""
    _p = _mk(tmp_path)
    _r = bp.scan_window("a.py", "m", since=_ts("2026-11-03 10:02:00"),
                        until=_ts("2026-11-03 10:04:00"), path=_p, mode="file")
    assert _r["count"] == 2, f"计数={_r['count']}（应 2）"


def test_scan_file_method_mode_needs_both(tmp_path):
    """`file_method` 档：日志行里没有方法名 ⇒ 一条都不算（与执行器同口径）。"""
    _p = _mk(tmp_path)
    _r = bp.scan_window("a.py", "no_such_method",
                        since=_ts("2026-11-03 10:00:00"),
                        until=_ts("2026-11-03 10:06:00"), path=_p, mode="file_method")
    assert _r["count"] == 0
    _r2 = bp.scan_window("a.py", "no_such_method",
                         since=_ts("2026-11-03 10:00:00"),
                         until=_ts("2026-11-03 10:06:00"), path=_p, mode="file_or_method")
    assert _r2["count"] == 3, "file_or_method 档应回落成按文件名命中"


# ---------------------------------------------------------------- ② 覆盖判据（核心）
def test_window_started_before_log_is_unavailable(tmp_path):
    """窗口起点早于日志首行 ⇒ 前半段没被观测 ⇒ unavailable + None（不是 0）。"""
    _p = _mk(tmp_path)
    _r = bp.scan_window("a.py", "m", since=_ts("2026-11-03 09:00:00"),
                        until=_ts("2026-11-03 10:06:00"), path=_p, mode="file")
    assert _r["covered"] is False
    assert "轮转截断" in _r["why"] or "晚于窗口起点" in _r["why"], _r["why"]


def test_stale_log_is_unavailable(tmp_path, monkeypatch):
    """日志末行比窗口终点早超过 fresh 阈值 ⇒ 无法证明「到 now 为止没错」。"""
    monkeypatch.setattr(config, "M438_BASELINE_LOG_FRESH_SEC", 60.0, raising=True)
    _p = _mk(tmp_path)
    _r = bp.scan_window("a.py", "m", since=_ts("2026-11-03 10:00:00"),
                        until=_ts("2026-11-03 12:00:00"), path=_p, mode="file")
    assert _r["covered"] is False
    assert "末行" in _r["why"], _r["why"]


def test_missing_log_is_unavailable(tmp_path):
    _r = bp.scan_window("a.py", "m", since=0, until=time.time(),
                        path=str(tmp_path / "nope.log"), mode="file")
    assert _r["covered"] is False
    assert _r["count"] == 0


# ---------------------------------------------------------------- ③ probe_baseline
def test_probe_returns_int_when_covered(tmp_path, monkeypatch):
    # 合成日志只有 6 分钟跨度 ⇒ 把「最小窗口」也降到 3 分钟才谈得上基线
    monkeypatch.setattr(config, "M438_BASELINE_MIN_WINDOW_HOURS", 0.05, raising=True)
    _p = _mk(tmp_path)
    _r = bp.probe_baseline("a.py", "m", path=_p,
                           now=_ts("2026-11-03 10:06:00"),
                           window_hours_=(6.0 / 60.0))    # 6 分钟窗口：起点正落在日志首行
    assert _r["baseline_source"] == bp.SOURCE_LOG_WINDOW
    assert _r["baseline_errors"] == 3, _r


def test_probe_returns_none_not_zero_when_uncovered(tmp_path):
    """★任务书红线：取不到就 None，绝不填 0。"""
    _p = _mk(tmp_path)
    _r = bp.probe_baseline("a.py", "m", path=_p,
                           now=_ts("2026-11-03 10:06:00"),
                           window_hours_=48.0)             # 窗口远超日志覆盖
    assert _r["baseline_source"] == bp.SOURCE_UNAVAILABLE
    assert _r["baseline_errors"] is None, "取不到却写了 0"


# ---------------------------------------------------------------- ④ 入队写入
def test_apply_writes_all_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M438_BASELINE_PROBE", True, raising=True)
    monkeypatch.setattr(config, "M438_BASELINE_SHADOW", True, raising=True)
    # 合成日志只有 6 分钟跨度 ⇒ 窗口必须缩到 6 分钟，否则会如实判「未覆盖」
    monkeypatch.setattr(config, "M438_BASELINE_WINDOW_HOURS", 6.0 / 60.0, raising=True)
    monkeypatch.setattr(config, "M438_BASELINE_MIN_WINDOW_HOURS", 0.05, raising=True)
    _p = _mk(tmp_path)
    _patch = {"file": "a.py", "method": "m"}
    _r = bp.apply_to_patch(_patch, path=_p, now=_ts("2026-11-03 10:06:00"))
    assert _r["applied"] is True
    assert _patch["baseline_errors"] == 3
    assert _patch["baseline_source"] == bp.SOURCE_LOG_WINDOW
    assert _patch["baseline_shadow"] is True
    assert isinstance(_patch["baseline_probed_at"], float)


def test_apply_is_idempotent(tmp_path, monkeypatch):
    """重试入队不得反复改写基线（否则同一补丁的基线会随时间漂移）。"""
    monkeypatch.setattr(config, "ENABLE_M438_BASELINE_PROBE", True, raising=True)
    _p = _mk(tmp_path)
    _patch = {"file": "a.py", "method": "m", "baseline_errors": 7,
              "baseline_source": "log_window"}
    _r = bp.apply_to_patch(_patch, path=_p)
    assert _r["applied"] is False
    assert _patch["baseline_errors"] == 7, "已有基线被覆盖"


def test_apply_switch_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M438_BASELINE_PROBE", False, raising=True)
    _patch = {"file": "a.py", "method": "m"}
    _r = bp.apply_to_patch(_patch, path=_mk(tmp_path))
    assert _r["applied"] is False
    assert "baseline_errors" not in _patch
    assert "baseline_source" not in _patch


def test_apply_unavailable_writes_none(tmp_path, monkeypatch):
    """不可判定时字段必须写 None + unavailable，而不是 0。"""
    monkeypatch.setattr(config, "ENABLE_M438_BASELINE_PROBE", True, raising=True)
    _patch = {"file": "a.py", "method": "m"}
    bp.apply_to_patch(_patch, path=str(tmp_path / "nope.log"))
    assert _patch["baseline_errors"] is None
    assert _patch["baseline_source"] == bp.SOURCE_UNAVAILABLE


def test_effective_window_is_intersection(tmp_path, monkeypatch):
    """请求 48h、日志只覆盖 6min ⇒ 用实际覆盖段，并回传真实窗口长度（不假装 48h）。"""
    monkeypatch.setattr(config, "M438_BASELINE_MIN_WINDOW_HOURS", 0.05, raising=True)
    _p = _mk(tmp_path)
    _r = bp.probe_baseline("a.py", "m", path=_p,
                           now=_ts("2026-11-03 10:06:00"),
                           window_hours_=48.0)
    assert _r["baseline_source"] == bp.SOURCE_LOG_WINDOW, _r
    assert _r["baseline_errors"] == 3
    assert 350 <= _r["baseline_window_sec"] <= 370, _r["baseline_window_sec"]
    assert "请求 48h" in _r["detail"] and "实际 0.1h" in _r["detail"], _r["detail"]


def test_too_short_window_is_unavailable(tmp_path):
    """实际可观测窗口低于下限 ⇒ unavailable（6 分钟 < 默认 0.5h）。"""
    _p = _mk(tmp_path)
    _r = bp.probe_baseline("a.py", "m", path=_p,
                           now=_ts("2026-11-03 10:06:00"),
                           window_hours_=48.0)
    assert _r["baseline_source"] == bp.SOURCE_UNAVAILABLE
    assert _r["baseline_errors"] is None
    assert "下限" in _r["detail"], _r["detail"]
