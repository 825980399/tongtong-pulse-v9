"""493.16 批单测：效果卡质量 veto / 规则级证据账 / 关系问法过滤 / session 传递收尾。

纪律：主笔 ENABLE_M493_SELF_QUERY_FASTPATH 仍 False；LEARNED_ALIASES 仍 False。
本文件只验 493.16 四刀子能力，不打开主笔。
"""
import json
import os

from nucleus.self_index import fastpath as fp
from nucleus.self_index import learned_alias as la
from nucleus.self_index import offline_local as ol
from nucleus.qica.QICA import QICA
from nucleus.mnemosyne.verification_learning_hub import VerificationLearningHub


# ── 刀1：效果卡质量 veto ──────────────────────────────────────────────
def test_q5_card_no_breathing_rhythm_misclaim():
    # Q5 错卡（PulseCortex→PulseLung）不得再把 select_model 解释成呼吸节律/通气档位
    cards = json.load(open("data/self_index/effects_cards.json", encoding="utf-8"))
    hit = [c for c in cards
           if c["from"] == "organs.brain.PulseCortex"
           and c["to"] == "organs.body.PulseLung"]
    assert hit, "PulseCortex→PulseLung 卡应存在"
    eff = hit[0]["effect"]
    assert "呼吸节律" not in eff, eff
    assert "通气档位" not in eff, eff
    # 正确描述选模型事件，且明确否定误归因
    assert ("select_model" in eff) or ("选模型" in eff)
    assert "并非" in eff and ("改写" in eff or "选模型" in eff)


def test_effect_section_has_clear_divider():
    # Q1 分层：效果段与结构段用明确分隔头分开
    cards = json.load(open("data/self_index/effects_cards.json", encoding="utf-8"))
    c = next(c for c in cards if c["confidence"] and c["confidence"] < 0.7)
    found = [{"from": c["from"], "to": c["to"],
              "event": c.get("event") or c.get("via")}]
    out = fp._effect_section(found, "它应用到会怎样")
    assert "—— 效果说明" in out, out
    assert "非结构连接" in out


def test_low_confidence_card_tagged():
    # confidence<0.7 的卡输出时标注「低置信」
    cards = json.load(open("data/self_index/effects_cards.json", encoding="utf-8"))
    c = next(c for c in cards if c["confidence"] and c["confidence"] < 0.7)
    assert c["confidence"] < 0.7
    found = [{"from": c["from"], "to": c["to"],
              "event": c.get("event") or c.get("via")}]
    out = fp._effect_section(found, "它应用到会怎样")
    assert "（低置信）" in out, out


def test_high_confidence_card_not_tagged():
    cards = json.load(open("data/self_index/effects_cards.json", encoding="utf-8"))
    c = next(c for c in cards if c["confidence"] and c["confidence"] >= 0.7)
    found = [{"from": c["from"], "to": c["to"],
              "event": c.get("event") or c.get("via")}]
    out = fp._effect_section(found, "它应用到会怎样")
    assert "（低置信）" not in out, out


# ── 刀2：规则级证据账 ────────────────────────────────────────────────
def test_absorb_trace_persisted_to_jsonl():
    # absorb 留痕落盘 data/self_index/absorb_trace.jsonl，含 downstream_delta
    q = QICA()
    n = q.absorb_growth_rules([{
        "type": "domain_knowledge", "concept": "曈曈的作息_test49316",
        "path": "/日常/作息", "confidence": 0.9, "source": "growth_distill",
    }])
    assert n == 1
    trace = q._m49315_absorb_trace
    # 对历史累积（jsonl 追加日志）鲁棒：按本测试 concept 命中判定
    hit = [e for e in trace
           if e.get("concept") == "曈曈的作息_test49316"
           and e.get("result") == "applied"]
    assert hit, "应有一条本测试的 absorb 记录"
    assert "domain_knowledge" in hit[-1]["downstream_delta"], hit[-1]
    # 落盘文件存在且含 downstream_delta 字段
    p = os.path.join("data", "self_index", "absorb_trace.jsonl")
    assert os.path.exists(p), "absorb_trace.jsonl 应已落盘"
    lines = [l for l in open(p, encoding="utf-8").read().splitlines() if l.strip()]
    assert lines, "jsonl 非空"
    persisted = [json.loads(l) for l in lines]
    assert any(e.get("concept") == "曈曈的作息_test49316"
               and "downstream_delta" in e for e in persisted)


def test_verification_learning_entries_have_applied():
    # verification_learning.json 每条 entry 都有 applied 布尔字段
    hub = VerificationLearningHub()
    assert hub._entries, "应有历史 entry"
    assert all("applied" in e for e in hub._entries), \
        "每条 entry 都应含 applied 字段"
    assert all(isinstance(e["applied"], bool) for e in hub._entries)


# ── 刀3：关系问法过滤 ────────────────────────────────────────────────
def test_relation_queries_not_in_candidate_pool():
    rel_rows = [
        {"query": "肝怎么连", "organ": "肝", "label": "alias_miss"},
        {"query": "肺和胃怎么协作", "organ": "肺", "label": "alias_correct"},
        {"query": "QICA和PulseCortex什么关系", "organ": "QICA", "label": "alias_correct"},
        {"query": "胃跟谁调用", "organ": "胃", "label": "alias_correct"},
    ]
    for r in rel_rows:
        assert la.extract_candidates_from_shadow_row(r) == [], r


def test_dryrun_candidates_no_relation_query():
    # alias_candidates_dryrun.jsonl 里已无「肝怎么连」关系问法
    content = open("data/self_index/alias_candidates_dryrun.jsonl",
                   encoding="utf-8").read()
    assert "肝怎么连" not in content


# ── 刀4：offline_local session 传递收尾 ──────────────────────────────
def test_offline_layered_answer_session_written_to_disk(tmp_path, monkeypatch):
    # 强制断网态（否则 offline_active 返回 False 直接早退，不落影子）
    monkeypatch.setattr(ol, "offline_active", lambda: True)
    monkeypatch.setattr(ol, "_cfg_on", lambda name: True)
    tmp = tmp_path / "shadow.jsonl"
    captured = []

    def _fake_record(_q, _ex, source_path=None, session_id=None, error_reason=None):
        captured.append(session_id)
        with open(tmp, "a", encoding="utf-8") as f:
            f.write(json.dumps({"query": _q, "session_id": session_id,
                                "source_path": source_path}) + "\n")

    monkeypatch.setattr(fp, "record_self_query_shadow", _fake_record)
    monkeypatch.setattr(fp, "self_query_fastpath_explain", lambda q: {"answer": None})
    ol.offline_layered_answer("胃在身体的哪个位置", session_id="test_001")
    assert captured and captured[0] == "test_001"
    rows = [json.loads(l) for l in tmp.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows[0]["session_id"] == "test_001"
