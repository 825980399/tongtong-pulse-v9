"""493.21 批单测：假闭环修复——两账独立 / 影子规则可见+去重 /
promotion_ready 移除 / _record_conversation 真实写入。

纪律：主笔 ENABLE_M493_SELF_QUERY_FASTPATH 仍 False；不换主笔、不加新机制、
不 promote、不跑真 A/B。逻辑单测 tmp 隔离，磁盘验收只读宽松断言。
"""
import json
import os
import threading

from nucleus.mnemosyne import ledger_reconcile as lr
from nucleus.mnemosyne.ContextSnapshot import ContextSnapshot
from nucleus.mnemosyne.verification_learning_hub import VerificationLearningHub
from nucleus.self_index import learned_alias as la
from nucleus.self_index import shadow_rules as sr
from nucleus.self_index import fastpath as fp
from organs.brain.PulseInnerWorld import PulseInnerWorld

_RULES = [
    {"rule_id": "semantic_lesson:def0edf132", "concept": "晚上好",
     "path": "/情感/问候", "source": "growth_distill"},
    {"rule_id": "semantic_lesson:0f85394856", "concept": "数字化生命",
     "path": "/知识/数字化生命", "source": "growth_distill"},
    {"rule_id": "semantic_lesson:64e1b41c3a", "concept": "科技新闻",
     "path": "/技术/新闻", "source": "growth_distill"},
]
_AT = os.path.join("data", "self_index", "absorb_trace.jsonl")
_CMP = os.path.join("data", "monitor", "shadow_rule_compare.jsonl")
_SH = os.path.join("data", "monitor", "self_query_shadow.jsonl")
_CM = os.path.join("data", "context", "conversation_memory.json")


def _rows(p):
    if not os.path.isfile(p):
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8").read().splitlines()
            if l.strip()]


# ── 刀1：两本账独立、不强求对齐 ──────────────────────────────────
def test_reconcile_is_readonly_policy(tmp_path):
    at = tmp_path / "at.jsonl"
    with open(at, "w", encoding="utf-8") as f:
        for r in _RULES:
            f.write(json.dumps(dict(r, result="applied"), ensure_ascii=False) + "\n")
    vl = tmp_path / "vl.json"
    vl.write_text(json.dumps({"entries": []}, ensure_ascii=False), encoding="utf-8")
    at_before, vl_before = at.read_text(encoding="utf-8"), vl.read_text(encoding="utf-8")
    r = lr.reconcile(at_path=str(at), vl_path=str(vl))
    assert r["policy"] == "two_independent_ledgers"
    assert r["repaired"] == []
    assert r["before"] == r["after"]
    assert at.read_text(encoding="utf-8") == at_before
    assert vl.read_text(encoding="utf-8") == vl_before, "只读：不写 vl"


def test_check_alignment_allows_none_links():
    # 关联字段允许 None：不因 None 报错，双向为 0 是正常观测值
    at_rows = [dict(r, result="applied", verification_learning_id=None) for r in _RULES]
    vl_rows = [{"id": f"vl_{i}", "applied": False, "absorb_trace_id": None}
               for i in range(3)]
    stat = lr.check_alignment(at_rows, vl_rows)
    assert stat["applied_rules"] == 3
    assert stat["bidirectional"] == 0
    assert stat["vl_applied_true"] == 0


def test_ten_records_do_not_overwrite_absorb_trace(tmp_path):
    # 任务书验收：跑 10 次 record() 后两账不互相覆盖
    at = tmp_path / "at.jsonl"
    with open(at, "w", encoding="utf-8") as f:
        for r in _RULES:
            f.write(json.dumps(dict(r, result="applied"), ensure_ascii=False) + "\n")
    at_before = at.read_text(encoding="utf-8")
    vl = tmp_path / "vl.json"
    vl.write_text(json.dumps({"entries": []}, ensure_ascii=False), encoding="utf-8")
    hub = VerificationLearningHub(file_path=str(vl))
    for i in range(10):
        hub.record(organ="qica", task_type="t", input_summary=f"q{i}",
                   local_result={}, confidence=0.9, relevance_score=0.9,
                   needs_verification=False)
    assert at.read_text(encoding="utf-8") == at_before, "vl 的 record 不得碰 absorb_trace"
    assert len(json.loads(vl.read_text(encoding="utf-8"))["entries"]) == 10
    assert len(_rows(str(at))) == 3


