# -*- coding: utf-8 -*-
"""★459 刀3：稳态内存观察工具（**只读**内存时序，只写一份报告）。

用法
----
    python tools/xingrui/memory_steady_report.py --hours 4

数据源（按优先级，自动降级）
----------------------------
1. ``data/metrics/runtime_metrics.jsonl``（任务书名义路径）；
2. **实测兜底**：``logs/pulse.log*`` 里的 ``[T-107a] 资源采样: RSS=…GB …
   进程启动≈…s … gen=…`` 行。459 批 T0 实测该 jsonl **不存在**，
   真实时序只在日志里（今日 95 条、跨多个进程），所以兜底不是可选装饰。

判定口径
--------
- 启动 < 30 分钟                ⇒ 预热期（读数不参与稳态判定）
- 连续 2 小时 > 5 MB/min 不回落  ⇒ 稳态泄漏
- 前 10 分钟高、之后回落到一半以下 ⇒ 预热期尾巴
- 其余                          ⇒ 正常波动

纪律：不 import config、不碰任何生产写口；唯一落盘就是
``data/reports/memory_steady_YYYYMMDD_HHMM.md``（可用 ``--out`` / ``--no-write`` 改）。
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc

NOMINAL_REL = os.path.join("data", "metrics", "runtime_metrics.jsonl")
LOG_REL_GLOB = os.path.join("logs", "pulse.log*")
REPORT_DIR_REL = os.path.join("data", "reports")

_LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*"
    r"\[T-107a\] 资源采样: RSS=([0-9.]+)GB.*"
    r"进程启动≈(\d+)s.*gen=(\S+)")

_TS_KEYS = ("ts", "timestamp", "time", "at", "epoch")
_RSS_MB_KEYS = ("rss_mb", "mem_rss_mb", "process_rss_mb", "heap_mb")
_RSS_GB_KEYS = ("rss_gb", "rss")


def _log(msg: str) -> None:
    print(msg)


# ---------------------------------------------------------------- 读数
def _parse_ts(v) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError as e:
            silent_exc(e, where="xingrui.memory_steady_report::_parse_ts", level="debug")
        for _fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return time.mktime(time.strptime(v[:19], _fmt))
            except ValueError as e:
                silent_exc(e, where="xingrui.memory_steady_report::_parse_ts fmt",
                           level="debug")
    return None


def _read_jsonl(path: str) -> tuple[list, str]:
    """读名义 jsonl；返回 ``[(ts, rss_mb, uptime_sec, gen)]`` 与数据源名。"""
    out: list = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError as e:
                    silent_exc(e, where="xingrui.memory_steady_report::_read_jsonl 坏行",
                               level="debug")
                    continue
                if not isinstance(r, dict):
                    continue
                _ts = next((_parse_ts(r[k]) for k in _TS_KEYS if k in r), None)
                _mb = None
                for k in _RSS_MB_KEYS:
                    if isinstance(r.get(k), (int, float)):
                        _mb = float(r[k])
                        break
                if _mb is None:
                    for k in _RSS_GB_KEYS:
                        if isinstance(r.get(k), (int, float)):
                            _mb = float(r[k]) * 1024.0
                            break
                if _ts is None or _mb is None:
                    continue
                _up = float(r.get("uptime_sec") or r.get("uptime") or 0.0)
                _gen = str(r.get("proc_gen") or r.get("gen") or "default")
                out.append((_ts, _mb, _up, _gen))
    except OSError as e:
        silent_exc(e, where="xingrui.memory_steady_report::_read_jsonl", level="debug")
        return [], "jsonl"
    return out, "jsonl"


def _read_logs(glob_rel: str) -> tuple[list, str]:
    """从 pulse.log* 的 `[T-107a] 资源采样` 行还原真实 RSS 时序。"""
    out: list = []
    _paths = sorted(glob.glob(os.path.join(_ROOT, glob_rel)))
    for _p in _paths:
        try:
            with open(_p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    _m = _LOG_RE.match(line)
                    if not _m:
                        continue
                    try:
                        _ts = time.mktime(time.strptime(
                            _m.group(1), "%Y-%m-%d %H:%M:%S"))
                    except ValueError as e:
                        silent_exc(e, where="xingrui.memory_steady_report::_read_logs ts",
                                   level="debug")
                        continue
                    out.append((_ts, float(_m.group(2)) * 1024.0,
                                float(_m.group(3)), _m.group(4)))
        except OSError as e:
            silent_exc(e, where="xingrui.memory_steady_report::_read_logs", level="debug")
            continue
    return out, "logs"


def load_series(data_path: str = "", log_glob: str = LOG_REL_GLOB) -> tuple[list, str]:
    """返回**当前进程**（最后一个 gen）的时序，按时间升序。"""
    _nominal = data_path or os.path.join(_ROOT, NOMINAL_REL)
    rows, src = [], "none"
    if os.path.isfile(_nominal):
        rows, src = _read_jsonl(_nominal)
    if not rows:
        rows, src = _read_logs(log_glob)
        src = f"{src}:[T-107a] 资源采样"
    if not rows:
        return [], f"无可用时序（找过 {NOMINAL_REL} 与 {LOG_REL_GLOB}）"
    _gens: dict[str, list] = {}
    for r in rows:
        _gens.setdefault(r[3], []).append(r)
    # 选「最后一条样本所属 gen」= 当前活体进程
    _last = max(rows, key=lambda x: x[0])
    _cur = sorted(_gens.get(_last[3], []), key=lambda x: x[0])
    return _cur, f"{src} | gen={_last[3]}"


# ---------------------------------------------------------------- 计算
def slope(points: list, window_sec: float, now: float) -> tuple:
    """最小二乘斜率（MB/min）。返回 ``(slope|None, n, delta_mb|None)``。"""
    _sel = [(t, v) for t, v, _u, _g in points if 0 <= now - t <= window_sec]
    if len(_sel) < 2:
        return None, len(_sel), None
    _x = [(t - _sel[0][0]) / 60.0 for t, _v in _sel]
    _y = [v for _t, v in _sel]
    _mx, _my = sum(_x) / len(_x), sum(_y) / len(_y)
    _den = sum((a - _mx) ** 2 for a in _x)
    if _den <= 0:
        return None, len(_sel), _y[-1] - _y[0]
    _num = sum((a - _mx) * (b - _my) for a, b in zip(_x, _y))
    return _num / _den, len(_sel), _y[-1] - _y[0]


def analyse(points: list, hours: float = 4.0) -> dict:
    """算 1h/4h/24h 斜率 + 启动前 10 分钟基线 + 判定。"""
    _now = max(t for t, _v, _u, _g in points)
    _uptime = max((u for _t, _v, u, _g in points), default=0.0)
    if not _uptime:
        _uptime = _now - min(t for t, _v, _u, _g in points)
    _windows = {}
    for _h in (1, 4, 24):
        _s, _n, _d = slope(points, _h * 3600.0, _now)
        _windows[f"{_h}h"] = {"slope": _s, "n": _n, "delta_mb": _d}
    _t0 = min(t for t, _v, _u, _g in points)
    _early = [(t, v, u, g) for t, v, u, g in points if t <= _t0 + 600.0]
    _base, _base_n, _base_d = slope(_early, 600.0, _early[-1][0] if _early else _now)
    _r1 = _windows["1h"]["slope"]
    _r2 = slope(points, 2 * 3600.0, _now)[0]
    if _uptime < 1800:
        _verdict = "预热期"
        _why = f"启动仅 {_uptime / 60:.1f} 分钟（<30min），启动阶跃未退，不参与稳态判定"
    elif (_r1 is not None and _r1 > 5 and _r2 is not None and _r2 > 5
          and _uptime > 7200):
        _verdict = "稳态泄漏"
        _why = (f"连续 2 小时 >5MB/min 不回落（1h={_r1:.2f}, 2h={_r2:.2f}），"
                f"uptime={_uptime / 3600:.1f}h")
    elif (_base is not None and _base > 5 and _r1 is not None
          and _r1 < _base * 0.5):
        _verdict = "预热期尾巴"
        _why = (f"启动前 10 分钟 {_base:.2f}MB/min，近 1 小时回落到 {_r1:.2f}MB/min")
    else:
        _verdict = "正常波动"
        _why = "未同时满足泄漏（连续2h>5MB/min）与预热回落判据"
    return {
        "now": _now, "uptime_sec": _uptime, "windows": _windows,
        "baseline_slope": _base, "baseline_n": _base_n,
        "baseline_delta_mb": _base_d, "rss_first_mb": points[0][1],
        "rss_last_mb": points[-1][1], "samples": len(points),
        "verdict": _verdict, "why": _why, "hours": hours,
    }


def _fmt(v, suffix: str = "") -> str:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}{suffix}"
    return f"{v}{suffix}"


def render(result: dict, source: str, hours: float) -> str:
    _lines = [
        "# 稳态内存观察报告",
        "",
        f"- 生成时间：`{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(result['now']))}`",
        f"- 数据源：`{source}`",
        f"- 分析窗口：`--hours {hours}`（另固定给 1h / 4h / 24h 三档）",
        f"- 样本数：{result['samples']}；进程已运行 {result['uptime_sec'] / 60:.1f} 分钟",
        f"- RSS：{_fmt(result['rss_first_mb'])} MB → {_fmt(result['rss_last_mb'])} MB",
        "",
        "## 增长率（MB/min，最小二乘斜率）",
        "",
        "| 窗口 | 样本 | 增量(MB) | 斜率(MB/min) |",
        "|---|---:|---:|---:|",
    ]
    for _k, _w in result["windows"].items():
        _lines.append(f"| {_k} | {_w['n']} | {_fmt(_w['delta_mb'])} "
                      f"| {_fmt(_w['slope'])} |")
    _lines += [
        (f"| 启动前10分钟(基线) | {result['baseline_n']} "
         f"| {_fmt(result['baseline_delta_mb'])} | {_fmt(result['baseline_slope'])} |"),
        "",
        "## 判定",
        "",
        f"**{result['verdict']}** —— {result['why']}",
        "",
        "> 工具只读内存时序、只写本报告；不修改任何生产代码路径或生产数据。",
        "> 日志兜底口径：`RSS=x.xxGB` ×1024 转 MB，按 `gen=` 分组只取当前活体进程。",
        "",
    ]
    return "\n".join(_lines)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="稳态内存观察（只读时序 + 出报告）")
    ap.add_argument("--hours", type=float, default=4.0, help="主分析窗口（小时）")
    ap.add_argument("--data", default="", help="内存时序 jsonl 路径（覆盖默认）")
    ap.add_argument("--log-glob", default=LOG_REL_GLOB, help="日志兜底 glob（相对仓根）")
    ap.add_argument("--out", default="", help="报告输出路径（覆盖默认）")
    ap.add_argument("--no-write", action="store_true", help="只打印、不落盘")
    a = ap.parse_args(argv)

    _pts, _src = load_series(a.data, a.log_glob)
    if not _pts:
        _log(f"[memory_steady] 无可用时序：{_src}")
        return 1
    _r = analyse(_pts, a.hours)

    _hdr = (f"{'窗口':<22}{'样本':>6}{'增量(MB)':>12}{'斜率(MB/min)':>16}")
    _log("=" * 60)
    _log(f"数据源   : {_src}")
    _log(f"样本数   : {_r['samples']}   进程运行: {_r['uptime_sec'] / 60:.1f} 分钟")
    _log(f"RSS      : {_fmt(_r['rss_first_mb'])} MB → {_fmt(_r['rss_last_mb'])} MB")
    _log(_hdr)
    _log("-" * 60)
    for _k, _w in _r["windows"].items():
        _log(f"{_k:<22}{_w['n']:>6}{_fmt(_w['delta_mb']):>12}{_fmt(_w['slope']):>16}")
    _log(f"{'启动前10分钟(基线)':<22}{_r['baseline_n']:>6}"
         f"{_fmt(_r['baseline_delta_mb']):>12}{_fmt(_r['baseline_slope']):>16}")
    _log("-" * 60)
    _log(f"判定     : {_r['verdict']} —— {_r['why']}")
    _log("=" * 60)

    _md = render(_r, _src, a.hours)
    if not a.no_write:
        _out = a.out or os.path.join(
            _ROOT, REPORT_DIR_REL,
            "memory_steady_" + time.strftime("%Y%m%d_%H%M",
                time.localtime(_r["now"])) + ".md")
        try:
            _dir = os.path.dirname(os.path.abspath(_out))
            os.makedirs(_dir, exist_ok=True)
            with open(_out, "w", encoding="utf-8", newline="\n") as f:
                f.write(_md)
            _log(f"报告已落盘: {_out}")
        except OSError as e:
            silent_exc(e, where="xingrui.memory_steady_report::main 落盘",
                       level="warning")
            _log("[memory_steady] 报告落盘失败（不影响上面的判定输出）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
