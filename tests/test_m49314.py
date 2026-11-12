"""493.14/493.15 批单测：效果卡扩面与输出收口 / dry-run 指标口径 / 影子字段 / pending 审查 / get_last_organ / absorb 留痕。

纪律：主笔 ENABLE_M493_SELF_QUERY_FASTPATH 仍 False；LEARNED_ALIASES 仍 False。
本文件只验各刀的子能力，不打开主笔。
"""
import json
import os
import time
import types

import pytest

from nucleus.self_index import fastpath as fp
from nucleus.self_index import learned_alias as la
from nucleus.self_index.alias_labeler import label_shadow_row
from nucleus.self_index.offline_local import offline_layered_answer
from organs.brain.PulseInnerWorld import PulseInnerWorld
from nucleus.qica.QICA import QICA


# ── 刀1：效果卡扩面 + confidence ──
def test_effect_cards_expanded_and_confidence():
    cards = json.load(open("data/self_index/effects_cards.json", encoding="utf-8"))
    # ≥25 张
    assert len(cards) >= 25, f"效果卡仅 {len(cards)} 张，需 ≥25"
    # 每张必须有人话 effect + 证据 + confidence（0-1）
    for c in cards:
        assert c.get("effect"), c
        assert c.get("evidence"), c
        _conf = c.get("confidence")
        assert isinstance(_conf, (int, float)) and 0.0 < _conf <= 1.0, c
    # 强制对：pulse_inner_world_support → InfoField 存在
    _iw = [c for c in cards
           if c["from"] == "organs.brain.pulse_inner_world_support"
           and c["to"] == "nucleus.field.InfoField"]
    assert _iw, "MUST 卡 pulse_inner_world_support→InfoField 缺失"
    assert _iw[0]["confidence"] >= 0.8, "多事件复用应 ≥0.8"


def test_effect_section_hits_new_card():
    # 新卡（PulseCortex→PulseLung）应能被匹配命中
    cards = json.load(open("data/self_index/effects_cards.json", encoding="utf-8"))
    _hit = fp._match_effect_card(cards, "organs.brain.PulseCortex",
                                 "organs.body.PulseLung", "")
    assert _hit is not None, "PulseCortex→PulseLung 新卡应可匹配"
    assert _hit.get("effect"), "命中卡应有 effect 文案"


# ── 刀2：dry-run 指标口径 ──
def test_metrics_dryrun_schema_and_online_zero():
    m = la.metrics_dryrun()
    # 改名后的键 + 新增 file_rows / promotion_ready
    assert "shadow_raw_candidates" in m, "candidate_rows_total 应改名为 shadow_raw_candidates"
    assert "file_rows" in m, "应新增 file_rows（落盘行数）"
    assert "promotion_ready" in m, "应基于 file_rows 的 promotion_ready"
    assert m["online_answer_delta"] == 0, "dry-run 绝不改线上回答（硬约束）"


def test_relation_query_filtered_from_pool():
    # 刀2-3：关系类 query 不进 alias 候选池
    _row = {"query": "肝怎么连", "organ": "肝", "would_answer": None,
            "skipped_by_guard": False}
    assert la.extract_candidates_from_shadow_row(_row) == []
    # 非关系类别名 query 仍进池
    _row2 = {"query": "胃其实是肝", "organ": "肝", "would_answer": None,
             "skipped_by_guard": False}
    assert la.extract_candidates_from_shadow_row(_row2) != []


# ── 刀3：影子 session_id / label schema ──
def test_label_schema_fixed_would_answer():
    # 修复后：带 would_answer 的行 → 正确非 null 标签（label_shadow_row 要求 query 非空）
    assert label_shadow_row({"query": "胃在哪", "would_answer": "胃 在 x",
                             "organ": "胃"}) == "alias_correct"
    # 旧的 schema 错位（explain 只有 answer 键，缺 would_answer）→ 被误判 alias_miss
    # （这正暴露了 493.14-3 的 bug：writer 传 explain 只含 answer → 标签算错）
    assert label_shadow_row({"query": "胃在哪", "answer": "胃 在 x",
                             "organ": "胃"}) == "alias_miss"


def test_offline_layered_answer_passes_session_id(monkeypatch):
    # offline_local 现接受 session_id 并传给影子记录
    _captured = {}

    def _fake_record(_q, _ex, source_path=None, session_id=None, error_reason=None):
        _captured["session_id"] = session_id
        return None

    # offline_local 在函数内 `from nucleus.self_index.fastpath import record_self_query_shadow`，
    # 故打补丁须落在 fastpath 模块属性上。
    monkeypatch.setattr(fp, "record_self_query_shadow", _fake_record)
    # 让 L0 落影子分支确定性执行（绕过 config 开关与本地索引实现）
    import nucleus.self_index.offline_local as ol
    monkeypatch.setattr(ol, "_cfg_on", lambda name: True)
    monkeypatch.setattr(ol, "offline_active", lambda: True)
    monkeypatch.setattr(fp, "self_query_fastpath_explain",
                        lambda _q: {"answer": "x", "confidence": 0.9, "organ": "胃"})
    offline_layered_answer("胃在哪", session_id="sess-ABC")
    assert _captured.get("session_id") == "sess-ABC", "session_id 应透传到影子记录"


