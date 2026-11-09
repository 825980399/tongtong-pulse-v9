# -*- coding: utf-8 -*-
"""485刀1（P0）：断网兜底出口重构 —— `answer is None` 即走本地链 + canned 出口。

★守任务书验收 6 条（docs/路灯与星轨对话/任务书/路灯_485批_断网兜底与补丁闸_20261109.md §485-1）：
  1. ctx 有值 + answer=None ⇒ 走兜底链（本地链/ canned），绝不静默；
  2. 本地链解出 ⇒ 发 SPEAK（复用既有输出路径），不委派；
  3. 本地链无解 ⇒ canned 话术出口，**逐字**等于 M485_OFFLINE_CANNED_REPLY，且发 SPEAK；
  4. 后台学习（is_background_learning=True）⇒ 绝不发 SPEAK（既有行为保留）；
  5. 开关关 ⇒ 行为与改前逐字一致（ctx 有值 + answer=None ⇒ 委派外脑）；
  6. canned 路径不消费委派幂等键（烛微坑四）。
★为什么每个用例自己造 Cortex：同 test_m458_main_chain_terminal.py 头注——
  只装本路径真正会碰到的状态，桩面越小红了才越说明是被测逻辑坏了。
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

_CANNED = "我现在连不上外部模型，先记下你这句话，恢复后我会接着处理"


def _mk_cortex():
    c = PulseCortex.__new__(PulseCortex)
    # —— 委派账本 ——
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
    c._remediation_pending = set()
    c._remediation_lock_time = {}
    c._decision_count = 0
    c._correction_retry = 0
    c._trajectory = None
    c._feedback_loop = None
    c._self_awareness = None
    c._narrative_engine = None
    c._experience_reflection = None
    c._curiosity_engine = None
    c._emotion_engine = None
    c._remediation_lock_extra = {}
    c._remediation_lock_meta = {}
    # —— 发射去重 ——
    c._turn_emit_lock = threading.Lock()
    c._turn_emit_seen = {}
    c._turn_emit_dedup_count = 0
    # —— 485 状态 ——
    c._m485_llm_unavailable_until = 0.0
    c._m485_delegate_failures = deque(maxlen=50)
    c._m485_degraded_written = False
    # —— 输出路径桩 ——
    c._strategy_selector = None
    c._vl_hub = None
    c._self_corrector = None
    c._cleanup_expired_remediation_locks = lambda *a, **k: None
    c._match_controller_action = lambda content: None
    c._compute_offline_survival = lambda q: None
    c._b3_receipt_settle = lambda *a, **k: None
    c._maybe_append_self_state = lambda a, *k, **kk: a
    c._add_narrative_continuity = lambda a, *k, **kk: a
    c._get_guidance = lambda *a, **k: ""
    c._m40_observe_self_awareness = lambda *a, **k: None
    c._verify_output_relevance = lambda *a, **k: {
        "needs_verification": False, "relevance_score": 1.0, "reason": ""}
    c._receipt_ledger_record = lambda *a, **k: None
    c._sent = []
    c._warn = []
    c._emit = lambda et, pl=None, **k: (c._sent.append((et, dict(pl or {}))) or "t")
    c._log = lambda lvl, msg="", *a, **k: c._warn.append(str(msg))
    c._log_ignored_exception = lambda *a, **k: None
    return c


def _speaks(c):
    return [p for (et, p) in c._sent if et == MouthEvent.SPEAK]


def _selects(c):
    return [p for (et, p) in c._sent if et == LungEvent.SELECT_MODEL]


_CTX_DICT = {"content": "帮我看看这个报错", "user_name": "小林",
             "intent": "调试", "timestamp": time.time(),
             "file_paths": [], "code_blocks": []}


# ------------------------------------------------ 1) ctx 有值 + 无答案 + 本地解出
def test_ctx_present_local_chain_speaks(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_OFFLINE_FALLBACK", True, raising=False)
    c = _mk_cortex()
    cid = "dlg:485:1"
    c._pending_inner_world[cid] = dict(_CTX_DICT)
    c._compute_offline_survival = lambda q: "本地推理得到的候选答案"
    c._on_inference_result({"correlation_id": cid, "answer": None,
                            "method": "inner_world_no_answer"})
    sp = _speaks(c)
    assert sp, "本地链解出 ⇒ 必须有 SPEAK（任务书验收：走兜底链不是静默）"
    assert "本地推理得到的候选答案" in sp[0]["content"]
    assert not _selects(c), "本地链解出 ⇒ 不委派外脑"


# ------------------------------------------------ 2) ctx 有值 + 无答案 + 本地无解
def test_ctx_present_unsolved_canned_exit(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_OFFLINE_FALLBACK", True, raising=False)
    monkeypatch.setattr(config, "ENABLE_M485_OFFLINE_DETECT", False, raising=False)
    c = _mk_cortex()
    cid = "dlg:485:2"
    c._pending_inner_world[cid] = dict(_CTX_DICT)
    c._on_inference_result({"correlation_id": cid, "answer": None,
                            "method": "inner_world_no_answer"})
    sp = _speaks(c)
    assert sp, "本地无解 ⇒ canned 出口必须发 SPEAK（不能只 return 状态）"
    assert sp[0]["content"] == _CANNED, "canned 话术必须逐字等于任务书固定文本"
    assert not _selects(c), "485-1 判定未启用 ⇒ 无解直接 canned，不委派已死外脑"


# ------------------------------------------------ 3) ctx 缺失 + 无答案 + 本地无解
def test_ctx_missing_unsolved_canned_exit(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_OFFLINE_FALLBACK", True, raising=False)
    monkeypatch.setattr(config, "ENABLE_M485_OFFLINE_DETECT", False, raising=False)
    c = _mk_cortex()
    cid = "dlg:485:3"
    c._on_inference_result({"correlation_id": cid, "answer": None,
                            "question": "今天天气怎么样", "method": "none"})
    sp = _speaks(c)
    assert sp, "ctx 缺失同样要兜底（改前：委派外脑；改后：canned）"
    assert sp[0]["content"] == _CANNED
    assert not _selects(c)


# ------------------------------------------------ 4) 后台学习绝不 SPEAK
def test_background_never_speaks(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_OFFLINE_FALLBACK", True, raising=False)
    c = _mk_cortex()
    cid = "dlg:485:4"
    c._on_inference_result({"correlation_id": cid, "answer": None,
                            "question": "后台消化的问题",
                            "is_background_learning": True, "method": "none"})
    assert _speaks(c) == [], "后台学习断网不能给用户发话术（任务书坑二）"


# ------------------------------------------------ 5) 开关关 ⇒ 逐字旧行为
def test_switch_off_old_behavior(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_OFFLINE_FALLBACK", False, raising=False)
    c = _mk_cortex()
    cid = "dlg:485:5"
    c._pending_inner_world[cid] = dict(_CTX_DICT)
    out = c._on_inference_result({"correlation_id": cid, "answer": None,
                                  "method": "inner_world_no_answer"})
    # 旧行为（458 测试钉死的契约）：ctx 有值 + answer=None ⇒ 委派外脑
    assert out.get("status") == "delegated_to_lung", out
    assert _selects(c), "开关关 ⇒ 保留既有委派"
    canned_sp = [p for p in _speaks(c) if p["content"] == _CANNED]
    assert canned_sp == [], "开关关 ⇒ 不出现 canned 出口"


# ------------------------------------------------ 6) 坑四：canned 不消费幂等键
def test_canned_does_not_consume_claim_key(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_OFFLINE_FALLBACK", True, raising=False)
    c = _mk_cortex()
    cid = "dlg:485:6"
    c._active_correlation_id = cid
    c._latest_correlation_id = cid
    out = c._m485_canned_exit(cid, "小林")
    assert out.get("status") == "offline_canned", out
    ok = c._claim_select_model_emit(cid, "同一轮的正式委派",
                                    is_background=False, is_remediation=False)
    assert ok is True, "canned 路径不得复用/消费委派幂等键（烛微坑四）"


# ------------------------------------------------ 7) 内在自主不发 canned
def test_inner_autonomous_no_canned_speak(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_OFFLINE_FALLBACK", True, raising=False)
    c = _mk_cortex()
    out = c._m485_canned_exit("", "用户")   # correlation_id 为空 = 内在自主
    assert out.get("status") == "offline_canned", out
    assert _speaks(c) == [], "内在自主无用户在等 ⇒ canned 不得进嘴巴"
