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
        silent_exc(_e, where="utils.causal_chain.tail_rows[read]", level="debug")
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
        silent_exc(_e, where="utils.causal_chain.tick_from_heartbeat", level="debug")
        return {"ok": False, "error": "%s: %s" % (type(_e).__name__, _e)}


# ===== 404 刀1：L4 因果强度判定 =====
# 背景：403 刀1 只落"谁导致谁"的事实（链的频次与中位间隔），没有"强弱"。
# 本段在**同一次窗口读取**上再算三项指标（口径与 build_chains 完全一致）：
#   ① 频次 count            —— A→B 在窗口内出现多少次
#   ② 条件概率 p_b_given_a  —— P(B|A) = count(A→B) / A 的全部出链数
#      （另给 p_a_given_b = P(A|B)，反向参考，成本为零）
#   ③ 时间衰减 weighted_count —— 近 RECENT_WINDOW_S 的链权重 RECENT_WEIGHT，其余 BASE_WEIGHT
# 只计算不告警（告警策略留给后续批次）。
STRENGTH_SCHEMA = "causal_strength/404.v1"
#: 「近期」窗口（秒）—— 最近 10 分钟
RECENT_WINDOW_S = 600.0
#: 近期权重（越近越重要）
RECENT_WEIGHT = 2.0
#: 窗口内其余部分的权重
BASE_WEIGHT = 1.0
#: 每日落盘保留的链数上限（按 weighted_count 排序）
STRENGTH_TOP_N = 50
#: 同一天内两次汇总的最小间隔（秒）—— 防止重启后反复重写同一天
STRENGTH_MIN_GAP_S = 3600.0
#: 每日钩子名（与 organ_emits 同一挂点）
STRENGTH_HOOK = "causal_strength_daily"


def strength_output_path(day: str | None = None, root: str | None = None) -> str:
    """当日强度台账 ``data/monitor/causal_strength_YYYYMMDD.json``（每天一个文件）。"""
    _day = day or time.strftime("%Y%m%d")
    return os.path.join(root or project_root(), _REL_DIR, "causal_strength_%s.json" % _day)


def _ratio(_num: float, _den: float) -> float | None:
    """安全比值：分母为 0 时返回 None（没观测到 ≠ 概率 0）。"""
    try:
        if not _den:
            return None
        return round(float(_num) / float(_den), 4)
    except (TypeError, ZeroDivisionError) as _e:
        silent_exc(_e, where="utils.causal_chain._ratio", level="debug")
        return None


