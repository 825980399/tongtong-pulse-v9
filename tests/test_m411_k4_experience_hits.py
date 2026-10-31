# -*- coding: utf-8 -*-
"""411 批刀4 单测：经验池命中（experience_hits）—— **先只记不拼**。

钉语义（不钉措辞）：
  * 产出的是**独立键** `experience_hits` / `experience_hit_n`，
    `search_experience`（搜索**工具**经验：best_tool / success_rate）一个字节都不许动；
  * 不命中返回 `[]`，**不返回空壳条目**（调用方据长度判"这次没命中"）；
  * 池里 70% 是同一段模板文本 ⇒ top2 必须**按文本去重**并如实记 `dup`；
  * 每条摘要 ≤ 80 字（任务书要求）；
  * 只算命中 + 打 DEBUG，**不拼进 prompt**。

T0 已推翻任务书的两处措辞（`search_experience` 本来就**不是**恒 None，它是被
搜索工具经验占用的另一个东西），所以本文件测的是"新键不再恒空壳"，
不是"旧键不再恒 None"——见交付报告 §偏差。
"""

from __future__ import annotations

import ast
import io
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.observability import experience_hits as eh  # noqa: E402


class _Pool:
    """经验池替身（真池的 `query_experiences(limit=...)` 契约）。"""

    def __init__(self, rows, raise_on_query=False):
        self.rows = list(rows)
        self.limits = []
        self.raise_on_query = raise_on_query

    def query_experiences(self, limit=10):
        self.limits.append(limit)
        if self.raise_on_query:
            raise RuntimeError("池子坏了（替身）")
        return list(self.rows)[-int(limit):] if limit else list(self.rows)


class _Logger:
    def __init__(self):
        self.lines = []

    def debug(self, msg, *a, **k):
        self.lines.append(str(msg))


def _row(i, summary, ts=0.0, **kw):
    _d = {"id": "e%d" % i, "summary": summary, "timestamp": ts}
    _d.update(kw)
    return _d


def _src(rel):
    _s = io.open(os.path.join(_ROOT, rel), encoding="utf-8", newline="").read()
    return _s.replace("\r\n", "\n")


# ---------------------------------------------------------------- A 分词

def test_01_tokens_of_splits_chinese_runs():
    assert eh.tokens_of("预测 失败") == {"预测", "失败"}


def test_02_tokens_of_empty_or_none():
    assert eh.tokens_of("") == set()
    assert eh.tokens_of(None) == set()
    assert eh.tokens_of("   ") == set()


def test_03_tokens_of_ignores_ascii():
    _t = eh.tokens_of("heart.beat 13 秒 心跳")
    assert "heart" not in _t
    assert all(re.fullmatch(r"[\u4e00-\u9fff]{2,4}", x) for x in _t)
    assert _t, "中文片段还是要抓到的"


# ---------------------------------------------------------------- B 打分

def test_11_score_counts_overlap():
    assert eh.score_one({"预测", "失败"}, {"summary": "预测 失败 复盘"}) == 2


def test_12_score_zero_on_empty_or_bad_input():
    assert eh.score_one(set(), {"summary": "预测 失败"}) == 0
    assert eh.score_one({"预测"}, "不是 dict") == 0
    assert eh.score_one({"预测"}, {"summary": ""}) == 0


def test_13_text_priority_summary_then_content_then_motivation():
    assert eh._text_of({"summary": "S", "content": "C", "motivation": "M"}) == "S"
    assert eh._text_of({"content": "C", "motivation": "M"}) == "C"
    assert eh._text_of({"motivation": "M"}) == "M"
    assert eh._text_of({}) == ""


# ---------------------------------------------------------------- C top_k 召回

def test_21_orders_by_score_then_recency():
    _pool = _Pool([_row(1, "预测 失败 甲", ts=100.0),
                   _row(2, "预测 失败 乙", ts=200.0),
                   _row(3, "预测 丙", ts=999.0)])
    _hits = eh.top_hits("预测 失败", top_k=2, pool=_pool)
    assert [h["id"] for h in _hits] == ["e2", "e1"], "同分按时间新→旧"


