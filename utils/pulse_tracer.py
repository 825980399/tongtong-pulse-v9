"""pulse_tracer —— 脉冲链路追踪器 · 全局记录脉冲收发事件

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
修复: 2026-09-10 星轨 — 原子写+频率限制+default类型修复，解决JSON解析刷屏
"""

import atexit
import json
import os
import logging

logger = logging.getLogger("pulse.tracer")
import threading
import time
from datetime import datetime

from nucleus._silent_except import silent_exc
from nucleus.data.DataAccessLayer import safe_read_json, safe_write_json
from nucleus.const import EnergyEvent  # 204批·刀4：报告即终点白名单（常量属性访问）

# 204批·刀4：报告即终点白名单（快照类事件本就'报告即终点'，不参与孤儿检测）
_REPORT_ENDPOINT_WHITELIST = {
    EnergyEvent.METABOLISM_SNAPSHOT,  # 代谢快照族（孤儿榜首）
}


# ★第201批 刀1（环窗参数化）：以下三值是「config 键缺失/非法时的回退默认值」，
#   取值 = 改造前实测值 → 默认零行为变化；扩容请调 config.py 对应三键。
_MAX_EVENTS = 200           # 事件环容量（槽） ← PULSE_TRACER_EVENT_RING_SIZE
_MAX_ORPHANS = 100          # 孤儿上限（条）   ← PULSE_TRACER_MAX_ORPHANS
#   ★偏差D#2：任务书称判定窗「1s」，实测生产路径 flush_to_file 原传 1.5s
#     （_detect_orphans 形参默认才是 1.0s）→ 判定窗回退默认取 1.5，零行为变化。
_DEFAULT_MIN_AGE_SEC = 1.5  # 判定窗（秒）     ← PULSE_TRACER_MIN_AGE_SEC
_FLUSH_INTERVAL = 2.0  # 写入频率限制：每2秒最多一次，避免竞态条件
_events = []
_lock = threading.Lock()

# 207批·刀1：target_organ 标注制填充率观测（仅 ENABLE_TARGET_ORGAN_FIELD 时计数，生死闸）
_TO_FILL_RATE_EVERY = 500   # 每 N 次 emit 打一行填充率 INFO
_TO_EMIT_TOTAL = 0
_TO_EMIT_FILLED = 0

# 207批·刀3：成对流落盘（STDP 换源）观测态全局
_EVENT_SEQ = 0                 # 事件单调递增序号（落盘去重用）
_LAST_PAIR_SEQ = -1            # 已落盘的最后一笔事件序号（哨兵，首笔 seq=0 亦落盘）
_LAST_PAIR_FLUSH_TS = 0.0      # 上次成对流强制 flush 时间戳（10min 双保险）
_PAIR_FILE_CAP = 20000         # pulse_pairs.jsonl 行数上限（扩到 2 万量级）
_PAIR_FLUSH_EVERY_SEC = 60.0    # 1 分钟强制 flush 双保险（210批·刀2：环容调大后配套收紧）
_pair_path = None
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

# ★第200批 刀1：判定撤销机制基础设施
#   订阅方提供器：给定 event_type 返回 True/False/None（None=未知，维持原判定）。
#   默认提供器惰性查询 EventBus 订阅表（旁路遥测面，ENABLE_EVENT_BUS=False 时无生产
#   接线 → 对运行路径零副作用）。命中订阅即平反该 emit（压掉 B 路异构发射假阳性）。
_subscription_provider = None


def set_subscription_provider(fn):
    """注入订阅方提供器（测试可注入假提供器；None 恢复默认）。

    提供器签名：``fn(event_type: str) -> bool | None``。
    返回 True 表示「存在订阅方」→ 该 emit 在判定撤销环节被平反（不算孤儿）。
    """
    global _subscription_provider
    _subscription_provider = fn


# ---- ★第201批 刀1：环/窗 参数化读取（防观测面吞噬）----
# 背景：类脑改造（加密脉冲流）会加剧「事件环 + 判定窗」竞争 → 假孤儿爆发、
#       观测面被新脉冲淹没（烛微200前置§3.1）。故三值改为 config 可配置。
# 纪律：
#   · 惰性 import config —— 本模块红线「禁止 import 期起线程/做 IO」，而 config
#     导入会读 data/config_override.json，故绝不在模块顶层引入；
#   · 键缺失/非数值/非法值一律回退默认（绝不抛）—— 故**不用 try/except**，
#     改以 isinstance 守卫实现（铁律150：新 handler 须无 except 或含 LOG_FUNCS）。
def _cfg_positive_int(name, default):
    """读 config 正整型配置；键缺失/布尔/非 int/<=0 → 回退 default。"""
    import config
    _v = getattr(config, name, default)
    if isinstance(_v, bool) or not isinstance(_v, int):
        return default
    return _v if _v > 0 else default