def build_strength(rows: list, now: float, window_s: float = WINDOW_S,
                   top_n: int | None = None) -> dict:
    """从 pairs 行算因果强度（**纯函数**，不碰盘）。

    链判定口径与 :func:`build_chains` 严格一致（同器官相邻 emit gap<1s、
    跨器官同 pulse_id 且 gap<5s），差别只在于本函数**额外按时间段分桶计数**。

    Args:
        rows: `pulse_pairs` 行。
        now: 窗口右端（秒）。
        window_s: 窗口长度（秒）。
        top_n: 每类保留条数；``None`` 用默认 ``STRENGTH_TOP_N``（传大值可取全量，对账/调试用）。

    Returns:
        ``{"totals": {...}, "same_organ": [...], "cross_organ": [...]}``；
        每条链：``from``/``to``/``count``/``weighted_count``/``recent_count``/
        ``base_count``/``p_b_given_a``/``p_a_given_b``/``gap_ms_p50``。
    """
    _now = float(now)
    _cut_recent = _now - RECENT_WINDOW_S
    _cut_all = _now - float(window_s)

    _emits = []
    _recvs = []
    for _r in rows or []:
        _ts = _r.get("ts")
        if not isinstance(_ts, (int, float)):
            continue
        if float(_ts) < _cut_all:
            continue
        _t = _r.get("type")
        if _t == "emit":
            _emits.append(_r)
        elif _t == "receive":
            _recvs.append(_r)

    # ---------- 同器官：相邻 emit（与 build_chains 同口径）----------
    _by_organ = defaultdict(list)
    for _r in _emits:
        _by_organ[str(_r.get("organ") or "")].append(_r)

    _same = defaultdict(lambda: {"n": 0, "recent": 0, "base": 0, "gaps": []})
    _same_out = defaultdict(int)   # A 的出链总数（P(B|A) 的分母）
    _same_in = defaultdict(int)    # B 的入链总数（P(A|B) 的分母）
    _same_links = 0
    for _organ, _lst in _by_organ.items():
        if len(_lst) < 2:
            continue
        _lst.sort(key=lambda x: (x.get("ts") or 0.0, x.get("seq") or 0))
        for _a, _b in zip(_lst, _lst[1:]):
            _ts_a = float(_a.get("ts") or 0.0)
            _ts_b = float(_b.get("ts") or 0.0)
            _gap = _ts_b - _ts_a
            if _gap < 0 or _gap >= SAME_ORGAN_MAX_GAP_S:
                continue
            _fa = str(_a.get("event_type") or "")
            _fb = str(_b.get("event_type") or "")
            _e = _same[(_fa, _fb)]
            _e["n"] += 1
            # 时间衰减按**链的完成时刻**（B 的 ts）分桶
            if _ts_b >= _cut_recent:
                _e["recent"] += 1
            else:
                _e["base"] += 1
            if len(_e["gaps"]) < MAX_GAP_SAMPLES:
                _e["gaps"].append(_gap)
            _same_out[_fa] += 1
            _same_in[_fb] += 1
            _same_links += 1

    _same_rows = []
    for (_fa, _fb), _v in _same.items():
        _w = _v["base"] * BASE_WEIGHT + _v["recent"] * RECENT_WEIGHT
        _same_rows.append({
            "from": _fa, "to": _fb,
            "count": _v["n"],
            "recent_count": _v["recent"],
            "base_count": _v["base"],
            "weighted_count": round(_w, 2),
            "p_b_given_a": _ratio(_v["n"], _same_out.get(_fa, 0)),
            "p_a_given_b": _ratio(_v["n"], _same_in.get(_fb, 0)),
            "gap_ms_p50": _p50(_v["gaps"]),
        })
    _same_rows.sort(key=lambda x: (-x["weighted_count"], -x["count"], x["from"], x["to"]))

    # ---------- 跨器官：同 pulse_id 的 emit → receive ----------
    _emit_by_pid = {}
    for _r in _emits:
        _pid = _r.get("pulse_id")
        if _pid and _pid not in _emit_by_pid:
            _emit_by_pid[_pid] = _r
    _cross = defaultdict(lambda: {"n": 0, "recent": 0, "base": 0, "gaps": []})
    _cross_out = defaultdict(int)
    _cross_in = defaultdict(int)
    _cross_links = 0
    _unmatched = 0
    _seen = set()
    for _rc in _recvs:
        _pid = _rc.get("pulse_id")
        _em = _emit_by_pid.get(_pid) if _pid else None
        if _em is None:
            _unmatched += 1
            continue
        _to = str(_rc.get("organ") or "")
        _dup_key = (_pid, _to)
        if _dup_key in _seen:
            continue
        _seen.add(_dup_key)
        _fo = str(_em.get("organ") or "")
        if not _fo or not _to or _fo == _to:
            continue
        _ts_e = float(_em.get("ts") or 0.0)
        _ts_r = float(_rc.get("ts") or 0.0)
        _gap = _ts_r - _ts_e
        if _gap < 0 or _gap >= CROSS_ORGAN_MAX_GAP_S:
            continue
        _ev = str(_rc.get("event_type") or _em.get("event_type") or "")
        # 跨器官链另记**发送方**事件名：实测两端恒同（receive 行记的就是"收到了哪个
        # 事件"，7010 行里 0 例外），但字段必须留在聚合键里 —— 万一将来某类事件
        # 的接收侧改成记"我因此产生的事件"，同名事件来自不同发送方也不会被合并。
        _ev_from = str(_em.get("event_type") or "")
        _e = _cross[(_fo, _to, _ev, _ev_from)]
        _e["n"] += 1
        if _ts_r >= _cut_recent:
            _e["recent"] += 1
        else:
            _e["base"] += 1
        if len(_e["gaps"]) < MAX_GAP_SAMPLES:
            _e["gaps"].append(_gap)
        _cross_out[_fo] += 1
        _cross_in[_to] += 1
        _cross_links += 1

    _cross_rows = []
    for (_fo, _to, _ev, _ev_from), _v in _cross.items():
        _w = _v["base"] * BASE_WEIGHT + _v["recent"] * RECENT_WEIGHT
        _cross_rows.append({
            "from_organ": _fo, "to_organ": _to, "event": _ev, "from_event": _ev_from,
            "count": _v["n"],
            "recent_count": _v["recent"],
            "base_count": _v["base"],
            "weighted_count": round(_w, 2),
            "p_b_given_a": _ratio(_v["n"], _cross_out.get(_fo, 0)),
            "p_a_given_b": _ratio(_v["n"], _cross_in.get(_to, 0)),
            "gap_ms_p50": _p50(_v["gaps"]),
        })
    _cross_rows.sort(key=lambda x: (-x["weighted_count"], -x["count"],
                                    x["from_organ"], x["to_organ"], x["event"],
                                    x["from_event"]))

    _n = STRENGTH_TOP_N if top_n is None else max(0, int(top_n))
    return {
        "totals": {
            "emit": len(_emits),
            "receive": len(_recvs),
            "same_organ_links": _same_links,
            "cross_organ_links": _cross_links,
            "unmatched_receive": _unmatched,
            "same_organ_distinct": len(_same_rows),
            "cross_organ_distinct": len(_cross_rows),
            "recent_window_s": RECENT_WINDOW_S,
            "recent_weight": RECENT_WEIGHT,
            "base_weight": BASE_WEIGHT,
        },
        "same_organ": _same_rows[:_n],
        "cross_organ": _cross_rows[:_n],
    }


def _write_json_atomic(record: dict, path: str) -> bool:
    """原子写（tmp + 3 次退避 replace）——403 刀4 的 WinError 5 教训复用。"""
    _dir = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(_dir):
        os.makedirs(_dir, exist_ok=True)
    _tmp = path + ".tmp"
    try:
        with open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(record, _f, ensure_ascii=False, indent=2)
            _f.flush()
            os.fsync(_f.fileno())
    except OSError as _e:
        silent_exc(_e, where="utils.causal_chain._write_json_atomic[write]", level="debug")
        return False
    _last = None
    for _i in range(3):
        try:
            os.replace(_tmp, path)
            return True
        except OSError as _e:
            _last = _e
            silent_exc(_e, where="utils.causal_chain._write_json_atomic[replace-retry]",
                       level="debug")
            if _i < 2:
                time.sleep(0.1)
    if _last is not None:
        silent_exc(_last, where="utils.causal_chain._write_json_atomic[replace]", level="warning")
    return False


