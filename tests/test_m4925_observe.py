"""492.5 批观测纠偏补丁单测。

每刀独立开关，覆盖 on/off 两种行为：
- 492.5-1：ark 主对话 HTTP 错误接入统一账本（复用 491-3 开关）
- 492.5-2：event_ledger 加 instance_id + 窗口增量 + 幽灵告警连续3窗口去抖
- 492.5-3：证据 request_id 真实query哈希 + 空query(<5字)跳过
- 492.5-4：层观测 flush 日上限1000行
- 492.5-5：内存告警稳态斜率符号修正 + exhausted 的 next_revive_at 写"下月15号"
- 492.5-6：publish_ms/lock_wait_ms 落 pulse_pairs + 嘴节点补 record
"""

import types

import pytest


@pytest.fixture
def cfg():
    import config as _cfg
    _saved = {}
    _keys = [
        "ENABLE_M491_CHANNEL_STATE_PERSIST",
        "ENABLE_M4925_LEDGER_INSTANCE_ID",
        "ENABLE_M4925_EVIDENCE_REQUEST_ID_FIX",
        "ENABLE_M4925_LAYER_OBSERVE_THROTTLE",
        "ENABLE_M4925_MEM_LEAK_SIGN_FIX",
        "ENABLE_M4925_CHANNEL_NEXT_REVIVE",
        "ENABLE_M4925_PUBLISH_TIMING_TRACE",
        "ENABLE_M490_EVIDENCE_TRACE_STATS",
        "ENABLE_M486_DIALOG_TRACE",
    ]
    for _k in _keys:
        _saved[_k] = getattr(_cfg, _k, None)
    yield _cfg
    for _k, _v in _saved.items():
        if _v is None:
            if hasattr(_cfg, _k):
                delattr(_cfg, _k)
        else:
            setattr(_cfg, _k, _v)


# ───────────────────────── 492.5-1 ark 主对话漏网 ─────────────────────────

def _raise403():
    import urllib.error
    raise urllib.error.HTTPError("http://x", 403, "Forbidden", {}, None)


def _build_probe_lung(monkeypatch):
    import organs.body.PulseLung as PL
    import nucleus.llm.adapter_registry as AR
    import nucleus.ssrf_guard as SG

    class _Ad:
        def build_request(self, model, messages, **kw):
            return {"model": model}

        def build_headers(self, key):
            return {"Authorization": "x"}

    class _Reg:
        def get(self, t, default=None):
            return _Ad()

    monkeypatch.setattr(AR, "get_adapter_registry", lambda: _Reg())
    monkeypatch.setattr(SG, "is_safe_http_url", lambda u: (True, ""))
    monkeypatch.setattr(PL.PulseLung, "_resolve_channel_timeout",
                        lambda self, ch, cfg: 10)
    return PL.PulseLung("4925-probe")


def test_4925_1_ark_http_error_accounted(cfg, monkeypatch):
    """主对话 403 漏网：开关开时 _call_channel 必须调 mark_http_error。"""
    import urllib.request
    import nucleus.llm.channel_state_store as css
    cfg.ENABLE_M4925_UNIFIED_CHANNEL_HEALTH_ALL_PATH = True

    _calls = []
    _orig = css.mark_http_error

    def _fake_mark(name, code, etype):
        _calls.append((name, code, etype))
        return _orig(name, code, etype)

    monkeypatch.setattr(css, "mark_http_error", _fake_mark)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _raise403())

    _lung = _build_probe_lung(monkeypatch)
    _ch = {"name": "ark-4925-test", "api_url": "http://x",
           "api_key": "k", "model": "m"}
    _lung._call_channel(_ch, "hello")
    assert _calls == [("ark-4925-test", 403, "quota_exhausted")]


