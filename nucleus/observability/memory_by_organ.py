# -*- coding: utf-8 -*-
"""器官级内存归因（★411 批刀1）—— **单进程内的诚实近似**。

任务书原意与 T0 实测的分歧（必须先看这一段）
-------------------------------------------
任务书：「每 300 秒采一次**各器官 RSS**，输出 top10」。

**单进程 Python 里"各器官 RSS"不存在。** 所有器官都跑在同一个 `python main.py`
进程里（`pid=36036`，实测 RSS 10.85GB），`psutil.Process().memory_info()` 只能给
**整进程**读数，内核不会按 Python 类/模块拆 RSS。任何声称"某器官占 X MB RSS"的
实现，要么在编，要么只是在测**自己的那点代理量**。

所以本模块给的是**代理指标**，并在产物里写明口径：

  * `rss_mb` —— 整进程 RSS（真实、但**不可分**），`rss_separable=false`；
  * 每个器官的 `objs` —— 该器官**自定义类**的实例数（`gc.get_objects()` +
    `type(o).__module__` 归属）；
  * 每个器官的 `est_mb` —— 这些实例的 `sys.getsizeof` 之和（**浅尺寸**：
    不含引用对象、不含 C 扩展内部分配、不含 `dict/list` 里的数据）。

为什么这个代理仍然有用：**泄漏的形状是"某类实例越来越多"**。
`objs` 的增量比"总 RSS 涨了 2.4GB"可解释得多；能指到器官就已经是进步。

口径纪律（对本仓铁律 276 的遵守）
--------------------------------
* 取不到就是 `None` + `reason`，**绝不填 0**（0 与"没采到"必须可分）；
* 超时/异常 ⇒ `state="partial"|"unavailable"` + `elapsed_ms`，**不静默截断**；
* 采样有硬预算（默认 8000ms）：超预算就停，并如实标 `partial=true`。
  宁可少采几个器官，也不能让 300s 的采样把心跳拖住。

开关：`ENABLE_M411_MEMORY_BY_ORGAN`（缺省 True）。关掉 ⇒ 采样函数直接返回
`{"state": "disabled"}`，调用链零副作用。
"""

from __future__ import annotations

import gc
import io
import json
import os
import sys
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("MemoryByOrgan")

_M411_MO_VERSION = "411.1"

#: 产物目录（相对项目根）
OUT_REL = os.path.join("data", "monitor")

#: 落盘间隔（任务书要求 300 秒一条）
SAMPLE_EVERY_SEC = 300.0

#: 单文件最多保留多少条（24h × 300s = 288；留 1.5 倍余量防漂移）
MAX_RECORDS_PER_FILE = 432

#: 单次采样时间预算（毫秒）——超出即 partial
DEFAULT_BUDGET_MS = 8000

#: 模块前缀 → 器官显示名（顺序敏感：先长后短）
ORGAN_PREFIXES: tuple[tuple[str, str], ...] = (
    ("organs.brain.", "大脑"),
    ("organs.body.", "身体"),
    ("organs.core.", "核心"),
    ("organs.identity.", "身份"),
    ("organs.senses.", "感官"),
    ("organs.limbic.", "边缘"),
    ("organs.", "其他器官"),
    ("nucleus.mnemosyne.", "记忆核"),
    ("nucleus.field.", "信息场"),
    ("nucleus.genesis.", "发生核"),
    ("nucleus.evolution.", "进化核"),
    ("nucleus.reasoning.", "推理核"),
    ("nucleus.self_awareness.", "自省核"),
    ("nucleus.world_model.", "世界模型"),
    ("nucleus.llm.", "语言核"),
    ("nucleus.", "核·其他"),
    ("functions.", "功能层"),
    ("utils.", "工具层"),
    ("tools.", "工具脚本"),
)