def run_strength_daily(now: float | None = None, root: str | None = None,
                       force: bool = False, window_s: float = WINDOW_S) -> dict:
    """跑一次因果强度汇总并落盘（每日 03:00 钩子 / 运维手工均可调）。

    判重口径：**当日文件存在且 age < STRENGTH_MIN_GAP_S ⇒ 跳过**。
    不用内存变量判重 —— 403 批的 ``_LAST_RUN`` 在进程重启后归零，
    实测同一小时被写了两条汇总（详见本批交付报告 T0 偏差表）。
    """
    _now = float(now if now is not None else time.time())
    _p = strength_output_path(root=root)
    if not force:
        try:
            if os.path.isfile(_p):
                # ★判重用**墙钟**，不用传入的 now：文件 mtime 永远是系统时间，
                #   用 now 比较会在"补跑历史窗口"（now 与系统时间偏离）时恒不命中，
                #   表现为同一天被反复重写。
                _age = time.time() - os.path.getmtime(_p)
                if 0 <= _age < STRENGTH_MIN_GAP_S:
                    return {"ok": True, "skipped": "recent", "path": _p,
                            "age_s": round(_age, 1)}
        except OSError as _e:
            silent_exc(_e, where="utils.causal_chain.run_strength_daily[mtime]", level="debug")

    _src = pairs_path(root=root)
    _tail = tail_rows(_src, window_s=window_s, now=_now)
    _st = build_strength(_tail["rows"], _now, window_s=window_s)
    _rec = {
        "schema": STRENGTH_SCHEMA,
        "kind": "daily_strength",
        "day": time.strftime("%Y%m%d", time.localtime(_now)),
        "generated_at": round(_now, 3),
        "window_s": round(float(window_s), 1),
        "rule": {
            "same_organ_max_gap_s": SAME_ORGAN_MAX_GAP_S,
            "cross_organ_max_gap_s": CROSS_ORGAN_MAX_GAP_S,
            "recent_window_s": RECENT_WINDOW_S,
            "recent_weight": RECENT_WEIGHT,
            "base_weight": BASE_WEIGHT,
            "metrics": {
                "count": "频次：窗口内 A→B 出现次数",
                "p_b_given_a": "条件概率：count(A→B) / A 的全部出链数",
                "p_a_given_b": "反向条件概率：count(A→B) / B 的全部入链数",
                "weighted_count": "时间衰减加权频次：近 10 分钟 x2、其余 x1",
            },
        },
        "source": {
            "file": os.path.basename(_src),
            "bytes_read": _tail["bytes"],
            "truncated": _tail["truncated"],
            "skipped_lines": _tail["skipped_lines"],
            "reason": _tail["reason"],
        },
        "totals": _st["totals"],
        "same_organ": _st["same_organ"],
        "cross_organ": _st["cross_organ"],
        "note": "只计算不告警（告警策略留后续批次）",
    }
    _ok = _write_json_atomic(_rec, _p)
    return {"ok": bool(_ok), "path": _p, "written": 1 if _ok else 0,
            "rows": len(_tail["rows"]), "truncated": _tail["truncated"],
            "totals": _st["totals"]}


def register_causal_strength_schedule(scheduler=None):
    """把每日 03:00 因果强度汇总挂进 DailyScheduler（与 organ_emits 同挂点）。

    返回调度器实例；不可用 / 不支持每日钩子时返回 None（不影响启动）。
    """
    try:
        _sched = scheduler
        if _sched is None:
            from nucleus.self_awareness.DailyScheduler import get_daily_scheduler
            _sched = get_daily_scheduler()
        if _sched is None or not hasattr(_sched, "add_daily_hook"):
            return None
        _sched.add_daily_hook(STRENGTH_HOOK, run_strength_daily)
        return _sched
    except Exception as _e:
        silent_exc(_e, where="utils.causal_chain.register_causal_strength_schedule",
                   level="warning")
        return None


