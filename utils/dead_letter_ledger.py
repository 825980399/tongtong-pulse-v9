# -*- coding: utf-8 -*-
"""402 刀2：死信三列台账（结构面 / 路由面 / 运行面并列，不合成一条）。

背景
----
烛微 401 修正：三处死信口径**不能合成一条记录**（会丢维度），应合成一张表三列：
  · subscribed  结构面 —— EventBus 有没有订阅方（`has_subscribers`，剔除 `*`/`**`）
  · routed      路由面 —— InfoField 有没有命中条件（`audit_ghost_listeners` 已剔除万能条件）
  · delivered   运行面 —— pulse_tracer 有没有投递（收发计数窗口增量）

为什么要三列
------------
一个事件可以「有订阅方但从未发射」（刀1 的 50 个契约断裂），也可以「发射了但
没人接」（真死信），还可以「有订阅、有发射、路由命中、却没投递」（handler 缺失）。
合成一条"死/未死"会把这三种治不了的病揉成一个数，排查时还得回去翻三处。

落盘与节奏
----------
`data/monitor/dead_letters_YYYYMMDD.jsonl`，每行一条 JSON：
  事件行   `{"ts","kind":"event","event",…,"subscribed","routed","delivered",…}`
  批次行   `{"ts","kind":"tick_summary","counts":{…}}`
每 300s 一批（墙钟自门，不依赖调用频率）；**批次行每批必落**，事件行只在三列
状态相对上一批**发生变化**时落 —— 否则 288 批/天 × 数百事件会把台账写成十几 MB，
反而没人看（这是与任务书「每 5 分钟落一条」的字面差异，理由见交付报告）。

只读设计
--------
本模块不参与业务判断、不改任何计数器、失败即降级（各面独立 try，各自 silent_exc）。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402

SCHEMA = "dead_letter_ledger/402.v1"

#: 落盘节奏（秒）。任务书要求 5 分钟。
INTERVAL_SEC = 300.0

#: 上一次落盘时刻 / 上一批的三列状态（进程内；重启后首批判为「全部变化」）
_LAST_TICK_TS = 0.0
_LAST_STATE: dict = {}


def _monitor_dir(root: str | None = None) -> str:
    return os.path.join(root or _ROOT, "data", "monitor")


def ledger_path(now: float | None = None, root: str | None = None) -> str:
    """当日台账路径：`data/monitor/dead_letters_YYYYMMDD.jsonl`。"""
    _ts = time.localtime(time.time() if now is None else now)
    return os.path.join(_monitor_dir(root),
                        "dead_letters_%s.jsonl" % time.strftime("%Y%m%d", _ts))


# ============================ 1) 事件全集 ============================

def const_events() -> set:
    """`nucleus/const.py` 里声明的全部事件值（*Event 类的 str 常量）。

    用运行时导入而不是读 402 刀1 的 JSON：台账要在没有生成过字典的机器上也能跑。
    导入失败 ⇒ 返回空集（台账退化为"只看运行时观测到的名字"），不抛。
    """
    _out: set = set()
    try:
        import nucleus.const as _c
    except Exception as _e:
        silent_exc(_e, where="dead_letter_ledger.const_events:import", level="debug")
        return _out
    for _name in dir(_c):
        if not _name.endswith("Event"):
            continue
        _cls = getattr(_c, _name, None)
        if not isinstance(_cls, type):
            continue
        for _k, _v in vars(_cls).items():
            if _k.startswith("_"):
                continue
            if isinstance(_v, str) and _v:
                _out.add(_v)
    return _out


# ============================ 2) 三面读数 ============================

def face_subscribed(events) -> dict:
    """结构面：EventBus 是否有订阅方（`*`/`**` 观察者不计）。"""
    _out: dict = {}
    try:
        from nucleus.events.EventBus import has_subscribers
    except Exception as _e:
        silent_exc(_e, where="dead_letter_ledger.face_subscribed:import", level="debug")
        return _out
    for _e_name in events:
        try:
            _out[_e_name] = bool(has_subscribers(_e_name))
        except Exception as _e:
            silent_exc(_e, where="dead_letter_ledger.face_subscribed:%s" % _e_name,
                       level="debug")
            _out[_e_name] = None
    return _out


def face_routed() -> dict:
    """路由面：{event: {"published": int, "has_real_consumer": bool|None}}。

    口径来源 `InfoField.audit_ghost_listeners()`（它已剔除 `*` 万能条件）+
    `event_publish_counts()`（402 刀2 新增的只读口）。
      · published == 0                ⇒ has_real_consumer = None（未发布，判不了）
      · 在 dead_events 名单里          ⇒ False（发了，但没有真消费者命中）
      · 其余                          ⇒ True
    """
    _out: dict = {}
    try:
        from nucleus.field.InfoField import get_info_field
    except Exception as _e:
        silent_exc(_e, where="dead_letter_ledger.face_routed:import", level="debug")
        return _out
    try:
        _field = get_info_field()
    except Exception as _e:
        silent_exc(_e, where="dead_letter_ledger.face_routed:get_info_field", level="debug")
        return _out
    if _field is None:
        return _out
    _counts: dict = {}
    _dead: set = set()
    try:
        _counts = dict(_field.event_publish_counts() or {})
    except Exception as _e:
        silent_exc(_e, where="dead_letter_ledger.face_routed:publish_counts", level="debug")
    try:
        _audit = _field.audit_ghost_listeners() or {}
        _dead = {str(r.get("event_type")) for r in (_audit.get("dead_events") or [])}
    except Exception as _e:
        silent_exc(_e, where="dead_letter_ledger.face_routed:audit", level="debug")
        return {}
    for _name, _n in _counts.items():
        _pub = int(_n or 0)
        _out[_name] = {"published": _pub,
                       "has_real_consumer": (None if _pub <= 0 else (_name not in _dead))}
    return _out


def face_delivered() -> dict:
    """运行面：`pulse_tracer.traffic_counts()` 的累计快照（调用方做差）。"""
    try:
        from utils.pulse_tracer import traffic_counts
        return dict(traffic_counts())
    except Exception as _e:
        silent_exc(_e, where="dead_letter_ledger.face_delivered", level="debug")
        return {"emits": {}, "receives": {}}


def face_dead_letters() -> dict:
    """运行面旁证：`pulse_tracer.dead_letter_stats()`（发布时即无订阅方）。"""
    try:
        from utils.pulse_tracer import dead_letter_stats
        _s = dead_letter_stats() or {}
        return {str(i.get("event")): int(i.get("count") or 0)
                for i in (_s.get("items") or [])}
    except Exception as _e:
        silent_exc(_e, where="dead_letter_ledger.face_dead_letters", level="debug")
        return {}


def _delta(cur: dict, prev: dict) -> dict:
    """按事件名求累计量之差（只保留正增量：计数器被 reset 时不产生负数）。"""
    _out = {}
    for _k, _v in (cur or {}).items():
        _d = int(_v or 0) - int((prev or {}).get(_k, 0) or 0)
        if _d > 0:
            _out[_k] = _d
    return _out


# ============================ 3) 组装一批 ============================

def _safe(fn, where: str, fallback):
    """调一个「面」的读数；失败即降级。

    ★三面必须**各自**降级：InfoField 单例不可用、EventBus 未初始化都只该让
      对应那一列变成"未知"，不该让整批不落盘 —— 否则最需要台账的时刻（某器官崩了）
      恰好是最没有台账的时刻。
    """
    try:
        return fn()
    except Exception as _e:
        silent_exc(_e, where=f"dead_letter_ledger.collect:{where}", level="debug")
        return fallback


def collect(*, window_sec: float = INTERVAL_SEC,
            prev_traffic: dict | None = None,
            root: str | None = None) -> dict:
    """采集一批三面读数。

    Args:
        window_sec: 窗口长度（仅记录在 detail 里，供读方判断口径）
        prev_traffic: 上一批的 `traffic_counts()`；None ⇒ 窗口增量取"累计量"
            （首个别进程无历史，只能给累计，detail 里会标 `baseline:"cumulative"`）

    Returns:
        {"ts","ts_epoch","rows":[{event,subscribed,routed,delivered,...}], "counts":{...}}
    """
    _now = time.time()
    _traffic = _safe(face_delivered, "delivered", {"emits": {}, "receives": {}})
    _recv_win = (_delta(_traffic.get("receives") or {},
                        (prev_traffic or {}).get("receives") or {})
                 if prev_traffic is not None
                 else dict(_traffic.get("receives") or {}))
    _emit_win = (_delta(_traffic.get("emits") or {},
                        (prev_traffic or {}).get("emits") or {})
                 if prev_traffic is not None
                 else dict(_traffic.get("emits") or {}))
    _routed = _safe(face_routed, "routed", {}) or {}
    _dead_letter = _safe(face_dead_letters, "dead_letters", {}) or {}
    _consts = _safe(const_events, "const_events", set()) or set()

    # 事件全集 = 常量声明 ∪ 三面运行时观测到的名字
    _names = set(_consts)
    _names.update(_routed)
    _names.update(_traffic.get("emits") or {})
    _names.update(_traffic.get("receives") or {})
    _names.update(_dead_letter)
    _events = sorted(n for n in _names if n and isinstance(n, str))

    _subs = _safe(lambda: face_subscribed(_events), "subscribed", {}) or {}

    _rows = []
    for _e in _events:
        _rt = _routed.get(_e) or {}
        _sub = _subs.get(_e)
        _rout = _rt.get("has_real_consumer")
        _recv = int(_recv_win.get(_e, 0) or 0)
        _emit = int(_emit_win.get(_e, 0) or 0)
        _dead_axes = []
        if _sub is False:
            _dead_axes.append("subscribed")
        if _rout is False:
            _dead_axes.append("routed")
        if _recv <= 0:
            _dead_axes.append("delivered")
        _rows.append({
            "event": _e,
            "subscribed": _sub,
            "routed": _rout,
            "delivered": _recv > 0,
            # ★「发了却没投递」才是真丢件；窗口内压根没发射的事件只是"未接线"，
            #   不该被 delivered=False 记成投递故障（两者治法完全不同）。
            "lost_in_flight": bool(_emit > 0 and _recv <= 0),
            "dead_axes": _dead_axes,
            "detail": {
                "has_constant": _e in _consts,
                "published_total": int((_rt.get("published") or 0)),
                "emits_window": _emit,
                "receives_window": _recv,
                "dead_letter_total": int(_dead_letter.get(_e, 0) or 0),
                "window_sec": round(float(window_sec), 1),
                "baseline": ("cumulative" if prev_traffic is None else "window"),
            },
        })

    _counts = {
        "events": len(_rows),
        "subscribed_true": sum(1 for r in _rows if r["subscribed"] is True),
        "subscribed_false": sum(1 for r in _rows if r["subscribed"] is False),
        "routed_true": sum(1 for r in _rows if r["routed"] is True),
        "routed_false": sum(1 for r in _rows if r["routed"] is False),
        "routed_unknown": sum(1 for r in _rows if r["routed"] is None),
        "delivered_true": sum(1 for r in _rows if r["delivered"] is True),
        "lost_in_flight": sum(1 for r in _rows if r["lost_in_flight"]),
        "dead_all_three": sum(1 for r in _rows if len(r["dead_axes"]) == 3),
        "no_constant": sum(1 for r in _rows if not r["detail"]["has_constant"]),
        **_traffic.get("meta", {}),
    }
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(_now)),
            "ts_epoch": _now, "rows": _rows, "counts": _counts,
            "traffic_snapshot": _traffic,
            "root": root or _ROOT}


# ============================ 4) 落盘 ============================

def _state_key(row: dict) -> tuple:
    return (row.get("subscribed"), row.get("routed"), row.get("delivered"))


def tick(*, force: bool = False, now: float | None = None,
         path: str | None = None, root: str | None = None,
         include_all: bool = False) -> dict | None:
    """跑一批；墙钟未到 300s 且非 force ⇒ 直接返回 None（零 IO）。

    Returns:
        {"path","written","skipped","counts","ts"} 或 None（未到点）
    """
    global _LAST_TICK_TS, _LAST_STATE
    _now = float(time.time() if now is None else now)
    if not force and (_now - _LAST_TICK_TS) < INTERVAL_SEC:
        return None

    _prev_traffic = None
    if _LAST_STATE.get("traffic") is not None:
        _prev_traffic = _LAST_STATE["traffic"]
    _batch = collect(window_sec=INTERVAL_SEC, prev_traffic=_prev_traffic, root=root)

    _prev = _LAST_STATE.get("rows") or {}
    _lines = []
    _state = {}
    _written = 0
    for _row in _batch["rows"]:
        _state[_row["event"]] = _state_key(_row)
        _changed = include_all or _prev.get(_row["event"]) != _state[_row["event"]]
        if not _changed:
            continue
        _rec = {"ts": _batch["ts"], "ts_epoch": _batch["ts_epoch"],
                "kind": "event", "schema": SCHEMA}
        _rec.update(_row)
        _lines.append(_rec)
        _written += 1

    # ★批次行每批必落：这是「每 5 分钟一条」的硬保证 ——
    #   事件行按状态变化稀疏落盘，若只看事件行，稳定期会看起来"台账停更"。
    _lines.append({"ts": _batch["ts"], "ts_epoch": _batch["ts_epoch"],
                   "kind": "tick_summary", "schema": SCHEMA,
                   "interval_sec": INTERVAL_SEC,
                   "counts": _batch["counts"],
                   "written_events": _written})

    _path = path or ledger_path(_now, root)
    _dir = os.path.dirname(_path)
    if _dir:
        os.makedirs(_dir, exist_ok=True)
    try:
        with io.open(_path, "a", encoding="utf-8", newline="\n") as _f:
            for _rec in _lines:
                _f.write(json.dumps(_rec, ensure_ascii=False, sort_keys=False) + "\n")
    except Exception as _e:
        silent_exc(_e, where=f"dead_letter_ledger.tick:write:{_path}", level="debug")
        return None

    _LAST_TICK_TS = _now
    _LAST_STATE = {"rows": _state, "traffic": _batch["traffic_snapshot"]}
    return {"path": _path, "written": _written, "written_lines": len(_lines),
            "counts": _batch["counts"], "ts": _batch["ts"]}


def tick_from_heartbeat() -> dict | None:
    """生产挂载点：由 `PulseMetricsCollector._on_heartbeat_alive` 每拍调用。"""
    return tick()


def reset_state() -> None:
    """清空进程内节流状态（**仅供测试**；生产运行期不要调）。"""
    global _LAST_TICK_TS, _LAST_STATE
    _LAST_TICK_TS = 0.0
    _LAST_STATE = {}


def read_ledger(path: str, limit: int = 0) -> list:
    """读回台账（供测试与诊断；坏行跳过）。"""
    _out = []
    try:
        with io.open(path, encoding="utf-8") as _f:
            for _ln in _f:
                _ln = _ln.strip()
                if not _ln:
                    continue
                try:
                    _out.append(json.loads(_ln))
                except Exception as _e:
                    silent_exc(_e, where="dead_letter_ledger.read_ledger:json",
                               level="debug")
                    continue
    except OSError as _e:
        silent_exc(_e, where="dead_letter_ledger.read_ledger:open", level="debug")
        return _out
    return _out[-limit:] if limit else _out


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="402 刀2：死信三列台账")
    _ap.add_argument("--once", action="store_true", help="立即落一批（等价 --force）")
    _ap.add_argument("--path", default="", help="自定义输出路径")
    _ap.add_argument("--include-all", action="store_true",
                     help="事件行全量落（不按状态变化稀疏）")
    _ap.add_argument("--json", action="store_true", help="打印本批 JSON")
    _args = _ap.parse_args(argv)
    _r = tick(force=True, path=(_args.path or None), include_all=_args.include_all)
    if _r is None:
        print("[dead-letter] 落盘失败（详见日志）")
        return 1
    if _args.json:
        print(json.dumps(_r, ensure_ascii=False, indent=1))
        return 0
    _c = _r["counts"]
    print("=== 死信三列台账 ===")
    print("  输出            : %s" % _r["path"])
    print("  时间            : %s" % _r["ts"])
    print("  事件总数        : %d" % _c["events"])
    print("  subscribed      : 真 %d / 假 %d"
          % (_c["subscribed_true"], _c["subscribed_false"]))
    print("  routed          : 真 %d / 假 %d / 未知 %d"
          % (_c["routed_true"], _c["routed_false"], _c["routed_unknown"]))
    print("  delivered       : 真 %d（其中发了没投递 %d）"
          % (_c["delivered_true"], _c.get("lost_in_flight", 0)))
    print("  三列全灭        : %d" % _c["dead_all_three"])
    print("  无常量的裸事件  : %d" % _c["no_constant"])
    print("  本批落盘        : %d 条事件行 + 1 条批次行" % _r["written"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
