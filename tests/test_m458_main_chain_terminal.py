# -*- coding: utf-8 -*-
"""458 刀1（P0）：主链终态 —— 委派账本 + 看门狗 + 超时兜底。

★本文件守五件事（任务书验收逐条对应）
  1. `_on_inference_result(answer=None, ctx 存在)` ⇒ 必须产生 `_delegate_pending[cid]`；
  2. 委派超时 + 心跳 ⇒ 必须 SPEAK 一次 + `_dialog_mark_output_done` 一次 + 账本清空；
  3. 委派成功后 REPLY ⇒ 账本清空、不重复 SPEAK；
  4. `ctx is None` 且 `answer is None` ⇒ 也必须走同一终态；
  5. 连续两问：第一问委派失败 ⇒ 第二问不得被 queued 卡住（回归 20:07 事故形状）。

★为什么每个用例都自己造一个 Cortex：`_on_inference_result` 是 4000 行类里的
  「结果收口」，真实实例要装配 50+ 器官。这里只装本次路径真正会碰到的状态，
  其余一律打桩 —— 打桩面越小，红了才说明是被测逻辑坏了。
"""
from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus.const import LungEvent, MouthEvent
from organs.brain.PulseCortex import PulseCortex


def _mk_cortex():
    c = PulseCortex.__new__(PulseCortex)
    # —— 委派账本（被测对象）——
    c._delegate_pending = {}
    c._delegate_stats = {"registered": 0, "settled_reply": 0, "timeout": 0, "suppressed": 0}
    # —— 推理暂存 / 消息暂存 ——
    c._pending_inner_world = {}
    c._pending_messages = {}
    # —— 对话闸 ——
    c._dialog_output_done = deque(maxlen=200)
    c._dialog_output_done_set = set()
    c._dialog_guard_stats = {"checked": 0, "dropped_cancelled": 0,
                             "dropped_stale": 0, "queued": 0, "hint_sent": 0}
    c._dialog_queue = deque(maxlen=1000)
    c._dialog_max_queue = 8
    c._active_correlation_id = ""
    c._latest_correlation_id = ""
    c._cancelled_correlations = deque(maxlen=200)
    c._cancelled_correlation_set = set()
    # —— 补救锁 ——
    c._remediation_lock_time = {}
    c._remediation_lock_extra = {}
    c._remediation_lock_meta = {}
    # —— 发射去重 ——
    c._turn_emit_lock = threading.Lock()
    c._turn_emit_seen = {}
    c._turn_emit_dedup_count = 0
    # —— 打桩：本次路径用不到或会打外网/写盘的部分 ——
    c._strategy_selector = None
    c._cleanup_expired_remediation_locks = lambda *a, **k: None
    c._match_controller_action = lambda content: None
    c._compute_offline_survival = lambda q: None
    c._sent = []
    c._marked = []
    c._warn = []
    c._emit = lambda et, pl=None, **k: (c._sent.append((et, dict(pl or {}))) or "t")
    c._log = lambda lvl, msg="", *a, **k: (c._warn.append(str(msg))
                                           if "委派超时" in str(msg) else None)
    c._log_ignored_exception = lambda *a, **k: None
    return c


def _speaks(c):
    return [p for (et, p) in c._sent if et == MouthEvent.SPEAK]


def _selects(c):
    return [p for (et, p) in c._sent if et == LungEvent.SELECT_MODEL]


# ---------------------------------------------------------------- 1) 空答案要入账
def test_empty_answer_registers_delegate():
    c = _mk_cortex()
    cid = "dlg:458:1"
    c._pending_inner_world[cid] = {"content": "这个函数是做什么用的", "user_name": "用户",
                                   "intent": "知识", "timestamp": time.time(),
                                   "file_paths": [], "code_blocks": []}
    out = c._on_inference_result({"correlation_id": cid, "answer": None,
                                  "method": "experience_failed_to_lung",
                                  "question": "这个函数是做什么用的"})
    assert out.get("status") == "delegated_to_lung", out
    assert cid in c._delegate_pending, "空答案委派必须先登记，否则看门狗无处可扫"
    rec = c._delegate_pending[cid]
    assert rec["prompt_len"] > 0
    assert rec["deadline"] > time.time()
    assert c._delegate_stats["registered"] == 1
    assert _selects(c), "委派事件必须真的发出去"