def _cfg_nonneg_float(name, default):
    """读 config 非负数值配置；键缺失/布尔/非数值/<0 → 回退 default。"""
    import config
    _v = getattr(config, name, default)
    if isinstance(_v, bool) or not isinstance(_v, (int, float)):
        return default
    _f = float(_v)
    return _f if _f >= 0 else default


def _ring_size():
    """事件环容量（槽）← PULSE_TRACER_EVENT_RING_SIZE，回退 _MAX_EVENTS。"""
    return _cfg_positive_int("PULSE_TRACER_EVENT_RING_SIZE", _MAX_EVENTS)


def _max_orphans():
    """孤儿记录上限（条）← PULSE_TRACER_MAX_ORPHANS，回退 _MAX_ORPHANS。"""
    return _cfg_positive_int("PULSE_TRACER_MAX_ORPHANS", _MAX_ORPHANS)


def _min_age_sec():
    """孤儿判定窗（秒）← PULSE_TRACER_MIN_AGE_SEC，回退 _DEFAULT_MIN_AGE_SEC。"""
    return _cfg_nonneg_float("PULSE_TRACER_MIN_AGE_SEC", _DEFAULT_MIN_AGE_SEC)

def _target_organ_field_enabled():
    """读 config 布尔开关 ENABLE_TARGET_ORGAN_FIELD（207批·刀1）；缺省 False。"""
    import config
    return bool(getattr(config, "ENABLE_TARGET_ORGAN_FIELD", False))


def _pulse_pair_dump_enabled():
    """读 config 布尔开关 ENABLE_PULSE_PAIR_DUMP（207批·刀3）；缺省 False。"""
    import config
    return bool(getattr(config, "ENABLE_PULSE_PAIR_DUMP", False))


def _get_pair_path():
    """207批·刀3 原单文件 pulse_pairs.jsonl；212批·刀1 改为按日分文件轮转。

    返回 data/monitor/pulse_pairs_YYYYMMDD.jsonl（每日首次 flush 创建新文件，同日追加）。
    不缓存路径：跨日（跨午夜）调用须重算日期，故每次实时拼装当日文件名。
    """
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    day = datetime.now().strftime("%Y%m%d")
    return os.path.join(base, "data", "monitor", "pulse_pairs_%s.jsonl" % day)


def _dump_pairs():
    """207批·刀3：将环内未落盘的 emit/receive 事件追加至当日 pulse_pairs_YYYYMMDD.jsonl。

    212批·刀1：路径改为按日分文件（见 _get_pair_path），每日首次 flush 创建新文件、同日追加。
    按事件序号增量落盘（避免重复）；行含 pulse_id/type/organ/event_type/ts。
    超过 _PAIR_FILE_CAP 行时回写为最近 _PAIR_FILE_CAP 行（观测采样，非全量）。
    仅 ENABLE_PULSE_PAIR_DUMP=True 时由 flush_to_file 调用（默认关，零 IO）。
    """
    global _LAST_PAIR_SEQ
    with _lock:
        _pending = [e for e in _events if e.get("seq", 0) > _LAST_PAIR_SEQ]
        if not _pending:
            return
        _rows = []
        for e in _pending:
            _LAST_PAIR_SEQ = max(_LAST_PAIR_SEQ, e.get("seq", 0))
            _rows.append({
                "pulse_id": e.get("pulse_id", ""),
                "type": e.get("type", ""),
                "organ": e.get("organ", ""),
                "event_type": e.get("event_type", ""),
                "ts": e.get("timestamp", 0.0),
                "seq": e.get("seq", 0),
                "target_organ": e.get("target_organ", ""),
                "amplitude": e.get("amplitude", None),
                "from": e.get("from", ""),
                "matched_organs": e.get("matched_organs", []),
            })
    try:
        _path = _get_pair_path()
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        with open(_path, "a", encoding="utf-8") as _pf:
            for _r in _rows:
                _pf.write(json.dumps(_r, ensure_ascii=False))
                _pf.write("\n")
        # 超容回写（仅在序号跨过整倍数时偶发执行，控制频率）
        if _LAST_PAIR_SEQ > _PAIR_FILE_CAP:
            _trim_pair_file(_path)
    except Exception as e:
        print(f"[WARNING] pulse_tracer._dump_pairs: {type(e).__name__}: {e}")


