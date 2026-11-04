# -*- coding: utf-8 -*-
"""450 刀3：O-A4 OBSERVE 影子（旁路记录 / 差异计数 / 不改决策 / 生产路径护栏）。"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import nucleus.field.active_memory_dual as AMD


@pytest.fixture()
def trace(tmp_path, monkeypatch):
    _p = os.path.join(str(tmp_path), "o_a4_observe_trace.jsonl")
    monkeypatch.setattr(config, "M450_OA4_TRACE_PATH", _p, raising=False)
    monkeypatch.setattr(config, "ENABLE_M450_OA4_OBSERVE", True, raising=False)
    monkeypatch.setattr(config, "ENABLE_M383_OA4_SHADOW", True, raising=False)
    # 默认把汇总间隔拉长：多数用例只关心"差异行"，不想被汇总行干扰
    monkeypatch.setattr(config, "M450_OA4_SUMMARY_SEC", 3600.0, raising=False)
    monkeypatch.setattr(config, "O_A4_DETERMINISTIC_RULES",
                        [("现在几点", "rule_current_time")], raising=False)
    AMD.reset_observe_stats()
    AMD.reset_shadow_stats()
    return _p


def _rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


# ---------------------------------------------------------------- 计数
def test_规则命中记一条差异(trace):
    _o = AMD.observe_llm_answer("现在几点？", "21:20")
    assert _o["rule_hit"] is True, _o
    _s = AMD.o_a4_observe_stats()
    assert _s["scored"] == 1, _s
    assert _s["rule_hit"] == 1, _s
    assert _s["diverged"] == 1, _s
    _r = _rows(trace)
    assert len(_r) == 1, _r
    assert _r[0]["kind"] == "observe", _r[0]
    assert _r[0]["o_a4_suggestion"] == "deterministic:rule_current_time", _r[0]
    assert _r[0]["actual_decision"] == "llm", _r[0]
    assert _r[0]["diverged"] is True, _r[0]


def test_无规则只进汇总不逐条落(trace, monkeypatch):
    """无规则 ⇒ 不落 observe 行；但汇总行必须落（否则"文件还在写"无从验证）。"""
    monkeypatch.setattr(config, "M450_OA4_SUMMARY_SEC", 0.0, raising=False)
    AMD.observe_llm_answer("随便聊聊", "今天不错")
    _s = AMD.o_a4_observe_stats()
    assert _s["scored"] == 1 and _s["no_rule"] == 1, _s
    assert _s["diverged"] == 0, _s
    _kinds = [r["kind"] for r in _rows(trace)]
    assert "observe" not in _kinds, _kinds
    assert _kinds.count("summary") >= 1, _kinds


def test_汇总行带覆盖率与可省未省率(trace, monkeypatch):
    monkeypatch.setattr(config, "M450_OA4_SUMMARY_SEC", 0.0, raising=False)
    AMD.observe_llm_answer("现在几点？", "21:20")
    AMD.observe_llm_answer("随便聊聊", "嗯")
    _sum = [r for r in _rows(trace) if r["kind"] == "summary"]
    assert _sum, _rows(trace)
    _s = _sum[-1]
    assert _s["scored"] == 2, _s
    assert _s["rule_hit"] == 1, _s
    assert _s["rule_coverage_pct"] == 50.0, _s
    assert _s["diverged_pct_of_hit"] == 100.0, _s
    assert _s["mode"] == "observe", _s


# ---------------------------------------------------------------- 不改决策
def test_返回值与383逐字一致(trace):
    """★包装层不得改语义：返回值必须等于内层实现。"""
    _a = AMD._m383_observe_impl("现在几点？", "21:20")
    AMD.reset_shadow_stats()
    _b = AMD.observe_llm_answer("现在几点？", "21:20")
    assert set(_a) == set(_b), (_a, _b)
    for _k in _a:
        assert _a[_k] == _b[_k], (_k, _a[_k], _b[_k])


def test_影子关掉时不记分母(trace, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M383_OA4_SHADOW", False, raising=False)
    _o = AMD.observe_llm_answer("现在几点？", "21:20")
    assert _o["on"] is False, _o
    assert AMD.o_a4_observe_stats()["scored"] == 0, AMD.o_a4_observe_stats()


def test_观测总开关关掉不落盘(trace, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M450_OA4_OBSERVE", False, raising=False)
    AMD.observe_llm_answer("现在几点？", "21:20")
    assert _rows(trace) == [], _rows(trace)
    assert AMD.o_a4_observe_stats()["scored"] == 0


# ---------------------------------------------------------------- 护栏
def test_测试进程不许写默认生产路径(monkeypatch):
    """★446/447 三次同型坑的护栏：pytest 里默认生产路径必须拒写。"""
    monkeypatch.setattr(config, "M450_OA4_TRACE_PATH",
                        AMD._M450_OA4_TRACE_REL_DEFAULT, raising=False)
    monkeypatch.setattr(config, "ENABLE_M383_OA4_SHADOW", True, raising=False)
    monkeypatch.setattr(config, "O_A4_DETERMINISTIC_RULES",
                        [("现在几点", "rule_current_time")], raising=False)
    _default = os.path.join(AMD._M450_OA4_ROOT, AMD._M450_OA4_TRACE_REL_DEFAULT)
    _before = os.path.getsize(_default) if os.path.exists(_default) else -1
    AMD.reset_observe_stats()
    AMD.observe_llm_answer("现在几点？", "21:20")
    _after = os.path.getsize(_default) if os.path.exists(_default) else -1
    assert _after == _before, (_before, _after)


def test_shadow_stats带observe块(trace):
    AMD.observe_llm_answer("现在几点？", "21:20")
    _s = AMD.shadow_stats()
    assert "observe" in _s, sorted(_s)
    assert _s["observe"]["mode"] == "observe", _s["observe"]
    assert _s["observe"]["scored"] == 1, _s["observe"]
