# -*- coding: utf-8 -*-
"""412 批刀1 单测：Step5a 叙事落盘（narrative_store 契约 412.1）。

钉语义（不钉措辞）：
  * 每天**一条**摘要 ⇒ 同天重跑必须**替换**而不是追加第二条（日级幂等）；
  * `headline` / `delta` / `intent` 本批**留空**（413 才填）—— 填任何值都是编造；
  * `empty`（没落过盘）与 `partial`（有文件但读不出内容）**分开**；
  * 连续天数允许**从昨天起算**（04:00 落盘的是前一天 ⇒ 只认今天会每天 00~04 假归零）；
  * 重启回放要能恢复事件体 ⇒ 摘要行必须带 `events`（否则"不归零"是假的）；
  * 阶段判定维度6 的零回归：没落盘 ⇒ **没有新信号**（与改造前逐字一致）。
"""

from __future__ import annotations

import ast
import io
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.observability import narrative_store as ns  # noqa: E402

_TODAY = 1_700_000_000.0


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    ns.set_data_dir(str(tmp_path))
    yield
    ns.set_data_dir(None)


def _events(day: int, n: int = 4):
    return [{"id": day * 100 + i, "content": "第%d天第%d件事" % (day, i),
             "type": "conversation", "user_name": "小林",
             "emotional_tone": "neutral", "timestamp": _TODAY + i}
            for i in range(n)]


def _mk_organ(events=None):
    """裸实例（不触发 __init__ 的注册/发射，只填本刀用到的属性）。"""
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


def _src(rel):
    _s = io.open(os.path.join(_ROOT, rel), encoding="utf-8", newline="").read()
    return _s.replace("\r\n", "\n")


# ---------------------------------------------------------------- A 行格式

def test_01_row_has_all_contract_fields():
    _r = ns.row_of("20261031", generated_at=_TODAY, narrative_events=3, continuous_days=1)
    for _k in ("date", "version", "headline", "delta", "intent",
               "prev_intent_verdict", "evidence_refs", "generated_at"):
        assert _k in _r, "任务书行格式缺 %s" % _k
    assert set(_r["delta"]) == {"nodes", "llm_ratio", "patches"}


def test_02_headline_delta_intent_left_empty():
    """413 才填的字段本批必须是空 —— 填任何值都是编造。"""
    _r = ns.row_of("20261031", generated_at=_TODAY)
    assert _r["headline"] == ""
    assert _r["intent"] == ""
    assert _r["prev_intent_verdict"] is None
    assert _r["delta"] == {"nodes": None, "llm_ratio": None, "patches": None}
    assert _r["evidence_refs"] == []


def test_03_events_are_carried_and_truncation_is_marked():
    _many = [{"id": i, "content": "c%d" % i} for i in range(120)]
    _r = ns.row_of("20261031", generated_at=_TODAY, events=_many)
    assert len(_r["events"]) == ns.EVENTS_PER_DAY
    assert _r["events_truncated"] is True, "截断必须标记，不许静默"
    _few = [{"id": i} for i in range(3)]
    assert ns.row_of("20261031", events=_few)["events_truncated"] is False


def test_04_unknown_fields_are_kept_not_dropped():
    _r = ns.row_of("20261031", generated_at=_TODAY, weekly_reports=2)
    assert _r["extra"]["weekly_reports"] == 2


# ---------------------------------------------------------------- B 落盘与幂等

def test_11_persist_creates_dir_and_file():
    _r = ns.persist_daily(ns.row_of(ns.day_stamp(_TODAY), generated_at=_TODAY), now=_TODAY)
    assert _r["ok"] is True, _r.get("reason")
    assert os.path.isfile(_r["path"])
    assert _r["rows"] == 1


def test_12_same_day_rerun_replaces_not_appends():
    for _ in range(3):
        _r = ns.persist_daily(ns.row_of(ns.day_stamp(_TODAY), generated_at=_TODAY), now=_TODAY)
    assert _r["rows"] == 1, "每天一条 ⇒ 重跑必须替换，不许变成 3 条"
    assert _r["replaced"] == 1


