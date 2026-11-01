# -*- coding: utf-8 -*-
"""413 批刀3 单测：Step5b 三字段（headline / delta / intent）。

钉**语义**，不钉措辞（411 铁律 3）：
  * `headline` 必须是一句话 —— 由模板拼出来，**不许引入生成模型**；
  * `delta` 必须是**数值对比**（cur / prev / delta），取不到一侧就写 `reason`，
    **绝不填 0 假充**（"0 值先怀疑采集失效"）；
  * `intent` 取值必须是 `VERDICTS` 的封闭集合成员，且能识别"相对前一日变化"；
  * **摘要日**：夜摘要在 04:00 跑，结算的是**前一天**
    （与 `continuous_days` 的"允许昨日锚"同口径；412 按 now 落当天是自相矛盾）；
  * `event_types` 经 `row_of` 会落进 `extra` —— 只读顶层会让 `first_type` /
    `broadening` 两条规则**永远不可能命中**，必须有兜底；
  * 开关关闭 ⇒ 逐字回到 412 的留空口径（可秒级回退）。
"""

from __future__ import annotations

import ast
import io
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


_D1 = _at(2026, 10, 31, 12)        # 摘要日 20261031
_D2 = _at(2026, 11, 1, 12)         # 摘要日 20261101
_P1 = _at(2026, 11, 1, 4)          # 04:00 落 ⇒ 结算 10-31
_P2 = _at(2026, 11, 2, 4)          # 04:00 落 ⇒ 结算 11-01
_T6 = _at(2026, 11, 1, 6, 30)      # 06:30 落 ⇒ 结算当天 11-01


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    ns.set_data_dir(str(tmp_path))
    yield
    ns.set_data_dir(None)


