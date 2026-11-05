# -*- coding: utf-8 -*-
"""455 刀1：种子证据跳过 0.65 闸（专属下限 0.55）+ 不抢检索命中的位。

★本文件守的四件事
  1. 种子候选带 ``seed_override`` 标记（三条路都要带：candidates / candidate_stats / PulseLung）；
  2. ``build_injection`` 对种子用 0.55、对其他候选仍用 0.65（**口径不能一起松**）；
  3. 排序上种子沉底（种子的 quality_weight≈0.8 不该把真正命中的经验挤下 top_k）；
  4. 验收入口 ``candidate_stats`` 与线上**同一条判据**（同一函数 ``score_floor``）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus import evidence_injection as EI
from nucleus import seed_evidence as SE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LUNG = os.path.join(ROOT, "organs", "body", "PulseLung.py")


def _item(text, score, *, seed=False, qw=0.8, cls="human_seed", reward=1.0,
          template=False):
    d = {"text": text, "score": score, "source_class": cls, "quality_weight": qw,
         "result_reward": {"intensity": reward}, "is_template": template,
         "source": "t"}
    if seed:
        d["seed_override"] = True
    return d


# ---------------------------------------------------------------- 判据本体
def test_seed_floor_is_055():
    assert EI.seed_min_score() == 0.55
    assert getattr(config, "M454_SEED_MIN_SCORE", None) == 0.55


def test_score_floor_only_affects_seed():
    base, seed = 0.65, EI.seed_min_score()
    assert EI.score_floor(_item("x", 0.6), base) == 0.65
    assert EI.score_floor(_item("x", 0.6, seed=True), base, seed) == 0.55


def test_seed_passes_at_060_but_plain_does_not():
    hit = _item("普通命中", 0.60, cls="qa", qw=0.6)
    seed = _item("种子命中", 0.60, seed=True)
    out = EI.build_injection([hit, seed], min_score=0.65, top_k=5, max_chars=120)
    texts = [x["text"] for x in out["items"]]
    assert "种子命中" in texts, out
    assert "普通命中" not in texts, out
    assert out["dropped"].get("low_score") == 1, out["dropped"]


def test_seed_below_055_still_dropped():
    """0.55 是下限不是 0 门槛 —— 完全不相关的种子不该硬进（任务书刀1 第 3 行）。"""
    seed = _item("毫不相关的种子", 0.54, seed=True)
    out = EI.build_injection([seed], min_score=0.65, top_k=5, max_chars=120)
    assert out["items"] == []
    assert out["dropped"].get("low_score") == 1


def test_seed_sinks_below_retrieved_hit():
    """种子的 qw(0.9) 高于命中(0.5)，但 top_k=1 时必须留**命中**（种子只补位）。

    ★reward 必须 ≥0.5：注入层还有一道 ``M453R_MIN_REWARD`` 闸，夹具给 0.0 会让命中
      以 ``low_reward`` 被丢，只剩种子 —— 那样测到的是 reward 闸，不是沉底排序。
    """
    hit = _item("检索真正命中", 0.70, cls="qa", qw=0.50, reward=0.6)
    seed = _item("人工种子", 0.60, seed=True, qw=0.90)
    out = EI.build_injection([seed, hit], min_score=0.65, top_k=1, max_chars=120)
    assert "low_reward" not in out["dropped"], out["dropped"]   # 前置：命中没被 reward 闸误伤
    assert [x["text"] for x in out["items"]] == ["检索真正命中"], out


def test_seed_still_goes_through_sanitize_and_topk():
    seed = _item("种" * 300, 0.60, seed=True)
    out = EI.build_injection([seed], min_score=0.65, top_k=5, max_chars=40)
    assert len(out["items"]) == 1
    assert len(out["items"][0]["text"]) <= 40, out["items"][0]["text"]


def test_switch_off_disables_seed_floor(monkeypatch):
    monkeypatch.setattr(config, "M454_SEED_MIN_SCORE", 0.65, raising=False)
    seed = _item("种子0.60", 0.60, seed=True)
    out = EI.build_injection([seed], min_score=0.65, top_k=5, max_chars=120)
    assert out["items"] == []


# ---------------------------------------------------------------- 三条路都带标记
def test_candidates_carry_seed_override():
    rows = SE.candidates("对比 A 和 B 的性能差异", limit=6)
    assert rows
    assert all(r.get("seed_override") is True for r in rows), rows[:2]


def test_lung_marks_seed_override():
    """生产合并点必须显式打标（任务书刀1 的改动位置）。"""
    with open(LUNG, encoding="utf-8") as f:
        src = f.read()
    assert '_s454["seed_override"] = True' in src


def test_candidate_stats_uses_same_floor():
    """★验收入口与线上同一条判据：stats 用 score_floor，不是自己写 0.65。"""
    s = SE.candidate_stats("我的代码为什么总是报错", score_mode="query")
    assert s["available"] is True
    assert s["seed_min_score"] == 0.55
    rows = SE.load_rows()
    expect = sum(1 for r in rows
                 if SE._match_score("我的代码为什么总是报错", r.get("question_pattern")) >= 0.55)
    assert s["raw_admitted"] == expect, (s["raw_admitted"], expect)
    assert s["raw_admitted"] >= 10, s


def test_query_mode_hits_are_query_dependent():
    """★任务书验收「≥10 条」是**查询相关**的：本用例把真实分布固化，防止后人误读。

    实测（50 条种子 / 0.55 下限）：假设类问题常只有 0~4 条，通用技术问题 7~20 条。
    """
    lo = SE.candidate_stats("假设系统负载突然翻十倍会怎样", score_mode="query")
    hi = SE.candidate_stats("我的代码为什么总是报错", score_mode="query")
    assert lo["raw_admitted"] < hi["raw_admitted"], (lo["raw_admitted"], hi["raw_admitted"])
    assert hi["raw_admitted"] >= 10
    assert lo["injectable"] <= lo["raw_admitted"]