def test_13_date_mismatch_is_refused():
    _r = ns.persist_daily(ns.row_of("19990101", generated_at=_TODAY), now=_TODAY)
    assert _r["ok"] is False
    assert _r["reason"].startswith("date_mismatch")
    assert not os.path.exists(_r["path"])


def test_14_file_is_pure_lf():
    _r = ns.persist_daily(ns.row_of(ns.day_stamp(_TODAY), generated_at=_TODAY), now=_TODAY)
    _b = io.open(_r["path"], "rb").read()
    assert _b.count(b"\r\n") == 0
    assert _b.endswith(b"\n")


def test_15_switch_off_writes_nothing(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M412_NARRATIVE_STORE", False, raising=False)
    assert ns.enabled() is False
    _r = ns.persist_daily(ns.row_of(ns.day_stamp(_TODAY), generated_at=_TODAY), now=_TODAY)
    assert _r["ok"] is False and _r["reason"] == "disabled"
    assert not os.path.isdir(ns.narrative_dir())


# ---------------------------------------------------------------- C 连续天数

def _persist_day(offset_days: int, n: int = 2):
    _now = _TODAY + offset_days * 86400.0
    return ns.persist_daily(
        ns.row_of(ns.day_stamp(_now), generated_at=_now, events=_events(offset_days, n)),
        now=_now)


def test_21_continuous_days_counts_back_from_today():
    for _d in (-2, -1, 0):
        _persist_day(_d)
    assert ns.continuous_days(now=_TODAY)["days"] == 3
    assert ns.continuous_days(now=_TODAY)["gap_days"] == 0


def test_22_gap_stops_the_streak():
    """中间断一天 ⇒ 从断点算，不许把更早的也算进来。"""
    for _d in (-4, -3, -1, 0):
        _persist_day(_d)
    _c = ns.continuous_days(now=_TODAY)
    assert _c["days"] == 2, "断档后应为 2（-1/0），不是 4"


def test_23_yesterday_only_is_allowed_anchor():
    """04:00 落的是**前一天** ⇒ 只认今天会在每天 00~04 之间假归零。"""
    _persist_day(-1)
    _c = ns.continuous_days(now=_TODAY)
    assert _c["days"] == 1
    assert _c["gap_days"] == 1
    assert _c["anchor_is_today"] is False


def test_24_nothing_today_or_yesterday_is_zero_with_reason():
    _persist_day(-5)
    _c = ns.continuous_days(now=_TODAY)
    assert _c["days"] == 0
    assert _c["reason"] == "no_record_today_or_yesterday"


# ---------------------------------------------------------------- D 回放

def test_31_load_recent_window_is_bounded():
    for _d in (-9, -2, -1, 0):
        _persist_day(_d)
    _r = ns.load_recent(days=7, now=_TODAY)
    assert _r["days_with_file"] == 3, "9 天前那份不在 7 天窗内"
    assert len(_r["rows"]) == 3


def test_32_load_recent_caps_rows_and_marks_truncated():
    """cap 裁的是**回放的行数**（每天一行），且必须标记 truncated。"""
    for _d in (-2, -1, 0):
        _persist_day(_d, n=60)          # 每天 60 条 > EVENTS_PER_DAY ⇒ 行里只带 50
    _r = ns.load_recent(days=7, now=_TODAY, cap=2)
    assert _r["truncated"] is True
    assert len(_r["rows"]) == 2
    assert all(len(_x["events"]) == ns.EVENTS_PER_DAY for _x in _r["rows"])


def test_33_bad_lines_are_counted_not_silently_dropped():
    _p = ns.day_path(_TODAY)
    os.makedirs(os.path.dirname(_p), exist_ok=True)
    io.open(_p, "w", encoding="utf-8", newline="\n").write(
        "{不是 json\n" + json.dumps(ns.row_of(ns.day_stamp(_TODAY), generated_at=_TODAY)) + "\n")
    _r = ns.load_recent(days=3, now=_TODAY)
    assert _r["bad_lines"] == 1
    assert len(_r["rows"]) == 1


def test_34_empty_vs_partial_are_different():
    assert ns.load_recent(days=3, now=_TODAY)["state"] == "empty"
    _p = ns.day_path(_TODAY)
    os.makedirs(os.path.dirname(_p), exist_ok=True)
    io.open(_p, "w", encoding="utf-8", newline="\n").write("{坏行\n")
    assert ns.load_recent(days=3, now=_TODAY)["state"] == "partial"


# ---------------------------------------------------------------- E 重启不归零（动态）

def test_41_organ_persist_then_load_restores_events():
    for _d in (-1, 0):
        _now = _TODAY + _d * 86400.0
        assert _mk_organ(_events(_d)).persist(now=_now)["ok"] is True
    _w = _mk_organ([])                      # 模拟重启：全新的空实例
    _r = _w.load(now=_TODAY)
    assert _r["state"] == "ok"
    assert _r["restored"] == 8, "两天 × 4 条"
    assert len(_w._narrative_events) == 8
    assert _w._narrative_events[0]["content"] == "第-1天第0件事"


def test_42_record_count_continues_after_replay():
    """回放后 id 游标必须接上，否则重启后的新事件会与旧事件**撞号**。

    （不调 `_on_record`：它还要节点池/发射链路等一堆器官状态，
      本刀只保证"游标接得上"，撞号与否看游标即可。）
    """
    _mk_organ(_events(0)).persist(now=_TODAY)
    _w = _mk_organ([])
    _w.load(now=_TODAY)
    assert _w._record_count == 3, "id 游标要接上回放的最大 id（0..3 ⇒ 3）"
    assert {int(_e["id"]) for _e in _w._narrative_events} == {0, 1, 2, 3}
    assert int(_w._record_count) + 1 == 4, "下一条的 id 不许与回放回来的撞号"


def test_43_load_without_files_says_empty_not_fabricated():
    _w = _mk_organ([])
    _r = _w.load(now=_TODAY)
    assert _r["state"] == "empty"
    assert _r["restored"] == 0
    assert _w._narrative_events == []


def test_44_stage_summary_is_unchanged_without_persisted_days():
    """★行为判据：没落盘 ⇒ 与改造前逐字一致（不许出现"连续落盘"信号）。"""
    _w = _mk_organ([])
    _w._narrative_store_state = {"state": "empty", "continuous_days": 0}
    assert "连续落盘" not in _w._generate_life_stage_summary()


def test_45_stage_summary_gains_signal_with_persisted_days():
    _w = _mk_organ([])
    _w._narrative_store_state = {"state": "ok", "continuous_days": 8}
    assert "连续落盘8天" in _w._generate_life_stage_summary()


# ---------------------------------------------------------------- F 静态接线

def test_51_organ_has_persist_and_load():
    _s = _src("organs/identity/PulseNarrativeSelf.py")
    _t = ast.parse(_s)
    _names = {n.name for n in ast.walk(_t) if isinstance(n, ast.FunctionDef)}
    assert "persist" in _names and "load" in _names
    assert "__init__" in _names


def test_52_init_replays_on_startup():
    _s = _src("organs/identity/PulseNarrativeSelf.py")
    _t = ast.parse(_s)
    _init = next(n for n in ast.walk(_t)
                 if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    _body = ast.get_source_segment(_s, _init) or ""
    assert "self.load()" in _body, "启动必须回放，否则落盘形同虚设"


def test_53_night_digest_writes_narrative_before_file():
    _s = _src("nucleus/evolution/night_orchestration.py")
    _i = _s.find("七、叙事落盘")
    assert _i > 0, "夜摘要必须有叙事落盘段"
    assert _i < _s.find('_digest_path, "w"'), "段必须在写盘之前"
    assert "persist_from_organ()" in _s


def test_54_night_segment_creates_no_scheduler():
    _s = _src("nucleus/evolution/night_orchestration.py")
    _seg = _s[_s.find("七、叙事落盘") - 900:_s.find("七、叙事落盘")]
    for _bad in ("threading.Thread", "Timer(", "sched.", "asyncio.create_task"):
        assert _bad not in _seg, "410/412 红线：不新建调度器"


def test_55_version_marker():
    assert ns.version().startswith("412.")