def test_disk_ledgers_independent_no_forced_links():
    if not os.path.isfile(_AT):
        return
    at_rows = lr.load_applied_rules()
    assert len(at_rows) >= 3
    assert all(r.get("verification_learning_id") is None for r in at_rows), \
        "493.21 方案A：不强求双向 ID，允许 None"


# ── 刀2：影子日志 shadow_suggestion + 3 规则定向触发 + compare 去重 ──
def test_three_rules_directional_trigger(tmp_path):
    # 任务书：3 个 query 各自触发对应规则
    expect = [("晚上好 曈曈", "semantic_lesson:def0edf132", "/情感/问候"),
              ("数字化生命是什么", "semantic_lesson:0f85394856", "/知识/数字化生命"),
              ("科技新闻", "semantic_lesson:64e1b41c3a", "/技术/新闻")]
    for q, rid, path in expect:
        sug = sr.build_shadow_suggestion(q, rules=_RULES)
        assert sug is not None, q
        assert sug["rules"][0]["rule_id"] == rid, (q, sug["rules"])
        assert sug["shadow_paths"][0] == path, (q, sug["shadow_paths"])
        assert sug["changed"] is True


def test_three_rules_write_shadow_suggestion_to_row(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    os.makedirs(os.path.join("data", "monitor"), exist_ok=True)
    os.makedirs(os.path.join("data", "self_index"), exist_ok=True)
    with open(os.path.join("data", "self_index", "absorb_trace.jsonl"),
              "w", encoding="utf-8") as f:
        for r in _RULES:
            f.write(json.dumps(dict(r, result="applied"), ensure_ascii=False) + "\n")
    sr._RULES_CACHE["rules"] = None
    for q in ("晚上好 曈曈", "数字化生命是什么", "科技新闻"):
        ex = fp.self_query_fastpath_explain(q)
        fp.record_self_query_shadow(q, ex, source_path="cortex", session_id="s")
    rows = _rows(str(tmp_path / "data" / "monitor" / "self_query_shadow.jsonl"))
    nonnull = [r for r in rows if r.get("shadow_suggestion")]
    assert len(nonnull) == 3, f"应有 3 条非空建议，实得 {len(nonnull)}"
    got = {r["shadow_suggestion"]["rules"][0]["rule_id"] for r in nonnull}
    assert got == {r["rule_id"] for r in _RULES}


def test_shadow_compare_dedup_query_ruleid(tmp_path):
    # 同一 query+rule_id 不重复写
    p = tmp_path / "cmp.jsonl"
    sug = sr.build_shadow_suggestion("晚上好", rules=_RULES)
    assert sr.record_shadow_compare(sug, str(p)) == 1
    assert sr.record_shadow_compare(sug, str(p)) == 0, "重复应跳过"
    assert sr.record_shadow_compare(sug, str(p)) == 0
    assert len(_rows(str(p))) == 1
    # 另一规则的同一 query 可写（key 不同）
    sug2 = sr.build_shadow_suggestion("科技新闻", rules=_RULES)
    assert sr.record_shadow_compare(sug2, str(p)) == 1
    assert len(_rows(str(p))) == 2


def test_disk_shadow_suggestion_and_compare():
    if not (os.path.isfile(_SH) and os.path.isfile(_CMP)):
        return
    rows = _rows(_SH)
    nonnull = [r for r in rows if r.get("shadow_suggestion")]
    assert len(nonnull) >= 3
    cmp = [c for c in _rows(_CMP)
           if c.get("rule_id") and c.get("primary_path") and c.get("shadow_path")]
    assert len(cmp) >= 3
    assert len({c["rule_id"] for c in cmp}) >= 3
    keys = [(c.get("query"), c.get("rule_id")) for c in cmp]
    assert len(keys) == len(set(keys)), "compare 不应有 query+rule_id 重复"


def test_shadow_does_not_change_primary_answer():
    ex = fp.self_query_fastpath_explain("晚上好 曈曈")
    assert ex.get("shadow_suggestion") is not None
    assert ex.get("answer") is not None
    assert "/情感/问候" not in (ex.get("answer") or "")


# ── 刀3：promotion_ready 移除 / 明确不 promote ────────────────────
def test_metrics_no_promotion_ready():
    m = la.metrics_dryrun()
    assert "promotion_ready" not in m, "493 阶段不 promote，删误导指标"
    assert "no_promote" in m.get("promotion_status", "")
    assert m["online_answer_delta"] == 0, "dry-run 绝不改线上回答"
    # 记录功能仍保留
    assert "file_rows" in m and "shadow_raw_candidates" in m


def test_dryrun_records_but_no_promote(tmp_path, monkeypatch):
    shadow = tmp_path / "s.jsonl"
    dryrun = tmp_path / "d.jsonl"
    with open(shadow, "w", encoding="utf-8") as f:
        f.write(json.dumps({"query": "胃在哪", "would_answer": "胃 在 x",
                            "action_id": "organ.location", "organ": "胃",
                            "confidence": 0.9, "skipped_by_guard": False},
                           ensure_ascii=False) + "\n")
    monkeypatch.setattr(la, "_dryrun_candidates_path", lambda: str(dryrun))
    monkeypatch.setattr(la, "_cfg_on", lambda name: True)
    la._SEED_VETO_DONE = True
    assert la.extract_and_append_dryrun(shadow_path=str(shadow)) == 1
    assert len(_rows(str(dryrun))) == 1, "记录功能保留"
    assert "promotion_ready" not in la.metrics_dryrun()
    la._SEED_VETO_DONE = False


# ── 刀4：_record_conversation 真实写入 ───────────────────────────
def _minimal_iw(cs):
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw._conversation_memory = []
    iw._conversation_memory_lock = threading.RLock()
    iw._max_conversation_memory = 30
    iw._context_snapshot = cs
    iw.self_awareness = None
    iw._log = lambda *a, **k: None
    iw._ingest_identity_claims = lambda q, u: None
    iw._get_current_emotion = lambda: "中性"
    iw._call_provider = lambda *a, **k: {}
    return iw


def test_record_conversation_writes_disk(tmp_path):
    # 生产方法真实写入（tmp 隔离，不碰生产磁盘）
    cs = ContextSnapshot(base_dir=str(tmp_path))
    iw = _minimal_iw(cs)
    q = "小林问肺和心在气血上怎么配合"
    a = "肺主气司呼吸，心主血脉推动运行，气为血之帅，两者共同维持气血循环与全身供氧。"
    iw._record_conversation(q, a, "小林", "simple_query_local", 0.95)
    p = os.path.join(str(tmp_path), "conversation_memory.json")
    assert os.path.isfile(p)
    mems = [m for u, part in json.load(open(p, encoding="utf-8"))["users"].items()
            for m in part.get("memories", [])]
    assert len(mems) == 1
    assert mems[0]["question"] == q
    assert mems[0].get("_source") is None, "真实记忆不带 seed 标记"


def test_record_conversation_respects_guest_privacy(tmp_path):
    # 访客/用户按隐私判据不落盘（不静默：DEBUG 留痕），非报错
    cs = ContextSnapshot(base_dir=str(tmp_path))
    iw = _minimal_iw(cs)
    a = "这是一段足够长的对话回答内容，用来通过长度与置信度门槛以便检查隐私判据。"
    iw._record_conversation("访客问了个问题", a, "访客", "simple_query_local", 0.95)
    assert not os.path.exists(os.path.join(str(tmp_path), "conversation_memory.json"))
    assert not cs._should_persist_conversation("访客", None)


def test_capacity_allows_growth():
    cs = ContextSnapshot()
    assert cs._max_conversation_per_user > 50, "容量上限需放宽，真实记忆才可见增长"


def test_disk_memory_grew_with_real_entry():
    if not os.path.isfile(_CM):
        return
    mems = [m for u, p in json.load(open(_CM, encoding="utf-8"))["users"].items()
            for m in p.get("memories", [])]
    assert len(mems) >= 51, f"记忆仅 {len(mems)} 条"
    real = [m for m in mems if not m.get("_source") and m.get("question")
            and "问" in m.get("question", "") and len(m.get("question", "")) > 10]
    assert real, "应有 _record_conversation 真实写入的非 seed 记忆"