#: 不算"器官自定义类"的模块（内置类型、第三方库）——它们的对象数只进总数
_NON_ORGAN_PREFIXES = ("builtins", "__main__", "abc", "typing", "json", "re", "io",
                       "collections", "threading", "asyncio", "numpy", "torch",
                       "faiss", "PIL", "requests", "urllib", "logging")

_DATA_DIR: str | None = None

#: 上次落盘时刻（模块级，跨实例共享——快照器只有一个实例，但工具会另建）
_LAST_WRITE: dict[str, float] = {"ts": 0.0}


def enabled() -> bool:
    """灰度开关。"""
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M411_MEMORY_BY_ORGAN", True))
    except Exception as _e:
        silent_exc(_e, where="observability.memory_by_organ:enabled", level="debug")
        return True


def project_root() -> str:
    """项目根（本文件上溯三层）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def data_root() -> str:
    """数据根（单测可重定向）。"""
    return _DATA_DIR or os.path.join(project_root(), "data")


def set_data_dir(path: str | None) -> None:
    """重定向读写根。"""
    global _DATA_DIR
    _DATA_DIR = path


def organ_of_module(module_name: str) -> str:
    """模块名 → 器官名；不属于任何器官（内置/第三方）⇒ 空串。"""
    _m = str(module_name or "")
    if not _m:
        return ""
    for _p in _NON_ORGAN_PREFIXES:
        if _m == _p or _m.startswith(_p + "."):
            return ""
    for _p, _name in ORGAN_PREFIXES:
        if _m.startswith(_p):
            return _name
    return ""


def sample(rss_mb: float | None = None, budget_ms: int = DEFAULT_BUDGET_MS,
           now: float | None = None, top_n: int = 10) -> dict[str, Any]:
    """采一次器官级对象分布。

    Args:
        rss_mb: 整进程 RSS（由调用方传入；本模块**不自己测**，避免双口径）。
        budget_ms: 时间预算；超时即停并标 partial。
        now: 注入时钟。
        top_n: 返回前 N 名（任务书要求 top10）。

    Returns:
        ``{"state", "reason", "basis", "rss_mb", "rss_separable", "elapsed_ms",
           "objects_total", "objects_attributed", "partial", "organs":[...], "ts"}``
    """
    _t0 = float(now if now is not None else time.time())
    if not enabled():
        return {"state": "disabled", "reason": "ENABLE_M411_MEMORY_BY_ORGAN=False",
                "basis": "", "rss_mb": rss_mb, "rss_separable": False,
                "elapsed_ms": 0, "objects_total": None, "objects_attributed": None,
                "partial": False, "organs": [], "ts": round(_t0, 3)}
    _wall = time.time()
    _budget = max(200, int(budget_ms))
    _objs: list = []
    try:
        _objs = gc.get_objects()
    except Exception as _e:
        silent_exc(_e, where="observability.memory_by_organ:sample:get_objects", level="warning")
        return {"state": "unavailable", "reason": "gc.get_objects_failed:%s" % type(_e).__name__,
                "basis": "gc_object_count", "rss_mb": rss_mb, "rss_separable": False,
                "elapsed_ms": 0, "objects_total": None, "objects_attributed": None,
                "partial": False, "organs": [], "ts": round(_t0, 3)}

    _stat: dict[str, dict[str, float]] = {}
    _total = 0
    _attributed = 0
    _partial = False
    _overflow = 0
    for _o in _objs:
        _total += 1
        if (_total & 0x3FF) == 0 and (time.time() - _wall) * 1000.0 > _budget:
            _partial = True
            break
        try:
            _mod = getattr(type(_o), "__module__", "") or ""
        except Exception as _e:
            silent_exc(_e, where="observability.memory_by_organ:sample:module", level="debug")
            _overflow += 1
            continue
        _organ = organ_of_module(_mod)
        if not _organ:
            continue
        _slot = _stat.setdefault(_organ, {"objs": 0.0, "bytes": 0.0})
        _slot["objs"] += 1
        _attributed += 1
        try:
            _slot["bytes"] += float(sys.getsizeof(_o))
        except Exception as _e:
            # ★浅尺寸取不到不致命：对象数仍然有效，只是字节数偏低
            silent_exc(_e, where="observability.memory_by_organ:sample:getsizeof", level="debug")
            _overflow += 1

    _rows = [{"organ": _k, "objs": int(_v["objs"]),
              "est_mb": round(_v["bytes"] / 1024.0 / 1024.0, 2)}
             for _k, _v in _stat.items()]
    _rows.sort(key=lambda x: (-x["objs"], x["organ"]))
    _total_bytes = sum(_v["bytes"] for _v in _stat.values()) or 1.0
    for _r in _rows:
        _r["pct"] = round(_r["est_mb"] * 1024.0 * 1024.0 / _total_bytes, 4)

    _elapsed = round((time.time() - _wall) * 1000.0, 1)
    return {
        "state": "partial" if _partial else "ok",
        "reason": ("budget_exceeded(%dms)" % _budget) if _partial else "",
        "basis": "gc_object_count+shallow_getsizeof（**不是 RSS**）",
        "rss_mb": rss_mb,
        "rss_separable": False,
        "elapsed_ms": _elapsed,
        "objects_total": _total,
        "objects_attributed": _attributed,
        "partial": _partial,
        "size_errors": _overflow,
        "organs": _rows[:max(1, int(top_n))],
        "ts": round(_t0, 3),
        "note": ("单进程内 RSS 无法按器官拆分；此处 objs=该器官自定义类实例数，"
                 "est_mb=浅尺寸之和（不含引用对象与 C 扩展内部分配）"),
    }


def top3(reading: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """快照里只放前 3 名（任务书要求，避免快照膨胀）。"""
    if not isinstance(reading, dict):
        return []
    return [{"organ": r.get("organ"), "objs": r.get("objs"), "est_mb": r.get("est_mb")}
            for r in (reading.get("organs") or [])[:3]]


def _organ_map() -> dict[str, Any]:
    """取活体器官表（`SelfAwarenessEngine._AWARENESS_ORGANS`）；取不到返回 {}。"""
    try:
        from nucleus.self_awareness.SelfAwarenessEngine import _AWARENESS_ORGANS
        return dict(_AWARENESS_ORGANS or {})
    except Exception as _e:
        silent_exc(_e, where="observability.memory_by_organ:_organ_map", level="debug")
        return {}


def _walk_organ(obj: Any, max_objects: int, deadline: float) -> dict[str, Any]:
    """从单个器官实例出发做**深度受限**对象图遍历。

    为什么必须从实例出发：T0 实测 20,547 个对象里只有 **9 个**能按
    `type(o).__module__` 归到器官（99.96% 是内置 dict/list/str）——
    按类归属等于什么都没测。真正吃内存的是"某个器官持有的容器"，
    所以从 `vars(organ)` 出发、走 2 层引用、按 visited 去重。

    Args:
        obj: 器官实例。
        max_objects: 本次遍历的对象上限（防单个器官吃掉全部预算）。
        deadline: `time.time()` 的绝对截止点。

    Returns:
        ``{"objs", "est_mb", "truncated", "reason"}``
    """
    _seen: set[int] = set()
    _n = 0
    _bytes = 0.0
    _trunc = False
    _budget_hit = False
    _frontier: list[Any] = []
    try:
        _frontier = list(vars(obj).values()) if hasattr(obj, "__dict__") else []
    except Exception as _e:
        silent_exc(_e, where="observability.memory_by_organ:_walk_organ:vars", level="debug")
        return {"objs": 0, "est_mb": 0.0, "truncated": False, "reason": "vars_failed"}

    _depth = 0
    while _frontier and _depth < 2:
        _next: list[Any] = []
        for _v in _frontier:
            if _n >= max_objects:
                _trunc = True
                break
            if (time.time() > deadline):
                _budget_hit = True
                break
            _vid = id(_v)
            if _vid in _seen:
                continue
            _seen.add(_vid)
            _n += 1
            try:
                _bytes += float(sys.getsizeof(_v))
            except Exception as _e:
                silent_exc(_e, where="observability.memory_by_organ:_walk_organ:size", level="debug")
            # 只对容器/实例继续下钻，字符串/数字不再展开（它们没有子对象）
            if isinstance(_v, (dict, list, tuple, set, frozenset)) or hasattr(_v, "__dict__"):
                try:
                    _next.extend(gc.get_referents(_v))
                except Exception as _e:
                    silent_exc(_e, where="observability.memory_by_organ:_walk_organ:refs",
                               level="debug")
        _frontier = _next
        _depth += 1
        if _trunc or _budget_hit:
            break
    return {"objs": _n, "est_mb": round(_bytes / 1024.0 / 1024.0, 2),
            "truncated": _trunc or _budget_hit,
            "reason": ("object_cap(%d)" % max_objects) if _trunc
                      else ("budget" if _budget_hit else "")}


def sample_organs(organ_map: dict[str, Any] | None = None,
                  rss_mb: float | None = None, budget_ms: int = DEFAULT_BUDGET_MS,
                  per_organ_cap: int = 20000, now: float | None = None,
                  top_n: int = 10) -> dict[str, Any]:
    """按**器官实例**做内存归因（本模块的主口径）。

    Returns:
        与 :func:`sample` 同形；`basis="organ_object_graph(depth<=2, shallow)"`；
        `coverage` = 已遍历对象占 `gc` 总对象数（诚实说明采样覆盖）。
    """
    _t0 = float(now if now is not None else time.time())
    if not enabled():
        return {"state": "disabled", "reason": "ENABLE_M411_MEMORY_BY_ORGAN=False",
                "basis": "", "rss_mb": rss_mb, "rss_separable": False,
                "elapsed_ms": 0, "objects_total": None, "objects_attributed": None,
                "partial": False, "organs": [], "ts": round(_t0, 3)}
    _map = organ_map if isinstance(organ_map, dict) and organ_map else _organ_map()
    if not _map:
        # 器官表拿不到 ⇒ 如实降级到类型归因，并说明为什么
        _r = sample(rss_mb=rss_mb, budget_ms=budget_ms, now=now, top_n=top_n)
        _r["reason"] = _r.get("reason") or "organ_map_unavailable→fallback_types"
        _r["mode"] = "types"
        return _r

    _wall = time.time()
    _deadline = _wall + max(0.2, int(budget_ms) / 1000.0)
    _rows: list[dict[str, Any]] = []
    _attrs = 0
    _partial = False
    for _name, _organ in list(_map.items()):
        if _organ is None or isinstance(_organ, (str, int, float, bool)):
            continue
        if time.time() > _deadline:
            _partial = True
            break
        _w = _walk_organ(_organ, max_objects=per_organ_cap, deadline=_deadline)
        _attrs += 1
        if _w["objs"] <= 0:
            continue
        _rows.append({"organ": str(_name), "objs": int(_w["objs"]),
                      "est_mb": float(_w["est_mb"]),
                      "truncated": bool(_w["truncated"]),
                      "reason": _w["reason"]})
    _rows.sort(key=lambda x: (-x["est_mb"], -x["objs"]))
    _sum_bytes = sum(r["est_mb"] for r in _rows) or 1.0
    for _r in _rows:
        _r["pct"] = round(_r["est_mb"] / _sum_bytes, 4)
    try:
        _gtotal: int | None = len(gc.get_objects())
    except Exception as _e:
        silent_exc(_e, where="observability.memory_by_organ:sample_organs:gtotal", level="debug")
        _gtotal = None
    _objs_sum = sum(r["objs"] for r in _rows)
    return {
        "state": "partial" if _partial else "ok",
        "reason": ("budget_exceeded(%dms)" % int(budget_ms)) if _partial else "",
        "mode": "organs",
        "basis": "organ_object_graph(depth<=2, shallow_getsizeof)（**不是 RSS**）",
        "rss_mb": rss_mb,
        "rss_separable": False,
        "elapsed_ms": round((time.time() - _wall) * 1000.0, 1),
        "objects_total": _gtotal,
        "objects_attributed": _objs_sum,
        "coverage": (round(_objs_sum / _gtotal, 4) if _gtotal else None),
        "organs_seen": _attrs,
        "partial": _partial,
        "organs": _rows[:max(1, int(top_n))],
        "ts": round(_t0, 3),
        "note": ("单进程内 RSS 无法按器官拆分；objs/est_mb 是**从器官实例出发、"
                 "深度≤2 的对象图**浅尺寸和（同一个对象只算一次）"),
    }


def day_path(now: float | None = None) -> str:
    """当日产物路径 `data/monitor/memory_by_organ_YYYYMMDD.json`。"""
    _t = time.localtime(now if now is not None else time.time())
    return os.path.join(data_root(), "monitor",
                        "memory_by_organ_%s.json" % time.strftime("%Y%m%d", _t))


def append_record(reading: dict[str, Any], now: float | None = None,
                  force: bool = False, sample_every_sec: float = SAMPLE_EVERY_SEC) -> dict[str, Any]:
    """把一次读数追加到当日文件（**按 300s 节流**，原子写）。

    Returns:
        ``{"ok", "path", "written", "reason", "records"}``
    """
    _t = float(now if now is not None else time.time())
    if not enabled():
        return {"ok": False, "path": day_path(_t), "written": False,
                "reason": "disabled", "records": None}
    if not isinstance(reading, dict) or reading.get("state") in ("unavailable", "disabled"):
        return {"ok": False, "path": day_path(_t), "written": False,
                "reason": "reading_not_usable(%s)" % str((reading or {}).get("state")),
                "records": None}
    if not force and (_t - float(_LAST_WRITE.get("ts") or 0.0)) < float(sample_every_sec):
        return {"ok": True, "path": day_path(_t), "written": False,
                "reason": "throttled(<%.0fs)" % float(sample_every_sec),
                "records": None}

    _p = day_path(_t)
    _doc: dict[str, Any] = {"version": _M411_MO_VERSION, "date": time.strftime("%Y%m%d", time.localtime(_t)),
                            "sample_every_sec": sample_every_sec,
                            "basis": reading.get("basis", ""),
                            "records": []}
    if os.path.exists(_p):
        try:
            _old = json.loads(io.open(_p, encoding="utf-8").read())
            if isinstance(_old, dict) and isinstance(_old.get("records"), list):
                _doc["records"] = _old["records"]
        except Exception as _e:
            # 读坏不阻断采样：**但要把旧的挪开**，别静默覆盖（数据面纪律）
            silent_exc(_e, where="observability.memory_by_organ:append_record:read",
                       level="warning")
            try:
                os.replace(_p, _p + ".broken411")
            except Exception as _e2:
                silent_exc(_e2, where="observability.memory_by_organ:append_record:quarantine",
                           level="debug")
    _doc["records"].append(reading)
    if len(_doc["records"]) > MAX_RECORDS_PER_FILE:
        _doc["records"] = _doc["records"][-MAX_RECORDS_PER_FILE:]
        _doc["truncated"] = True
    _tmp = _p + ".tmp411"
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with io.open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(_doc, _f, ensure_ascii=False, indent=1)
        os.replace(_tmp, _p)
        _LAST_WRITE["ts"] = _t
        return {"ok": True, "path": _p, "written": True, "reason": "",
                "records": len(_doc["records"])}
    except Exception as _e:
        silent_exc(_e, where="observability.memory_by_organ:append_record:write", level="warning")
        return {"ok": False, "path": _p, "written": False,
                "reason": "write_failed:%s" % type(_e).__name__, "records": None}


def version() -> str:
    """契约版本。"""
    return _M411_MO_VERSION