# ===== 404 刀2：L7 记忆回放接口 =====
# 任务书：`get_causal_chain_for_event(event_name, pulse_id=None)` —— 向前回溯上游链、
# 向后追溯下游链、最多 5 层（防止无限递归）；**不新增文件**，直接从 `pairs` 与
# `causal_chains` 里查；只做接口不做 UI。
#
# 为什么节点是「器官::事件」而不是裸事件名
# ---------------------------------------
# 初版按裸事件名建图，拿真实语料一试就塌了，原因在数据里而不在代码里：
# `pulse_pairs` 的 receive 行记的是"我收到了什么事件"，不是"我因此产出了什么事件" ——
# 实测 7010 条 receive 行 `event_type` **0% 缺失，且 100% 等于发送方的 event_type**
# （5360 条跨器官链无一例外）。也就是说跨器官投递在 pairs 里不是 `事件A -> 事件B`，
# 而是**同一个事件从器官 A 送到器官 B**。按裸事件名建图时 from==to，只能当自环丢掉
# （实测一次丢掉 211 条边，`眼睛->血管/视觉皮层` 这条最大的流直接消失，
#  单脉冲回放更是全空 —— 而单脉冲恰恰只有 1 次 emit + N 次 receive）。
# ⇒ 节点改成 `器官::事件` 复合键，一张图同时装下两类边、零丢边：
#   * 同器官顺序边 `心脏::heart.beat -> 心脏::heart.alive`（真·事件级因果顺序）
#   * 跨器官投递边 `眼睛::eyes.stream_frame -> 血管::eyes.stream_frame`（真·投递事实）
#
# ★语义边界（必须让读方看见，否则一定会被误读）
# ---------------------------------------------
# 跨器官边证明的是"X 被送到了 B"，**不是**"B 因为 X 产出了新事件"。后者要拿
# "接收时刻之后的器官 emit"另行对齐口径，pairs 本身证不了，本刀不做（留 405+）。
# 返回体里用 `note` 显式写死这句，不靠文档。
#
# 环的处理
# --------
# 事件图必然有环（`heart.beat -> heart.alive -> heart.beat` 遍地都是）。用"每个节点
# 只在**最小深度**展开一次"的 BFS 收敛：重复到达的边照样**列出来**（标 `back_edge=True`，
# 读方才知有闭环），但不再往下展开 —— 既有界，又不把环藏起来（藏起来会被误读成链断了）。
REPLAY_SCHEMA = "causal_replay/404.v1"
#: 任务书钉死：最多回溯 5 层
REPLAY_MAX_DEPTH = 5
#: 单节点每层最多展开多少条边（高扇出节点有几百条出链，全展开回放没法看）
REPLAY_MAX_EDGES_PER_NODE = 20
#: 入图的最小边频次（1 = 只要出现过就入图）
REPLAY_MIN_COUNT = 1
#: 复合节点分隔符 —— 器官名与事件名都不含 `::`，`partition` 拆分安全
NODE_SEP = "::"
#: 语义边界（随结果一起返回，写死不给读方留想象空间）
REPLAY_NOTE = ("跨器官边 = 投递事实（X 从 A 送到 B），不是 B 因 X 产出了新事件；"
               "同器官边 = 同一器官相邻两次 emit 的顺序。只回放 pairs 能直接证明的关系。")


def split_node(_key: str) -> tuple:
    """``"眼睛::eyes.stream_frame"`` → ``("眼睛", "eyes.stream_frame")``。"""
    _organ, _sep, _ev = str(_key or "").partition(NODE_SEP)
    if not _sep:
        return str(_key or ""), ""
    return _organ, _ev


def _node_key(_organ: str, _event: str) -> str:
    return "%s%s%s" % (_organ, NODE_SEP, _event)


def _merge_edge(_agg: dict, _fk: tuple, _tk: tuple, _kind: str,
                _gap: float, _recent: bool) -> None:
    """把一条边并入 ``((器官,事件),(器官,事件))`` 聚合桶（含时间衰减分桶）。"""
    _e = _agg.get((_fk, _tk))
    if _e is None:
        _agg[(_fk, _tk)] = {
            "count": 1,
            "recent": 1 if _recent else 0,
            "base": 0 if _recent else 1,
            "gaps": [_gap],
            "kinds": {_kind},
        }
        return
    _e["count"] += 1
    if _recent:
        _e["recent"] += 1
    else:
        _e["base"] += 1
    if len(_e["gaps"]) < MAX_GAP_SAMPLES:
        _e["gaps"].append(_gap)
    _e["kinds"].add(_kind)


