# -*- coding: utf-8 -*-
"""414 批刀2 单测：`narrative_events` 进 jsonl delta。

契约要点
--------
* `delta` 从 412 的三格扩到**四格**，第四格仍是 cell 结构
  `{cur, prev, delta, source, reason}` —— 取不到写 reason，**不填 0 假充**；
* **顶层** `narrative_events` 仍是「当日条数 int」（`load()` 要读它），
  与 `delta.narrative_events`（与前一日之差）语义不同，两者并存；
* 开关 `ENABLE_M414_NARRATIVE_EVENTS` 关闭 ⇒ `delta` 退回三格（412 契约可秒级回退）；
* 412 既有的「键集恰好是这些」判据语义不变，只是键集多了一格（已同步改）。
"""

import io
import json
import os
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.observability import narrative_digest as nd  # noqa: E402
from nucleus.observability import narrative_store as ns  # noqa: E402


def _at(y, m, d, h, mi=0):
    return time.mktime((y, m, d, h, mi, 0, 0, 0, -1))


_D1 = _at(2026, 10, 31, 12)
_P1 = _at(2026, 11, 1, 4)       # 04:00 落 ⇒ 摘要日 10-31
_T6 = _at(2026, 11, 1, 6, 30)   # 06:30 落 ⇒ 摘要日 11-01
_P2 = _at(2026, 11, 2, 4)       # 次日 04:00 落 ⇒ 摘要日 11-01


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    ns.set_data_dir(str(tmp_path))
    yield
    ns.set_data_dir(None)


def _evs(base, n=4):
    return [{"id": int(time.strftime("%d%H%M", time.localtime(base))) + i,
             "content": "第%d件事" % i, "type": "conversation", "user_name": "小林",
             "emotional_tone": "neutral", "timestamp": base + i * 60}
            for i in range(n)]


def _mk_organ(events=None):
    from organs.identity.PulseNarrativeSelf import PulseNarrativeSelf
    _w = PulseNarrativeSelf.__new__(PulseNarrativeSelf)
    _w._narrative_events = list(events or [])
    _w._weekly_reports = []
    _w._record_count = len(events or [])
    _w._narrative_store_state = {}
    _w.node_pool = None
    _w.info_field = None
    _w._organs_ref = {}
    _w._dynamic_values = {}
    return _w


# ================================================================ A row_of 契约

def test_a1_delta_has_four_cells_by_default():
    _r = ns.row_of("20261101")
    assert set(_r["delta"]) == {"nodes", "llm_ratio", "patches",
                                "narrative_events"}, sorted(_r["delta"])
    assert _r["delta"]["narrative_events"] is None


def test_a2_top_level_narrative_events_is_still_the_count():
    """顶层是「当日条数」，不是 cell —— 两者语义不同，都要在。"""
    _r = ns.row_of("20261101", narrative_events=7)
    assert _r["narrative_events"] == 7
    assert isinstance(_r["narrative_events"], int)


def test_a3_switch_off_falls_back_to_three_cells(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M414_NARRATIVE_EVENTS", False, raising=False)
    _r = ns.row_of("20261101", narrative_events=7,
                   delta_narrative_events={"cur": 7, "prev": 3, "delta": 4,
                                           "source": "x"})
    assert set(_r["delta"]) == {"nodes", "llm_ratio", "patches"}, sorted(_r["delta"])
    # 顶层不受这个开关影响（那是 412 的契约）
    assert _r["narrative_events"] == 7


def test_a4_delta_events_does_not_leak_into_extra():
    """`delta_narrative_events` 是显式参数，绝不能掉进 `extra`。"""
    _r = ns.row_of("20261101", delta_narrative_events={"cur": 1})
    assert (_r.get("extra") or {}).get("delta_narrative_events") is None
    assert _r["delta"]["narrative_events"] == {"cur": 1}


def test_a5_unknown_fields_still_kept():
    """414 的改动不能把 412「未知字段不吞」的纪律弄丢。"""
    _r = ns.row_of("20261101", something_new=42)
    assert (_r.get("extra") or {}).get("something_new") == 42


# ================================================================ B diff_of cell

def test_b1_events_cell_shape():
    _d = nd.diff_of({"nodes": {"value": 1}, "llm_ratio": {"value": 0.5},
                     "patches": {"value": 0}}, prev_row={"narrative_events": 3},
                    day_events=8)
    _c = _d["narrative_events"]
    assert set(("cur", "prev", "delta")) <= set(_c), _c
    assert _c["cur"] == 8 and _c["prev"] == 3 and _c["delta"] == 5


def test_b2_missing_values_write_reason_not_zero():
    """取不到 = None + reason，**不是 0**（0 会被读成"没有变化"）。"""
    _d = nd.diff_of({}, prev_row=None, day_events=None)
    _c = _d["narrative_events"]
    assert _c["cur"] is None
    assert _c["delta"] is None
    assert _c.get("reason"), _c


# ================================================================ C 端到端落盘

def _read_rows(day_stamp):
    _p = os.path.join(ns.narrative_dir(), "%s%s%s"
                      % (ns.FILE_PREFIX, day_stamp, ns.FILE_SUFFIX))
    if not os.path.isfile(_p):
        return [], _p
    _out = []
    for _ln in io.open(_p, encoding="utf-8", newline="").read().split("\n"):
        if _ln.strip():
            _out.append(json.loads(_ln))
    return _out, _p


def test_c1_persist_writes_fourth_cell_with_real_count():
    _ev_list = _evs(_D1, n=5)
    _w = _mk_organ(_ev_list)
    _r = _w.persist(now=_P1)
    assert _r["ok"] is True, _r
    _rows, _p = _read_rows(ns.day_stamp(_D1))
    assert _rows, "没落盘：%s" % _p
    _row = _rows[-1]
    _c = _row["delta"]["narrative_events"]
    assert isinstance(_c, dict), _c
    assert _c["cur"] == 5, _c
    assert _row["narrative_events"] == 5


def test_c2_second_day_sees_prev_count_and_delta():
    """前一日有 5 条、当日 8 条 ⇒ prev=5 / cur=8 / delta=+3。"""
    _mk_organ(_evs(_D1, n=5)).persist(now=_P1)
    _mk_organ(_evs(_T6, n=8)).persist(now=_T6)
    _rows, _ = _read_rows(ns.day_stamp(_T6))
    _c = _rows[-1]["delta"]["narrative_events"]
    assert _c["cur"] == 8, _c
    assert _c["prev"] == 5, _c
    assert _c["delta"] == 3, _c
    assert _c.get("source"), _c


def test_c3_jsonl_is_still_one_line_per_day():
    """加了第四格不能破坏「每天一条」的日级幂等。"""
    _mk_organ(_evs(_D1, n=3)).persist(now=_P1)
    _mk_organ(_evs(_D1, n=3)).persist(now=_P1)
    _rows, _ = _read_rows(ns.day_stamp(_D1))
    assert len(_rows) == 1, len(_rows)


def test_c4_all_four_cells_present_in_jsonl():
    _mk_organ(_evs(_D1, n=2)).persist(now=_P1)
    _rows, _ = _read_rows(ns.day_stamp(_D1))
    assert set(_rows[-1]["delta"]) == {"nodes", "llm_ratio", "patches",
                                       "narrative_events"}, sorted(_rows[-1]["delta"])