def test_22_dedups_identical_texts_and_records_dup():
    """真池里 70% 是同一段模板 ⇒ 不去重的话 top2 是两条一模一样的信息。"""
    _pool = _Pool([_row(1, "预测 失败 模板"), _row(2, "预测 失败 模板"),
                   _row(3, "预测 失败 另一个")])
    _hits = eh.top_hits("预测 失败", top_k=5, pool=_pool)
    _texts = [h["text"] for h in _hits]
    assert len(_texts) == len(set(_texts)), "同文本只留一条"
    assert len(_hits) == 2
    assert max(h["dup"] for h in _hits) == 1, "重复次数要如实记下来"


def test_23_top_k_is_capped_and_has_floor_of_one():
    _pool = _Pool([_row(1, "预测 失败 甲"), _row(2, "预测 失败 乙"),
                   _row(3, "预测 失败 丙")])
    assert len(eh.top_hits("预测 失败", top_k=2, pool=_pool)) == 2
    assert len(eh.top_hits("预测 失败", top_k=0, pool=_pool)) == 1


def test_24_snippet_is_truncated_to_80_chars():
    _long = "预测 失败 " + ("很长的描述" * 60)
    _hits = eh.top_hits("预测 失败", top_k=1, pool=_Pool([_row(1, _long)]))
    assert len(_hits[0]["text"]) <= eh.SNIPPET_CHARS


def test_25_returns_empty_list_when_nothing_matches():
    """不命中就 `[]` —— 不许返回空壳条目（否则调用方会以为"命中了一条空的"）。"""
    assert eh.top_hits("完全无关的问题", top_k=2,
                       pool=_Pool([_row(1, "预测 失败")])) == []
    assert eh.top_hits("预测 失败", top_k=2, pool=_Pool([])) == []
    assert eh.top_hits("", top_k=2, pool=_Pool([_row(1, "预测 失败")])) == []


def test_26_broken_pool_or_bad_rows_do_not_raise():
    assert eh.top_hits("预测 失败", pool=_Pool([], raise_on_query=True)) == []
    _p = _Pool(["字符串行", None, _row(9, "预测 失败")])
    assert [h["id"] for h in eh.top_hits("预测 失败", pool=_p)] == ["e9"]


def test_27_bad_timestamp_is_none_but_hit_survives():
    _hits = eh.top_hits("预测 失败", pool=_Pool([_row(1, "预测 失败",
                                                     ts="2026-10-31 03:00:00")]))
    assert len(_hits) == 1
    assert _hits[0]["ts"] is None


def test_28_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M411_EXPERIENCE_HITS", False, raising=False)
    assert eh.enabled() is False
    assert eh.top_hits("预测 失败", pool=_Pool([_row(1, "预测 失败")])) == []


# ---------------------------------------------------------------- D 池成分

def test_31_pool_composition_measures_template_ratio():
    _p = _Pool([_row(1, "模板A"), _row(2, "模板A"), _row(3, "模板A"),
                _row(4, "另一条", is_summarized=True)])
    _c = eh.pool_composition(_p)
    assert _c["state"] == "ok"
    assert _c["total"] == 4
    assert _c["distinct_texts"] == 2
    assert _c["template_ratio"] == 0.75
    assert _c["with_content"] == 0
    assert _c["summarized"] == 1


def test_32_pool_composition_empty_and_broken():
    _e = eh.pool_composition(_Pool([]))
    assert _e["state"] == "empty"
    assert _e["reason"] == "pool_returned_nothing"

    _b = eh.pool_composition(_Pool([], raise_on_query=True))
    assert _b["state"] == "unavailable"
    assert "query_failed" in _b["reason"]


# ---------------------------------------------------------------- E 观测出口

def test_41_observe_logs_and_returns_hits():
    _lg = _Logger()
    _p = _Pool([_row(1, "预测 失败 甲"), _row(2, "预测 失败 乙")])
    _r = eh.observe("预测 失败", top_k=2, pool=_p, logger=_lg, now=123.0)
    assert _r["enabled"] is True
    assert _r["n"] == 2
    assert _r["candidates"] == 2
    assert _r["at"] == 123.0
    assert "411刀4" in _r["log"]
    assert "经验命中 2 条" in _r["log"]
    assert _lg.lines == [_r["log"]], "日志与返回值必须是同一行文本（便于对账）"


