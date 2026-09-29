"""pulse_tracer —— 脉冲链路追踪器 · 全局记录脉冲收发事件

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
修复: 2026-09-10 星轨 — 原子写+频率限制+default类型修复，解决JSON解析刷屏
"""

import atexit
import os
import threading
import time
from nucleus.data.DataAccessLayer import safe_read_json, safe_write_json
from nucleus._silent_except import silent_exc

_MAX_EVENTS = 200
_MAX_ORPHANS = 100
_FLUSH_INTERVAL = 2.0  # 写入频率限制：每2秒最多一次，避免竞态条件
_events = []
_lock = threading.Lock()
_output_path = None
_orphan_path = None
_last_flush_time = 0.0
# ★第117批 T-117a（方案A）：占闸专用锁。刻意与 _events 的 _lock 分开，
#   避免「拿事件快照」与「抢刷闸」互相排队（原实现的 convoy 正是锁串联造成）。
_gate_lock = threading.Lock()
# ★第117批 T-117a（方案B）：后台守护刷。
#   红线：★禁止在 import 期起线程/做 IO —— 只能由 log_emit/log_receive 懒启动。
_flusher_thread = None
_flusher_started = False
_flusher_lock = threading.Lock()
_flusher_stop = threading.Event()
_FLUSHER_TICK = 0.5  # 后台刷巡检间隔（秒）；真闸仍是 _FLUSH_INTERVAL=2.0

def _get_output_path():
    global _output_path
    if _output_path is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _output_path = os.path.join(base, "data", "monitor", "pulse_trace_events.json")
    return _output_path

def _get_orphan_path():
    global _orphan_path
    if _orphan_path is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _orphan_path = os.path.join(base, "data", "monitor", "pulse_orphans.json")
    return _orphan_path

def log_emit(organ_name, event_type, layer, pulse_id=""):
    """记录脉冲发射事件"""
    event = {
        "timestamp": time.time(),
        "type": "emit",
        "organ": organ_name,
        "event_type": event_type,
        "layer": layer,
        "pulse_id": pulse_id,
        "matched_organs": []
    }
    with _lock:
        _events.append(event)
        if len(_events) > _MAX_EVENTS:
            _events.pop(0)
    _ensure_flusher()  # ★T-117a 方案B：运行期懒启动（import 期绝不起线程）

def log_receive(organ_name, event_type, source_organ):
    """记录脉冲接收事件"""
    with _lock:
        for evt in reversed(_events):
            if evt.get("type") == "emit" and evt.get("event_type") == event_type:
                if organ_name not in evt["matched_organs"]:
                    evt["matched_organs"].append(organ_name)
                break
        _events.append({
            "timestamp": time.time(),
            "type": "receive",
            "organ": organ_name,
            "event_type": event_type,
            "from": source_organ,
        })
        if len(_events) > _MAX_EVENTS:
            _events.pop(0)
    _ensure_flusher()  # ★T-117a 方案B：运行期懒启动（import 期绝不起线程）

def _detect_orphans(events, min_age=1.0):
    """检测孤儿脉冲：发射超过min_age秒且无接收的emit事件"""
    orphans = []
    now = time.time()
    for evt in events:
        if evt.get("type") != "emit":
            continue
        if (now - evt["timestamp"]) < min_age:
            continue
        if not evt.get("matched_organs"):
            orphans.append({
                "timestamp": evt["timestamp"],
                "organ": evt["organ"],
                "event_type": evt["event_type"],
                "layer": evt.get("layer", ""),
                "pulse_id": evt.get("pulse_id", ""),
                "error": "孤儿脉冲: 发射后无器官接收"
            })
    return orphans

def _claim_flush_gate() -> bool:
    """★第117批 T-117a（方案A）：原子占闸 —— 判过的瞬间即占位。

    原实现是**头判尾更**：本函数开头判「距上次 >= _FLUSH_INTERVAL？」、
    却在函数**末尾**（完成两轮 safe_write_json 之后）才更新 _last_flush_time。
    闸非原子 => 突发窗内 k 个并发者全部判「该我刷」，k 份近乎同内容的重复写
    去排同一把 per-path 锁 = **写放大 k×**，且无一是必要 IO（烛微实测 19:35 块
    参与者 11 个线程，等待者=器官接收线程，卡在业务之前 → 拖死器官主循环）。

    改为「判 + 更」在同一把 _gate_lock 内一次完成：一个 2s 窗口内恒定只有 1 个
    写者能穿越，写放大 11× → 1×。
    """
    global _last_flush_time
    with _gate_lock:
        _now = time.time()
        if _now - _last_flush_time < _FLUSH_INTERVAL:
            return False
        _last_flush_time = _now  # 判完立刻占闸
        return True