def build_event_graph(rows: list, now: float, window_s: float = WINDOW_S,
                      min_count: int = REPLAY_MIN_COUNT) -> dict:
    """把 pairs 行还原成**「器官::事件」复合节点有向图**（纯函数，不碰盘）。

    链判定口径与 :func:`build_chains` / :func:`build_strength` 严格一致
    （同器官相邻 emit gap<1s、跨器官同 `pulse_id` 且 gap<5s），差别只在节点粒度。

    Args:
        rows: `pulse_pairs` 行。
        now: 窗口右端（秒）。
        window_s: 窗口长度（秒）。
        min_count: 入图的最小边频次。

    Returns:
        ``{"out_edges","in_edges","nodes","meta"}``；``nodes`` 是
        ``{节点键: {"organ","event","out_deg","in_deg","count"}}``，
        邻接表每条边：``node``（对端节点键）/``organ``/``event``（对端拆开）/
        ``kind``/``kinds``/``count``/``recent_count``/``base_count``/
        ``weighted_count``/``p_b_given_a``/``p_a_given_b``/``gap_ms_p50``。
    """
    _now = float(now)
    _cut = _now - float(window_s)
    _cut_recent = _now - RECENT_WINDOW_S
    _mc = max(1, int(min_count))

    _emits = []
    _recvs = []
    _bad = 0
    for _r in rows or []:
        if not isinstance(_r, dict):
            _bad += 1
            continue
        _ts = _r.get("ts")
        if not isinstance(_ts, (int, float)):
            _bad += 1
            continue
        if float(_ts) < _cut:
            continue
        _t = _r.get("type")
        if _t == "emit":
            _emits.append(_r)
        elif _t == "receive":
            _recvs.append(_r)

    _agg = {}
    _same_links = 0
    _cross_links = 0
    _unmatched = 0
    _dup_receive = 0
    _no_name = 0

    # ---------- 同器官顺序边：相邻 emit（与 build_chains 同口径）----------
    _by_organ = defaultdict(list)
    for _r in _emits:
        _by_organ[str(_r.get("organ") or "")].append(_r)
    for _organ, _lst in _by_organ.items():
        if len(_lst) < 2:
            continue
        _lst.sort(key=lambda x: (x.get("ts") or 0.0, x.get("seq") or 0))
        for _a, _b in zip(_lst, _lst[1:]):
            _ts_a = float(_a.get("ts") or 0.0)
            _ts_b = float(_b.get("ts") or 0.0)
            _gap = _ts_b - _ts_a
            if _gap < 0 or _gap >= SAME_ORGAN_MAX_GAP_S:
                continue
            _fa = str(_a.get("event_type") or "")
            _fb = str(_b.get("event_type") or "")
            if not _organ or not _fa or not _fb:
                _no_name += 1
                continue
            _merge_edge(_agg, (_organ, _fa), (_organ, _fb), "same_organ",
                        _gap, _ts_b >= _cut_recent)
            _same_links += 1

    # ---------- 跨器官投递边：同 pulse_id 的 emit → receive ----------
    _emit_by_pid = {}
    for _r in _emits:
        _pid = _r.get("pulse_id")
        if _pid and _pid not in _emit_by_pid:
            _emit_by_pid[_pid] = _r
    _seen = set()
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
            _no_name += 1
            continue
        _ts_e = float(_em.get("ts") or 0.0)
        _ts_r = float(_rc.get("ts") or 0.0)
        _gap = _ts_r - _ts_e
        if _gap < 0 or _gap >= CROSS_ORGAN_MAX_GAP_S:
            continue
        # ★接收行记的是"收到了哪个事件"，所以两端事件名本来就是同一个；
        #   优先取接收行自己的，读不到才回落到发送行。
        _ev = str(_rc.get("event_type") or _em.get("event_type") or "")
        if not _ev:
            _no_name += 1
            continue
        _merge_edge(_agg, (_fo, _ev), (_to, _ev), "cross_organ",
                    _gap, _ts_r >= _cut_recent)
        _cross_links += 1

    # ---------- 组装邻接表 ----------
    _out_tot = defaultdict(int)
    _in_tot = defaultdict(int)
    for (_fk, _tk), _e in _agg.items():
        if _e["count"] < _mc:
            continue
        _out_tot[_fk] += _e["count"]
        _in_tot[_tk] += _e["count"]

    _out = defaultdict(list)
    _in = defaultdict(list)
    _meta_node = defaultdict(lambda: {"count": 0})
    _edges = 0
    _below = 0
    for (_fk, _tk), _e in _agg.items():
        if _e["count"] < _mc:
            _below += 1
            continue
        _w = _e["base"] * BASE_WEIGHT + _e["recent"] * RECENT_WEIGHT
        _kinds = sorted(_k for _k in _e["kinds"] if _k)
        _base = {
            "kind": _kinds[0] if len(_kinds) == 1 else ("mixed" if _kinds else ""),
            "kinds": _kinds,
            "count": _e["count"],
            "recent_count": _e["recent"],
            "base_count": _e["base"],
            "weighted_count": round(_w, 2),
            "p_b_given_a": _ratio(_e["count"], _out_tot.get(_fk, 0)),
            "p_a_given_b": _ratio(_e["count"], _in_tot.get(_tk, 0)),
            "gap_ms_p50": _p50(_e["gaps"]),
        }
        _ko = _node_key(*_fk)
        _kt = _node_key(*_tk)
        _d = dict(_base)
        _d["node"] = _kt
        _d["organ"] = _tk[0]
        _d["event"] = _tk[1]
        _out[_ko].append(_d)
        _d = dict(_base)
        _d["node"] = _ko
        _d["organ"] = _fk[0]
        _d["event"] = _fk[1]
        _in[_kt].append(_d)
        _edges += 1
        _meta_node[_ko]["count"] += _e["count"]
        _meta_node[_kt]["count"] += _e["count"]

    for _lst in _out.values():
        _lst.sort(key=lambda e: (-e["weighted_count"], -e["count"], e["node"]))
    for _lst in _in.values():
        _lst.sort(key=lambda e: (-e["weighted_count"], -e["count"], e["node"]))

    _nodes = {}
    for _k in set(_out) | set(_in):
        _organ, _ev = split_node(_k)
        _nodes[_k] = {
            "organ": _organ,
            "event": _ev,
            "out_deg": len(_out.get(_k) or []),
            "in_deg": len(_in.get(_k) or []),
            "count": int(_meta_node[_k]["count"]),
        }

    return {
        "out_edges": dict(_out),
        "in_edges": dict(_in),
        "nodes": _nodes,
        "meta": {
            "node_sep": NODE_SEP,
            "events": len({v["event"] for v in _nodes.values()}),
            "organs": len({v["organ"] for v in _nodes.values()}),
            "nodes": len(_nodes),
            "edges": _edges,
            "rows_used": len(rows or []),
            "bad_rows": _bad,
            "min_count": _mc,
            "below_min_count_edges": _below,
            "no_name_links": _no_name,
            "same_organ_links": _same_links,
            "cross_organ_links": _cross_links,
            "unmatched_receive": _unmatched,
            "duplicate_receive": _dup_receive,
            "window_s": float(window_s),
        },
    }