def test_4925_1_off_not_accounted(cfg, monkeypatch):
    """开关关时 _call_channel 不调 mark_http_error（逐字回改前）。"""
    import urllib.request
    import nucleus.llm.channel_state_store as css
    cfg.ENABLE_M4925_UNIFIED_CHANNEL_HEALTH_ALL_PATH = False

    _calls = []
    monkeypatch.setattr(css, "mark_http_error",
                        lambda n, c, e: _calls.append((n, c, e)))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _raise403())

    _lung = _build_probe_lung(monkeypatch)
    _ch = {"name": "ark-4925-off", "api_url": "http://x",
           "api_key": "k", "model": "m"}
    _lung._call_channel(_ch, "hello")
    assert _calls == []


def test_4925_1_channel_client_path_accounted(cfg, monkeypatch):
    """进化/协作/controller_refine 路径：channel_client.call_channel 在 HTTP 失败上报。"""
    import urllib.request
    import nucleus.reasoning.channel_client as CC
    import nucleus.llm.channel_state_store as css
    import nucleus.llm.adapter_registry as AR
    import nucleus.ssrf_guard as SG
    cfg.ENABLE_M491_CHANNEL_STATE_PERSIST = True
    cfg.ENABLE_M4925_UNIFIED_CHANNEL_HEALTH_ALL_PATH = True

    _calls = []
    monkeypatch.setattr(css, "mark_http_error",
                        lambda n, c, e: _calls.append((n, c, e)))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _raise403())

    class _Ad:
        def build_request(self, model, messages, **kw):
            return {"model": model}

        def build_headers(self, key):
            return {"Authorization": "x"}

    class _Reg:
        def get(self, t, default=None):
            return _Ad()

    monkeypatch.setattr(AR, "get_adapter_registry", lambda: _Reg())
    monkeypatch.setattr(SG, "is_safe_http_url", lambda u: (True, ""))

    _ch = {"name": "ark-4925-cc", "api_url": "http://x",
           "model": "m", "adapter": "openai_compatible"}
    CC.call_channel(_ch, "hi", api_key="k")
    assert _calls and _calls[0][0] == "ark-4925-cc" and _calls[0][1] == 403


def test_4925_1_tie_break_by_name_hash(cfg, monkeypatch):
    """开关开：score 并列按渠道名哈希固定次序（确定性，不随传入顺序变）；关则回退。"""
    import nucleus.llm.channel_balance as CB
    cfg.ENABLE_M4925_UNIFIED_CHANNEL_HEALTH_ALL_PATH = True
    monkeypatch.setattr(CB, "channel_load",
                        lambda name, **k: {"score": 5.0, "headroom": 1.0,
                                           "success": 1.0, "remaining": 1.0})
    _a, _ = CB.order_by_load([{"name": "ark"}, {"name": "zhipu-47-flash"}])
    _b, _ = CB.order_by_load([{"name": "zhipu-47-flash"}, {"name": "ark"}])
    _oa = [x.get("name") for x in _a]
    _ob = [x.get("name") for x in _b]
    assert _oa == _ob, (_oa, _ob)
    # 关开关：回到按原下标（随传入顺序变）
    cfg.ENABLE_M4925_UNIFIED_CHANNEL_HEALTH_ALL_PATH = False
    _a2, _ = CB.order_by_load([{"name": "ark"}, {"name": "zhipu-47-flash"}])
    _b2, _ = CB.order_by_load([{"name": "zhipu-47-flash"}, {"name": "ark"}])
    _oa2 = [x.get("name") for x in _a2]
    _ob2 = [x.get("name") for x in _b2]
    assert _oa2 != _ob2, (_oa2, _ob2)


# ───────────────────────── 492.5-2 event_ledger ─────────────────────────

def test_4925_2_instance_id_format():
    import os
    import nucleus.field.InfoField as IF
    _id = IF._m488_instance_id()
    assert str(os.getpid()) in _id
    assert "#" in _id


def test_4925_2_switch_default(cfg):
    cfg.ENABLE_M4925_LEDGER_INSTANCE_ID = False
    import nucleus.field.InfoField as IF
    assert IF._m4925_ledger_on() is False


