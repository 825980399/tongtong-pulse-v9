# -*- coding: utf-8 -*-
"""485刀2（P0）：断网判定 —— 单次 12s / 连续 3 次 5 分钟 ⇒ 降级窗口 120s。

★守任务书验收 4 条（§485-2）：
  1. 单次：判定启用 ⇒ 委派等待上限收为 12s（不看 90s 旧口径）；
  2. 连续：5 分钟窗口内 3 次委派超时 ⇒ 置 llm_unavailable_until=now+120s，
     期间对话兜底直接本地链+canned，不再委派；
  3. 降级状态写入 data/monitor/llm_degraded_state.json（health_snapshot 经此观测）；
  4. 开关关 ⇒ 不记失败账、无降级窗口，deadline 逐字回 458 旧口径。
★施工顺序说明：本刀与 485-1 同区协同（canned/委派判定同一分支），先红证据由
  test_m485_offline_fallback.py 的首跑 5F 覆盖；本文件为刀 2 专属判据的独立守护。
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
from nucleus.offline_state import read_llm_degraded_state, write_llm_degraded_state
from organs.brain.PulseCortex import PulseCortex

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "tests"))
from test_m485_offline_fallback import _mk_cortex  # noqa: E402  复用同款桩面


def _speaks(c):
    return [p for (et, p) in c._sent if et == MouthEvent.SPEAK]


def _selects(c):
    return [p for (et, p) in c._sent if et == LungEvent.SELECT_MODEL]


# ------------------------------------------------ 1) 单次 12s
def test_single_delegate_timeout_12s(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_OFFLINE_DETECT", True, raising=False)
    monkeypatch.setattr(config, "M485_DELEGATE_TIMEOUT_SEC", 12.0, raising=False)
    c = _mk_cortex()
    _now = time.time()
    c._m458_note_delegate("dlg:485d:1", "问题", "none")
    _dl = c._delegate_pending["dlg:485d:1"]["deadline"] - _now
    assert 11.0 <= _dl <= 13.0, f"判定启用 ⇒ 委派等待上限 12s，实测 { _dl:.1f}s"


# ------------------------------------------------ 2) 开关关 ⇒ 旧口径
def test_detect_off_keeps_old_deadline(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_OFFLINE_DETECT", False, raising=False)
    c = _mk_cortex()
    _now = time.time()
    c._m458_note_delegate("dlg:485d:2", "问题", "none")
    _dl = c._delegate_pending["dlg:485d:2"]["deadline"] - _now
    assert _dl > 60.0, f"开关关 ⇒ 沿用 458 旧口径（90s 一档），实测 {_dl:.1f}s"


# ------------------------------------------------ 3) 连续 3 次 ⇒ 降级窗口
def test_three_failures_open_degrade_window(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_OFFLINE_DETECT", True, raising=False)
    monkeypatch.setattr(config, "M485_FAILURE_THRESHOLD", 3, raising=False)
    monkeypatch.setattr(config, "M485_DEGRADE_WINDOW_SEC", 120.0, raising=False)
    c = _mk_cortex()
    c._active_correlation_id = "dlg:485d:3"
    c._latest_correlation_id = "dlg:485d:3"
    _root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "tmp", "bt_485_state")
    os.makedirs(_root, exist_ok=True)
    import nucleus.offline_state as _os_mod
    _orig = _os_mod._state_path
    monkeypatch.setattr(_os_mod, "_state_path", lambda root=None: os.path.join(_root, "llm_degraded_state.json"))
    try:
        for i in range(3):
            _cid = f"dlg:485d:3_{i}"
            c._m458_note_delegate(_cid, "问题", "none")
            c._delegate_pending[_cid]["deadline"] = time.time() - 1.0
            c._active_correlation_id = _cid      # 对话在等 ⇒ 看门狗须 SPEAK
            c._latest_correlation_id = _cid
            n = c._m458_sweep_delegates()
        assert n >= 1
        assert c._m485_llm_degraded(), "窗口内第 3 次失败 ⇒ 必须进入降级窗口"
        st = read_llm_degraded_state(_root)
        assert st is not None and st["degraded"] is True, st
        assert st["until"] > time.time(), st
        assert len(_speaks(c)) == 3, "每次超时仍要有看门狗兜底 SPEAK（与 458 语义一致）"
    finally:
        monkeypatch.setattr(_os_mod, "_state_path", _orig)
        write_llm_degraded_state(False, root=_root)


# ------------------------------------------------ 4) 降级窗口内 ⇒ 直接 canned 不委派
def test_degraded_window_canned_no_delegate(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_OFFLINE_FALLBACK", True, raising=False)
    monkeypatch.setattr(config, "ENABLE_M485_OFFLINE_DETECT", True, raising=False)
    # ★隔离：降级状态文件写进测试临时目录，绝不写生产 data/
    _root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "tmp", "bt_485_state")
    os.makedirs(_root, exist_ok=True)
    import nucleus.offline_state as _os_mod
    _orig = _os_mod._state_path
    monkeypatch.setattr(_os_mod, "_state_path", lambda root=None: os.path.join(_root, "llm_degraded_state.json"))
    c = _mk_cortex()
    c._m485_set_degraded(time.time() + 60.0, reason="test")
    cid = "dlg:485d:4"
    c._pending_inner_world[cid] = {"content": "问题", "user_name": "小林",
                                   "timestamp": time.time(),
                                   "file_paths": [], "code_blocks": []}
    out = c._on_inference_result({"correlation_id": cid, "answer": None,
                                  "method": "inner_world_no_answer"})
    assert out.get("status") == "offline_canned", out
    assert not _selects(c), "降级窗口内 ⇒ 不委派（任务书：不再委派）"
    sp = _speaks(c)
    assert sp and sp[0]["content"] == config.M485_OFFLINE_CANNED_REPLY, sp
    monkeypatch.setattr(_os_mod, "_state_path", _orig)
    write_llm_degraded_state(False, root=_root)


# ------------------------------------------------ 5) 开关关 ⇒ 不记失败账、无窗口
def test_detect_off_no_bookkeeping(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_OFFLINE_DETECT", False, raising=False)
    c = _mk_cortex()
    c._m458_note_delegate("dlg:485d:5", "问题", "none")
    c._delegate_pending["dlg:485d:5"]["deadline"] = time.time() - 1.0
    c._m458_sweep_delegates()
    assert len(getattr(c, "_m485_delegate_failures", [])) == 0, "开关关 ⇒ 不记失败账"
    assert not c._m485_llm_degraded(), "开关关 ⇒ 无降级窗口"


# ------------------------------------------------ 6) 窗口自然过期 ⇒ 状态文件翻回
def test_window_expiry_clears_state(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_OFFLINE_DETECT", True, raising=False)
    c = _mk_cortex()
    _root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "tmp", "bt_485_state")
    os.makedirs(_root, exist_ok=True)
    import nucleus.offline_state as _os_mod
    _orig = _os_mod._state_path
    monkeypatch.setattr(_os_mod, "_state_path", lambda root=None: os.path.join(_root, "llm_degraded_state.json"))
    try:
        c._m485_set_degraded(time.time() - 1.0, reason="test")   # until 已过去 ⇒ 可过期
        assert read_llm_degraded_state(_root)["degraded"] is True
        # 窗口过期后扫账 ⇒ 状态翻回
        c._m458_sweep_delegates(now=time.time() + 5.0)
        st = read_llm_degraded_state(_root)
        assert st is not None and st["degraded"] is False, st
        assert not c._m485_llm_degraded()
    finally:
        monkeypatch.setattr(_os_mod, "_state_path", _orig)
        write_llm_degraded_state(False, root=_root)
