"""pulse_tracer —— 脉冲链路追踪器 · 全局记录脉冲收发事件

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
修复: 2026-09-10 星轨 — 原子写+频率限制+default类型修复，解决JSON解析刷屏
"""

import os
import threading
import time
from nucleus.data.DataAccessLayer import safe_read_json, safe_write_json

_MAX_EVENTS = 200
_MAX_ORPHANS = 100
_FLUSH_INTERVAL = 2.0  # 写入频率限制：每2秒最多一次，避免竞态条件
_events = []
_lock = threading.Lock()
_output_path = None
_orphan_path = None
_last_flush_time = 0.0

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

def flush_to_file():
    """将当前事件列表写入JSON文件，同时更新孤儿脉冲记录
    
    优化：
    1. 频率限制：每2秒最多写入一次，避免频繁IO和竞态条件
    2. 原子写：使用safe_write_json，避免写入中途被读取导致解析失败
    3. 类型修复：default=[]而不是default={}
    """
    global _last_flush_time
    
    # 频率限制：避免每个脉冲都写文件
    now = time.time()
    if now - _last_flush_time < _FLUSH_INTERVAL:
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
            key = f"{o['timestamp']:.3f}_{o['organ']}_{o['event_type']}"
            if key not in existing_keys:
                existing_orphans.append(o)
                existing_keys.add(key)
        
        # 限制总数
        if len(existing_orphans) > _MAX_ORPHANS:
            existing_orphans = existing_orphans[-_MAX_ORPHANS:]
        
        # 原子写孤儿记录
        safe_write_json(orphan_path, existing_orphans, backup=False)
        
        _last_flush_time = now
    except Exception as e:
        print(f"[WARNING] pulse_tracer.py:152: {type(e).__name__}: {e}")