def test_4925_2_on_write_instance_id_and_delta(cfg, monkeypatch):
    """开关开：落盘记录带 instance_id + 窗口增量（emitted_delta 等）。"""
    import json
    import nucleus.field.InfoField as IF
    from nucleus.data import write_guard as wg

    cfg.ENABLE_M4925_LEDGER_INSTANCE_ID = True
    monkeypatch.setattr(IF, "_m4925_ledger_on", lambda: True)
    monkeypatch.setattr(IF, "_m488_instance_id", lambda: "INST#1#2")
    monkeypatch.setattr(wg, "guard_write", lambda *a, **k: True)

    _writes = []

    def _fake_open(path, mode="r", **kw):
        if "event_ledger" in str(path) and "a" in mode:
            class _F:
                def write(self, s):
                    _writes.append(s)

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False
            return _F()
        return open(path, mode, **kw)

    monkeypatch.setattr("builtins.open", _fake_open)

    import threading
    _obj = types.SimpleNamespace(
        _event_matched_count={"E1": 5},
        _event_handled_count={"E1": 3},
        _event_dropped={},
        _event_publish_count={"E1": 5},
        _m488_last_written={},
        _m488_ghost_streak={},
        _m488_lock=threading.Lock(),
    )
    _obj._m488_compute_ledger = lambda: IF.InfoField._m488_compute_ledger(_obj)
    IF.InfoField._m488_write_ledger_file(_obj)

    assert len(_writes) == 1
    _row = json.loads(_writes[0])
    assert _row["instance_id"] == "INST#1#2"
    assert _row["ledger"]["E1"]["emitted_delta"] == 5
    assert _row["ledger"]["E1"]["matched_delta"] == 5


def test_4925_2_off_write_verbatim(cfg, monkeypatch):
    """开关关：落盘记录无 instance_id、无增量字段（逐字回改前）。"""
    import json
    import nucleus.field.InfoField as IF
    from nucleus.data import write_guard as wg

    cfg.ENABLE_M4925_LEDGER_INSTANCE_ID = False
    monkeypatch.setattr(wg, "guard_write", lambda *a, **k: True)

    _writes = []

    def _fake_open(path, mode="r", **kw):
        if "event_ledger" in str(path) and "a" in mode:
            class _F:
                def write(self, s):
                    _writes.append(s)

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False
            return _F()
        return open(path, mode, **kw)

    monkeypatch.setattr("builtins.open", _fake_open)

    import threading
    _obj = types.SimpleNamespace(
        _event_matched_count={"E1": 5},
        _event_handled_count={"E1": 3},
        _event_dropped={},
        _event_publish_count={"E1": 5},
        _m488_last_written={},
        _m488_ghost_streak={},
        _m488_lock=threading.Lock(),
    )
    _obj._m488_compute_ledger = lambda: IF.InfoField._m488_compute_ledger(_obj)
    IF.InfoField._m488_write_ledger_file(_obj)

    assert len(_writes) == 1
    _row = json.loads(_writes[0])
    assert "instance_id" not in _row
    assert "emitted_delta" not in _row["ledger"]["E1"]


def test_4925_2_ghost_dedup_needs_3_windows(cfg, monkeypatch):
    """开关开：幽灵丢失需连续3窗口才告警（去抖）。"""
    import nucleus.field.InfoField as IF
    from nucleus.data import write_guard as wg

    cfg.ENABLE_M4925_LEDGER_INSTANCE_ID = True
    monkeypatch.setattr(IF, "_m4925_ledger_on", lambda: True)
    monkeypatch.setattr(IF, "_m488_instance_id", lambda: "X")
    monkeypatch.setattr(wg, "guard_write", lambda *a, **k: True)
    monkeypatch.setattr(IF, "_module_logger", types.SimpleNamespace(
        warning=lambda *a, **k: _warns.append(a[0])))

    _warns = []
    import threading
    _obj = types.SimpleNamespace(
        _event_matched_count={"E1": 10},
        _event_handled_count={"E1": 3},
        _event_dropped={},
        _event_publish_count={"E1": 10},
        _m488_last_written={},
        _m488_ghost_streak={},
        _m488_lock=threading.Lock(),
    )
    _obj._m488_compute_ledger = lambda: IF.InfoField._m488_compute_ledger(_obj)
    for _ in range(2):
        IF.InfoField._m488_write_ledger_file(_obj)
    assert len(_warns) == 0  # 连续不足3窗口不报
    IF.InfoField._m488_write_ledger_file(_obj)
    assert len(_warns) == 1  # 第3窗口才报


