"""第82批 T-d：对话 cid 端到端（D167）+ 短答案净化白名单（D168）。

D167：搜索终止回退时 correlation_id 失配（注册键 search_query[:80] vs
控制器 preprocess 后的 search_topic），pop 恒空 → 后台静默不回用户。
修复：cid 随 OPEN_URL payload 透传，控制器回传带回，消费端优先取。

D168：S6 短答案净化——真短答案（问候/短事实/你是谁）不得被清空成"我还需要再想想"；
      含内部标记词（关联知识/[核心智慧]/代码片段:）一律硬清空不放行。
"""
from __future__ import annotations

import os
import sys

import pytest

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402


def _bare_iw():
    """构造未走 __init__ 的空壳，仅用于测纯文本处理方法。"""
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw._active_search_correlation = {}
    iw._log = lambda *a, **k: None
    return iw


# ---------- D167：cid 三分支 ----------

class TestSearchCidResolution:
    def test_prefers_passthrough_cid(self):
        iw = _bare_iw()
        # 旧注册表也有一条，但应优先透传 cid
        iw._active_search_correlation["旧主题"] = "old-cid"
        payload = {"search_correlation_id": "via-controller-123", "search_topic": "旧主题"}
        assert iw._pick_search_cid(payload, "旧主题") == "via-controller-123"

    def test_falls_back_to_registry_pop_when_no_passthrough(self):
        iw = _bare_iw()
        iw._active_search_correlation["主题X"] = "reg-cid"
        payload = {"search_topic": "主题X"}  # 无透传字段
        assert iw._pick_search_cid(payload, "主题X") == "reg-cid"
        # pop 后应移除
        assert "主题X" not in iw._active_search_correlation

    def test_graceful_degrade_empty_when_nothing(self):
        iw = _bare_iw()
        payload = {"search_topic": "不存在的主题"}
        assert iw._pick_search_cid(payload, "不存在的主题") == ""


# ---------- D167 补强1：控制器带 cid → 消费端真拿到（端到端链路） ----------

class TestControllerCarriesCid:
    def test_stage_feedback_payload_carries_cid(self):
        """控制器 _emit_stage_feedback 必须把 correlation_id 写回 payload，
        消费端 payload.get('search_correlation_id') 才能拿到非空 cid。"""
        from organs.motor.PulseController import PulseController
        pc = PulseController.__new__(PulseController)
        captured = {}

        class _Core:
            def emit(self, **kw):
                captured.update(kw)
                return kw

        class _Field:
            def publish(self, ev):
                captured["published"] = ev

        pc.pulse_core = _Core()
        pc.info_field = _Field()
        pc.organ_name = "PulseController"

        pc._emit_stage_feedback(1, "主题", ["kw"], 3, "stage1_completed", "note",
                                correlation_id="cid-xyz-123")
        assert captured["payload"]["search_correlation_id"] == "cid-xyz-123"

    def test_stage_feedback_cid_default_empty(self):
        """不带 cid 时字段为空串，不破坏旧链路（优雅降级）。"""
        from organs.motor.PulseController import PulseController
        pc = PulseController.__new__(PulseController)
        captured = {}

        class _Core:
            def emit(self, **kw):
                captured.update(kw)
                return kw

        class _Field:
            def publish(self, ev):
                captured["published"] = ev

        pc.pulse_core = _Core()
        pc.info_field = _Field()
        pc.organ_name = "PulseController"

        pc._emit_stage_feedback(1, "主题", ["kw"], 3, "stage1_completed")
        assert captured["payload"]["search_correlation_id"] == ""


# ---------- D168：短答案净化 ----------

