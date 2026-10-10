# -*- coding: utf-8 -*-
"""★第146批 T146-11②：`_on_inference_request` 离线金标测试（**准备**，不做正式拆分）。

目标：把「推理入口」的**返回结构**与 **emit 事件序列**钉成可回归的金标，
使第147批正式拆分 `_on_inference_request` 时有「行为等价」的判定基线。

设计要点：
  1. **完全离线**：除本题显式桩外，所有未注入依赖退化为宽容空对象（`_PermissiveNull`），
     被测代码按自身降级分支走；同时把 `socket.socket.connect` 换成抛错的哨兵，
     任何真实联网都会在测试里**直接失败**而非静默通过。
  2. **mock 渠道**：`_emit` 被替换为记录器，事件不进 EventBus。
  3. **断言内容**：返回必须是 dict 且含 `status`；`answer` 若存在必须是 `str`；
     事件序列与 `KEY_ROUTES` 中登记的期望**前缀序列**一致；
     `answer` 不得残留未渲染占位符（`<XXX>`）——联动 T146-3 验收口径。

⚠️ 已知边界：多步 / 知识边界 / 深搜 / 管道 四类在纯离线桩下走向 `no_match` 降级分支，
   因为它们的下游引擎（agent/搜索/深度推理）未注入。本批金标锁的是**结构与降级行为**，
   不锁它们的最终答案内容；正式拆分（第147批）时需用同一批 payload 做等价比对。
"""
from __future__ import annotations

import re
import socket
import sys

import pytest

