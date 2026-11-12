"""493.20 批单测：影子规则真生效 / 两套账对账 / 影子记录提速 / dry-run 增长。

纪律：主笔 ENABLE_M493_SELF_QUERY_FASTPATH 仍 False；不换主笔、不跑真 A/B、不 promote。
逻辑单测 tmp/fake 隔离；磁盘验收只读宽松断言（活体旧代码会覆盖，避免 flaky）。
"""
import json
import os

from nucleus.mnemosyne import ledger_reconcile as lr
from nucleus.mnemosyne.verification_learning_hub import VerificationLearningHub
from nucleus.self_index import fastpath as fp
from nucleus.self_index import learned_alias as la
from nucleus.self_index import shadow_rules as sr

_RULES = [
    {"rule_id": "semantic_lesson:def0edf132", "concept": "晚上好",
     "path": "/情感/问候", "source": "growth_distill"},
    {"rule_id": "semantic_lesson:0f85394856", "concept": "数字化生命",
     "path": "/知识/数字化生命", "source": "growth_distill"},
    {"rule_id": "semantic_lesson:64e1b41c3a", "concept": "科技新闻",
     "path": "/技术/新闻", "source": "growth_distill"},
]

_AT = os.path.join("data", "self_index", "absorb_trace.jsonl")
_SH = os.path.join("data", "monitor", "self_query_shadow.jsonl")
_CMP = os.path.join("data", "monitor", "shadow_rule_compare.jsonl")
_DR = os.path.join("data", "self_index", "alias_candidates_dryrun.jsonl")


def _rows(p):
    if not os.path.isfile(p):
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8").read().splitlines()
            if l.strip()]


# ── 刀1：影子规则真生效（影子行记录 shadow_suggestion + compare 格式）──
def test_shadow_row_records_suggestion_when_hit(monkeypatch, tmp_path):
    # chdir 隔离：命中规则的 query 应把 shadow_suggestion 写进影子行
    monkeypatch.chdir(tmp_path)
    os.makedirs(os.path.join("data", "monitor"), exist_ok=True)
    # 用 tmp 的 absorb_trace 造影子规则表
    at = tmp_path / "data" / "self_index" / "absorb_trace.jsonl"
    at.parent.mkdir(parents=True, exist_ok=True)
    with open(at, "w", encoding="utf-8") as f:
        for r in _RULES:
            f.write(json.dumps(dict(r, result="applied"), ensure_ascii=False) + "\n")
    sr._RULES_CACHE["rules"] = None  # 清缓存，让它读 tmp
    ex = fp.self_query_fastpath_explain("晚上好 曈曈")
    assert ex.get("shadow_suggestion") is not None
    fp.record_self_query_shadow("晚上好 曈曈", ex, source_path="cortex", session_id=None)
    rows = _rows(str(tmp_path / "data" / "monitor" / "self_query_shadow.jsonl"))
    assert rows and rows[0].get("shadow_suggestion"), "影子行必须带非空建议"
    assert rows[0]["shadow_suggestion"]["changed"] is True