def walk_event_chain(graph: dict, node_key: str, direction: str,
                     max_depth: int = REPLAY_MAX_DEPTH,
                     max_edges_per_node: int = REPLAY_MAX_EDGES_PER_NODE) -> dict:
    """从 ``node_key`` 沿图上溯（``up``）或下溯（``down``），按层返回（纯函数）。

    ``direction`` 取 ``up``/``upstream``/``back``/``backward`` 视为上游，其余为下游。

    Returns:
        ``{"direction","start","max_depth","levels":[{"depth","events":[...]}],
        "visited","cycles","truncated","truncated_depth","truncated_breadth"}``。
        ``truncated_depth`` = 撞到 ``max_depth`` 且末层节点还有边（任务书的"最多 5 层"
        就是在这里截断，截断必须显式标出来，不能假装链到这儿就完了）；
        ``truncated_breadth`` = 某节点边数超过 ``max_edges_per_node``。
    """
    _d = "up" if str(direction or "").strip().lower() in (
        "up", "upstream", "back", "backward") else "down"
    _adj = graph.get("in_edges" if _d == "up" else "out_edges") or {}
    _depth = max(0, int(max_depth))
    _cap = max(1, int(max_edges_per_node))
    _levels = []
    _seen = {node_key: 0}
    _cycles = []
    _frontier = [node_key]
    _breadth_hit = False
    for _dep in range(1, _depth + 1):
        _nodes = []
        _next = []
        for _parent in _frontier:
            _edges = _adj.get(_parent) or []
            if len(_edges) > _cap:
                _breadth_hit = True
            for _e in _edges[:_cap]:
                _child = str(_e.get("node") or "")
                if not _child:
                    continue
                _back = _child in _seen
                _nodes.append({
                    "node": _child,
                    "organ": _e.get("organ"),
                    "event": _e.get("event"),
                    "via": _parent,
                    "via_organ": split_node(_parent)[0],
                    "via_event": split_node(_parent)[1],
                    "depth": _dep,
                    "kind": _e.get("kind"),
                    "count": _e.get("count"),
                    "weighted_count": _e.get("weighted_count"),
                    # 下游看 P(子|父)，上游看 P(父的上一层|当前) —— 都朝"离开当前节点"的方向
                    "p": _e.get("p_b_given_a") if _d == "down" else _e.get("p_a_given_b"),
                    "p_reverse": _e.get("p_a_given_b") if _d == "down" else _e.get("p_b_given_a"),
                    "gap_ms_p50": _e.get("gap_ms_p50"),
                    "back_edge": _back,
                })
                if _back:
                    if _child not in _cycles:
                        _cycles.append(_child)
                    continue
                _seen[_child] = _dep
                _next.append(_child)
        if not _nodes:
            break
        _levels.append({"depth": _dep, "events": _nodes})
        if not _next:
            break
        _frontier = _next

    _depth_hit = False
    if _levels and len(_levels) >= _depth:
        for _n in _levels[-1]["events"]:
            if _n["back_edge"]:
                continue
            if _adj.get(_n["node"]):
                _depth_hit = True
                break
    return {
        "direction": _d,
        "start": node_key,
        "max_depth": _depth,
        "levels": _levels,
        "visited": sorted(_seen),
        "cycles": sorted(_cycles),
        "truncated": bool(_depth_hit or _breadth_hit),
        "truncated_depth": bool(_depth_hit),
        "truncated_breadth": bool(_breadth_hit),
    }


def _merge_levels(_walks: list, _root_set: set) -> list:
    """把多条起点的同向链合并成一张"去重后按深度排序"的平表。

    同一节点可能从不同起点、不同深度到达 ⇒ 只保留**最小深度**那条（回放要的是"最近的一条路"；
    更远的那条只说明存在绕行路径，不影响因果可达性判断）。
    """
    _best = {}
    for _w in _walks:
        for _l in _w.get("levels") or []:
            for _n in _l.get("events") or []:
                _k = _n.get("node")
                if not _k:
                    continue
                # 起点自身作为环路回边出现时不进平表（起点已在 roots 里单列）
                if _n.get("back_edge") and _k in _root_set:
                    continue
                _cur = _best.get(_k)
                if _cur is None or int(_n.get("depth") or 0) < int(_cur.get("depth") or 0):
                    _best[_k] = _n
    return sorted(_best.values(),
                  key=lambda n: (int(n.get("depth") or 0), -int(n.get("count") or 0),
                                 str(n.get("node") or "")))


