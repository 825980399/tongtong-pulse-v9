# -*- coding: utf-8 -*-
"""423 批刀1 门控单测：候选窗口放开（分层召回）。

判据来源一律**另一个入口**：
  * 「池里有哪些条目 / 它们在时间上排第几」由测试自己构造并用 `_Pool` 契约复算，
    不拿 `top_hits` 的输出当基准；
  * 「命中是否含目标」比对的是**目标条目的 id**（构造时写死的），不是 top_hits 自报。
"""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.observability import experience_hits as eh


class _Pool:
    """真池契约替身：`query_experiences(limit)` = **按时间倒序取最近 limit 条**。

    与生产 `ExperiencePool.query_experiences` 的 `results.sort(-timestamp)[:limit]`
    逐字同语义（423 刀1 T0 读的就是这段实现）。
    """

    def __init__(self, rows):
        self.rows = sorted(rows, key=lambda r: -float(r.get("timestamp") or 0))
        self.limits: list[int] = []

    def query_experiences(self, limit=10):
        self.limits.append(int(limit))
        return [dict(r) for r in self.rows[:int(limit)]]


def _row(i, text, ts):
    return {"id": f"p{i:05d}", "summary": text, "timestamp": float(ts),
            "is_template": False}


def test_01_constants_are_layered_not_single_shot():
    """窗口必须真的放开，且分层常量的关系自洽（不是把 200 换个数字）。"""
    assert eh.CANDIDATE_LIMIT >= 500, "首层窗口必须 ≥500（200 → 500 是本刀的最小目标）"
    assert eh.CANDIDATE_GROWTH >= 2, "扩窗倍数 <2 就不是分层，是线性爬"
    assert eh.CANDIDATE_MAX >= eh.CANDIDATE_LIMIT * 4, (
        "上限至少要能覆盖首层的 4 倍，否则第二次扩窗就被夹死")
    assert eh.CANDIDATE_MAX >= 2000, "任务书要求'至少 2000 条'"


def test_02_small_pool_stops_after_one_layer():
    """池比首层小 ⇒ 一次查询就到底，不许无意义地再扩一次。"""
    pool = _Pool([_row(i, f"预测 失败 记录 {i}", 1000 + i) for i in range(10)])
    r = eh.top_hits_ex("预测 失败", top_k=2, pool=pool)
    assert len(r["layers"]) == 1, "池只有 10 条 < 首层窗口，必须一层就停"
    assert r["exhausted"] is True, "返回条数 < 请求窗口 ⇒ 必须标记已到底"
    assert pool.limits == [eh.CANDIDATE_LIMIT], "只许查一次，且用首层窗口"
    assert len(r["hits"]) == 2


def test_03_big_pool_extends_when_target_outside_first_layer():
    """目标条目落在首层窗口之外 ⇒ 必须扩窗把它捞回来。

    构造：2000 条，目标在时间上排第 600 位（index 600），首层 500 拿不到。
    """
    rows = [_row(i, f"无关 内容 {i}", 10_000 - i) for i in range(2000)]
    # 目标：时间排第 600（比前 600 条都旧），文本含 query 词片
    rows[600] = _row(600, "预测 失败 的特征与处置", 10_000 - 600)
    pool = _Pool(rows)

    r = eh.top_hits_ex("预测 失败", top_k=2, pool=pool)
    assert len(r["layers"]) >= 2, "首层 500 拿不到目标 ⇒ 必须扩到第二层"
    assert r["layers"][0]["window"] == eh.CANDIDATE_LIMIT
    assert r["layers"][0]["hits"] == 0, "首层确实一条都没命中（这是本用例的前提）"
    assert r["layers"][-1]["window"] > eh.CANDIDATE_LIMIT
    assert any(h["id"] == "p00600" for h in r["hits"]), "扩窗后必须把目标捞回来"

    # 反证：显式单窗口（423 之前的语义）就是拿不到 —— 证明本用例有判别力
    old = eh.top_hits("预测 失败", top_k=2, pool=pool, limit=200)
    assert not any(h["id"] == "p00600" for h in old), "limit=200 下本不该命中（对照臂）"


def test_04_top_hits_return_shape_unchanged():
    """`top_hits` 的返回形态必须与 423 之前逐字一致（list / 空 / ≤top_k）。"""
    pool = _Pool([_row(i, f"预测 失败 记录 {i}", 1000 + i) for i in range(5)])
    _hits = eh.top_hits("预测 失败", top_k=2, pool=pool)
    assert isinstance(_hits, list)
    assert len(_hits) <= 2
    assert set(_hits[0]) >= {"id", "score", "score_weighted", "weight",
                             "is_template", "text", "ts", "motivation", "dup"}
    assert eh.top_hits("完全无关的问题 甲乙丙", top_k=2, pool=pool) == []
    assert eh.top_hits("", top_k=2, pool=pool) == []
    assert eh.top_hits("预测 失败", top_k=2, pool=_Pool([])) == []


def test_05_observe_still_calls_top_hits(monkeypatch):
    """`observe` 必须继续走 `top_hits` —— 既有测试用 monkeypatch 打这个桩。

    ★这条是**从失败里长出来的**：首版让 observe 直调 `top_hits_ex`，
      `test_m411_k4::test_51` 的桩当场失效、去查生产池而红。
    """
    _p = _Pool([_row(1, "预测 失败 记录", 1000.0)])
    _orig = eh.top_hits
    _seen = {"n": 0}

    def _spy(q, top_k=2, pool=None, limit=None):
        _seen["n"] += 1
        return _orig(q, top_k=top_k, pool=_p)

    monkeypatch.setattr(eh, "top_hits", _spy)
    r = eh.observe("预测 失败", pool=None)
    assert _seen["n"] == 1, "observe 必须调用 top_hits 一次（桩点契约）"
    assert r["n"] == len(r["hits"]) == 1
    assert "经验命中" in r["log"]


def test_06_observe_reports_candidate_window():
    """日志与返回值里的候选口径必须跟着首层窗口走（不再是写死的 200）。"""
    _p = _Pool([_row(i, f"预测 失败 {i}", 1000 + i) for i in range(3)])
    r = eh.observe("预测 失败", pool=_p)
    assert r["candidates"] == 3, "候选数必须是真实池条数"
    assert f"窗口 {eh.CANDIDATE_LIMIT}" in r["log"], r["log"]
    assert _p.limits and all(x == eh.CANDIDATE_LIMIT for x in _p.limits), (
        "observe 的每次 query 都应使用首层窗口常量")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
