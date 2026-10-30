# -*- coding: utf-8 -*-
"""★403 刀1：L4 因果时序链（只记录事实，不判强度）。

背景
----
现有观测只能回答"发生了什么"（事件），回答不了"谁导致了谁"。
`data/monitor/pulse_pairs_YYYYMMDD.jsonl` 已经把每次发射与每次接收都落了盘
（同一 `pulse_id` 的两行 = 一次投递的两个端点），但没有任何模块把它还原成链。

本模块只做一件事：**每小时从 pairs 里抽一段窗口，把因果链事实落成一条汇总**。

两类链（口径不同，分开计数，绝不合并）
--------------------------------------
* **同器官**：同一器官相邻两次 emit，间隔 < 1s ⇒ `A事件 → B事件`
  （器官内部的处理顺序；相邻 = 链式，不取"窗口内任意两点"，否则 O(n²) 且噪声爆炸）
* **跨器官**：A 器官 emit → B 器官 receive，同一 `pulse_id`、间隔 < 5s ⇒ `A器官 → B器官`
  （投递事实；`pulse_id` 保证配对精确，不靠时间邻近猜）

为什么用滑动窗口而不是增量 offset
----------------------------------
增量 offset 要维护状态文件，进程重启 / 文件轮转 / 半行截断三处都能让 offset 失效，
而失效的表现是"台账静默停更"——比多读几 MB 难查得多。这里每小时只跑一次，
尾部读最近 3600s（典型 6MB/小时，上限 16MB），失败可见、重启自愈、无需状态。

落盘
----
``data/monitor/causal_chains_YYYYMMDD.jsonl``：每小时追加**一条** `kind=hourly_summary`。
`/data/` 整目录被 .gitignore 忽略 ⇒ 只落盘不进 git（与 402 批同）。
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict

from nucleus._silent_except import silent_exc

SCHEMA = "causal_chains/403.v1"

#: 同器官链：相邻 emit 的最大间隔（秒）
SAME_ORGAN_MAX_GAP_S = 1.0
#: 跨器官链：emit→receive 的最大间隔（秒）
CROSS_ORGAN_MAX_GAP_S = 5.0
#: 汇总窗口长度（秒）—— "每小时一条"
WINDOW_S = 3600.0
#: 尾部读取上限（字节）—— 超限即标注 truncated，绝不假装读全
MAX_TAIL_BYTES = 16 * 1024 * 1024
#: 每类链保留的 TOP 条数
TOP_N = 20
#: 时长分位采样上限（避免为求中位数把内存吃穿）
MAX_GAP_SAMPLES = 5000
#: 窗口内最多解析的行数（防护性上限）
MAX_ROWS = 400000

_REL_DIR = os.path.join("data", "monitor")

#: 进程内墙钟闸（同一进程内每小时最多跑一次；跨进程/重启不依赖它）
_LAST_RUN = {"ts": 0.0, "hour": None}


def project_root() -> str:
    """项目根（本文件位于 ``<root>/utils/``）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def output_path(day: str | None = None, root: str | None = None) -> str:
    """当日台账路径 ``data/monitor/causal_chains_YYYYMMDD.jsonl``。"""
    _day = day or time.strftime("%Y%m%d")
    return os.path.join(root or project_root(), _REL_DIR, "causal_chains_%s.jsonl" % _day)


def pairs_path(day: str | None = None, root: str | None = None) -> str:
    """当日脉冲对路径（不存在时回退到昨天，跨零点窗口用）。"""
    _root = root or project_root()
    _day = day or time.strftime("%Y%m%d")
    _p = os.path.join(_root, _REL_DIR, "pulse_pairs_%s.jsonl" % _day)
    if os.path.exists(_p):
        return _p
    _y = time.strftime("%Y%m%d", time.localtime(time.time() - 86400))
    return os.path.join(_root, _REL_DIR, "pulse_pairs_%s.jsonl" % _y)