def replay_from_rows(rows: list, event_name: str, pulse_id=None, *,
                     now: float | None = None, window_s: float = WINDOW_S,
                     max_depth: int = REPLAY_MAX_DEPTH,
                     min_count: int = REPLAY_MIN_COUNT,
                     max_edges_per_node: int = REPLAY_MAX_EDGES_PER_NODE) -> dict:
    """回放的**纯函数内核**：给定 rows 直接出结果，不碰盘（判据全部走这里）。"""
    _ev = str(event_name or "").strip()
    _out = {
        "schema": REPLAY_SCHEMA,
        "event": _ev,
        "pulse_id": pulse_id,
        "max_depth": max(0, int(max_depth)),
        "window_s": float(window_s),
        "note": REPLAY_NOTE,
        "roots": [],
        "upstream": [],
        "downstream": [],
        "graph": {},
        "summary": {},
        "empty_reason": "",
    }
    if not _ev:
        _out["empty_reason"] = "bad_event_name"
        return _out
    _now = float(now if now is not None else time.time())
    _rows = rows or []
    if pulse_id is not None:
        _pid = str(pulse_id)
        _rows = [_r for _r in _rows if isinstance(_r, dict)
                 and str(_r.get("pulse_id") or "") == _pid]
    _g = build_event_graph(_rows, _now, window_s, min_count=min_count)
    _out["graph"] = dict(_g["meta"])
    _out["graph"]["pulse_filtered_rows"] = len(_rows)

    _roots = [k for k, v in _g["nodes"].items() if v["event"] == _ev]
    _roots.sort(key=lambda k: (-(_g["nodes"][k]["out_deg"] + _g["nodes"][k]["in_deg"]),
                               _g["nodes"][k]["organ"], k))
    if not _roots:
        _out["empty_reason"] = ("pulse_not_observed" if pulse_id is not None
                                else "event_not_in_window")
        _out["summary"] = {"roots": 0, "upstream_nodes": 0, "downstream_nodes": 0,
                           "upstream_depth": 0, "downstream_depth": 0,
                           "events_involved": [], "organs_involved": [],
                           "cycles": [], "truncated": False}
        return _out

    _ups = []
    _downs = []
    for _k in _roots:
        _u = walk_event_chain(_g, _k, "up", max_depth, max_edges_per_node)
        _d = walk_event_chain(_g, _k, "down", max_depth, max_edges_per_node)
        _ups.append(_u)
        _downs.append(_d)
        _out["roots"].append({
            "node": _k,
            "organ": _g["nodes"][_k]["organ"],
            "event": _g["nodes"][_k]["event"],
            "out_deg": _g["nodes"][_k]["out_deg"],
            "in_deg": _g["nodes"][_k]["in_deg"],
            "upstream": _u,
            "downstream": _d,
        })

    _root_set = set(_roots)
    _out["upstream"] = _merge_levels(_ups, _root_set)
    _out["downstream"] = _merge_levels(_downs, _root_set)
    _ev_all = sorted({str(n.get("event") or "") for n in _out["upstream"] + _out["downstream"]}
                     - {""})
    _org_all = sorted({str(n.get("organ") or "") for n in _out["upstream"] + _out["downstream"]}
                      - {""})
    if not _out["upstream"] and not _out["downstream"]:
        _out["empty_reason"] = "no_chain"
    _out["summary"] = {
        "roots": len(_out["roots"]),
        "upstream_nodes": len(_out["upstream"]),
        "downstream_nodes": len(_out["downstream"]),
        "upstream_depth": max([int(n.get("depth") or 0) for n in _out["upstream"]] or [0]),
        "downstream_depth": max([int(n.get("depth") or 0) for n in _out["downstream"]] or [0]),
        "events_involved": _ev_all,
        "organs_involved": _org_all,
        "cycles": sorted({c for w in _ups + _downs for c in (w.get("cycles") or [])}),
        "truncated": bool(any(w.get("truncated") for w in _ups + _downs)),
        "truncated_depth": bool(any(w.get("truncated_depth") for w in _ups + _downs)),
        "truncated_breadth": bool(any(w.get("truncated_breadth") for w in _ups + _downs)),
    }
    return _out


def _count_event_mentions(record: dict, event_name: str) -> tuple:
    """数一条小时汇总里该事件出现在多少条链上（上游/下游两侧都算）。"""
    _n = 0
    _same = record.get("same_organ")
    _same = _same.get("top") if isinstance(_same, dict) else None
    for _c in (_same if isinstance(_same, list) else []):
        if not isinstance(_c, dict):
            continue
        if str(_c.get("from") or "") == event_name or str(_c.get("to") or "") == event_name:
            _n += int(_c.get("n") or 0) or 1
    _cross = record.get("cross_organ")
    _cross = _cross.get("top") if isinstance(_cross, dict) else None
    for _c in (_cross if isinstance(_cross, list) else []):
        if not isinstance(_c, dict):
            continue
        if str(_c.get("event") or "") == event_name:
            _n += int(_c.get("n") or 0) or 1
    return _n, str(record.get("hour") or "")


def _scan_chain_history(event_name: str, root: str | None = None,
                        days_back: int = 1, day: str | None = None) -> dict:
    """在 ``causal_chains_*.jsonl`` 的小时汇总里查该事件出现过哪些小时（只读）。

    ★**只做"见过没"的旁证，不用它建图**：403 的汇总里 ``cross_organ.top[]`` 只留
    接收方事件名、不落器官对儿之外的节点信息，拿它建图会凭器官对儿**臆造边**。
    ⇒ 窗口内没读到该事件时，这里至少能回答"它上个小时还在，是窗口滚出去了"。
    """
    _out = {"files": 0, "hours": [], "mentions": 0, "reason": "", "days_back": 0}
    _name = str(event_name or "")
    if not _name:
        _out["reason"] = "bad_event_name"
        return _out
    _root = root or project_root()
    _now = time.time()
    _hours = []
    _mentions = 0
    _n_days = 0 if day else max(0, int(days_back))
    for _off in range(0, _n_days + 1):
        _d = day or time.strftime("%Y%m%d", time.localtime(_now - _off * 86400.0))
        _p = output_path(day=_d, root=_root)
        if not os.path.exists(_p):
            continue
        try:
            with open(_p, "r", encoding="utf-8", errors="replace") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _r = json.loads(_line)
                    except (ValueError, TypeError) as _e:
                        silent_exc(_e, where="utils.causal_chain._scan_chain_history[parse]",
                                   level="debug")
                        continue
                    if not isinstance(_r, dict):
                        continue
                    _n, _h = _count_event_mentions(_r, _name)
                    if not _n:
                        continue
                    _mentions += _n
                    if _h and _h not in _hours:
                        _hours.append(_h)
        except OSError as _e:
            silent_exc(_e, where="utils.causal_chain._scan_chain_history[read]", level="debug")
            continue
        _out["files"] += 1
    _out["hours"] = sorted(_hours)
    _out["mentions"] = _mentions
    _out["days_back"] = _n_days
    if not _out["files"]:
        _out["reason"] = "no_summary_file"
    return _out