# ───────────────────────── 492.5-3 证据 request_id ─────────────────────────

def test_4925_3_empty_query_skipped_on(cfg):
    """开关开：空/过短 query 不记账（返回 None）。"""
    import nucleus.m490_diag as M
    cfg.ENABLE_M490_EVIDENCE_TRACE_STATS = True
    cfg.ENABLE_M4925_EVIDENCE_REQUEST_ID_FIX = True
    assert M.note_first_gate("", 0, 0, [], []) is None
    assert M.note_first_gate("ab", 0, 0, [], []) is None
    assert M.note_first_gate("真实有效用户问题", 0, 0, [], []) is not None


def test_4925_3_empty_query_recorded_off(cfg):
    """开关关：空 query 仍记账（逐字回改前行为）。"""
    import nucleus.m490_diag as M
    cfg.ENABLE_M490_EVIDENCE_TRACE_STATS = True
    cfg.ENABLE_M4925_EVIDENCE_REQUEST_ID_FIX = False
    assert M.note_first_gate("", 0, 0, [], []) is not None


def test_4925_3_real_query_hash_not_empty_constant(cfg):
    """开关开：真实 query 哈希 ≠ md5('') 常量，避免塌缩成同一 id。"""
    import hashlib
    import nucleus.m490_diag as M
    cfg.ENABLE_M490_EVIDENCE_TRACE_STATS = True
    cfg.ENABLE_M4925_EVIDENCE_REQUEST_ID_FIX = True
    _row = M.note_first_gate("用户问今天北京天气怎么样", 0, 0, [], [])
    _empty_const = hashlib.md5(b"").hexdigest()[:16]
    assert _row["request_id"] != _empty_const
    assert _row["request_id"] == hashlib.md5("用户问今天北京天气怎么样".encode(
        "utf-8")).hexdigest()[:16]


def test_4925_3_second_gate_real_query_passthrough(cfg):
    """第二道闸透传真实 query：build_injection 加 query 形参并下传。"""
    import inspect
    import nucleus.evidence_injection as EI
    _src = inspect.getsource(EI.build_injection)
    assert "query: str = \"\"" in _src
    _src2 = inspect.getsource(EI._m490_flush_second)
    assert "query: str = \"\"" in _src2
    assert "query=query" in _src2


# ───────────────────────── 492.5-4 层观测日上限 ─────────────────────────

def test_4925_4_daily_cap_on(cfg, monkeypatch):
    """开关开：flush 最多写 1000 行 + 1 条汇总，超量丢弃。"""
    import nucleus.m490_diag as M

    cfg.ENABLE_M4925_LAYER_OBSERVE_THROTTLE = True
    monkeypatch.setattr(M, "_m4925_layer_throttle_on", lambda: True)
    _written = []
    monkeypatch.setattr(M, "append_jsonl", lambda prefix, row: _written.append(row))
    # 重置模块级计数器
    M._layer_written_count = 0
    M._layer_written_day = ""
    _saved = dict(M._layer_counts)
    try:
        M._layer_counts.clear()
        for _i in range(2000):
            M._layer_counts[("O", f"EV{_i}", "L1", "L2")] = 1
        M._flush_layer_snapshot()
    finally:
        M._layer_counts.clear()
        M._layer_counts.update(_saved)
    assert len(_written) == 1001
    assert _written[-1].get("_summary") is True
    assert _written[-1]["dropped"] == 1000


