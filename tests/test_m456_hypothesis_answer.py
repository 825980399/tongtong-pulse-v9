# -*- coding: utf-8 -*-
"""456 刀1：hypotheses 进最终答案（质量闸）+ 刀5：假设类种子。

覆盖任务书验收：
  · 刀1：假设真拼进答案、confidence 低的不进主答案、开关关回 455 行为、
    evidence_ids 只用真实 id（不编造）
  · 刀5：假设类问题 candidate_stats 命中 ≥3 条（基线为 0~3）
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from nucleus import seed_evidence as SE
from nucleus.reasoning import hypothesize as HP
from organs.brain.PulseInnerWorld import PulseInnerWorld as IW

SEED = os.path.join(ROOT, "data", "eval", "human_seed_evidence.json")


@pytest.fixture()
def iw():
    """最小实例：只有 _log 被打桩，其余方法都是被测的真实实现。"""
    _i = IW.__new__(IW)
    _i._log = lambda *a, **k: None
    IW._M456_HYPS.set(None)
    yield _i
    IW._M456_HYPS.set(None)


def _h(claim: str, conf: float, ids: list | None = None) -> dict:
    return {"claim": claim, "confidence": conf, "evidence_ids": list(ids or []),
            "assumptions": ["前提A"], "test": "看读数 X 是否下降"}


# =====================================================================
# 刀1 · 质量闸（纯函数，不碰任何依赖）
# =====================================================================
def test_block_strong_prefix():
    out = IW._m456_hypothesis_block([_h("换方案会更快", 0.8, ["E1"])])
    assert "较有依据的推测：" in out
    assert "【未验证推测】" not in out
    assert "依据 E1" in out


def test_block_mid_prefix():
    out = IW._m456_hypothesis_block([_h("可能变慢", 0.5, ["E1"])])
    assert "【未验证推测】：" in out
    assert "较有依据的推测" not in out


def test_block_low_conf_dropped():
    """conf < 0.35 ⇒ 不进答案（一个字都不出现）。"""
    assert IW._m456_hypothesis_block([_h("纯猜", 0.34, ["E1"])]) == ""
    assert IW._m456_hypothesis_block([_h("纯猜", 0.0, [])]) == ""


def test_block_two_tiers_together():
    out = IW._m456_hypothesis_block([_h("强假设", 0.9, ["E1"]),
                                     _h("弱假设", 0.4, ["E2"]),
                                     _h("被丢弃", 0.1, [])])
    assert "较有依据的推测：" in out and "【未验证推测】：" in out
    assert "强假设" in out and "弱假设" in out
    assert "被丢弃" not in out


def test_block_empty_and_bad_items():
    assert IW._m456_hypothesis_block(None) == ""
    assert IW._m456_hypothesis_block([None, {}, {"claim": ""}]) == ""


def test_block_never_invents_evidence_ids():
    """★刀1 证据约束：没有 id 就不写「依据 E*」，绝不补一个编号上去。

    ★断言只认 `依据 E`：前缀「较有**依据**的推测」本来就有「依据」二字，
      拿裸「依据」当判据会永远假红（本条首版就是这么栽的）。
    """
    out = IW._m456_hypothesis_block([_h("无依据假设", 0.8, [])])
    assert "依据 E" not in out
    assert "无依据假设" in out


# =====================================================================
# 刀1 · 收口拼装
# =====================================================================
def test_attach_appends_to_answer(iw, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M455_HYPOTHESIZE", True, raising=False)
    IW._M456_HYPS.set([_h("换微服务会更快", 0.8, ["E1"])])
    _res = {"status": "ok", "answer": "原来的答案。"}
    out = iw._m456_attach_hypotheses(_res, _Ctx("如果当初不采用微服务架构会怎样"))
    assert out["answer"].startswith("原来的答案。")
    assert "较有依据的推测：" in out["answer"]
    assert out["hypotheses_attached"] == 1
    # 原 dict 不被就地改动（避免影响其它持有者）
    assert _res["answer"] == "原来的答案。"


def test_attach_switch_off_returns_same_object(iw, monkeypatch):
    """★开关回滚：关掉 ⇒ 连 dict 都不复制，行为逐字节回到 455。"""
    monkeypatch.setattr(config, "ENABLE_M455_HYPOTHESIZE", False, raising=False)
    IW._M456_HYPS.set([_h("假设", 0.9, ["E1"])])
    _res = {"status": "ok", "answer": "答案"}
    assert iw._m456_attach_hypotheses(_res, _Ctx("如果当初会怎样")) is _res


def test_attach_non_hypothesis_question_untouched(iw, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M455_HYPOTHESIZE", True, raising=False)
    IW._M456_HYPS.set([_h("假设", 0.9, ["E1"])])
    _res = {"status": "ok", "answer": "答案"}
    assert iw._m456_attach_hypotheses(_res, _Ctx("深度学习是什么")) is _res


def test_attach_without_answer_untouched(iw, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M455_HYPOTHESIZE", True, raising=False)
    IW._M456_HYPS.set([_h("假设", 0.9, ["E1"])])
    _res = {"status": "skipped", "reason": "空问题"}
    assert iw._m456_attach_hypotheses(_res, _Ctx("如果当初会怎样")) is _res


def test_attach_is_idempotent(iw, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M455_HYPOTHESIZE", True, raising=False)
    _claim = "换方案会更快"
    _block = IW._m456_hypothesis_block([_h(_claim, 0.8, ["E1"])])
    _res = {"status": "ok", "answer": "原答案\n\n" + _block}
    IW._M456_HYPS.set([_h(_claim, 0.8, ["E1"])])
    assert iw._m456_attach_hypotheses(_res, _Ctx("如果当初会怎样")) is _res


def test_pending_is_consumed_once(iw):
    IW._M456_HYPS.set([_h("A", 0.8, ["E1"])])
    assert len(iw._m456_take_pending()) == 1
    assert iw._m456_take_pending() == []


def test_pending_is_capped_at_two(iw):
    IW._M456_HYPS.set(None)
    IW._m456_push_pending([_h("A", 0.8), _h("B", 0.7), _h("C", 0.6)])
    assert len(IW._M456_HYPS.get()) == 2


def test_generate_for_answer_uses_this_turn_answer(iw, monkeypatch):
    """v2 没跑过假设时，用本轮答案当唯一证据（只 1 条，不额外检索）。"""
    _seen = {}

    def _fake(question, evidence, **kw):
        _seen["q"] = question
        _seen["ev"] = list(evidence)
        _seen["kw"] = kw
        return {"status": "ok", "hypotheses": [_h("推出来的", 0.7, ["E1"])]}

    monkeypatch.setattr(HP, "hypothesize", _fake)
    out = iw._m456_generate_for_answer(_Ctx("如果当初会怎样"), {"answer": "本轮答案正文"})
    assert [h["claim"] for h in out] == ["推出来的"]
    assert len(_seen["ev"]) == 1 and _seen["ev"][0]["id"] == "E1"
    assert _seen["ev"][0]["text"] == "本轮答案正文"
    assert _seen["kw"]["origin"] == "user_query"


def test_generate_for_answer_no_answer_no_llm(iw, monkeypatch):
    _called = {"n": 0}

    def _fake(*a, **k):
        _called["n"] += 1
        return {"status": "ok", "hypotheses": []}

    monkeypatch.setattr(HP, "hypothesize", _fake)
    assert iw._m456_generate_for_answer(_Ctx("如果当初会怎样"), {}) == []
    assert _called["n"] == 0


def test_attach_generates_when_pending_empty(iw, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M455_HYPOTHESIZE", True, raising=False)
    monkeypatch.setattr(HP, "hypothesize",
                        lambda *a, **k: {"status": "ok",
                                         "hypotheses": [_h("补出来的", 0.5, ["E1"])]})
    IW._M456_HYPS.set(None)
    out = iw._m456_attach_hypotheses({"status": "ok", "answer": "主体答案"},
                                     _Ctx("假设负载翻十倍会不会扛不住"))
    assert "【未验证推测】：" in out["answer"]


def test_attach_exception_does_not_break_answer(iw, monkeypatch):
    """拼装失败只 warning 并返回原结果 —— 绝不能把这一轮回答搞没。"""
    monkeypatch.setattr(config, "ENABLE_M455_HYPOTHESIZE", True, raising=False)
    IW._M456_HYPS.set([_h("假设", 0.9, ["E1"])])

    def _boom(cls, hyps):
        raise RuntimeError("boom")

    monkeypatch.setattr(IW, "_m456_hypothesis_block", classmethod(_boom))
    _res = {"status": "ok", "answer": "答案"}
    assert iw._m456_attach_hypotheses(_res, _Ctx("如果当初会怎样")) is _res


# =====================================================================
# 刀1 · 触发判据（事实型不做假设）
# =====================================================================
@pytest.mark.parametrize("q,expect", [
    ("如果当初不采用微服务架构会怎样", True),
    ("假设负载翻十倍会不会扛不住", True),
    ("推测一下明年的技术栈会怎么变", True),
    ("深度学习是什么", False),
    ("这个接口多少 QPS", False),
    ("你是谁", False),
])
def test_is_hypothesis_question(q, expect):
    assert IW._m455_is_hypothesis_question(q) is expect


# =====================================================================
# 刀5 · 假设类种子
# =====================================================================
def _seed_json():
    with open(SEED, encoding="utf-8") as _f:
        return json.load(_f)


def test_seed_file_has_40_and_d9():
    d = _seed_json()
    assert d["count"] == 40 and len(d["items"]) == 40
    _d9 = [x for x in d["items"] if x["dim"] == "D9_hypothesis"]
    assert len(_d9) == 10
    assert len({x["question_pattern"] for x in _d9}) == 10


def test_seed_d9_items_are_wellformed():
    d = _seed_json()
    for x in d["items"]:
        if x["dim"] != "D9_hypothesis":
            continue
        for k in ("question_pattern", "method", "outcome", "caveat",
                  "confidence", "source", "reward", "timestamp"):
            assert x.get(k) not in (None, ""), (x["id"], k)
        assert len(x["caveat"]) >= 10, x["id"]
        assert x["source"] == "human_seed" and x["reward"] == 1.0


@pytest.mark.parametrize("q", [
    "如果当初不采用微服务架构会怎样",
    "假设负载翻十倍会不会扛不住",
    "假如把缓存层去掉，系统会怎样",
    "如果我当时选了另一条路会怎样",
])
def test_hypothesis_queries_hit_at_least_3(q):
    """★任务书刀5 验收：假设类问题命中 ≥3 条（基线为 0）。"""
    s = SE.candidate_stats(q, score_mode="query")
    assert s["available"] is True
    assert s["raw_admitted"] >= 3, s
    assert s["injectable"] >= 3, s


def test_seed_total_60_and_dims_9():
    s = SE.stats()
    assert s["total"] == 60, s
    assert s["by_source"] == {"human_seed": 40, "kal": 20}, s
    assert s["by_dim"].get("D9_hypothesis") == 10, s


class _Ctx:
    """最小上下文：`_m456_attach_hypotheses` 只读 `.question`。"""

    def __init__(self, question):
        self.question = question