# ── 刀4：pending 审查结论 ──
def test_pending_review_conclusions():
    ps = json.load(open("data/patches/pending_patches.json", encoding="utf-8"))
    # 索引 1/4/10 → rejected（dead_code 真死或误报）
    assert ps[1]["status"] == "rejected"
    assert ps[4]["status"] == "rejected"
    assert ps[10]["status"] == "rejected"
    # 索引 2/3/5/8 → deferred（long_method 技术债）
    for i in (2, 3, 5, 8):
        assert ps[i]["status"] == "deferred", (i, ps[i]["status"])


# ── 刀5：get_last_organ 三路径 ──
def _fake_mem(entries):
    return types.SimpleNamespace(_conversation_memory=entries)


def test_get_last_organ_recent_organ():
    # 小林最近一次带 organ 的记忆（胃/肝/肺），胃为最新（列表末位）
    mem = _fake_mem([
        {"user_name": "小林", "organ": "肝", "ts": time.time() - 20, "organ_confidence": 0.7},
        {"user_name": "小林", "organ": "肺", "ts": time.time() - 10, "organ_confidence": 0.8},
        {"user_name": "小林", "organ": "胃", "ts": time.time(), "organ_confidence": 0.9},
        {"user_name": "小林", "organ": None, "ts": time.time()},
    ])
    res = PulseInnerWorld.get_last_organ(mem, "小林")
    assert res["organ"] == "胃"  # 最近非 None 的器官


def test_get_last_organ_cross_user_none():
    mem = _fake_mem([
        {"user_name": "小林", "organ": "胃", "ts": time.time()},
    ])
    assert PulseInnerWorld.get_last_organ(mem, "阿明") is None


def test_get_last_organ_older_than_24h_none():
    mem = _fake_mem([
        {"user_name": "小林", "organ": "胃", "ts": time.time() - 90000},  # >86400
    ])
    assert PulseInnerWorld.get_last_organ(mem, "小林", max_age_s=86400) is None


def test_get_last_organ_qica_explicit_binds():
    q = QICA()
    a = q.classify_internal({
        "content": "它呢", "user_name": "用户",
        "context_organ": "胃", "context_organ_age": 3,
    })
    assert a["anaphora_resolved"]["organ"] == "胃"


# ── 493.15 刀1：效果卡输出收口（effect_card_hit / 去重 / 纯结构边人话说明）──
def test_explain_always_has_effect_card_hit_field():
    # 每条输出都有 effect_card_hit 字段（True/False）
    for _q in ("你好", "器官有多少个", "胃在哪"):
        _ex = fp.self_query_fastpath_explain(_q)
        assert "effect_card_hit" in _ex, _q
        assert isinstance(_ex["effect_card_hit"], bool)
    # 命中卡的关系问法 → True（真实模块图 QICA→大脑皮层 有卡）
    _ex2 = fp.self_query_fastpath_explain("QICA 怎么把话传给大脑皮层")
    assert _ex2.get("effect_card_hit") is True, _ex2


def test_effect_section_dedup_no_repeat():
    # 同一 (from,to) 的 4 条边只输出 1 条效果文本（不再重复 3 次）
    _found = [{"from": "nucleus.qica.QICA", "to": "organs.brain.PulseCortex"}] * 4
    _out = fp._effect_section(_found, "QICA 怎么把话传给大脑皮层")
    assert _out.count("效果：") == 1, _out


def test_effect_section_human_note_pure_structure():
    # 有边无卡 + 问协作 → 基于边方向给一句人话说明（不编效果）
    _found = [{"from": "organs.body.PulseLung", "to": "organs.body.PulseStomach"}]
    _out = fp._effect_section(_found, "肺和胃怎么协作")
    assert "这些边意味着" in _out, _out
    assert "具体运行效果" in _out  # 诚实：不编效果数据


# ── 493.15 刀5：absorb 最小留痕 ──
def test_absorb_growth_rules_leaves_trace():
    q = QICA()
    _before = getattr(q, "_m49315_absorb_applied", 0)
    _n = q.absorb_growth_rules([
        {"type": "domain_knowledge", "concept": "曈曈的作息", "path": "/日常/作息",
         "confidence": 0.9, "source": "growth_distill"},
    ])
    assert _n == 1
    assert q._m49315_absorb_applied == _before + 1
    _trace = q._m49315_absorb_trace[-1]
    assert _trace["result"] == "applied"
    assert _trace["source"] == "growth_distill"
    assert "ts" in _trace and "rule_id" in _trace