def _flusher_loop():
    """★第117批 T-117a（方案B）：后台守护刷循环。

    器官主循环（BasePulseOrgan 的 emit/handle）不再承担同步 IO；
    真正的写盘发生在这里，频率仍受 _claim_flush_gate 的 2s 闸约束。
    """
    while not _flusher_stop.is_set():
        _flusher_stop.wait(_FLUSHER_TICK)
        if _flusher_stop.is_set():
            break
        if not _events:
            continue  # 无事件则不做无谓 IO
        try:
            flush_to_file()
        except Exception as _fe:
            print(f"[WARNING] pulse_tracer.flusher: "
                  f"{type(_fe).__name__}: {_fe}")


def _ensure_flusher():
    """★第117批 T-117a（方案B）：首次记录事件时懒启动后台刷线程。

    ★红线：本函数**只能**由 log_emit / log_receive 在运行期调用；
    模块 import 期绝不起线程、绝不做 IO（潜意识 :466 案反复验证的教训）。
    """
    global _flusher_thread, _flusher_started
    if _flusher_started:
        return
    with _flusher_lock:
        if _flusher_started:
            return
        _flusher_started = True
        _flusher_thread = threading.Thread(
            target=_flusher_loop, name="PulseTracerFlusher", daemon=True)
        _flusher_thread.start()


def _flush_at_exit():
    """进程退出前终刷（atexit）—— 绕过 2s 闸，但不为无事件做无谓 IO。"""
    try:
        if not _events:
            return
        _flusher_stop.set()
        flush_to_file(force=True)
    except Exception as e:
        silent_exc(e, "utils/pulse_tracer.py:169:退出终刷异常", level="warning")


atexit.register(_flush_at_exit)


def flush_to_file(force: bool = False):
    """将当前事件列表写入JSON文件，同时更新孤儿脉冲记录
    
    优化：
    1. 频率限制：每2秒最多写入一次，避免频繁IO和竞态条件
    2. 原子写：使用safe_write_json，避免写入中途被读取导致解析失败
    3. 类型修复：default=[]而不是default={}
    """
    # ★第117批 T-117a（方案A）：原「本处判、函数尾更」的头判尾更已改为原子占闸，
    #   详见 _claim_flush_gate 的注释。force=True（atexit 终刷）绕过 2s 闸。
    if not force and not _claim_flush_gate():
        return
    
    try:
        path = _get_output_path()
        orphan_path = _get_orphan_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        with _lock:
            events_snapshot = list(_events)
        
        # 写入实时事件（原子写）
        safe_write_json(path, events_snapshot[-150:], backup=False)
        
        # 检测孤儿脉冲
        new_orphans = _detect_orphans(events_snapshot, min_age=1.5)
        
        # 读取已有孤儿记录，合并去重（修复：default=[]而不是{}）
        existing_orphans = []
        if os.path.exists(orphan_path):
            try:
                existing_orphans = safe_read_json(orphan_path, default=[])
                if not isinstance(existing_orphans, list):
                    existing_orphans = []
            except Exception as e:
                print(f"[WARNING] pulse_tracer.py:128: {type(e).__name__}: {e}")
                existing_orphans = []
        
        # 去重：基于 timestamp + organ + event_type
        existing_keys = set()
        for o in existing_orphans:
            if isinstance(o, dict):
                key = f"{o.get('timestamp',0):.3f}_{o.get('organ','')}_{o.get('event_type','')}"
                existing_keys.add(key)
        
        for o in new_orphans:
            if not isinstance(o, dict):
                continue
            key = f"{o['timestamp']:.3f}_{o['organ']}_{o['event_type']}"
            if key not in existing_keys:
                existing_orphans.append(o)
                existing_keys.add(key)
        
        # 限制总数
        if len(existing_orphans) > _MAX_ORPHANS:
            existing_orphans = existing_orphans[-_MAX_ORPHANS:]
        
        # 原子写孤儿记录
        safe_write_json(orphan_path, existing_orphans, backup=False)
        # ★第117批 T-117a：此处的 _last_flush_time 更新已删除（头判尾更的根因），
        #   占位统一在 _claim_flush_gate 内「判完即占」完成。
    except Exception as e:
        print(f"[WARNING] pulse_tracer.py:152: {type(e).__name__}: {e}")