def test_42_observe_logs_zero_and_does_not_fake_hits():
    _lg = _Logger()
    _r = eh.observe("完全无关的问题", pool=_Pool([_row(1, "预测 失败")]), logger=_lg)
    assert _r["n"] == 0
    assert _r["hits"] == []
    assert "经验命中 0 条" in _r["log"]


def test_43_observe_when_disabled_is_silent(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M411_EXPERIENCE_HITS", False, raising=False)
    _lg = _Logger()
    _r = eh.observe("预测 失败", pool=_Pool([_row(1, "预测 失败")]), logger=_lg)
    assert _r == {"enabled": False, "n": 0, "hits": [], "candidates": 0,
                  "at": _r["at"], "log": ""}
    assert _lg.lines == []


def test_44_observe_survives_broken_logger():
    class _Bad:
        def debug(self, *a, **k):
            raise RuntimeError("logger 坏了")

    _r = eh.observe("预测 失败", pool=_Pool([_row(1, "预测 失败")]), logger=_Bad())
    assert _r["n"] == 1, "打日志失败不许影响命中结果"


def test_45_observe_queries_with_candidate_limit():
    _p = _Pool([_row(1, "预测 失败")])
    eh.observe("预测 失败", pool=_p)
    assert _p.limits and all(x == eh.CANDIDATE_LIMIT for x in _p.limits)


# ---------------------------------------------------------------- F 真接线（动态）

def _bare_inner_world():
    """造一个只填了必需属性的 PulseInnerWorld（不启动任何线程/器官）。"""
    from organs.brain.PulseInnerWorld import PulseInnerWorld
    _w = PulseInnerWorld.__new__(PulseInnerWorld)
    _w.hormones = None
    _w.node_pool = None
    return _w


def test_51_build_memory_context_fills_experience_keys(monkeypatch):
    """★动态：真方法跑一遍，两个新键必须被 observe 的结果填上。"""
    _p = _Pool([_row(1, "预测 失败 甲", ts=5.0)])
    _orig = eh.top_hits
    monkeypatch.setattr(
        eh, "top_hits",
        lambda q, top_k=2, pool=None, limit=0: _orig(q, top_k=top_k, pool=_p))
    _w = _bare_inner_world()
    _c = _w._build_memory_context("预测 失败 怎么处理", "测试")
    assert "experience_hits" in _c and "experience_hit_n" in _c
    assert _c["experience_hit_n"] == len(_c["experience_hits"]) == 1
    assert _c["experience_hits"][0]["text"].startswith("预测")
    # ★搜索工具经验不许被覆盖
    assert _c["search_experience"] is None


def test_52_build_memory_context_degrades_when_observe_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("观测坏了（替身）")

    monkeypatch.setattr(eh, "observe", _boom)
    _w = _bare_inner_world()
    _c = _w._build_memory_context("预测 失败 怎么处理", "测试")
    assert _c["experience_hits"] == []
    assert _c["experience_hit_n"] == 0, "异常时给「没命中」，不许让整段记忆上下文崩掉"
    assert _c["user_name"] == "测试"


# ---------------------------------------------------------------- G 静态接线

def _memory_context_body():
    _s = _src("organs/brain/PulseInnerWorld.py")
    _fn = None
    for _n in ast.walk(ast.parse(_s)):
        if isinstance(_n, ast.FunctionDef) and _n.name == "_build_memory_context":
            _fn = _n
            break
    assert _fn is not None
    return _s, "\n".join(_s.split("\n")[_fn.lineno - 1:_fn.end_lineno])


def test_61_block_is_inside_build_memory_context_and_before_return():
    _s, _body = _memory_context_body()
    _i = _body.find("★411 刀4")
    assert _i > 0, "刀4 的块必须真的在 _build_memory_context 里"
    _seg = _body[_i:]
    assert 'context["experience_hits"]' in _seg
    assert 'context["experience_hit_n"]' in _seg
    assert _seg.index("context[\"experience_hits\"]") < _seg.rindex("return context")


def test_62_never_overwrites_search_experience():
    _s, _body = _memory_context_body()
    _i = _body.find("★411 刀4")
    assert _i > 0
    _seg = _body[_i:]
    assert 'context["search_experience"]' not in _seg, (
        "search_experience 是搜索工具经验（best_tool/success_rate），"
        "覆盖它会让下游读到不存在的东西")


def test_63_version_marker():
    assert eh.version().startswith("411.")