def test_4925_4_no_cap_off(cfg, monkeypatch):
    """开关关：flush 全量写出（逐字回改前，无上限）。"""
    import nucleus.m490_diag as M

    cfg.ENABLE_M4925_LAYER_OBSERVE_THROTTLE = False
    monkeypatch.setattr(M, "_m4925_layer_throttle_on", lambda: False)
    _written = []
    monkeypatch.setattr(M, "append_jsonl", lambda prefix, row: _written.append(row))
    M._layer_written_count = 0
    M._layer_written_day = ""
    _saved = dict(M._layer_counts)
    try:
        M._layer_counts.clear()
        for _i in range(50):
            M._layer_counts[("O", f"EV{_i}", "L1", "L2")] = 1
        M._flush_layer_snapshot()
    finally:
        M._layer_counts.clear()
        M._layer_counts.update(_saved)
    assert len(_written) == 50


# ───────────────────────── 492.5-5 内存符号 + 复活时间 ─────────────────────────

def test_4925_5_sign_fix_in_source():
    """内存告警符号修正：源码含 (_s if _sign_fix else abs(_s)) 且受开关门控。"""
    _src = open("nucleus/runtime_metrics.py", encoding="utf-8").read()
    assert "(_s if _sign_fix else abs(_s))" in _src
    assert '_sign_fix = bool(getattr(_c449, "ENABLE_M4925_MEM_LEAK_SIGN_FIX", True))' in _src


def test_4925_5_next_revive_helper_future():
    """_next_month_reset_ts 返回未来时间戳（下月15号±随机分）。"""
    import time
    import nucleus.llm.channel_state_store as css
    _ts = css._next_month_reset_ts(time.time())
    assert isinstance(_ts, float)
    assert _ts > time.time()


def test_4925_5_next_revive_future_on(cfg, monkeypatch):
    """开关开：exhausted 的 next_revive_at 写"下月15号"，是未来时间戳。"""
    import time
    import nucleus.llm.channel_state_store as css
    cfg.ENABLE_M491_CHANNEL_STATE_PERSIST = True
    cfg.ENABLE_M4925_CHANNEL_NEXT_REVIVE = True
    monkeypatch.setattr(css, "_load_locked", lambda: None)  # 仅内存态，隔离文件
    monkeypatch.setattr(css, "_STATES", {})                  # 干净内存态
    _name = "ark-4925-revive-on"
    for _ in range(3):
        css.mark_http_error(_name, 403, "quota_exhausted")
    assert css.state_of(_name) == css._EXHAUSTED
    assert css._STATES[_name]["next_revive_at"] > time.time()


def test_4925_5_next_revive_zero_off(cfg, monkeypatch):
    """开关关：exhausted 的 next_revive_at 回退 0.0 哨兵（逐字回改前）。"""
    import nucleus.llm.channel_state_store as css
    cfg.ENABLE_M491_CHANNEL_STATE_PERSIST = True
    cfg.ENABLE_M4925_CHANNEL_NEXT_REVIVE = False
    monkeypatch.setattr(css, "_load_locked", lambda: None)
    monkeypatch.setattr(css, "_STATES", {})
    _name = "ark-4925-revive-off"
    for _ in range(3):
        css.mark_http_error(_name, 403, "quota_exhausted")
    assert css._STATES[_name]["next_revive_at"] == 0.0


# ───────────────────────── 492.5-6 publish 埋点 + 嘴节点 ─────────────────────────

def test_4925_6_log_publish_appends_on(cfg, monkeypatch):
    """开关开：log_publish 入列一条 type=publish 且带耗时字段的事件。"""
    import utils.pulse_tracer as PT
    cfg.ENABLE_M4925_PUBLISH_TIMING_TRACE = True
    _before = len(PT._events)
    PT.log_publish("pid1", 12.0, 3.0, 5, "cid1")
    assert len(PT._events) == _before + 1
    _ev = PT._events[-1]
    assert _ev["type"] == "publish"
    assert _ev["publish_ms"] == 12.0
    assert _ev["lock_wait_ms"] == 3.0
    assert _ev["queue_depth"] == 5


