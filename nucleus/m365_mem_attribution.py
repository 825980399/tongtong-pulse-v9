# -*- coding: utf-8 -*-
"""★365刀B2：内存增长归属的**同进程**采样接线（只读观测）。

为什么必须同进程
----------------
`gc.get_objects()` 只看得见**本进程**的对象；跨进程拿不到归属。364 的 static 部分
（`tools/memory_attribution_probe.py`）已经能采类型增量/gc 分代/最小二乘斜率/warmup 剔除，
但只能在活体**自己**跑 —— 所以本模块把探针挂进进程内，由命令面拉起，不新增定时器、
不改告警阈值、不动 release338 shadow 与 live 闸。

口径（与 364 A1 一致，不新造判据）
--------------------------------
* 采样一律走 `tools/memory_attribution_probe.sample_once`（单一真源，不抄第二份）；
* `warmup` 内样本标 `in_warmup=True`，**不参与**后段斜率；
* 结论只给 `leak_suspect` / `stable`，不下"泄漏"终论；归属须配合停学对照段人工标注。

边界
----
* 同一时刻只允许一个采样线程（重复 start 返回 `already_running`，不静默覆盖）；
* 采样线程是 daemon，异常**留痕**（写 status().error），绝不静默死掉；
* 产物落 `logs/`（不是 `data/`：观测件不进受管数据面，避免与 write_guard 冲突）。
"""
from __future__ import annotations

import json
import os
import threading
import time

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("m365_mem_attribution")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROBE_PATH = os.path.join(_ROOT, "tools", "memory_attribution_probe.py")

_PROBE = {"mod": None}
_STATE = {
    "thread": None,
    "started_at": None,
    "duration": 0.0,
    "interval": 0.0,
    "warmup": 0.0,
    "top_n": 12,
    "note": "",
    "out": "",
    "pid": None,
    "samples": 0,
    "last": None,
    "finished": False,
    "error": None,
    "stop": False,
}
_LOCK = threading.Lock()
#: 最近一次采样读数（供 status 快速读，不必重算）
_MAX_KEEP = 6


def _load_probe():
    """按路径加载探针模块（tools/ 不是包，不能 `import tools.xxx`）。失败留痕不静默。"""
    if _PROBE.get("mod") is not None:
        return _PROBE["mod"]
    import importlib.util
    if not os.path.isfile(_PROBE_PATH):
        _module_logger.warning("[365刀B2] 探针文件不存在：%s", _PROBE_PATH)
        return None
    try:
        _spec = importlib.util.spec_from_file_location("m365_mem_probe", _PROBE_PATH)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _PROBE["mod"] = _mod
        return _mod
    except Exception as _e:
        silent_exc(_e, where="m365_mem_attribution::_load_probe", level="warning")
        _module_logger.warning("[365刀B2] 探针加载失败 %s: %s", type(_e).__name__, _e)
        return None


def _write_line(_fh, _row: dict) -> None:
    if _fh is None:
        return
    try:
        _fh.write(json.dumps(_row, ensure_ascii=False) + "\n")
        _fh.flush()
    except Exception as _e:
        silent_exc(_e, where="m365_mem_attribution::_write_line", level="warning")


