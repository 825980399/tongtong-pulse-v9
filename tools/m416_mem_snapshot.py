# -*- coding: utf-8 -*-
"""第416批刀2：内存快照 / 对比工具（**纯只读**）。

为什么需要它（11-01 实测复盘）
------------------------------
`data/monitor/health_snapshot.json` 里 `memory_by_organ` 给出：

    objects_total = 1,357,641    objects_attributed = 7,956    coverage = 0.0059
    top3 = [{"organ":"肝","objs":3415,"est_mb":0.81}, ...]

即「按器官对象图浅尺寸归因」只覆盖 **0.59%** 的对象、最大 organ 也只有 0.81MB，
而同一时刻 `process_rss_mb = 28706.1`。⇒ 28GB 里有 27.9GB 归不到任何器官
（铁律 14：对象图浅尺寸这个方法本身就不成立，不是"没算仔细"）。

所以本工具换口径：**盯「会被整份读进内存的落盘对象」的尺寸 + 进程 RSS**，
两次采样做差，把 RSS 涨量拆成「这些对象涨了多少」与「没归因的残差」。
这是外部进程也能做的归因（不必在框架进程内），因此可以拿来做验收判据。

用法
----
    python tools/m416_mem_snapshot.py --take --out tmp/mem_a.json
    python tools/m416_mem_snapshot.py --take --out tmp/mem_b.json
    python tools/m416_mem_snapshot.py --diff tmp/mem_a.json tmp/mem_b.json
    python tools/m416_mem_snapshot.py --watch --minutes 10 --interval 30 --out tmp/mem_w.json

口径声明（不填 0）
------------------
* 文件/目录不存在 ⇒ `{"state": "missing", "mb": null}`（**不是 0.0**，铁律 276）；
* 读数取不到（无 psutil 等）⇒ 顶层字段 `null`，并在 `state` 里写明原因；
* `unattributed_mb = rss_delta - measured_delta`，可以为负（对象缩了），
  负值代表"内存涨了但不是这些对象涨的"以外的另一种情况，照实报。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from typing import Any

# 与 `tools/m415_deferred_verify.py` 同口径：把仓根放进 sys.path，
# 这样独立跑（`python tools/m416_mem_snapshot.py`）也能 import 到 `nucleus`。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus._silent_except import silent_exc  # noqa: E402

MB = 1024.0 * 1024.0

#: 默认盯的落盘对象（名字 -> 相对仓根路径；目录也行）
DEFAULT_PATHS = (
    ("knowledge_snapshot", "data/knowledge/pulse_knowledge_snapshot.json"),
    ("knowledge_dir", "data/knowledge"),
    ("call_graph", "data/call_graph"),
    ("experience_pool", "data/experience/experience_pool.json"),
    ("experience_dir", "data/experience"),
    ("narrative", "data/narrative"),
    ("tm_baselines", "data/reports/memory"),
    ("monitor_dir", "data/monitor"),
    ("reports_dir", "data/reports"),
    ("vector_dir", "data/vector"),
    ("models_dir", "data/models"),
    ("snapshot_cache", "data"),
)

#: 健康快照里要抄的计数字段（key -> 取值路径，用 / 分隔）
HEALTH_KEYS = (
    ("gc_count", "gc/count"),
    ("threads", "resource_probe/threads"),
    ("handles", "resource_probe/handles"),
    ("objects_total", "memory_by_organ/objects_total"),
    ("objects_attributed", "memory_by_organ/objects_attributed"),
    ("organ_est_mb", "memory_by_organ/top3"),
    ("proc_gen", "proc_gen"),
    ("beat_count", "beat_count"),
)


# ---------------------------------------------------------------- 基础读数

def _size_mb(path: str) -> dict:
    """单文件或目录的字节数（目录递归求和）。缺失 ⇒ state=missing + mb=None。"""
    if not os.path.exists(path):
        return {"state": "missing", "mb": None, "files": 0, "bytes": None}
    try:
        if os.path.isfile(path):
            _b = os.path.getsize(path)
            return {"state": "ok", "mb": round(_b / MB, 2), "files": 1, "bytes": _b}
        _tot = 0
        _n = 0
        for _dp, _dn, _fn in os.walk(path):
            _dn[:] = [d for d in _dn if d != "__pycache__" and not d.startswith(".")]
            for _f in _fn:
                try:
                    _tot += os.path.getsize(os.path.join(_dp, _f))
                    _n += 1
                except OSError as _e1:
                    # 单文件读不到（被占用/刚删）不影响总量口径，但要留痕
                    silent_exc(_e1, where="tools.m416_mem_snapshot._size_mb.walk",
                               level="debug")
                    continue
        return {"state": "ok", "mb": round(_tot / MB, 2), "files": _n, "bytes": _tot}
    except OSError as _e:
        silent_exc(_e, where="tools.m416_mem_snapshot._size_mb", level="debug")
        return {"state": "error:%s" % type(_e).__name__, "mb": None,
                "files": 0, "bytes": None}


def _dig(obj: Any, path: str) -> Any:
    """按 `a/b/c` 取值；中间缺任一环返回 None（不抛）。"""
    _cur = obj
    for _k in str(path).split("/"):
        if not isinstance(_cur, dict) or _k not in _cur:
            return None
        _cur = _cur[_k]
    return _cur


def _find_engine_pid() -> int | None:
    """找「跑 main.py 的那个 python 进程」pid（找不到 ⇒ None，不猜）。"""
    try:
        import psutil
    except ImportError as _e_imp:
        # 没有 psutil ⇒ 明确标 unavailable（调用方据 state 判 FAIL，不当 0）
        silent_exc(_e_imp, where="tools.m416_mem_snapshot._find_engine_pid.import",
                   level="warning")
        return None
    _me = os.getpid()
    for _p in psutil.process_iter(["pid", "cmdline"]):
        try:
            _info = _p.info
            _cmd = " ".join(_info.get("cmdline") or [])
            if _info.get("pid") == _me:
                continue
            if "main.py" in _cmd and "python" in _cmd.lower():
                return int(_info["pid"])
        except Exception:  # noqa: BLE001
            silent_exc(Exception("proc_info_unreadable"),
                       where="tools.m416_mem_snapshot._find_engine_pid",
                       level="debug")
            continue
    return None


def _proc_rss(pid: int | str | None = None) -> dict:
    """目标进程 RSS（MB）+ 系统占用百分比。

    ★416 实测教训：默认必须能**看别人**。本工具是外部采样器，
      `os.getpid()` 拿到的是采样器自己的 RSS（几十 MB），拿它当框架读数
      就是「量错了对象」——10 分钟涨幅会恒为 0，看着像"修好了"（假绿）。

    Args:
        pid: 目标 pid；`"auto"` ⇒ 自动找跑 main.py 的进程；None ⇒ 本进程。
    """
    try:
        import psutil
    except ImportError as _e:
        silent_exc(_e, where="tools.m416_mem_snapshot._proc_rss.import", level="warning")
        return {"state": "unavailable:no_psutil", "rss_mb": None,
                "system_percent": None, "pid": None, "_err": type(_e).__name__}
    _target = pid
    if _target == "auto":
        _target = _find_engine_pid()
        if _target is None:
            return {"state": "unavailable:engine_not_found", "rss_mb": None,
                    "system_percent": None, "pid": None}
    try:
        _vm = psutil.virtual_memory()
        _p = (psutil.Process(int(_target)) if _target is not None
              else psutil.Process(os.getpid()))
        return {"state": "ok", "rss_mb": round(_p.memory_info().rss / MB, 2),
                "system_percent": round(float(_vm.percent), 1),
                "pid": int(_p.pid)}
    except Exception as _e:  # noqa: BLE001
        silent_exc(_e, where="tools.m416_mem_snapshot._proc_rss", level="debug")
        return {"state": "error:%s" % type(_e).__name__, "rss_mb": None,
                "system_percent": None, "pid": (None if _target is None
                                                else int(_target))}


def _read_health(root: str) -> dict:
    """抄 `data/monitor/health_snapshot.json` 里的计数字段（读不到 ⇒ 标 missing）。"""
    _p = os.path.join(root, "data", "monitor", "health_snapshot.json")
    if not os.path.isfile(_p):
        return {"state": "missing"}
    try:
        with io.open(_p, encoding="utf-8") as _f:
            _o = json.load(_f)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="tools.m416_mem_snapshot._read_health", level="warning")
        return {"state": "unreadable:%s" % type(_e).__name__}
    _out: dict[str, Any] = {"state": "ok", "at": _o.get("timestamp")}
    for _k, _path in HEALTH_KEYS:
        _out[_k] = _dig(_o, _path)
    return _out


# ---------------------------------------------------------------- 快照 / 差分

def collect(root: str | None = None, paths=None, health: dict | None = None,
            now: float | None = None, pid=None) -> dict:
    """打一份快照。

    Args:
        root: 仓根；None ⇒ 从本文件向上两级。
        paths: [(name, rel_or_abs)]；None ⇒ `DEFAULT_PATHS`。
        health: 健康快照替身（单测注入）；None ⇒ 真读盘。
        now: 采样时刻（单测注入）。
        pid: 要测的进程；`"auto"` ⇒ 自动找 main.py；None ⇒ 本进程。
    """
    _root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _paths = list(paths if paths is not None else DEFAULT_PATHS)
    _objs: dict[str, dict] = {}
    for _name, _rel in _paths:
        _abs = _rel if os.path.isabs(_rel) else os.path.join(_root, _rel)
        _objs[str(_name)] = _size_mb(_abs)
    _pr = _proc_rss(pid)
    return {
        "at": float(now if now is not None else time.time()),
        "at_text": time.strftime("%Y-%m-%d %H:%M:%S",
                                 time.localtime(float(now if now is not None
                                                      else time.time()))),
        "root": _root,
        "rss_mb": _pr.get("rss_mb"),
        "system_percent": _pr.get("system_percent"),
        "rss_state": _pr.get("state"),
        "target_pid": _pr.get("pid"),
        "objects": _objs,
        "health": (health if health is not None else _read_health(_root)),
    }


def diff(a: dict, b: dict, top: int = 12) -> dict:
    """两份快照做差。

    Returns:
        ``{"rss_delta_mb", "items": [{name, delta_mb, mb_a, mb_b}], "measured_delta_mb",
           "unattributed_mb", "verdict", "state"}``

    `state` 三态，不用 0 冒充：
        * `ok` —— 两份都能读
        * `unavailable` —— 任一份缺 rss ⇒ 无法算差值
        * `partial` —— rss 有，但有对象读数缺失（差分只覆盖取到的那些）
    """
    _rss_a = (a or {}).get("rss_mb")
    _rss_b = (b or {}).get("rss_mb")
    _items: list[dict] = []
    _meas = 0.0
    _missing: list[str] = []
    _oa = (a or {}).get("objects") or {}
    _ob = (b or {}).get("objects") or {}
    for _name, _va in _oa.items():
        _vb = _ob.get(_name)
        if not isinstance(_va, dict) or not isinstance(_vb, dict):
            continue
        _ma, _mb_ = _va.get("mb"), _vb.get("mb")
        if _ma is None or _mb_ is None:
            _missing.append(_name)
            continue
        _d = round(float(_mb_) - float(_ma), 2)
        _meas += _d
        _items.append({"name": _name, "delta_mb": _d,
                       "mb_a": _ma, "mb_b": _mb_})
    _items.sort(key=lambda x: -abs(x["delta_mb"]))
    _out: dict[str, Any] = {
        "state": "ok",
        "seconds": round(float((b or {}).get("at", 0)) - float((a or {}).get("at", 0)), 1),
        "rss_delta_mb": None,
        "measured_delta_mb": round(_meas, 2),
        "unattributed_mb": None,
        "items": _items[:max(1, int(top))],
        "items_all": len(_items),
        "missing_objects": _missing,
        "verdict": "",
    }
    if _rss_a is None or _rss_b is None:
        _out["state"] = "unavailable"
        _out["verdict"] = "任一份缺 rss ⇒ 算不了差值（不填 0）"
        return _out
    if _missing:
        _out["state"] = "partial"
    _rd = round(float(_rss_b) - float(_rss_a), 2)
    _out["rss_delta_mb"] = _rd
    _out["unattributed_mb"] = round(_rd - _meas, 2)
    _top = _items[0] if _items else None
    if _top and _top["delta_mb"] > 0:
        _out["verdict"] = ("RSS %+.1f MB，其中已钉对象 %+.1f MB（最大 %s %+.1f MB），"
                           "未归因 %+.1f MB"
                           % (_rd, _meas, _top["name"], _top["delta_mb"],
                              _out["unattributed_mb"]))
    else:
        _out["verdict"] = ("RSS %+.1f MB，已钉对象合计 %+.1f MB，未归因 %+.1f MB"
                           % (_rd, _meas, _out["unattributed_mb"]))
    return _out


def watch(minutes: float = 10.0, interval: float = 30.0, root: str | None = None,
          paths=None, sleep=None, pid=None) -> dict:
    """连续采样 `minutes` 分钟，返回 `{"samples": [...], "diff": 首末差}`。"""
    _sleep = sleep or time.sleep
    _n = max(1, int(round(float(minutes) * 60.0 / max(1.0, float(interval)))))
    _samples = []
    for _i in range(_n + 1):
        _samples.append(collect(root=root, paths=paths, pid=pid))
        if _i < _n:
            _sleep(float(interval))
    return {"samples": _samples,
            "n": len(_samples),
            "interval_sec": float(interval),
            "diff": diff(_samples[0], _samples[-1])}


# ---------------------------------------------------------------- CLI

def _main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="416 内存快照/对比（纯只读）")
    _ap.add_argument("--take", action="store_true", help="打一份快照")
    _ap.add_argument("--out", default="", help="快照/采样输出文件")
    _ap.add_argument("--diff", nargs=2, metavar=("A", "B"), help="比对两份快照")
    _ap.add_argument("--watch", action="store_true", help="连续采样")
    _ap.add_argument("--minutes", type=float, default=10.0)
    _ap.add_argument("--interval", type=float, default=30.0)
    _ap.add_argument("--json", action="store_true", help="只输出 JSON")
    _ap.add_argument("--pid", default=None,
                     help="要测的进程 pid；auto=自动找跑 main.py 的进程；缺省=本进程")
    _ns = _ap.parse_args(argv)

    def _dump(obj):
        if _ns.out:
            _d = os.path.dirname(os.path.abspath(_ns.out))
            if _d and not os.path.isdir(_d):
                os.makedirs(_d, exist_ok=True)
            with io.open(_ns.out, "w", encoding="utf-8", newline="\n") as _f:
                _f.write(json.dumps(obj, ensure_ascii=False, indent=1))
        print(json.dumps(obj, ensure_ascii=False, indent=1))

    if _ns.diff:
        _a = json.load(io.open(_ns.diff[0], encoding="utf-8"))
        _b = json.load(io.open(_ns.diff[1], encoding="utf-8"))
        _d = diff(_a, _b)
        _dump(_d)
        return 0 if _d["state"] != "unavailable" else 2
    if _ns.watch:
        _w = watch(minutes=_ns.minutes, interval=_ns.interval, pid=_ns.pid)
        if _ns.json:
            _dump(_w)
        else:
            print("采样 %d 次 / 间隔 %.0fs" % (_w["n"], _w["interval_sec"]))
            for _s in _w["samples"]:
                print("  %s  rss=%sMB  sys=%s%%"
                      % (_s["at_text"], _s["rss_mb"], _s["system_percent"]))
            print("首末差：", json.dumps(_w["diff"], ensure_ascii=False)[:600])
            if _ns.out:
                _dump(_w)
        return 0
    _s = collect(pid=_ns.pid)
    _dump(_s)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