def test_4925_6_log_publish_noop_off(cfg):
    """开关关：log_publish 不入列（逐字回改前）。"""
    import utils.pulse_tracer as PT
    cfg.ENABLE_M4925_PUBLISH_TIMING_TRACE = False
    _before = len(PT._events)
    PT.log_publish("pid2", 1.0, 1.0, 1)
    assert len(PT._events) == _before


def test_4925_6_dump_adds_publish_fields():
    """dump 给 publish 类型事件附带 publish_ms/lock_wait_ms/queue_depth（源码级）。"""
    _src = open("utils/pulse_tracer.py", encoding="utf-8").read()
    assert 'e.get("type") == "publish"' in _src
    assert '_row["publish_ms"]' in _src
    assert '_row["lock_wait_ms"]' in _src
    assert '_row["queue_depth"]' in _src


def test_4925_6_mouth_node_wired():
    """PulseMouth 嘴节点已接 dialog_latency_trace.record（代码级校验）。"""
    _src = open("organs/motor/PulseMouth.py", encoding="utf-8").read()
    assert "dialog_latency_trace" in _src
    assert "_dlt.record(correlation_id, MouthEvent.REPLY" in _src


def test_4925_6_dialog_trace_gating(cfg):
    """dialog_latency_trace.record 受 486 开关门控（关 ⇒ None）。"""
    import nucleus.dialog_latency_trace as DLT
    cfg.ENABLE_M486_DIALOG_TRACE = False
    assert DLT.record("cid", "REPLY", "L1", "PulseMouth", "emit") is None
    cfg.ENABLE_M486_DIALOG_TRACE = True
    assert DLT.record("cid", "REPLY", "L1", "PulseMouth", "emit") is not None


# ───────────────────────── 492.5-7 看门狗超时双发治理 ─────────────────────────

def _make_mouth():
    import organs.motor.PulseMouth as PM
    _m = PM.PulseMouth()
    _m._speak_tts = lambda *a, **k: None
    _emitted = []
    _m._emit = lambda *a, **k: _emitted.append((a, k))
    return _m, _emitted


def test_4925_7_timeout_then_discard_on(cfg):
    """开关开：看门狗超时兜底吐第一句，后续同 cid 答案被丢弃（不双发）。"""
    import organs.motor.PulseMouth as PM
    cfg.ENABLE_M4925_WATCHDOG_CANCEL_BACKGROUND = True
    _m, _emitted = _make_mouth()
    # 第一句：458刀1 委派看门狗超时兜底（source=cortex_watchdog）
    _m._on_speak({"content": "等了很久没回应", "user_name": "用户",
                  "source": "cortex_watchdog", "reasoning_path": "delegate_timeout",
                  "correlation_id": "CID1"})
    # 第二句：肺最终答案（同 cid，后台请求完成）
    _m._on_speak({"content": "这是真正的答案", "user_name": "用户",
                  "source": "lung", "correlation_id": "CID1"})
    _replies = [e for e in _emitted if e[0] and e[0][0] == PM.MouthEvent.REPLY]
    assert len(_replies) == 1, [e[0][1].get("content") for e in _replies]


def test_4925_7_off_double_emit(cfg):
    """开关关：超时兜底后仍会冒出第二句（逐字回改前双发行为）。"""
    import organs.motor.PulseMouth as PM
    cfg.ENABLE_M4925_WATCHDOG_CANCEL_BACKGROUND = False
    _m, _emitted = _make_mouth()
    _m._on_speak({"content": "等了很久没回应", "user_name": "用户",
                  "source": "cortex_watchdog", "reasoning_path": "delegate_timeout",
                  "correlation_id": "CID2"})
    _m._on_speak({"content": "这是真正的答案", "user_name": "用户",
                  "source": "lung", "correlation_id": "CID2"})
    _replies = [e for e in _emitted if e[0] and e[0][0] == PM.MouthEvent.REPLY]
    assert len(_replies) == 2