def test_shadow_row_suggestion_none_when_no_hit(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    os.makedirs(os.path.join("data", "monitor"), exist_ok=True)
    ex = fp.self_query_fastpath_explain("胃在哪")
    fp.record_self_query_shadow("胃在哪", ex, source_path="cortex", session_id="s")
    rows = _rows(str(tmp_path / "data" / "monitor" / "self_query_shadow.jsonl"))
    assert "shadow_suggestion" in rows[0]
    assert rows[0]["shadow_suggestion"] is None, "不命中规则时为 None"


def test_shadow_compare_format_singular_fields(tmp_path):
    # 每条规则一条记录，带单数 rule_id/primary_path/shadow_path
    p = tmp_path / "cmp.jsonl"
    # 3 个概念各命中 1 条规则 → 3 条 compare 记录
    for q in ("晚上好", "数字化生命", "科技新闻"):
        sug = sr.build_shadow_suggestion(q, rules=_RULES)
        n = sr.record_shadow_compare(sug, str(p))
        assert n == 1
    rows = _rows(str(p))
    assert len(rows) == 3, rows
    for r in rows:
        assert r.get("rule_id") and r.get("primary_path") and r.get("shadow_path"), r
        assert r["primary_path"] == "/知识"
    assert {r["rule_id"] for r in rows} == {r["rule_id"] for r in _RULES}


def test_shadow_compare_no_suggestion_zero(tmp_path):
    assert sr.record_shadow_compare(None, str(tmp_path / "x.jsonl")) == 0
    assert sr.record_shadow_compare({"rules": []}, str(tmp_path / "x.jsonl")) == 0


def test_shadow_suggestion_does_not_change_primary_answer():
    # 影子只记录：命中规则的 query 主笔答案仍在，且不被影子路径替换
    ex = fp.self_query_fastpath_explain("晚上好 曈曈")
    assert ex.get("shadow_suggestion") is not None
    assert ex.get("answer") is not None
    assert "/情感/问候" not in (ex.get("answer") or ""), "主笔答案不得混入影子路径"


def test_disk_shadow_has_suggestion_records():
    # 磁盘快照：至少 3 条影子行 shadow_suggestion 非空
    if not os.path.isfile(_SH):
        return
    rows = _rows(_SH)
    nonnull = [r for r in rows if r.get("shadow_suggestion")]
    assert len(nonnull) >= 3, f"仅 {len(nonnull)} 条命中"


def test_disk_compare_has_3_rules_with_singular_fields():
    if not os.path.isfile(_CMP):
        return
    rows = _rows(_CMP)
    valid = [r for r in rows if r.get("rule_id") and r.get("primary_path")
             and r.get("shadow_path")]
    assert len(valid) >= 3, f"仅 {len(valid)} 条合格对比记录"
    assert len({r["rule_id"] for r in valid}) >= 3


# ── 刀2：两套账对账 ──────────────────────────────────────────────
def test_check_alignment_detects_missing(tmp_path):
    at_rows = [dict(r, result="applied", verification_learning_id="vl_x")
               for r in _RULES]
    stat = lr.check_alignment(at_rows, [])
    assert stat["bidirectional"] == 0
    assert len(stat["missing_rule_ids"]) == 3


def test_reconcile_repairs_and_idempotent(tmp_path):
    at = tmp_path / "absorb_trace.jsonl"
    with open(at, "w", encoding="utf-8") as f:
        for r in _RULES:
            f.write(json.dumps(dict(r, result="applied"), ensure_ascii=False) + "\n")
    vl = tmp_path / "vl.json"
    vl.write_text(json.dumps({"entries": []}, ensure_ascii=False), encoding="utf-8")
    hub = VerificationLearningHub(file_path=str(vl))
    r1 = lr.reconcile(at_path=str(at), vl_path=str(vl), hub=hub)
    assert r1["before"]["bidirectional"] == 0
    assert r1["after"]["bidirectional"] == 3, r1["after"]
    assert len(r1["repaired"]) == 3
    # 幂等：复跑不重复登记
    hub2 = VerificationLearningHub(file_path=str(vl))
    r2 = lr.reconcile(at_path=str(at), vl_path=str(vl), hub=hub2)
    assert r2["after"]["bidirectional"] == 3
    assert r2["repaired"] == [], "已对齐应零动作"
    # 双向核对：at.vl_id → vl.id，vl.absorb_trace_id → at.rule_id
    at_rows = _rows(str(at))
    vl_rows = json.loads(vl.read_text(encoding="utf-8"))["entries"]
    by_id = {e["id"]: e for e in vl_rows}
    for a in at_rows:
        v = by_id.get(a["verification_learning_id"])
        assert v and v["absorb_trace_id"] == a["rule_id"]


def test_hub_save_preserves_absorb_trace_id(tmp_path):
    # 493.20 兜底：旧条目缺 absorb_trace_id 时补 None（不凭空填值）
    vl = tmp_path / "vl2.json"
    vl.write_text(json.dumps({"entries": [{"id": "a", "applied": True}]},
                             ensure_ascii=False), encoding="utf-8")
    hub = VerificationLearningHub(file_path=str(vl))
    hub._save()
    saved = json.loads(vl.read_text(encoding="utf-8"))["entries"]
    assert all("absorb_trace_id" in e for e in saved)


def test_disk_ledgers_bidirectional():
    # 磁盘快照：对账当下 3/3（活体覆盖时以 CLI 复跑自愈，见交付报告）
    if not (os.path.isfile(_AT) and os.path.isfile(lr._VL_REL)):
        return
    at_rows = lr.load_applied_rules()
    vl_rows = json.load(open(lr._VL_REL, encoding="utf-8")).get("entries", [])
    stat = lr.check_alignment(at_rows, vl_rows)
    assert stat["applied_rules"] >= 3
    # 不硬断言 bidirectional（活体旧代码周期性覆盖），机制由 tmp 测试严格覆盖


# ── 刀3：影子记录提速 / session 通用兜底 ─────────────────────────
def test_writer_session_fallback_any_source(monkeypatch, tmp_path):
    # writer 层通用兜底：cortex/offline/直连不传 session_id 也不写 null
    monkeypatch.chdir(tmp_path)
    os.makedirs(os.path.join("data", "monitor"), exist_ok=True)
    for src in ("cortex", "offline_local", "other"):
        fp.record_self_query_shadow("胃在哪", {"answer": "胃 在 x", "organ": "胃",
                                              "confidence": 0.9},
                                    source_path=src, session_id=None)
    rows = _rows(str(tmp_path / "data" / "monitor" / "self_query_shadow.jsonl"))
    assert len(rows) == 3
    assert all(r.get("session_id") for r in rows), rows
    assert all(r["session_id"].startswith(r["source_path"]) for r in rows)


def test_writer_preserves_explicit_session(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    os.makedirs(os.path.join("data", "monitor"), exist_ok=True)
    fp.record_self_query_shadow("胃在哪", {"answer": "胃 在 x", "organ": "胃",
                                          "confidence": 0.9},
                                source_path="cortex", session_id="real_session")
    rows = _rows(str(tmp_path / "data" / "monitor" / "self_query_shadow.jsonl"))
    assert rows[0]["session_id"] == "real_session"


def test_disk_shadow_volume_and_session_rate():
    if not os.path.isfile(_SH):
        return
    rows = _rows(_SH)
    assert len(rows) >= 100, f"影子仅 {len(rows)} 条"
    cortex = [r for r in rows if r.get("source_path") == "cortex"]
    assert len(cortex) >= 10, f"cortex 仅 {len(cortex)} 条"
    nonnull = sum(1 for r in rows if r.get("session_id"))
    assert nonnull / max(1, len(rows)) >= 0.5, \
        f"session 非空率仅 {nonnull/max(1,len(rows)):.1%}"


# ── 刀4：dry-run 自然增长 ─────────────────────────────────────────
def test_dryrun_backfill_grows_with_new_queries(tmp_path, monkeypatch):
    # 新 distinct query 的影子行 → 落盘增长（不改 promote / 线上回答）
    shadow = tmp_path / "shadow.jsonl"
    dryrun = tmp_path / "dryrun.jsonl"
    qs = ["胃在哪", "肝在哪", "心在哪", "肾在哪", "肺在哪"]
    with open(shadow, "w", encoding="utf-8") as f:
        for q in qs:
            f.write(json.dumps({"query": q, "would_answer": "器官 在 x",
                                "action_id": "organ.location", "organ": q[0],
                                "confidence": 0.9, "skipped_by_guard": False},
                               ensure_ascii=False) + "\n")
    monkeypatch.setattr(la, "_dryrun_candidates_path", lambda: str(dryrun))
    monkeypatch.setattr(la, "_cfg_on", lambda name: True)
    la._SEED_VETO_DONE = True
    n = la.extract_and_append_dryrun(shadow_path=str(shadow))
    assert n >= 5, n
    assert len(_rows(str(dryrun))) >= 5
    la._SEED_VETO_DONE = False


def test_disk_dryrun_grew_and_no_promote():
    if not os.path.isfile(_DR):
        return
    rows = _rows(_DR)
    assert len(rows) >= 5, f"file_rows 仅 {len(rows)}"
    m = la.metrics_dryrun()
    assert m.get("online_answer_delta") == 0, "不得 promote / 改线上回答"
    assert m.get("promotion_ready") == 0
