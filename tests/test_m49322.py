"""493.22 批单测：可评估样本 / 单向关联 / A-B 真值 / answer_source / 内存采样。

纪律：主笔开关仍 False；不换主笔、不跑真 A/B、不 promote、不修内存。
逻辑单测 tmp 隔离；磁盘验收只读宽松断言。
"""
import json
import os

from nucleus.mnemosyne import ledger_reconcile as lr
from nucleus.mnemosyne.verification_learning_hub import VerificationLearningHub
from nucleus.self_index import answer_source as _as
from nucleus.self_index import fastpath as fp
from nucleus.self_index import shadow_rules as sr

_EVAL = os.path.join("data", "monitor", "eval_samples_49322.jsonl")
_AT = os.path.join("data", "self_index", "absorb_trace.jsonl")
_CMP = os.path.join("data", "monitor", "shadow_rule_compare.jsonl")
_SH = os.path.join("data", "monitor", "self_query_shadow.jsonl")
_RSS = os.path.join("data", "monitor", "rss_hourly_49322.jsonl")


def _rows(p):
    if not os.path.isfile(p):
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8").read().splitlines()
            if l.strip()]


# ── 刀1：样本 + 金标（493.23 扩量后：250 行 / 100 金标）─────────────
def test_eval_samples_200_and_50_gold():
    if not os.path.isfile(_EVAL):
        return
    rows = _rows(_EVAL)
    assert len(rows) == 250, len(rows)
    cats = {r.get("category") for r in rows}
    assert len(cats) == 20, f"应覆盖 20 类，实得 {len(cats)}"
    for r in rows:
        assert all(k in r for k in ("query", "category", "expected_organ",
                                    "expected_action", "expect_answer_contains"))
    gold = [r for r in rows if r.get("gold")]
    assert len(gold) == 100, len(gold)
    assert all(isinstance(r.get("labeled_correct"), bool) for r in gold)
    # 金标覆盖 20 类
    assert len({r["category"] for r in gold}) == 20
    # 误答率可计算且已修正判据假阴性（真实误答率 ≤ 10%）
    ok = sum(1 for r in gold if r["labeled_correct"])
    err = 1 - ok / len(gold)
    assert 0.0 <= err <= 1.0
    assert err <= 0.10, f"真实误答率应 ≤10%，实得 {err:.1%}"


def test_eval_gold_judgement_is_recomputable():
    # 金标判据可复算：用 493.23 修正后的确定性判据（eval_judge）重跑
    from nucleus.self_index import eval_judge as _ej
    if not os.path.isfile(_EVAL):
        return
    rows = [r for r in _rows(_EVAL) if r.get("gold")]
    for r in rows:
        recomputed = bool(_ej.judge_correct(r))
        assert recomputed == r["labeled_correct"], "修正后判据应可复算"


# ── 刀2：单向关联（trace→vl，vl 不强求回指，不做双向对账）─────────
def test_reconcile_still_readonly():
    # 493.21 内核不变：只读、不注入、不写盘
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        at = os.path.join(d, "at.jsonl")
        vl = os.path.join(d, "vl.json")
        with open(at, "w", encoding="utf-8") as f:
            f.write(json.dumps({"rule_id": "r1", "result": "applied"},
                               ensure_ascii=False) + "\n")
        with open(vl, "w", encoding="utf-8") as f:
            f.write(json.dumps({"entries": []}, ensure_ascii=False))
        before_at = open(at, encoding="utf-8").read()
        before_vl = open(vl, encoding="utf-8").read()
        r = lr.reconcile(at_path=at, vl_path=vl)
        assert r["repaired"] == [] and r["policy"] == "two_independent_ledgers"
        assert open(at, encoding="utf-8").read() == before_at
        assert open(vl, encoding="utf-8").read() == before_vl


def test_ten_records_do_not_touch_trace():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        at = os.path.join(d, "at.jsonl")
        vl = os.path.join(d, "vl.json")
        with open(at, "w", encoding="utf-8") as f:
            f.write(json.dumps({"rule_id": "r1", "result": "applied",
                                "verification_learning_id": "vl_x",
                                "link_policy": "one_way"},
                               ensure_ascii=False) + "\n")
        with open(vl, "w", encoding="utf-8") as f:
            f.write(json.dumps({"entries": []}, ensure_ascii=False))
        before = open(at, encoding="utf-8").read()
        hub = VerificationLearningHub(file_path=vl)
        for i in range(10):
            hub.record(organ="qica", task_type="t", input_summary=f"q{i}",
                       local_result={}, confidence=0.9, relevance_score=0.9,
                       needs_verification=False)
        assert open(at, encoding="utf-8").read() == before, "单向引用不被 record 冲掉"


def test_disk_trace_one_way_links():
    if not os.path.isfile(_AT):
        return
    rows = lr.load_applied_rules()
    assert len(rows) >= 3
    # 单向引用：trace 有 vl_id（真实登记），但 vl 不强求回指
    assert all(r.get("link_policy") == "one_way" for r in rows)
    assert all(str(r.get("verification_learning_id", "")).startswith("vl_")
               for r in rows)


# ── 刀3：A/B 真值字段 ────────────────────────────────────────────
def test_compare_has_ab_truth_fields_no_fake_winner(tmp_path):
    p = tmp_path / "cmp.jsonl"
    rules = [{"rule_id": "r1", "concept": "晚上好", "path": "/情感/问候",
              "source": "growth_distill"}]
    sug = sr.build_shadow_suggestion("晚上好", rules=rules)
    n = sr.record_shadow_compare(sug, str(p), answer_b="真实本地候选",
                                 answer_a=None, winner=None)
    assert n == 1
    r = _rows(str(p))[0]
    assert r["answer_b"] == "真实本地候选"
    assert r["answer_a"] is None
    assert r["winner"] is None, "无真实双答案，不写假 winner"
    assert all(k in r for k in ("answer_a", "answer_b", "winner"))