def _trim_pair_file(path):
    """当日 pulse_pairs_YYYYMMDD.jsonl 超容时回写为最近 _PAIR_FILE_CAP 行（观测采样）。"""
    try:
        with open(path, "r", encoding="utf-8") as _f:
            _lines = _f.readlines()
        if len(_lines) <= _PAIR_FILE_CAP:
            return
        _keep = _lines[-_PAIR_FILE_CAP:]
        _tmp = path + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            _f.writelines(_keep)
        os.replace(_tmp, path)
    except Exception as e:
        print(f"[WARNING] pulse_tracer._trim_pair_file: {type(e).__name__}: {e}")


def _default_bus_sub_provider(event_type):
    """默认订阅方提供器：惰性查询 EventBus 订阅表（旁路遥测面）。

    返回 True=存在匹配订阅（精确或通配）；False=无订阅；None=查询失败（维持原判定）。
    对任意异常静默降级为 None，绝不扰动运行路径。
    """
    try:
        from nucleus.events.orphan_receivers import register_growth_need_detected_receiver
        try:
            register_growth_need_detected_receiver()
        except Exception as _re:
            silent_exc(_re, "utils/pulse_tracer.py:_default_bus_sub_provider:注册确保失败",
                       level="debug")
        from nucleus.events.EventBus import _match, get_event_bus
        bus = get_event_bus()
        if bus.subscription_count("**") > 0:
            return True
        if bus.subscription_count(event_type) > 0:
            return True
        try:
            return any(_match(s.pattern, event_type) for s in bus._subs.values())
        except Exception as _we:
            silent_exc(_we, "utils/pulse_tracer.py:_default_bus_sub_provider:通配匹配失败", level="debug")
            return False
    except Exception as e:
        silent_exc(e, "utils/pulse_tracer.py:_default_bus_sub_provider", level="debug")
        return None


# 启用默认提供器（★200刀1：判定撤销机制默认生效；查询失败自动降级为原判定）
_subscription_provider = _default_bus_sub_provider


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

def log_emit(organ_name, event_type, layer, pulse_id="", amplitude=None, target_organ=""):
    """记录脉冲发射事件（207批·刀1：新增 target_organ 语义标注记录 + 填充率生死闸）"""
    global _TO_EMIT_TOTAL, _TO_EMIT_FILLED, _EVENT_SEQ
    # ★第222批·刀1：int-3幽灵追踪——target_organ 必须为 str，非 str 视为幽灵来源并告警+归一
    if not isinstance(target_organ, str):
        logger.warning(
            "pulse_tracer.log_emit: target_organ 非 str 类型=%.80r（organ=%s event=%s→疑似幽灵源，已归一为空串）",
            target_organ, organ_name, event_type)
        target_organ = ""
    event = {
        "timestamp": time.time(),
        "type": "emit",
        "organ": organ_name,
        "event_type": event_type,
        "layer": layer,
        "pulse_id": pulse_id,
        "amplitude": amplitude,
        "target_organ": target_organ,
        "matched_organs": []
    }
    with _lock:
        event["seq"] = _EVENT_SEQ
        _EVENT_SEQ += 1
        _events.append(event)
        if len(_events) > _ring_size():
            _events.pop(0)
        # 207批·刀1：仅当字段启用时统计填充率（否则恒空无意义，生死闸）
        if _target_organ_field_enabled():
            _TO_EMIT_TOTAL += 1
            if target_organ:
                _TO_EMIT_FILLED += 1
            if _TO_EMIT_TOTAL % _TO_FILL_RATE_EVERY == 0:
                _ratio = (_TO_EMIT_FILLED / _TO_EMIT_TOTAL) * 100.0
                logger.info("pulse_tracer.target_organ 填充率: 窗口=%d 非空=%d 占比=%.1f%%",
                            _TO_EMIT_TOTAL, _TO_EMIT_FILLED, _ratio)
    _ensure_flusher()  # ★T-117a 方案B：运行期懒启动（import 期绝不起线程）

def log_receive(organ_name, event_type, source_organ, pulse_id=""):
    """记录脉冲接收事件（207批·刀3：补 pulse_id 直连，配对从启发式变精确）。

    - pulse_id 非空时按 pulse_id 精确回填对应 emit 的 matched_organs（替代事件类型
      反扫启发式）；为空时维持原有事件类型启发式（向后兼容）。
    - 接收记录直记 pulse_id，供 STDP 成对流落盘精确配对。
    """
    with _lock:
        global _EVENT_SEQ
        # 207批·刀3：优先按 pulse_id 精确匹配 emit；失败回退事件类型启发式
        if pulse_id:
            _hit = False
            for evt in reversed(_events):
                if evt.get("type") == "emit" and evt.get("pulse_id") == pulse_id:
                    if organ_name not in evt["matched_organs"]:
                        evt["matched_organs"].append(organ_name)
                    _hit = True
                    break
            if not _hit:
                for evt in reversed(_events):
                    if evt.get("type") == "emit" and evt.get("event_type") == event_type:
                        if organ_name not in evt["matched_organs"]:
                            evt["matched_organs"].append(organ_name)
                        break
        else:
            for evt in reversed(_events):
                if evt.get("type") == "emit" and evt.get("event_type") == event_type:
                    if organ_name not in evt["matched_organs"]:
                        evt["matched_organs"].append(organ_name)
                    break
        _evt = {
            "timestamp": time.time(),
            "type": "receive",
            "organ": organ_name,
            "event_type": event_type,
            "from": source_organ,
            "pulse_id": pulse_id,
        }
        _evt["seq"] = _EVENT_SEQ
        _EVENT_SEQ += 1
        _events.append(_evt)
        if len(_events) > _ring_size():
            _events.pop(0)
    _ensure_flusher()  # ★T-117a 方案B：运行期懒启动（import 期绝不起线程）