def _evs(base, n=4, typ="conversation", tone="neutral"):
    return [{"id": int(time.strftime("%d%H%M", time.localtime(base))) + i,
             "content": "第%d件事" % i, "type": typ, "user_name": "小林",
             "emotional_tone": tone, "timestamp": base + i * 60}
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


# ================================================================ A headline

def test_a1_empty_day_says_so_instead_of_faking():
    _h = nd.pick_headline([])
    assert _h["basis"] == "empty"
    assert _h["text"], "空日也要给一句话，不能空串"
    assert _h["detail"]["day_events"] == 0


def test_a2_continuity_wins_when_streak_advances():
    _h = nd.pick_headline(_evs(_D2), prev_row={"continuous_days": 2}, cont_days=3)
    assert _h["basis"] == "continuity"
    assert "3" in _h["text"], "模板里必须带上当前连续天数"


def test_a3_first_type_when_a_new_event_type_appears():
    _day = _evs(_D2, typ="insight")
    _h = nd.pick_headline(_day, prev_row={"event_types": ["conversation"]}, cont_days=1)
    assert _h["basis"] == "first_type"
    assert _h["detail"]["new_types"] == ["insight"]
    assert _h["event_id"] is not None


def test_a3b_event_types_in_extra_still_work():
    """★回归：`row_of` 把 event_types 放进 `extra`，只读顶层会恒空。"""
    _day = _evs(_D2, typ="insight")
    _h = nd.pick_headline(_day, prev_row={"extra": {"event_types": ["conversation"]}},
                          cont_days=1)
    assert _h["basis"] == "first_type", "extra 里的 event_types 必须能兜底读到"


def test_a4_tone_peak_when_no_new_type():
    _day = _evs(_D2, typ="conversation")
    _day[1]["emotional_tone"] = "joy"
    _h = nd.pick_headline(_day, prev_row={"event_types": ["conversation"]}, cont_days=1)
    assert _h["basis"] == "tone_peak"
    assert _h["detail"]["score"] > 0
    assert _h["event_id"] == _day[1]["id"]


def test_a5_first_event_is_the_last_resort():
    _day = _evs(_D2, typ="conversation")
    _h = nd.pick_headline(_day, prev_row={"event_types": ["conversation"]}, cont_days=1)
    assert _h["basis"] == "first_event"
    assert _h["event_id"] == _day[0]["id"]


def test_a6_basis_is_always_in_closed_set():
    for _h in (nd.pick_headline([]),
               nd.pick_headline(_evs(_D2), prev_row={"continuous_days": 1}, cont_days=2),
               nd.pick_headline(_evs(_D2, typ="x"), prev_row={"event_types": []},
                                cont_days=0),
               nd.pick_headline(_evs(_D2), cont_days=0)):
        assert _h["basis"] in nd.BASES, _h["basis"]


def test_a7_headline_is_one_single_line():
    _day = _evs(_D2)
    _day[0]["content"] = "含换行的内容\n第二行"
    _h = nd.pick_headline(_day, cont_days=0)
    assert isinstance(_h["text"], str) and _h["text"]
    assert "\n" not in _h["text"], "headline 必须是一句话，不许带换行"
    assert len(_h["text"]) <= 80, "过长的截断必须在模板里做掉"


# ================================================================ B delta

def test_b1_cell_subtracts_when_both_sides_present():
    _c = nd._cell(14411, 14300, "src")
    assert _c["delta"] == 111
    assert _c["cur"] == 14411 and _c["prev"] == 14300
    assert _c["source"] == "src"


def test_b2_cell_never_fills_zero_for_missing_side():
    _c = nd._cell(5, None, "src")
    assert _c["delta"] is None, "缺一侧就写 None，绝不用 0 假充"
    assert _c["reason"] == "missing_one_side"
    _c2 = nd._cell(None, 5, "src", reason="probe_failed")
    assert _c2["delta"] is None and _c2["reason"] == "probe_failed"


def test_b3_diff_has_four_numeric_cells():
    _cur = {"nodes": {"value": 14411, "source": "hourly"},
            "llm_ratio": {"value": 0.61, "source": "derived"},
            "patches": {"value": 0, "source": "history"}}
    _prev = {"delta": {"nodes": {"cur": 14400}, "llm_ratio": {"cur": 0.51},
                       "patches": {"cur": 2}}, "narrative_events": 3}
    _d = nd.diff_of(_cur, _prev, day_events=4)
    assert set(_d) == {"nodes", "llm_ratio", "patches", "narrative_events"}
    assert _d["nodes"]["delta"] == 11
    assert _d["llm_ratio"]["delta"] == 0.1
    assert _d["patches"]["delta"] == -2
    assert _d["narrative_events"]["delta"] == 1
    for _k, _v in _d.items():
        assert set(_v) >= {"cur", "prev", "delta", "source"}, _k


def test_b4_first_day_has_no_prev_but_is_not_faked():
    _cur = {"nodes": {"value": 14411, "source": "hourly"},
            "llm_ratio": {"value": 0.6, "source": "d"},
            "patches": {"value": 0, "source": "h"}}
    _d = nd.diff_of(_cur, {}, day_events=4)
    for _k in ("nodes", "llm_ratio", "patches", "narrative_events"):
        assert _d[_k]["prev"] is None
        assert _d[_k]["delta"] is None, "%s：首日不许出现 0 差值" % _k
    assert _d["narrative_events"]["cur"] == 4


def test_b5_collect_stats_is_readonly_and_self_explaining():
    _s = nd.collect_stats()
    assert set(_s) == {"nodes", "llm_ratio", "patches"}
    for _k, _v in _s.items():
        assert _v["value"] is None or isinstance(_v["value"], (int, float)), _k
        assert _v["reason"] or _v["source"], "%s：取不到必须写原因" % _k


def test_b6_patches_uses_the_digest_day():
    """★口径静态判据：patches 的"当日"必须走 `digest_day`，与 headline 同日。

    为什么不做动态注入：`collect_stats` 的数据根固定为 `project_root()`，
    在测试里改写它会污染生产读数（411 铁律：别往会被落盘的对象写观测字段）。
    这里改为锁源码口径 —— 一字之差就是「04:00 数到的是当天 0~4 点」。
    """
    _t = ast.parse(_src("nucleus/observability/narrative_digest.py"))
    _fn = next(n for n in ast.walk(_t)
               if isinstance(n, ast.FunctionDef) and n.name == "collect_stats")
    _seg = ast.get_source_segment(_src("nucleus/observability/narrative_digest.py"), _fn) or ""
    assert "digest_day(now)" in _seg, "patches 段必须用摘要日口径"
    assert "_day = day_stamp(now)" not in _seg, "旧的当天口径必须清干净"


# ================================================================ C intent

def test_c1_no_event_is_quiet():
    _i = nd.intent_of([], prev_row={"narrative_events": 5}, cont_days=3)
    assert _i["verdict"] == "quiet"


def test_c2_after_a_gap_it_is_resuming():
    _i = nd.intent_of(_evs(_D2), prev_row={"narrative_events": 0}, cont_days=1)
    assert _i["verdict"] == "resuming"


def test_c3_big_drop_is_declining():
    _i = nd.intent_of(_evs(_D2, n=2), prev_row={"narrative_events": 10}, cont_days=3)
    assert _i["verdict"] == "declining"


def test_c4_new_type_is_broadening():
    _i = nd.intent_of(_evs(_D2, typ="insight"),
                      prev_row={"narrative_events": 4, "event_types": ["conversation"]},
                      cont_days=3)
    assert _i["verdict"] == "broadening"


def test_c4b_event_types_in_extra_is_read():
    _i = nd.intent_of(_evs(_D2, typ="insight"),
                      prev_row={"narrative_events": 4,
                                "extra": {"event_types": ["conversation"]}},
                      cont_days=3)
    assert _i["verdict"] == "broadening", "extra 兜底失效会让 broadening 永远打不出来"


def test_c5_more_events_and_streak_is_deepening():
    _i = nd.intent_of(_evs(_D2, n=4),
                      prev_row={"narrative_events": 4,
                                "event_types": ["conversation"]}, cont_days=3)
    assert _i["verdict"] == "deepening"


def test_c6_otherwise_stable():
    _i = nd.intent_of(_evs(_D2, n=4),
                      prev_row={"narrative_events": 5,
                                "event_types": ["conversation"]}, cont_days=1)
    assert _i["verdict"] == "stable"


def test_c7_verdict_is_in_closed_set():
    _cases = [([], {"narrative_events": 1}, 0),
              (_evs(_D2), {"narrative_events": 0}, 1),
              (_evs(_D2, n=1), {"narrative_events": 9}, 2),
              (_evs(_D2, typ="x"), {"narrative_events": 4, "event_types": ["c"]}, 3),
              (_evs(_D2), {"narrative_events": 4, "event_types": ["conversation"]}, 4),
              (_evs(_D2), {"narrative_events": 9, "event_types": ["conversation"]}, 0)]
    for _evs_, _prev, _cd in _cases:
        assert nd.intent_of(_evs_, _prev, _cd)["verdict"] in nd.VERDICTS


def test_c8_change_is_detected_against_prev_verdict():
    _i = nd.intent_of(_evs(_D2, n=2),
                      prev_row={"narrative_events": 10, "intent": "deepening"},
                      cont_days=3)
    assert _i["verdict"] == "declining"
    assert _i["prev_verdict"] == "deepening"
    assert _i["changed"] is True
    _same = nd.intent_of([], prev_row={"narrative_events": 1, "intent": "quiet"},
                         cont_days=0)
    assert _same["changed"] is False, "同 verdict 不许报 changed"


# ================================================================ D 摘要日

def test_d1_four_am_settles_the_previous_day():
    assert nd.digest_day(_P1) == time.strftime("%Y%m%d", time.localtime(_D1))
    assert nd.digest_anchor(_P1) == _P1 - 86400.0


def test_d2_daytime_settles_today():
    assert nd.digest_day(_T6) == time.strftime("%Y%m%d", time.localtime(_T6))
    assert nd.digest_anchor(_T6) == _T6


def test_d3_digest_day_matches_day_stamp_of_anchor():
    for _t in (_P1, _T6, _D1):
        assert nd.digest_day(_t) == ns.day_stamp(nd.digest_anchor(_t))


def test_d4_events_of_day_follows_the_anchor():
    _mix = _evs(_D1, n=2) + _evs(_D2, n=3)
    _got = nd.events_of_day(_mix, now=_P1)
    assert len(_got) == 2, "04:00 落盘只算**前一天**的事件，不是当天 0~4 点"
    assert all(time.strftime("%Y%m%d", time.localtime(e["timestamp"])) == nd.digest_day(_P1)
               for e in _got)


# ================================================================ E 端到端

def test_e1_persist_fills_three_fields():
    _w = _mk_organ(_evs(_D2))
    _r = _w.persist(now=_T6)
    assert _r["ok"] is True, _r.get("reason")
    _dg = _r["digest"]
    assert _dg["headline"], "headline 不许空"
    assert _dg["headline_basis"] in nd.BASES
    assert _dg["intent"] in nd.VERDICTS, "intent 必须是封闭集合成员"
    assert set(_dg["delta"]) == {"nodes", "llm_ratio", "patches", "narrative_events"}
    _ne = _dg["delta"]["narrative_events"]
    assert _ne["cur"] == 4, "当日事件数必须是数，不是空"
    assert isinstance(_ne["delta"], (int, float)) or _ne["delta"] is None


def test_e2_persist_at_4am_lands_on_previous_day_file():
    _w = _mk_organ(_evs(_D1))
    _r = _w.persist(now=_P1)
    assert _r["ok"] is True, _r.get("reason")
    assert _r["day"] == nd.digest_day(_P1)
    assert os.path.isfile(ns.day_path(_D1)), "04:00 必须落**前一天**的文件"
    assert not os.path.exists(ns.day_path(_P1)), "不许在当天建一个 0 内容的文件"


def test_e3_row_date_is_the_digest_day():
    _w = _mk_organ(_evs(_D1))
    _r = _w.persist(now=_P1)
    _rows, _bad = ns._read_lines(_r["path"])
    assert _bad == 0
    assert _rows[-1]["date"] == nd.digest_day(_P1)


def test_e4_switch_off_falls_back_to_412_blank(monkeypatch):
    _w = _mk_organ(_evs(_D2))
    monkeypatch.setattr(config, nd.SWITCH, False, raising=False)
    _r = _w.persist(now=_T6)
    assert _r["ok"] is True
    _rows, _ = ns._read_lines(_r["path"])
    _row = _rows[-1]
    assert _row["headline"] == ""
    assert _row["intent"] == ""
    # ★414刀2 改契约：delta 增第 4 格。这格受 `ENABLE_M414_NARRATIVE_EVENTS`
    #   控制（414 的开关），**不**受 413 摘要开关控制 ⇒ 413 开关关闭时它仍在，
    #   只是值为 None（与三格同为 None 口径）。
    assert _row["delta"] == {"nodes": None, "llm_ratio": None, "patches": None,
                             "narrative_events": None}


def test_e5_second_night_sees_the_first_night_values():
    _mk_organ(_evs(_D1, n=4)).persist(now=_P1)
    _r2 = _mk_organ(_evs(_D2, n=6)).persist(now=_P2)
    assert _r2["ok"] is True, _r2.get("reason")
    _ne = _r2["digest"]["delta"]["narrative_events"]
    assert _ne["prev"] == 4, "前一日事件数必须从**前一天的行**里读出来"
    assert _ne["cur"] == 6
    assert _ne["delta"] == 2


def test_e6_narrative_jsonl_is_pure_lf():
    _r = _mk_organ(_evs(_D2)).persist(now=_T6)
    _b = io.open(_r["path"], "rb").read()
    assert _b.count(b"\r\n") == 0
    assert _b.endswith(b"\n")


def test_e7_prev_row_reads_the_previous_file():
    _mk_organ(_evs(_D1, n=3)).persist(now=_P1)
    # ★入参是**摘要日锚点**（persist 内部正是这么传的），不是落盘时刻
    _p = ns.prev_row(now=nd.digest_anchor(_P2))
    assert isinstance(_p, dict) and _p, "锚点前一日必须有行可读"
    assert _p["date"] == ns.day_stamp(_D1)


# ================================================================ F 静态契约

_BANNED = {"openai", "anthropic", "transformers", "torch", "tensorflow", "llama_cpp",
           "ollama", "dashscope", "zhipuai", "qianfan", "cohere", "replicate",
           "litellm", "google", "genai", "vllm", "sentence_transformers"}
_ALLOW = {"os", "time", "json", "typing", "__future__", "config", "nucleus"}


def _src(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8", newline="").read() \
        .replace("\r\n", "\n")


def test_f1_no_generative_model_is_imported():
    """任务书红线：headline **不许 LLM 生成** —— 静态锁死 import 面。"""
    _t = ast.parse(_src("nucleus/observability/narrative_digest.py"))
    _mods = set()
    for _n in ast.walk(_t):
        if isinstance(_n, ast.Import):
            for _a in _n.names:
                _mods.add(_a.name.split(".")[0])
        elif isinstance(_n, ast.ImportFrom):
            if _n.module and _n.level == 0:
                _mods.add(_n.module.split(".")[0])
    _bad = _mods & _BANNED
    assert not _bad, "出现生成模型依赖：%s" % sorted(_bad)
    _extra = _mods - _ALLOW
    assert not _extra, "新增依赖需先过红线评审：%s" % sorted(_extra)


def test_f2_switch_is_registered_in_config():
    assert hasattr(config, nd.SWITCH), "开关必须登记在 config.py [M413-CFG]"
    assert getattr(config, nd.SWITCH) is True


def test_f3_digest_module_is_pure_lf():
    _b = io.open(os.path.join(_ROOT, "nucleus/observability/narrative_digest.py"),
                 "rb").read()
    assert _b.count(b"\r\n") == 0
    assert _b.endswith(b"\n")


def test_f4_persist_uses_the_anchor_for_the_row_date():
    _s = _src("organs/identity/PulseNarrativeSelf.py")
    _t = ast.parse(_s)
    _fn = next(n for n in ast.walk(_t)
               if isinstance(n, ast.FunctionDef) and n.name == "persist")
    _seg = ast.get_source_segment(_s, _fn) or ""
    assert "_anchor" in _seg, "persist 必须用摘要日锚点"
    assert "digest_anchor" in _seg
    assert "persist_daily(_row, now=_anchor)" in _seg, "落盘时刻也要用锚点，否则撞 date_mismatch"


def test_f5_version_is_413():
    assert nd.version().startswith("413.")