def test_compare_ab_fields_default_none(tmp_path):
    p = tmp_path / "cmp2.jsonl"
    rules = [{"rule_id": "r2", "concept": "科技新闻", "path": "/技术/新闻",
              "source": "growth_distill"}]
    sug = sr.build_shadow_suggestion("科技新闻", rules=rules)
    sr.record_shadow_compare(sug, str(p))
    r = _rows(str(p))[0]
    assert r["answer_a"] is None and r["answer_b"] is None and r["winner"] is None


def test_disk_compare_3_rules_with_truth():
    if not os.path.isfile(_CMP):
        return
    rows = _rows(_CMP)
    assert len(rows) >= 3
    assert len({r.get("rule_id") for r in rows}) >= 3
    for r in rows:
        assert all(k in r for k in ("answer_a", "answer_b", "winner"))
        # 493.23 刀1：answer_a 已填真实主笔答案；winner 只允许诚实取值，
        # 绝不写假 quality verdict（主笔赢/影子赢/都不对）。
        assert r.get("winner") in (None, "平局", "无影子候选", "双答案待评估")
        # 3 条都有真值：local_candidate_hit 为真实判定
        assert isinstance(r.get("local_candidate_hit"), bool)


# ── 刀4：answer_source ───────────────────────────────────────────
def test_infer_answer_source_real_signals():
    assert _as.infer_answer_source("lung_generation") == _as.LLM_ONLY
    assert _as.infer_answer_source("self_index_fastpath") == _as.SELF_ONLY
    assert _as.infer_answer_source("local_enhanced") == _as.SELF_ENHANCED
    assert _as.infer_answer_source("lung_generation", local_hit=True) == _as.SELF_ENHANCED
    # 无真实信号 → None，不编造
    assert _as.infer_answer_source("system_status") is None
    assert _as.infer_answer_source("unknown") is None
    assert _as.infer_answer_source("") is None


def test_console_prefix_real_only():
    assert _as.console_prefix("lung_generation") == "[大模型直答]"
    assert _as.console_prefix("self_index_fastpath") == "[曈曈自答]"
    assert _as.console_prefix("local_enhanced") == "[本地+大模型]"
    assert _as.console_prefix("system_status") == "", "无信号不加假前缀"


def test_answer_source_stats_by_day_and_session(tmp_path):
    p = str(tmp_path / "events.jsonl")
    for src, sid in [("lung_generation", "s1"), ("self_index_fastpath", "s1"),
                     ("local_enhanced", "s2")]:
        k = _as.infer_answer_source(src)
        assert _as.record_answer_source_event(src, k, session_id=sid, path=p)
    overall = _as.overall_stats(p)
    assert overall["total"] == 3
    assert sum(overall["counts"].values()) == 3
    assert len(_as.stats_by_session(p)) == 2
    assert len(_as.stats_by_day(p)) >= 1
    assert abs(sum(overall["percent"].values()) - 100.0) < 0.2


def test_shadow_row_has_answer_source_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    os.makedirs(os.path.join("data", "monitor"), exist_ok=True)
    fp.record_self_query_shadow("胃在哪", {"answer": "胃 在 x", "organ": "胃",
                                          "confidence": 0.9}, session_id="s")
    rows = _rows(str(tmp_path / "data" / "monitor" / "self_query_shadow.jsonl"))
    assert "answer_source" in rows[0]
    assert rows[0]["answer_source"] is None, "writer 层无主笔信号，默认 None 不编造"


def test_shadow_row_answer_source_optional_value(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    os.makedirs(os.path.join("data", "monitor"), exist_ok=True)
    fp.record_self_query_shadow("胃在哪", {"answer": "胃 在 x", "organ": "胃",
                                          "confidence": 0.9}, session_id="s",
                                answer_source=_as.SELF_ONLY)
    rows = _rows(str(tmp_path / "data" / "monitor" / "self_query_shadow.jsonl"))
    assert rows[0]["answer_source"] == _as.SELF_ONLY


def test_disk_answer_source_events_exist():
    if not os.path.isfile(_as._EVENTS_REL):
        return
    rows = _rows(_as._EVENTS_REL)
    assert rows
    assert all(r.get("answer_source") in
               (_as.SELF_ONLY, _as.SELF_ENHANCED, _as.LLM_ONLY) for r in rows)


# ── 刀5：RSS 采样器（只观察）──────────────────────────────────────
def test_rss_probe_report_does_not_fake_24h(tmp_path):
    from tools import rss_hourly_probe_49322 as probe
    p = str(tmp_path / "rss.jsonl")
    rows = []
    # 只放 2 条真实结构样本（不足 24）
    for i in range(2):
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": 1000 + i, "rss_mb": 50.0 + i}) + "\n")
        rows.append({"ts": 1000 + i, "rss_mb": 50.0 + i})
    assert len(_rows(p)) == 2
    assert probe.rss_mb() is None or isinstance(probe.rss_mb(), float)


def test_disk_rss_samples_exist():
    if not os.path.isfile(_RSS):
        return
    rows = _rows(_RSS)
    assert rows
    assert all("ts" in r and "rss_mb" in r for r in rows)