ROOT = __import__("os").path.dirname(__import__("os").path.dirname(
    __import__("os").path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402

#: 未渲染占位符模式（与 config._PLACEHOLDER_RE 同口径）
_UNRENDERED = re.compile(r"<[A-Z_]{2,32}>")


# --------------------------------------------------------------------------
# 宽容空对象：把「未显式注入的依赖」全部降级为"无"，让被测代码走自身的降级分支
# --------------------------------------------------------------------------
class _PermissiveNull:
    def __bool__(self):
        return False

    def __len__(self):
        return 0

    def __iter__(self):
        return iter(())

    def __contains__(self, _x):
        return False

    def __call__(self, *a, **k):
        return None

    def __getattr__(self, _n):
        return _PermissiveNull()

    def __getitem__(self, _k):
        return _PermissiveNull()

    def __setitem__(self, _k, _v):
        return None

    def get(self, _k, default=None):
        return default

    def setdefault(self, _k, default=None):
        return default


KEY_ROUTES: dict[str, dict] = {
    "simple": {
        "question": "你是谁",
        "expect_status": "simple_query_local",
        "expect_events": ["inference.result"],
    },
    "explicit_search": {
        "question": "搜索一下量子计算最新进展",
        "expect_status": "explicit_search",
        "expect_events": ["controller.open_url", "inference.result"],
    },
    "multi_step": {
        "question": "请分三步说明如何优化这个系统的内存占用",
        "expect_status": "no_match",
        "expect_events": ["growth.need_detected", "inference.result"],
    },
    "knowledge_boundary": {
        "question": "请说明XYZ9未知协议的内部实现细节",
        "expect_status": "no_match",
        "expect_events": ["growth.need_detected", "inference.result"],
    },
    "calc": {
        "question": "12+34等于多少",
        "expect_status": "simple_query_local",
        "expect_events": ["inference.result"],
    },
    "deep_search": {
        "question": "深度搜索一下 ogbn-arxiv 的 SOTA",
        "expect_status": "no_match",
        "expect_events": ["growth.need_detected", "inference.result"],
    },
    "qica": {
        "question": "归纳心跳 GLiNER2 与 Alibaba 两个案例的共同规律",
        "expect_status": "deep_think_forced",
        "expect_events": ["inference.result"],
    },
    "pipeline": {
        "question": "请比较 A 方案和 B 方案的异同并给出结论",
        "expect_status": "no_match",
        "expect_events": ["growth.need_detected", "inference.result"],
    },
}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """任意真实网络连接 → 测试立即失败（证明本金标真离线）。"""

    def _boom(*a, **k):
        raise AssertionError("金标测试禁止真实网络访问（socket.connect 被调用）")

    monkeypatch.setattr(socket.socket, "connect", _boom, raising=True)
    monkeypatch.setattr(socket.socket, "connect_ex", _boom, raising=True)


@pytest.fixture(autouse=True)
def _offline_inner_world_class():
    """给 PulseInnerWorld 临时装上缺失属性兜底，测试结束后必须还原。"""
    had = hasattr(PulseInnerWorld, "__getattr__")
    old = getattr(PulseInnerWorld, "__getattr__", None)

    def _missing(self, name):
        return _PermissiveNull()

    PulseInnerWorld.__getattr__ = _missing  # type: ignore[attr-defined]
    try:
        yield
    finally:
        if had:
            PulseInnerWorld.__getattr__ = old  # type: ignore[attr-defined]
        else:
            delattr(PulseInnerWorld, "__getattr__")


def _make_inner_world() -> tuple[PulseInnerWorld, list]:
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    events: list = []
    iw.__dict__.update({
        "event_bus": None, "self_awareness": None, "node_pool": None,
        "knowledge_tree": None, "stress_axis": None,
        "_autonomous_deriver": None, "_cognitive_pipeline": None,
        "_identity_rules": {}, "_inference_count": 0, "_retrieval_miss_count": 0,
        "_cache_max": 100, "_max_trace": 10,
        "_simple_query_local_enabled": True, "_confidence_guard_enabled": True,
        "_model_cache": {}, "_simple_query_cache": {},
        "_simple_query_cache_order": [],
        "_failed_domain_records": {}, "_failed_domain_log_count": 0,
        "_m29_routing_stats": {}, "_m29_routing_log_count": 0,
    })

    def _emit(event_type, payload=None, priority=5, ttl_ns=0, layer="L1"):
        events.append((str(event_type), dict(payload or {})))
        return "p%d" % len(events)

    iw._emit = _emit                                    # type: ignore[attr-defined]
    iw._log = lambda *a, **k: None                      # type: ignore[attr-defined]
    iw._build_memory_context = lambda *a, **k: ""       # type: ignore[attr-defined]
    iw._capture_meta_state = lambda *a, **k: {}         # type: ignore[attr-defined]
    iw._assess_question_complexity = lambda *a, **k: 0.3  # type: ignore[attr-defined]
    iw._get_emotion_reasoning_modulation = lambda *a, **k: {}  # type: ignore[attr-defined]
    iw._call_provider = lambda prov, *a, **k: k.get("default")  # type: ignore[attr-defined]
    iw._cache_inference = lambda *a, **k: None          # type: ignore[attr-defined]
    iw._trace_inference = lambda *a, **k: None          # type: ignore[attr-defined]
    iw._evidence_conf = lambda base, *a, **k: base      # type: ignore[attr-defined]
    iw._enhance_answer = lambda *a, **k: (k.get("answer") or "")  # type: ignore[attr-defined]
    return iw, events


@pytest.mark.parametrize("case_id", sorted(KEY_ROUTES))
def test_golden_route(case_id: str):
    spec = KEY_ROUTES[case_id]
    iw, events = _make_inner_world()
    payload = {"question": spec["question"], "user_name": "小林",
               "correlation_id": case_id}
    if case_id == "qica":
        payload["strategy_context"] = {"qica_suggested_method": "",
                                       "qica_knowledge_paths": []}

    res = iw._on_inference_request(payload)

    # ---- 结构断言 ----
    assert isinstance(res, dict), "推理入口必须返回 dict，实际={!r}".format(type(res))
    assert "status" in res, "返回结构缺 status 键：{!r}".format(sorted(res))

    # ---- 路由断言 ----
    assert res["status"] == spec["expect_status"], (
        "[{}] 路由漂移：期望 {}，实际 {}".format(case_id, spec["expect_status"], res["status"]))

    # ---- 事件序列断言 ----
    got = [e[0] for e in events]
    assert got == spec["expect_events"], (
        "[{}] emit 事件序列漂移：期望 {}，实际 {}".format(case_id, spec["expect_events"], got))

    # ---- correlation_id 透传断言（RESULT 事件必须带上，供上层串联） ----
    result_events = [p for name, p in events if name == "inference.result"]
    assert result_events, "[{}] 未发出 inference.result".format(case_id)
    assert result_events[0].get("correlation_id") == case_id, \
        "[{}] RESULT 事件未透传 correlation_id".format(case_id)

    # ---- 占位符泄漏断言（联动 T146-3 验收） ----
    answer = res.get("answer")
    if isinstance(answer, str) and answer:
        assert not _UNRENDERED.search(answer), \
            "[{}] 答案残留未渲染占位符：{}".format(case_id, answer[:80])


def test_identity_answer_has_no_placeholder():
    """★T146-3 验收直测：问「你是谁」不得出现尖括号，且不得出现同义反复。"""
    iw, _events = _make_inner_world()
    res = iw._on_inference_request({"question": "你是谁", "user_name": "小林",
                                    "correlation_id": "identity"})
    answer = str(res.get("answer") or "")
    assert answer, "身份问答必须给出非空答案"
    assert not _UNRENDERED.search(answer), "答案出现未渲染占位符：{}".format(answer[:120])
    assert "<" not in answer and ">" not in answer, "答案出现尖括号：{}".format(answer[:120])
    assert "我叫曈曈" in answer, "身份锚点缺失：{}".format(answer[:60])
    # 同义反复自检：不应再出现「我叫X，小名X」
    assert "小名曈曈" not in answer, "仍存在「小名曈曈」同义反复"
