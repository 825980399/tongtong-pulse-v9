# -*- coding: utf-8 -*-
"""451 刀2：对话体验评分校准（委托 ≠ 答得差；confidence 非数值不再炸）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import organs.core.PulseMotivationCycle as MC


class _Pool:
    def __init__(self):
        self.calls = []

    def record_experience(self, **kw):
        self.calls.append(kw)
        return True


def _organ():
    """不走 BasePulseOrgan 构造（会拉起整套器官骨架），只装本方法需要的四个属性。"""
    o = object.__new__(MC.PulseMotivationCycle)
    o.experience_pool = _Pool()
    o._last_inference_record_time = 0.0
    o._inference_record_cooldown = 0.0
    o._log = lambda *a, **k: None
    return o


def _call(o, **kw):
    _p = {"question": "那你在读取你的运行日志的时候会关注什么",
          "answer": "会关注 WARNING", "method": "rule",
          "confidence": 0.9, "user_name": "访客"}
    _p.update(kw)
    return o._on_inference_result(_p)


# ---------------------------------------------------------------- 委托 ≠ 挫败
def test_委托兜底不记挫败():
    """★病灶：本地答不了 → RESULT{answer=None, confidence=0.0} 被当成"答砸了"。"""
    o = _organ()
    _r = _call(o, answer=None, method="meta_not_search", confidence=0.0)
    assert _r["mode"] == "delegated", _r
    _kw = o.experience_pool.calls[-1]
    assert _kw["reward_intensity"] == 0.3, _kw
    assert _kw["emotion_tags"] == ["平静"], _kw
    assert _kw["process_pressure"] == 0.1, _kw
    assert "挫败" not in _kw["emotion_tags"], _kw


def test_空答案但方法不是委托_仍按低分处理():
    """只放过"确实委托"这一种；rule 通道给空答案是真失败，不能一起洗白。"""
    o = _organ()
    _r = _call(o, answer="", method="rule", confidence=0.1)
    assert _r["mode"] == "low", _r
    _kw = o.experience_pool.calls[-1]
    assert _kw["reward_intensity"] == 0.1, _kw
    assert "挫败" in _kw["emotion_tags"], _kw


def test_高置信度仍记正向():
    o = _organ()
    _r = _call(o, confidence=0.9, method="rule", answer="答")
    assert _r["mode"] == "high", _r
    _kw = o.experience_pool.calls[-1]
    assert _kw["reward_intensity"] > 0.8, _kw
    assert _kw["emotion_tags"] == ["满足", "自信"], _kw


def test_中置信度记中性():
    o = _organ()
    _r = _call(o, confidence=0.6, answer="答")
    assert _r["mode"] == "mid", _r
    assert o.experience_pool.calls[-1]["reward_intensity"] == 0.3


# ---------------------------------------------------------------- 健壮性
def test_confidence为None不炸():
    """★`payload.get("confidence", 0.5)` 在键存在且为 None 时返回 None ⇒ `None>=0.8` 抛。"""
    o = _organ()
    _r = _call(o, confidence=None, answer="答")
    assert _r["status"] == "recorded", _r
    assert _r["mode"] == "mid", _r


def test_confidence为字符串不炸():
    o = _organ()
    _r = _call(o, confidence="高", answer="答")
    assert _r["status"] == "recorded", _r


def test_content带模式字段():
    o = _organ()
    _call(o, answer=None, method="inference_fallback_to_lung", confidence=0.0)
    assert "模式：delegated" in o.experience_pool.calls[-1]["content"]


# ---------------------------------------------------------------- 接线护栏
def test_源码里保留委托判据():
    _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "organs", "core", "PulseMotivationCycle.py")
    with open(_p, encoding="utf-8", errors="replace") as f:
        _src = f.read()
    assert "_DELEGATED_METHODS" in _src
    assert "meta_not_search" in _src
    assert "inference_fallback_to_lung" in _src
