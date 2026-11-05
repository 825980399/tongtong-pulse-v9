# -*- coding: utf-8 -*-
"""456 刀2/刀3/刀4：qica_intent_type 透传 + 意图 handler shadow。

覆盖任务书验收：
  · 刀2：技术推理问题时 `strategy_context["qica_intent_type"] == "技术推理"`；
    元认知报告时 == "元认知报告"
  · 刀3：代码类问题 would_handler=="code_reasoning"；纯概念问题不触发
  · 刀4：元认知问题 would_handler=="self_report"；handled_by 已标后不再预演
  · 三个都不真执行 handler（只写 trace）
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from nucleus import dialogue_process_trace as TR
from organs.brain.PulseCortex import PulseCortex
from organs.brain.PulseInnerWorld import PulseInnerWorld as IW


# =====================================================================
# 刀2 · Cortex 透传
# =====================================================================
def _mk_cortex():
    _e = PulseCortex.__new__(PulseCortex)
    _e._intent_routes = dict(getattr(config, "INTENT_ROUTES", {}) or {})
    _e._pending_inner_world = {}
    _e.inner_world = object()          # 非 None ⇒ 才会构造 strategy_context
    _e.autonomous_deriver = None
    _e.node_pool = None
    _e._decision_count = 0
    _e._sent = []
    _e._emit = lambda et, pl=None, **k: (_e._sent.append((et, dict(pl or {}))) or "t")
    _e._log = lambda *a, **k: None
    _e._log_ignored_exception = lambda *a, **k: None
    # 只桩掉"需要装配才能跑"的两处，被测的写键逻辑保持真实
    _e._build_strategy_context = lambda *a, **k: {}
    _e._detect_context_signal = lambda *a, **k: {}
    return _e


def _sc_of(_e):
    for _et, _pl in _e._sent:
        if isinstance(_pl, dict) and "strategy_context" in _pl:
            return _pl["strategy_context"]
    return None


def test_cortex_passes_tech_intent_type():
    _e = _mk_cortex()
    _e._route_by_intent("cid1", "代码", "这个函数报错怎么修", "用户",
                        intent_type="技术推理")
    _sc = _sc_of(_e)
    assert _sc is not None, "没有发出推理请求"
    assert _sc.get("qica_intent_type") == "技术推理", _sc


def test_cortex_passes_meta_intent_type():
    _e = _mk_cortex()
    _e._route_by_intent("cid2", "状态", "你最近运行得怎么样", "用户",
                        intent_type="元认知报告")
    assert _sc_of(_e).get("qica_intent_type") == "元认知报告"


def test_cortex_default_intent_type_is_empty():
    """老调用点（QICA 未注入的兜底路由）不传该参数 ⇒ 空串，绝不猜一个值。"""
    _e = _mk_cortex()
    _e._route_by_intent("cid3", "知识", "查一下", "用户")
    assert _sc_of(_e).get("qica_intent_type") == ""


def test_cortex_classify_result_call_passes_intent_type():
    """刀2 的关键接线：`_on_classify_result` 必须把 intent_type 传下去。"""
    import inspect
    _src = inspect.getsource(PulseCortex._on_classify_result)
    _i = _src.find("_route_by_intent(")
    assert _i >= 0
    assert "intent_type=intent_type" in _src[_i:], "调用点没传 intent_type"


# =====================================================================
# 刀3/刀4 · would_handler 纯函数
# =====================================================================
@pytest.mark.parametrize("intent,q,n,expect", [
    ("技术推理", "这个函数报错 stackoverflow 怎么修", 2, "code_reasoning"),
    ("技术推理", "接口 500 错误，日志里有 traceback", 1, "code_reasoning"),
    ("技术推理", "什么是量子纠缠", 1, ""),                    # 无代码词
    ("技术推理", "这个函数报错怎么修", 0, ""),                 # 无代码类证据
    ("一般对话", "这个函数报错怎么修", 3, ""),                 # 意图不在触发面
    ("元认知报告", "你最近运行得怎么样", 0, "self_report"),
    ("元认知报告", "随便什么", 0, "self_report"),              # 元认知不看证据
    ("状态查询", "你还好吗", 0, ""),
    ("", "这个函数报错", 3, ""),
])
def test_would_handler_table(intent, q, n, expect):
    _h, _why = IW._m456_would_handler(intent, q, n)
    assert _h == expect, (intent, q, n, _h, _why)
    assert isinstance(_why, str) and _why


def test_code_evidence_count_from_question():
    assert IW._m456_code_evidence_count("这个函数报错怎么修") >= 1
    assert IW._m456_code_evidence_count("什么是量子纠缠") == 0


def test_code_evidence_count_from_context_prefers_sentences():
    _ctx = "第一句讲的是背景。第二句体现了报错堆栈。第三句是函数定义。"
    assert IW._m456_code_evidence_count("随便", _ctx) == 2


# =====================================================================
# 刀3/刀4 · 影子入口（只写 trace，绝不调 handler）
# =====================================================================
class _Ctx:
    def __init__(self, question, intent_type="", memory_context="", handled_by=""):
        self.question = question
        self.correlation_id = "cid-test"
        self._memory_context = memory_context
        self.payload = {"strategy_context": {
            "qica_intent_type": intent_type,
            "handled_by": handled_by,
        }}


@pytest.fixture()
def iw():
    _i = IW.__new__(IW)
    _i._log = lambda *a, **k: None
    return _i


@pytest.fixture()
def stamped(monkeypatch):
    _box = {}
    monkeypatch.setattr(TR, "stamp", lambda cid, **kw: _box.update(kw))
    return _box


def test_shadow_code_reasoning_writes_trace(iw, stamped, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M456_INTENT_SHADOW", True, raising=False)
    _out = iw._m456_intent_shadow(_Ctx("这个函数报错了怎么修", "技术推理"))
    assert _out["would_handler"] == "code_reasoning"
    assert stamped["would_handler"] == "code_reasoning"
    assert stamped["qica_intent_type"] == "技术推理"
    assert stamped["code_evidence_count"] >= 1
    assert stamped["reason"]


def test_shadow_self_report_writes_trace(iw, stamped, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M456_INTENT_SHADOW", True, raising=False)
    _out = iw._m456_intent_shadow(_Ctx("你最近运行得怎么样", "元认知报告"))
    assert _out["would_handler"] == "self_report"
    assert stamped["would_handler"] == "self_report"


def test_shadow_concept_question_not_triggered(iw, stamped, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M456_INTENT_SHADOW", True, raising=False)
    _out = iw._m456_intent_shadow(_Ctx("什么是量子纠缠", "技术推理"))
    assert _out["would_handler"] == ""
    # 未触发也写一笔（否则「影子没跑」和「影子跑了但没触发」分不清）
    assert stamped["would_handler"] is None
    assert "无代码" in stamped["reason"] or "代码" in stamped["reason"]


def test_shadow_skips_when_handled_by_present(iw, stamped, monkeypatch):
    """★刀4 防重复：handler 已实弹处理过 ⇒ 本轮不再预演。"""
    monkeypatch.setattr(config, "ENABLE_M456_INTENT_SHADOW", True, raising=False)
    _out = iw._m456_intent_shadow(
        _Ctx("你最近运行得怎么样", "元认知报告", handled_by="self_report"))
    assert _out["would_handler"] == ""
    assert "已由 self_report 处理" in stamped["reason"]
    assert stamped["handled_by"] == "self_report"


def test_shadow_switch_off_does_nothing(iw, stamped, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M456_INTENT_SHADOW", False, raising=False)
    assert iw._m456_intent_shadow(_Ctx("这个函数报错", "技术推理")) == {}
    assert stamped == {}


def test_shadow_never_calls_handler(iw, stamped, monkeypatch):
    """影子就是影子：连"实弹 handler"的方法都不该被调到。"""
    monkeypatch.setattr(config, "ENABLE_M456_INTENT_SHADOW", True, raising=False)
    _called = []
    monkeypatch.setattr(IW, "_execute_qica_method",
                        lambda self, *a, **k: _called.append(a) or None)
    monkeypatch.setattr(IW, "_execute_meta_cognitive_report",
                        lambda self, *a, **k: _called.append(a) or None)
    iw._m456_intent_shadow(_Ctx("这个函数报错了", "技术推理"))
    iw._m456_intent_shadow(_Ctx("你最近运行得怎么样", "元认知报告"))
    assert _called == []


def test_shadow_never_raises_on_bad_ctx(iw, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M456_INTENT_SHADOW", True, raising=False)

    class _Bad:
        question = None
        correlation_id = None
        payload = None

    assert iw._m456_intent_shadow(_Bad()) == {}
