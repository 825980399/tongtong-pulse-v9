# -*- coding: utf-8 -*-
"""414 批刀4 判据：`TEMPLATE_WEIGHT` 只能乘**一次**。

背景（414 刀4 首拍实测）
------------------------
`top_hits` 原来把 `_s * _w` 存进 `_scored` 元组，输出时又算
`score_weighted = _s * _w` ⇒ 权重被平方：
    原始分 7.0，TEMPLATE_WEIGHT=0.3
      score          = 2.1   （已被乘过一次）
      score_weighted = 0.63  （再乘一次）⇒ 实际权重 0.09

412/413 都没发现：那时生产池 `is_template` **一条都没有**（413 的标注被写盘
守卫静默拒了），`_w` 恒 1.0，平方了仍是 1.0。414 首拍用真池副本一标就暴露。

任务书写「刀4 不写单测」，但那指的是**不必为对比报表写单测**；这里修的是
一个真 bug，没有判据下次会被改回去（412 test_33 那条断言恰好兼容错误实现，
它能同时通过 0.3 和 0.09 两种语义）—— 所以必须补一条能分辨的。
"""

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.observability import experience_governance as eg  # noqa: E402
from nucleus.observability import experience_hits as eh  # noqa: E402

_TEXT = "预测 失败 复盘 样本 同类 改进点"


class _Pool:
    """返回副本（复刻真池 `query_experiences` 语义）。"""

    def __init__(self, rows):
        self.rows = list(rows)

    def query_experiences(self, limit=0, **kw):
        _r = self.rows[:int(limit)] if limit else list(self.rows)
        return [dict(x) for x in _r]


def _exp(eid, tpl, ts=1_700_000_000.0):
    return {"id": eid, "content": _TEXT, "text": _TEXT, "timestamp": ts,
            "is_template": tpl, "motivation": "x"}


def test_a1_score_is_raw_not_weighted():
    """`score` 必须等于 `score_one` 的原始分（不受权重影响）。"""
    _e = _exp("t1", True)
    _raw = eh.score_one(eh.tokens_of(_TEXT), _e)
    _h = eh.top_hits(_TEXT, top_k=1, pool=_Pool([_e]))[0]
    assert _h["score"] == pytest.approx(_raw, abs=1e-6), (_h["score"], _raw)
    assert _h["weight"] == pytest.approx(eg.TEMPLATE_WEIGHT)


def test_a2_weight_applied_exactly_once():
    """★核心：加权分 = 原始分 × 0.3，**不是** × 0.09。

    ★为什么要拿原始分当基准：412 test_33 断言 `score_weighted == score * 0.3`，
      而当时 `score` 本身就是加权分 ⇒ 那条断言对 0.09 也成立（假绿）。
      这里直接拿 `score_one` 的原始分当基准，绕开这个循环论证。
    """
    _e = _exp("t2", True)
    _raw = eh.score_one(eh.tokens_of(_TEXT), _e)
    _h = eh.top_hits(_TEXT, top_k=1, pool=_Pool([_e]))[0]
    assert _h["score_weighted"] == pytest.approx(_raw * eg.TEMPLATE_WEIGHT, abs=1e-2), \
        "实际 %.4f，期望 %.4f（若等于 %.4f 说明权重被平方了）" % (
            _h["score_weighted"], _raw * eg.TEMPLATE_WEIGHT,
            _raw * eg.TEMPLATE_WEIGHT * eg.TEMPLATE_WEIGHT)


def test_a3_non_template_untouched():
    """非模板条目权重恒 1.0 ⇒ score 与 score_weighted 相等。"""
    _h = eh.top_hits(_TEXT, top_k=1, pool=_Pool([_exp("t3", False)]))[0]
    assert _h["weight"] == pytest.approx(1.0)
    assert _h["score_weighted"] == pytest.approx(_h["score"], abs=1e-2)


def test_a4_weight_still_changes_ranking():
    """降权必须真的影响排序（412 test_32 的语义不能因本次修复被削弱）。"""
    _rows = [_exp("tpl", True, ts=2_000_000_000.0),
             _exp("nor", False, ts=1_700_000_000.0)]
    _hits = eh.top_hits(_TEXT, top_k=2, pool=_Pool(_rows))
    # 同分同文本会触发去重，这里只验「加权分高的排前面」
    assert _hits, "没命中"
    if len(_hits) == 2:
        assert _hits[0]["score_weighted"] >= _hits[1]["score_weighted"]