def _detect_orphans(events, min_age=None, subscription_provider=None):
    """检测孤儿脉冲：发射超过min_age秒且无接收的emit事件。

    ★第200批 刀1（判定撤销机制）：新增 subscription_provider 形参。
    若提供器对某 event_type 返回 True（存在订阅方 → 旁路遥测面已登记接收方），
    则该 emit 视为"判定面伪孤儿"予以平反（压掉 B 路异构发射假阳性），不再计入孤儿。
    提供器返回 None（未知/查询失败）时维持原判定（保守）。

    ★第201批 刀1（环窗参数化）：min_age 改为 None 哨兵 —— 未显式传入时取
    ``_min_age_sec()``（config PULSE_TRACER_MIN_AGE_SEC，默认 1.5s，与原生产路径一致）。
    判定用 ``is None`` 而非真值判断：min_age=0.0 是合法值（零窗仍须生效）。
    """
    if min_age is None:
        min_age = _min_age_sec()
    orphans = []
    now = time.time()
    for evt in events:
        if evt.get("type") != "emit":
            continue
        if (now - evt["timestamp"]) < min_age:
            continue
        if evt.get("matched_organs"):
            # 迟到 receive 已平反（log_receive 在匹配时回填 matched_organs）
            continue
        # ★200刀1：订阅表兜底 —— 存在订阅方则平反
        if subscription_provider is not None:
            try:
                _has_sub = subscription_provider(evt["event_type"])
            except Exception as _pe:
                silent_exc(_pe, "utils/pulse_tracer.py:_detect_orphans:提供器异常", level="debug")
                _has_sub = None
            if _has_sub is True:
                continue
        # ★204批·刀4：报告即终点白名单 —— 快照类事件本就'报告即终点'
        if evt.get("event_type") in _REPORT_ENDPOINT_WHITELIST:
            continue
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
    # 207批·刀3：成对流落盘双保险（10min 强制 flush，绕过 2s 闸）
    if _pulse_pair_dump_enabled():
        global _LAST_PAIR_FLUSH_TS
        _now = time.time()
        if _now - _LAST_PAIR_FLUSH_TS >= _PAIR_FLUSH_EVERY_SEC:
            _dump_pairs()
            _LAST_PAIR_FLUSH_TS = _now
    if not force and not _claim_flush_gate():
        return
    
    try:
        path = _get_output_path()
        orphan_path = _get_orphan_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # 207批·刀3：成对流随 2s 闸落盘（增量、去重、超容回写）
        if _pulse_pair_dump_enabled():
            _dump_pairs()
        
        with _lock:
            events_snapshot = list(_events)
        
        # 写入实时事件（原子写）
        safe_write_json(path, events_snapshot[-150:], backup=False)
        
        # 检测孤儿脉冲（★200刀1：传入订阅方提供器做判定撤销兜底）
        #   ★201刀1：判定窗不再硬编码 1.5，改由 _detect_orphans 取 config
        #     PULSE_TRACER_MIN_AGE_SEC（默认 1.5 → 零行为变化）
        new_orphans = _detect_orphans(
            events_snapshot, subscription_provider=_subscription_provider)
        
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
        
        # 限制总数（★201刀1：上限改由 config PULSE_TRACER_MAX_ORPHANS 驱动）
        _orphan_cap = _max_orphans()
        if len(existing_orphans) > _orphan_cap:
            existing_orphans = existing_orphans[-_orphan_cap:]
        
        # 原子写孤儿记录
        safe_write_json(orphan_path, existing_orphans, backup=False)
        # ★第117批 T-117a：此处的 _last_flush_time 更新已删除（头判尾更的根因），
        #   占位统一在 _claim_flush_gate 内「判完即占」完成。
    except Exception as e:
        print(f"[WARNING] pulse_tracer.py:152: {type(e).__name__}: {e}")