# ---------------------------------------------------------------- 2) 超时兜底
def test_watchdog_speaks_and_releases_gate():
    c = _mk_cortex()
    cid = "dlg:458:2"
    c._active_correlation_id = cid
    c._latest_correlation_id = cid
    c._m458_note_delegate(cid, "这个函数是做什么用的", "experience_failed_to_lung")
    # 把期限推到过去，模拟「肺一直没回」
    c._delegate_pending[cid]["deadline"] = time.time() - 1.0

    n = c._m458_sweep_delegates()

    assert n == 1
    assert cid not in c._delegate_pending, "超时后必须出账"
    _sp = _speaks(c)
    assert len(_sp) == 1, _sp
    assert _sp[0]["source"] == "cortex_watchdog"
    assert "请稍候" not in _sp[0]["content"], "★兜底文案不许写请稍候（会诱发继续追问）"
    assert "没能拿到结果" in _sp[0]["content"]
    assert cid in c._dialog_output_done_set, "必须 _dialog_mark_output_done 放行队列"
    assert c._delegate_stats["timeout"] == 1
    assert any("委派超时兜底" in m for m in c._warn), c._warn


def test_watchdog_respects_extended_deadline():
    """extend_remediation_lock 的宽限对委派账本同样生效（不再是无调用的死代码）。"""
    c = _mk_cortex()
    cid = "dlg:458:2b"
    c._m458_note_delegate(cid, "q", "m")
    c._delegate_pending[cid]["deadline"] = time.time() - 1.0
    assert c.extend_remediation_lock(cid, extra_seconds=30.0, stage="delegate") is True
    assert c._m458_sweep_delegates() == 0, "续期后不应被判超时"
    assert cid in c._delegate_pending


# ---------------------------------------------------------------- 3) 正常结算
def test_reply_settles_delegate_without_duplicate_speak():
    c = _mk_cortex()
    cid = "dlg:458:3"
    c._active_correlation_id = cid
    c._latest_correlation_id = cid
    c._m458_note_delegate(cid, "问题", "none")
    c.on_pulse({"event_type": MouthEvent.REPLY,
                "payload": {"correlation_id": cid, "source": "lung"}})
    assert cid not in c._delegate_pending
    assert c._delegate_stats["settled_reply"] == 1
    assert _speaks(c) == [], "正常回复不该触发兜底 SPEAK"
    # 再扫一次也不能重复出兜底
    assert c._m458_sweep_delegates() == 0


# ---------------------------------------------------------------- 4) ctx 缺失
def test_ctx_missing_still_registers_delegate():
    c = _mk_cortex()
    cid = "dlg:458:4"
    out = c._on_inference_result({"correlation_id": cid, "answer": None,
                                  "question": "这个函数是做什么用的",
                                  "method": "none"})
    assert out.get("status") == "delegated_to_lung", out
    assert cid in c._delegate_pending


# ---------------------------------------------------------------- 5) 连续两问
def test_second_question_not_stuck_after_delegate_timeout():
    """★回归 20:07 事故形状：第一问委派丢失后，第二问必须能继续。"""
    c = _mk_cortex()
    cid1, cid2 = "dlg:458:5a", "dlg:458:5b"
    # 第一问进入对话闸
    assert c._dialog_register_input(cid1)["action"] == "dispatch"
    c._pending_messages[cid1] = {"content": "第一问", "user_name": "用户"}
    c._m458_note_delegate(cid1, "第一问", "experience_failed_to_lung")
    # 第二问到达 —— 事故里这里就是「正在处理上一个问题」
    assert c._dialog_register_input(cid2)["action"] == "queued"
    c._pending_messages[cid2] = {"content": "第二问", "user_name": "用户"}
    assert cid2 in c._dialog_queue
    # 委派超时 ⇒ 看门狗必须把闸放开并把队列推起来
    c._delegate_pending[cid1]["deadline"] = time.time() - 1.0
    assert c._m458_sweep_delegates() == 1
    assert cid1 in c._dialog_output_done_set
    assert cid2 not in c._dialog_queue, "队列必须被派发"
    assert c._active_correlation_id == cid2


# ---------------------------------------------------------------- 附加：开关与边界
def test_watchdog_switch_off_registers_nothing(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M458_DELEGATE_WATCHDOG", False, raising=False)
    c = _mk_cortex()
    c._m458_note_delegate("dlg:458:6", "q", "m")
    assert c._delegate_pending == {}
    assert c._m458_sweep_delegates() == 0


def test_background_delegate_not_registered():
    c = _mk_cortex()
    c._m458_note_delegate("dlg:458:7", "q", "m", is_dialogue=False)
    assert c._delegate_pending == {}, "后台学习没有用户在等，不该占看门狗"


def test_deadline_reads_config(monkeypatch):
    monkeypatch.setattr(config, "DIALOG_DELEGATE_TIMEOUT_SEC", 12.0, raising=False)
    c = _mk_cortex()
    _now = time.time()
    c._m458_note_delegate("dlg:458:8", "q", "m")
    assert 11.0 <= c._delegate_pending["dlg:458:8"]["deadline"] - _now <= 13.0
