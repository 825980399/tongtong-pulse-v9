# -*- coding: utf-8 -*-
"""第86批 相关任务 门控测试：搜索事件契约（信号分层）预埋。

背景（第83批 T-c1(3) 提出）：终止信号与结果信号此前混用同一事件
``controller.search_stage_completed``，订阅方无法按**事件类型**区分
「这一阶段有结果了」与「这次搜索被终止了」，于是把「终止后的空结果」
（stage=-1 / articles_found=0）按结果信号解读，返回 ``accepted/continue``
（即被读成「我来兜底 / 继续」）。

分层后：``SearchEvent.COMPLETED / FAILED / TERMINATED`` 为独立终态事件，
终止信号走 ``_handle_search_terminated``（仅登记，不触发结果审查/兜底）。

本文件断言：
  1. 事件契约常量已定义并登记到统一注册表；
  2. 终止信号（新事件名 & 旧事件名+status=terminate）都不会被读成结果通路；
  3. 终止分支的发射点改发独立事件，且**自身既有兜底行为保持不变**；
  4. 订阅点对 SEARCH_TERMINATED 返回 terminate 判定且不触发兜底。
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import nucleus.const as _const  # noqa: E402
from nucleus.const import Event, is_registered_event  # noqa: E402
from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402

_LEGACY = "controller.search_stage_completed"
_TOPIC = "今天的科技新闻"
_KWS = ["探索", "适合", "生活"]          # 与主题零重叠 → 触发终止审查
_TERMINATED = "search.terminated"


def _bare_iw():
    """构造未走 __init__ 的空壳，仅覆盖本通路所需属性（铁律80：显式给全，勿依赖 __init__）。"""
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw._log = lambda *a, **k: None
    iw._search_experience = {}
    iw._search_experience_max = 500
    iw._direct_to_lung_questions = set()
    iw._m83_search_retry = {_TOPIC: 1}   # 跳过第83批「零重叠先重试一次」
    iw._insight_board = None
    iw._emitted = []
    iw._fallback_calls = []
    iw._quality_signals = []

    def _emit(event_type, payload=None, **kw):
        iw._emitted.append((event_type, payload or {}))
        return None

    def _fallback(*a, **k):
        iw._fallback_calls.append((a, k))
        return {"status": "fallback"}

    iw._emit = _emit
    iw._fallback_to_lung_model = _fallback
    iw._observe_search_quality = lambda *a, **k: iw._quality_signals.append((a, k))
    iw._make_experience_key = lambda t: str(t)[:60]
    iw._pick_search_cid = lambda payload, topic: "cid-m86"
    return iw


_TERMINATE_PAYLOAD = {
    "stage": -1,
    "search_topic": _TOPIC,
    "keywords": _KWS,
    "articles_found": 0,
    "status": "terminate",
    "note": "内在世界审查判定阶段1关键词与原始主题无关，终止搜索",
}


# ======================================================================
# 1) 事件契约常量
# ======================================================================

class TestSearchEventContract:
    def test_constants_defined_and_registered(self):
        _se = getattr(_const, "SearchEvent", None)
        assert _se is not None, "nucleus/const.py 缺少 SearchEvent 事件契约类"
        assert _se.COMPLETED == "search.completed"
        assert _se.FAILED == "search.failed"
        assert _se.TERMINATED == _TERMINATED
        assert len({_se.COMPLETED, _se.FAILED, _se.TERMINATED}) == 3, "三个终态事件值必须互不相同"

    def test_event_registry_contains_terminal_events(self):
        assert getattr(Event, "SEARCH_TERMINATED", None) == _TERMINATED
        assert getattr(Event, "SEARCH_COMPLETED", None) == "search.completed"
        assert getattr(Event, "SEARCH_FAILED", None) == "search.failed"
        assert is_registered_event(_TERMINATED) is True, "新事件必须登记到统一事件注册表"


# ======================================================================
# 2) 终止信号不得被读成结果通路
# ======================================================================

class TestTerminateNotReadAsResult:
    def test_new_terminated_event_returns_terminate_verdict(self):
        iw = _bare_iw()
        out = iw.on_pulse({"event_type": _TERMINATED, "payload": dict(_TERMINATE_PAYLOAD)})
        assert out is not None, "订阅点未响应 SEARCH_TERMINATED（事件未被处理）"
        assert out.get("action") == "none", (
            "终止信号被读成了结果通路动作: %r（终止不得进入结果审查/兜底）" % (out,))
        assert iw._fallback_calls == [], "终止信号订阅通路不得触发兜底回退"

    def test_legacy_terminate_payload_still_layered(self):
        """旧信号守护：同一事件名 + status=terminate 也不得被读成 accepted/continue。"""
        iw = _bare_iw()
        out = iw.on_pulse({"event_type": _LEGACY, "payload": dict(_TERMINATE_PAYLOAD)})
        assert out is not None
        assert out.get("action") != "continue", (
            "旧格式终止信号被误读为结果通路（accepted/continue）: %r" % (out,))
        assert out.get("status") in ("search_terminated", "stage1_irrelevant"), (
            "终止判定口径异常: %r" % (out,))
        assert iw._fallback_calls == []


# ======================================================================
# 3) 发射点：终止分支改发独立终止事件
# ======================================================================

class TestTerminateEmitPoint:
    def _drive_stage1(self):
        iw = _bare_iw()
        payload = {
            "stage": 1,
            "search_topic": _TOPIC,
            "keywords": list(_KWS),
            "articles_found": 0,
            "status": "stage1_completed",
            "note": "",
        }
        out = iw.on_pulse({"event_type": _LEGACY, "payload": payload})
        return iw, out

    def test_terminate_branch_emits_dedicated_event(self):
        iw, out = self._drive_stage1()
        assert out is not None
        _types = [t for t, _ in iw._emitted]
        assert _TERMINATED in _types, (
            "终止分支未发独立终止事件（实发: %r）——终止与结果信号仍混用同一事件类型" % (_types,))
        assert _LEGACY not in _types, (
            "终止分支仍复用「阶段完成」结果事件（实发: %r）" % (_types,))
        _payload = [p for t, p in iw._emitted if t == _TERMINATED][0]
        assert _payload.get("status") == "terminate", "终止事件须保留 status=terminate（控制器兼容）"

    def test_terminate_branch_own_fallback_preserved(self):
        """分层不得误杀终止分支自身的后台兜底学习（行为零变化）。"""
        iw, out = self._drive_stage1()
        assert len(iw._fallback_calls) == 1, (
            "终止分支自身的后台兜底被改动: %d 次" % len(iw._fallback_calls))


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