def _loop(duration: float, interval: float, warmup: float, top_n: int,
          note: str, out: str) -> None:
    _mod = _load_probe()
    _fh = None
    if out:
        try:
            _d = os.path.dirname(out)
            if _d:
                os.makedirs(_d, exist_ok=True)
            _fh = open(out, "a", encoding="utf-8")
        except OSError as _e:
            silent_exc(_e, where="m365_mem_attribution::_loop open", level="warning")
            _module_logger.warning("[365刀B2] 采样产物打不开 %s：%s", out, _e)
            _fh = None
    _t0 = time.time()
    _keep: list = []
    try:
        while _mod is not None:
            _s = _mod.sample_once(int(top_n))
            _s["elapsed"] = round(time.time() - _t0, 1)
            _s["in_warmup"] = _s["elapsed"] < float(warmup)
            _s["note"] = note
            _write_line(_fh, _s)
            _keep.append({k: _s.get(k) for k in ("ts", "rss_mb", "obj_total",
                                                 "elapsed", "in_warmup")})
            if len(_keep) > _MAX_KEEP:
                del _keep[:-_MAX_KEEP]
            with _LOCK:
                _STATE["samples"] += 1
                _STATE["last"] = _keep[-1]
            if _s["elapsed"] >= float(duration):
                break
            if _STATE.get("stop"):
                _module_logger.info("[365刀B2] 采样收到停止信号，第 %d 拍退出",
                                    _STATE["samples"])
                break
            time.sleep(max(0.05, float(interval)))
        with _LOCK:
            _STATE["finished"] = True
    except Exception as _e:
        silent_exc(_e, where="m365_mem_attribution::_loop", level="warning")
        with _LOCK:
            _STATE["error"] = "%s: %s" % (type(_e).__name__, _e)
            _STATE["finished"] = True
        _module_logger.warning("[365刀B2] 采样线程异常退出：%s: %s",
                               type(_e).__name__, _e)
    finally:
        if _fh is not None:
            try:
                _fh.close()
            except OSError as _ce:
                _module_logger.debug("[365刀B2] 采样产物关闭失败：%s", _ce)


def start(duration: float = 600.0, interval: float = 30.0, warmup: float = 120.0,
          top_n: int = 12, note: str = "", out: str = "") -> dict:
    """拉起同进程采样线程（默认 10 分钟 / 30 秒一采 / 前 120 秒算 warmup）。"""
    with _LOCK:
        _t = _STATE.get("thread")
        if _t is not None and _t.is_alive():
            return {"ok": False, "why": "already_running",
                    "samples": _STATE["samples"], "pid": _STATE["pid"]}
        if not out:
            out = os.path.join(_ROOT, "logs",
                               "mem_attribution_%d_%s.jsonl"
                               % (os.getpid(), time.strftime("%Y%m%d_%H%M%S")))
        _STATE.update({"started_at": time.time(), "duration": float(duration),
                       "interval": float(interval), "warmup": float(warmup),
                       "top_n": int(top_n), "note": str(note), "out": out,
                       "pid": os.getpid(), "samples": 0, "last": None,
                       "finished": False, "error": None, "stop": False})
        _th = threading.Thread(target=_loop, name="m365_mem_attrib",
                               args=(float(duration), float(interval), float(warmup),
                                     int(top_n), str(note), out),
                               daemon=True)
        _STATE["thread"] = _th
        _th.start()
    _module_logger.info(
        "[365刀B2] 同进程内存归属采样已启动 pid=%s duration=%.0fs interval=%.0fs "
        "warmup=%.0fs top_n=%d note=%s out=%s",
        os.getpid(), duration, interval, warmup, top_n, note or "-", out)
    return {"ok": True, "pid": os.getpid(), "out": out,
            "duration": float(duration), "interval": float(interval),
            "warmup": float(warmup), "top_n": int(top_n), "note": str(note)}


def status() -> dict:
    """读采样状态（只读拷贝；不含线程对象）。"""
    with _LOCK:
        _t = _STATE.get("thread")
        _alive = bool(_t is not None and _t.is_alive())
        return {"running": _alive, "pid": _STATE["pid"],
                "samples": _STATE["samples"], "last": _STATE["last"],
                "finished": _STATE["finished"], "error": _STATE["error"],
                "out": _STATE["out"], "note": _STATE["note"],
                "elapsed": (round(time.time() - _STATE["started_at"], 1)
                            if _STATE["started_at"] else None),
                "duration": _STATE["duration"], "interval": _STATE["interval"],
                "warmup": _STATE["warmup"]}


def stop() -> dict:
    """停止采样（置 finished；线程在下一拍自行退出，不强杀）。"""
    with _LOCK:
        _t = _STATE.get("thread")
        if _t is None or not _t.is_alive():
            return {"ok": False, "why": "not_running", "samples": _STATE["samples"]}
        _STATE["duration"] = 0.0
        _STATE["stop"] = True
        return {"ok": True, "samples": _STATE["samples"], "note": "下一拍退出"}