class TestShortAnswerSanitize:
    @pytest.mark.parametrize("text", [
        "你好",
        "在吗",
        "1+1等于2",
        "我了解到，你好",
        "我了解到，1+1等于2",
    ])
    def test_real_short_answer_preserved(self, text):
        out = _bare_iw()._sanitize_internal_content(text, text)
        # 真短答案不得被清空成空
        assert out.strip(), f"短答案被误清空: {text!r} -> {out!r}"

    def test_greeting_not_killed(self):
        out = _bare_iw()._sanitize_internal_content("你好呀，我在呢", "你好")
        assert "你好" in out

    @pytest.mark.parametrize("text", [
        "关联知识：[节点A] 这是内部内容不该外露",
        "[核心智慧] 内部思考过程",
        "代码片段: def f(): pass 内部实现",
    ])
    def test_internal_marker_hard_removed(self, text):
        out = _bare_iw()._sanitize_internal_content(text, text)
        # 含内部标记词：残留正文不得含原标记，且不应把内部内容当短答案放行
        assert "关联知识" not in out
        assert "[核心智慧]" not in out
        assert "代码片段" not in out

    def test_real_answer_with_internal_marker_hard_cleared(self):
        # 真答案里混入内部标记 → 即使残留>5字也硬清空（补强2：一律不放行）
        text = "我了解到，关联知识：[xxx] 一些内部残留正文不该放出"
        out = _bare_iw()._sanitize_internal_content(text, text)
        assert out.strip() == "" or "关联知识" not in out


# ---------- 第82批 T-e：_search_deep 主线程路径不得丢 cid ----------

class TestMainThreadPathCarriesCid:
    """星轨独立终验抓到的主线程漏洞回归。

    `_search_deep` 有两条进 `_search_deep_headless` 的路径：
    - 异步线程路径（非主线程 → executor.submit(_execute_headless_search)）已带 cid；
    - 主线程路径（直接调用）此前未传 correlation_id → cid 断链、后台静默。
    本用例固定走主线程分支，断言 cid 被原样传下去。
    """

    def _bare_pc(self):
        import threading

        from organs.motor.PulseController import PulseController
        pc = PulseController.__new__(PulseController)
        pc._log = lambda *a, **k: None
        pc._load_level_lock = threading.Lock()
        pc._load_level = "normal"
        pc._last_deep_search_time = 0.0
        pc._deep_search_active = False
        pc._operation_count = 0
        pc._url_open_count = 0
        pc._check_permission = lambda *a, **k: True
        pc._get_config = lambda k, d=None: {
            "deep_search_cooldown": 0, "deep_search_total_timeout": 60}.get(k, d)
        pc._get_headless_config = lambda k, d=None: (True if k == "enabled" else d)
        pc._current_load_level = lambda: "normal"
        return pc

    def test_search_deep_main_thread_passes_cid(self):
        from organs.motor import PulseController as _pcmod
        pc = self._bare_pc()
        captured = {}

        def _fake_headless(search_topic, reason, max_articles=3, correlation_id=""):
            captured["correlation_id"] = correlation_id
            captured["topic"] = search_topic
            return {"status": "headless_completed"}

        pc._search_deep_headless = _fake_headless
        _orig_pw = _pcmod.PLAYWRIGHT_AVAILABLE
        _pcmod.PLAYWRIGHT_AVAILABLE = True
        try:
            out = pc._search_deep("http://example.com", "原因", "主题",
                                  {"search_correlation_id": "cid-main-abc"})
        finally:
            _pcmod.PLAYWRIGHT_AVAILABLE = _orig_pw

        assert captured.get("correlation_id") == "cid-main-abc", \
            f"主线程路径丢 cid（任务书 T-e 缺陷）: {captured!r}"
        assert out.get("status") == "headless_completed"

    def test_search_deep_main_thread_no_cid_degrades_empty(self):
        """不带 cid 时不崩，correlation_id 落空串（优雅降级）。"""
        from organs.motor import PulseController as _pcmod
        pc = self._bare_pc()
        captured = {}

        def _fake_headless(search_topic, reason, max_articles=3, correlation_id=""):
            captured["correlation_id"] = correlation_id
            return {"status": "headless_completed"}

        pc._search_deep_headless = _fake_headless
        _orig_pw = _pcmod.PLAYWRIGHT_AVAILABLE
        _pcmod.PLAYWRIGHT_AVAILABLE = True
        try:
            pc._search_deep("http://example.com", "原因", "主题", {})
        finally:
            _pcmod.PLAYWRIGHT_AVAILABLE = _orig_pw

        assert captured.get("correlation_id") == ""