def tail_rows(path: str, window_s: float = WINDOW_S,
              now: float | None = None,
              max_bytes: int = MAX_TAIL_BYTES) -> dict:
    """从文件尾部取最近 ``window_s`` 秒的行（不整读历史）。

    Returns:
        ``{"rows": list, "truncated": bool, "bytes": int, "path": str,
        "reason": str}``；文件不存在时 ``rows=[]``、``reason=file_missing``。
    """
    _now = float(now if now is not None else time.time())
    _cut = _now - float(window_s)
    _out = {"rows": [], "truncated": False, "bytes": 0, "path": path,
            "reason": "", "skipped_lines": 0}
    if not path or not os.path.exists(path):
        _out["reason"] = "file_missing"
        return _out
    try:
        _size = os.path.getsize(path)
        _start = max(0, _size - int(max_bytes))
        if _start > 0:
            _out["truncated"] = True
        with open(path, "rb") as _f:
            if _start:
                _f.seek(_start)
                # 丢掉被切断的半行（除非从文件头开始）
                _f.readline()
            _buf = _f.read()
        _out["bytes"] = len(_buf)
    except OSError as _e:
        silent_exc(_e, where="utils.causal_chain.tail_rows[read]")
        _out["reason"] = "read_failed"
        return _out

    _rows = []
    for _line in _buf.splitlines():
        if not _line.strip():
            continue
        try:
            _r = json.loads(_line)
        except (ValueError, TypeError) as _e:
            _out["skipped_lines"] += 1
            if _out["skipped_lines"] == 1:
                silent_exc(_e, where="utils.causal_chain.tail_rows[parse]", level="debug")
            continue
        if not isinstance(_r, dict):
            _out["skipped_lines"] += 1
            continue
        _ts = _r.get("ts")
        if not isinstance(_ts, (int, float)):
            _out["skipped_lines"] += 1
            continue
        if _ts < _cut:
            continue
        _rows.append(_r)
        if len(_rows) >= MAX_ROWS:
            _out["truncated"] = True
            _out["reason"] = "max_rows"
            break
    _out["rows"] = _rows
    return _out


