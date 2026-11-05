# -*- coding: utf-8 -*-
"""458 刀2（P1）：experience 检测器「无答案不委派」。

★为什么必须带开关：这条改动同时影响 406/407/408 三套回归与 t146 四条金标。
  默认关（刀2a）⇒ 行为逐字节不变；打开（刀2b）⇒ 检测器 return None，
  把机会交回检测器链，委派收敛到 `_ir_finalize` 一个出口。
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import config
from organs.brain.PulseInnerWorld import PulseInnerWorld


class _Ctx:
    def __init__(self, q="帮我看看这个函数为什么报错"):
        self.question = q
        self.payload = {"correlation_id": "cid-458"}
        self.user_name = "u"
        self.correlation_id = "cid-458"
        self.guidance = None
        self._memory_context = ""
        self._reasoning_start_time = 0.0
        self._question_complexity = 0.3
        self.question_features = {}
        self.tool_requested = False
        self.search_query = q
        self.empathetic_note = ""


class _RX:
    """经验库替身：命中 unknown 类型、置信度 0.95（事故里的那个分数）。"""

    last_search_diagnostics = {"best_score": 0.95, "threshold": 0.5,
                               "n_experiences": 128, "hit": True}

    def search(self, q, exclude_types=None):
        return {"derivation_type": "unknown", "confidence": 0.95,
                "source": "local_fallback",
                "matched_example": "site:baike.baidu.com 编程开发 概念 原理"}


def _mk_iw():
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw.node_pool = None
    iw._logs = []
    iw._fallback_calls = []
    iw._log = lambda lvl, msg="", *a, **k: iw._logs.append(str(msg))
    iw._fallback_to_lung_model = (
        lambda ctx, guidance=None: (iw._fallback_calls.append(ctx) or
                                    {"status": "inference_fallback_to_lung",
                                     "answer": None}))
    return iw


@pytest.fixture()
def rx_patch():
    import nucleus.mnemosyne.ReasoningExperience as _rxmod
    with patch.object(_rxmod, "get_reasoning_experience", lambda: _RX()):
        yield


def test_switch_default_on():
    assert config.ENABLE_M458_EXPERIENCE_NO_DELEGATE is True
    assert PulseInnerWorld._m458_experience_no_delegate() is True


def test_switch_on_returns_none_and_never_delegates(rx_patch):
    iw = _mk_iw()
    out = iw._detect_experience_route(_Ctx())
    assert out is None, f"开关打开后必须 return None，实际 {out}"
    assert iw._fallback_calls == [], "检测器内不许再委派肺（唯一出口在 _ir_finalize）"
    assert any("[458刀2]" in m for m in iw._logs), iw._logs[-3:]


def test_switch_off_keeps_458_before_behavior(rx_patch, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M458_EXPERIENCE_NO_DELEGATE", False, raising=False)
    iw = _mk_iw()
    out = iw._detect_experience_route(_Ctx())
    assert out is not None
    assert out.get("status") == "experience_failed_to_lung", out
    assert len(iw._fallback_calls) == 1, "开关关时必须与改前逐字一致（仍然委派）"


def test_gate_is_before_delegate_in_source():
    """源码接线断言：闸必须在 `_fallback_to_lung_model` **之前** return None。

    ★为什么用行号而不是文本序：`_fallback_to_lung_model` 在整个文件里出现多次，
      只有 AST 能定位到本函数里真正那一次。
    """
    import ast
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "organs", "brain", "PulseInnerWorld.py"),
               encoding="utf-8").read()
    tree = ast.parse(src)
    _fn = None
    for nd in ast.walk(tree):
        if isinstance(nd, ast.FunctionDef) and nd.name == "_detect_experience_route":
            _fn = nd
            break
    assert _fn is not None
    _gate = [n for n in ast.walk(_fn)
             if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "_m458_experience_no_delegate"]
    _dele = [n for n in ast.walk(_fn)
             if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "_fallback_to_lung_model"]
    assert _gate, "闸函数未被调用"
    assert _dele, "委派调用不见了？"
    assert min(n.lineno for n in _gate) < min(n.lineno for n in _dele), \
        "意图闸必须排在委派之前"