def get_causal_chain_for_event(event_name: str, pulse_id=None, *,
                               max_depth: int = REPLAY_MAX_DEPTH,
                               window_s: float = WINDOW_S,
                               root: str | None = None,
                               now: float | None = None,
                               min_count: int = REPLAY_MIN_COUNT,
                               max_edges_per_node: int = REPLAY_MAX_EDGES_PER_NODE,
                               include_history: bool = True) -> dict:
    """L7 记忆回放：给一个事件名，返回它的上游链与下游链（默认最多 5 层）。

    **不新增落盘文件**：图从 ``data/monitor/pulse_pairs_YYYYMMDD.jsonl`` 尾部窗口现算，
    历史旁证从 ``data/monitor/causal_chains_YYYYMMDD.jsonl`` 小时汇总里现查。

    Args:
        event_name: 事件名（``event_type``，如 ``heart.beat``）。
        pulse_id: 只回放这一个脉冲。给了就只喂该 ``pulse_id`` 的行 —— 单脉冲通常
            1 次 emit + N 次 receive ⇒ 事件级只有跨器官投递边（`眼睛::X -> 血管::X`），
            这是它的实际投递路径，不是缺陷。
        max_depth: 最多上溯/下溯层数（默认 5，任务书上限）。
        window_s: pairs 回看窗口（秒），默认 1 小时。
        root: 工程根（测试注入临时目录）。
        now: 窗口右端（秒），默认墙钟。
        min_count: 入图的最小边频次。
        max_edges_per_node: 每层每节点最多展开多少条边。
        include_history: 是否扫小时汇总做历史旁证。

    Returns:
        ``{"schema","event","pulse_id","max_depth","window_s","note","roots",
        "upstream","downstream","graph","summary","empty_reason","source",
        "history","generated_at"}``。``upstream``/``downstream`` 是**平表**
        （按 ``depth`` 升序，同节点只留最小深度那条路），``roots`` 保留每个起点各自的
        分层细节。``empty_reason``：``bad_event_name`` / ``event_not_in_window`` /
        ``pulse_not_observed`` / ``no_chain`` / ``""``。
    """
    _now = float(now if now is not None else time.time())
    _src = pairs_path(root=root)
    _tail = tail_rows(_src, window_s=window_s, now=_now)
    _res = replay_from_rows(_tail["rows"], event_name, pulse_id, now=_now,
                            window_s=window_s, max_depth=max_depth,
                            min_count=min_count, max_edges_per_node=max_edges_per_node)
    _res["source"] = {
        "kind": "pairs",
        "file": os.path.basename(_src),
        "path": _src,
        "exists": os.path.exists(_src),
        "rows": len(_tail["rows"]),
        "bytes": _tail["bytes"],
        "truncated": bool(_tail["truncated"]),
        "skipped_lines": _tail["skipped_lines"],
        "reason": _tail["reason"],
    }
    _res["history"] = None
    if include_history:
        try:
            _res["history"] = _scan_chain_history(
                _res["event"], root=root,
                day=time.strftime("%Y%m%d", time.localtime(_now)))
        except Exception as _e:
            silent_exc(_e, where="utils.causal_chain.get_causal_chain_for_event[history]", level="debug")
            _res["history"] = {"files": 0, "hours": [], "mentions": 0,
                               "reason": "scan_failed", "days_back": 0}
    _res["generated_at"] = round(time.time(), 3)
    return _res


def render_chain_tree(result: dict) -> str:
    """把回放结果画成缩进文本树（CLI / 日志用，不参与判定）。"""
    _r = result or {}
    _lines = ["event=%s%s" % (_r.get("event") or "",
                              ("  pulse_id=%s" % _r["pulse_id"]) if _r.get("pulse_id") else "")]
    if _r.get("empty_reason"):
        _lines.append("  (empty: %s)" % _r["empty_reason"])
    _lines.append("  起点：%s" % ", ".join(
        "%s" % _x.get("node") for _x in (_r.get("roots") or [])) or "（无）")
    for _key, _label in (("upstream", "上游 <-"), ("downstream", "下游 ->")):
        _lst = _r.get(_key) or []
        _dp = max([int(_n.get("depth") or 0) for _n in _lst] or [0])
        # p 的方向必须标出来：上游是 P(上游|本)，下游是 P(本|下游)，
        # 两者都朝"离开本节点"的方向算，但读起来一个是"被谁导致"、一个是"导致谁"。
        _ptag = "P(经|本)" if _key == "upstream" else "P(本|经)"
        _lines.append("  %s  节点=%d 最大层=%d%s" % (
            _label, len(_lst), _dp,
            " [撞到深度上限，链未走完]" if (_r.get("summary") or {}).get("truncated_depth") else ""))
        for _n in _lst:
            _lines.append("    %s%s (n=%s %s=%s 经 %s%s%s)%s" % (
                "  " * max(0, int(_n.get("depth") or 1) - 1),
                _n.get("node"), _n.get("count"), _ptag, _n.get("p"),
                _n.get("via_organ"), NODE_SEP, _n.get("via_event"),
                ",闭环" if _n.get("back_edge") else ""))
    _h = _r.get("history") or {}
    if _h.get("hours"):
        _lines.append("  历史旁证：%d 个小时见过（%s）" % (
            len(_h["hours"]), ", ".join(_h["hours"][-3:])))
    return "\n".join(_lines)


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