def _p50(_vals: list) -> float | None:
    """中位间隔（毫秒）。空集返回 None（没测到 ≠ 0）。"""
    if not _vals:
        return None
    _s = sorted(_vals)
    _n = len(_s)
    _m = _s[_n // 2] if _n % 2 else (_s[_n // 2 - 1] + _s[_n // 2]) / 2.0
    return round(_m * 1000.0, 2)


def build_chains(rows: list) -> dict:
    """把一批 pairs 行还原成两类因果链（**纯函数**，不碰盘）。

    Args:
        rows: `pulse_pairs` 行（含 ``type``/``organ``/``event_type``/``ts``/``pulse_id``）。

    Returns:
        ``{"same_organ": {...}, "cross_organ": {...}, "counts": {...}}``
        其中每条链的结构：
        同器官 ``{"from","to","n","gap_ms_p50"}``；
        跨器官 ``{"from_organ","to_organ","event","n","gap_ms_p50"}``。
    """
    _emits = []
    _recvs = []
    _bad = 0
    for _r in rows or []:
        _t = _r.get("type")
        _ts = _r.get("ts")
        if not isinstance(_ts, (int, float)):
            _bad += 1
            continue
        if _t == "emit":
            _emits.append(_r)
        elif _t == "receive":
            _recvs.append(_r)
        else:
            _bad += 1

    # ---------- 同器官：相邻 emit ----------
    _by_organ = defaultdict(list)
    for _r in _emits:
        _by_organ[str(_r.get("organ") or "")].append(_r)
    _same = defaultdict(lambda: {"n": 0, "gaps": []})
    _same_links = 0
    for _organ, _lst in _by_organ.items():
        if len(_lst) < 2:
            continue
        _lst.sort(key=lambda x: (x.get("ts") or 0.0, x.get("seq") or 0))
        for _a, _b in zip(_lst, _lst[1:]):
            _gap = float(_b.get("ts") or 0.0) - float(_a.get("ts") or 0.0)
            if _gap < 0 or _gap >= SAME_ORGAN_MAX_GAP_S:
                continue
            _key = (str(_a.get("event_type") or ""), str(_b.get("event_type") or ""))
            _e = _same[_key]
            _e["n"] += 1
            if len(_e["gaps"]) < MAX_GAP_SAMPLES:
                _e["gaps"].append(_gap)
            _same_links += 1

    _same_top = []
    for (_fa, _fb), _v in _same.items():
        _same_top.append({"from": _fa, "to": _fb, "n": _v["n"],
                          "gap_ms_p50": _p50(_v["gaps"])})
    _same_top.sort(key=lambda x: (-x["n"], x["from"], x["to"]))

    # ---------- 跨器官：同 pulse_id 的 emit → receive ----------
    _emit_by_pid = {}
    for _r in _emits:
        _pid = _r.get("pulse_id")
        if _pid and _pid not in _emit_by_pid:
            _emit_by_pid[_pid] = _r
    _cross = defaultdict(lambda: {"n": 0, "gaps": []})
    _pair = defaultdict(int)
    _cross_links = 0
    _unmatched = 0
    # ★去重键必须是 (pulse_id, 接收器官)，不能只用 pulse_id：
    #   一次 emit 本来就会被**多个**器官同时接收（`matched_organs` 可证，
    #   实际收到的每个器官都是一条独立的跨器官链）。只按 pulse_id 去重会把
    #   第二个以后的接收方全丢掉，跨器官链数会严重偏少。
    _seen = set()
    _dup_receive = 0
    for _rc in _recvs:
        _pid = _rc.get("pulse_id")
        _em = _emit_by_pid.get(_pid) if _pid else None
        if _em is None:
            _unmatched += 1
            continue
        _to = str(_rc.get("organ") or "")
        _dup_key = (_pid, _to)
        if _dup_key in _seen:
            _dup_receive += 1
            continue
        _seen.add(_dup_key)
        _fo = str(_em.get("organ") or "")
        if not _fo or not _to or _fo == _to:
            continue
        _gap = float(_rc.get("ts") or 0.0) - float(_em.get("ts") or 0.0)
        if _gap < 0 or _gap >= CROSS_ORGAN_MAX_GAP_S:
            continue
        _key = (_fo, _to, str(_rc.get("event_type") or _em.get("event_type") or ""))
        _e = _cross[_key]
        _e["n"] += 1
        if len(_e["gaps"]) < MAX_GAP_SAMPLES:
            _e["gaps"].append(_gap)
        _pair[(_fo, _to)] += 1
        _cross_links += 1

    _cross_top = []
    for (_fo, _to, _ev), _v in _cross.items():
        _cross_top.append({"from_organ": _fo, "to_organ": _to, "event": _ev,
                           "n": _v["n"], "gap_ms_p50": _p50(_v["gaps"])})
    _cross_top.sort(key=lambda x: (-x["n"], x["from_organ"], x["to_organ"], x["event"]))

    _pair_top = [{"from_organ": _k[0], "to_organ": _k[1], "n": _v}
                 for _k, _v in _pair.items()]
    _pair_top.sort(key=lambda x: (-x["n"], x["from_organ"], x["to_organ"]))

    return {
        "counts": {
            "emit": len(_emits),
            "receive": len(_recvs),
            "bad_rows": _bad,
            "same_organ_links": _same_links,
            "cross_organ_links": _cross_links,
            "unmatched_receive": _unmatched,
            "duplicate_receive": _dup_receive,
        },
        "same_organ": {
            "links": _same_links,
            "distinct_chains": len(_same_top),
            "top": _same_top[:TOP_N],
        },
        "cross_organ": {
            "links": _cross_links,
            "distinct_chains": len(_cross_top),
            "distinct_pairs": len(_pair_top),
            "top": _cross_top[:TOP_N],
            "by_pair": _pair_top[:TOP_N],
            # 涉及器官（**含发送方**）：只看接收方会漏掉"只发不收"的器官
            "organs_involved": sorted({x["from_organ"] for x in _pair_top}
                                      | {x["to_organ"] for x in _pair_top}),
        },
    }


def summarize_window(rows: list, window_start: float, window_end: float,
                     source: dict | None = None) -> dict:
    """生成一条小时汇总记录（纯函数；测试直接喂造的行即可）。"""
    _chains = build_chains(rows)
    _win = max(0.0, float(window_end) - float(window_start))
    # ★实际覆盖时长：尾部按字节截断时，读到的行覆盖不到整个窗口。
    #   不写这个字段，读方会把"只覆盖 40 分钟"的汇总当成"整小时"，链数直接偏少。
    _ts = [float(_r.get("ts")) for _r in (rows or [])
           if isinstance(_r.get("ts"), (int, float))]
    _cov = (max(_ts) - min(_ts)) if len(_ts) >= 2 else 0.0
    _cov = min(_cov, _win)
    return {
        "kind": "hourly_summary",
        "schema": SCHEMA,
        "hour": time.strftime("%Y-%m-%dT%H", time.localtime(float(window_end))),
        "window_start": round(float(window_start), 3),
        "window_end": round(float(window_end), 3),
        "window_s": round(_win, 1),
        "coverage_s": round(_cov, 1),
        "coverage_ratio": round(_cov / _win, 3) if _win > 0 else None,
        "complete": bool(_win > 0 and _cov >= _win - 1.0),
        "generated_at": round(time.time(), 3),
        "rule": {
            "same_organ_max_gap_s": SAME_ORGAN_MAX_GAP_S,
            "cross_organ_max_gap_s": CROSS_ORGAN_MAX_GAP_S,
            "same_organ_basis": "同一器官相邻两次 emit（链式相邻，非窗口内任意两点）",
            "cross_organ_basis": "同一 pulse_id 的 emit→receive（配对精确，不靠时间邻近猜）",
        },
        "source": dict(source or {}),
        "counts": _chains["counts"],
        "same_organ": _chains["same_organ"],
        "cross_organ": _chains["cross_organ"],
        "note": "只记录事实，不判因果强度（强度判定留给后续批次）",
    }


def append_summary(summary: dict, path: str | None = None) -> str:
    """追加一条汇总到台账（一次 open-append，不做原子替换——jsonl 只需行完整）。"""
    _p = path or output_path()
    _dir = os.path.dirname(os.path.abspath(_p))
    if not os.path.isdir(_dir):
        os.makedirs(_dir, exist_ok=True)
    _line = json.dumps(summary, ensure_ascii=False, sort_keys=False)
    # ★newline="" 必须显式给：Windows 文本模式默认把 \n 翻成 \r\n，
    #   jsonl 会变成 CRLF 混合行尾（台账类文件按 LF 消费）。
    with open(_p, "a", encoding="utf-8", newline="\n") as _f:
        _f.write(_line + "\n")
        _f.flush()
        os.fsync(_f.fileno())
    return _p


def run_once(now: float | None = None, root: str | None = None,
             window_s: float = WINDOW_S) -> dict:
    """跑一次汇总并落盘（可被测试/运维直接调用）。"""
    _now = float(now if now is not None else time.time())
    _src = pairs_path(root=root)
    _tail = tail_rows(_src, window_s=window_s, now=_now)
    _summary = summarize_window(
        _tail["rows"], _now - window_s, _now,
        source={"file": os.path.basename(_src), "bytes_read": _tail["bytes"],
                "truncated": _tail["truncated"], "skipped_lines": _tail["skipped_lines"],
                "reason": _tail["reason"]})
    _p = append_summary(_summary, output_path(root=root))
    return {"ok": True, "path": _p, "written": 1,
            "rows": len(_tail["rows"]), "counts": _summary["counts"],
            "truncated": _tail["truncated"]}


def tick_from_heartbeat(now: float | None = None) -> dict:
    """心跳挂钩入口：墙钟按整小时桶判一次（不依赖调度器启动顺序）。

    失败一律吞掉（观测件不得反噬主循环），但**每次失败都留痕**。
    """
    _now = float(now if now is not None else time.time())
    _hour = int(_now // WINDOW_S)
    if _LAST_RUN["hour"] == _hour:
        return {"skipped": "same_hour"}
    _LAST_RUN["hour"] = _hour
    _LAST_RUN["ts"] = _now
    try:
        return run_once(now=_now)
    except Exception as _e:
        silent_exc(_e, where="utils.causal_chain.tick_from_heartbeat")
        return {"ok": False, "error": "%s: %s" % (type(_e).__name__, _e)}


if __name__ == "__main__":  # pragma: no cover - 手工试跑
    import argparse

    _ap = argparse.ArgumentParser(description="403 刀1 因果时序链")
    _ap.add_argument("--once", action="store_true", help="立刻跑一次汇总")
    _ap.add_argument("--window", type=float, default=WINDOW_S, help="窗口秒数")
    _a = _ap.parse_args()
    if _a.once:
        print(json.dumps(run_once(window_s=_a.window), ensure_ascii=False))
    else:
        print(json.dumps(tick_from_heartbeat(), ensure_ascii=False))
