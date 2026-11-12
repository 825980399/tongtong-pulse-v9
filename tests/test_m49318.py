"""493.18 批单测：长程记忆 organ 三路径 / 两套账双向追溯 /
offline_local session 生产兜底 / A-B 机制调研结论固化。

纪律：主笔 ENABLE_M493_SELF_QUERY_FASTPATH 仍 False；LEARNED_ALIASES 仍 False。
逻辑单测用 tmp/fake 隔离（不污染生产磁盘数据）；另设只读磁盘快照验收。
"""
import json
import os
import time

from nucleus.qica.QICA import QICA
from nucleus.mnemosyne.verification_learning_hub import VerificationLearningHub
from nucleus.self_index import fastpath as fp
from nucleus.self_index import offline_local as ol
from organs.brain.PulseInnerWorld import PulseInnerWorld


# ── 刀1：get_last_organ 三路径 + QICA context_organ 注入 ──────────
def _iw_with(memories):
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw.set_conversation_memory(memories)
    return iw


def _mem(organ, age_s, user="小林"):
    ts = time.time() - age_s
    return {"question": f"{user}问{organ}在哪", "user_name": user,
            "organ": organ, "organ_confidence": 0.9, "entities": [organ],
            "ts": ts, "timestamp": ts}


def test_get_last_organ_recent_and_cross_user():
    now_mems = [
        _mem("肾", 20 * 3600),   # 带 organ 但较早
        {"user_name": "小林", "organ": None, "ts": time.time()},  # 最近但无 organ → 跳过
        _mem("胃", 600),          # 最近带 organ
        _mem("肝", 3 * 3600, user="另一个人"),  # 跨用户，不算小林
    ]
    iw = _iw_with(now_mems)
    r = iw.get_last_organ("小林")
    assert r is not None and r["organ"] == "胃", r
    assert r["age_s"] < 86400
    assert iw.get_last_organ("陌生人") is None  # 跨用户取 None


def test_get_last_organ_over_24h_none():
    old = [_mem("胃", 90000)]  # 25 小时前
    assert _iw_with(old).get_last_organ("小林") is None


def test_qica_context_organ_binds_anaphora():
    # 「它呢」经显式 context_organ 绑定到胃
    q = QICA()
    a = q.classify_internal({"content": "它呢", "user_name": "小林",
                             "context_organ": "胃", "context_organ_age": 3,
                             "context_organ_confidence": 0.9})
    assert a.get("anaphora_resolved", {}).get("organ") == "胃"


def test_disk_conversation_memory_has_organ_seeds():
    # 磁盘快照验收：≥5 条带 organ 的真实记忆（活体可改写，故只读宽松断言）
    p = os.path.join("data", "context", "conversation_memory.json")
    if not os.path.exists(p):
        return
    d = json.load(open(p, encoding="utf-8"))
    mems = []
    for _u, part in d.get("users", {}).items():
        mems.extend(part.get("memories", []))
    with_organ = [m for m in mems if m.get("organ")]
    assert len(with_organ) >= 5, f"带 organ 记忆仅 {len(with_organ)} 条"


# ── 刀2：两套账交叉引用 / 双向可追溯 ──────────────────────────────
def test_two_ledgers_bidirectional_trace(tmp_path):
    vf = tmp_path / "vl.json"
    vf.write_text(json.dumps({"entries": []}, ensure_ascii=False), encoding="utf-8")
    hub = VerificationLearningHub(file_path=str(vf))
    rid = "semantic_lesson:def0edf132"
    vl_id = hub.record(
        organ="qica", task_type="rule_absorb",
        input_summary=f"QICA 吸收规则 {rid}", local_result={},
        confidence=0.9, relevance_score=0.9, needs_verification=False,
        api_better=False, lesson="规则吸收落地",
        remediation_triggered=True, origin="absorb_trace_reconcile",
        absorb_trace_id=rid)
    # absorb_trace 侧回填 verification_learning_id（走真实 record 入口）
    q = QICA()
    q._m49317_absorb_trace_path = str(tmp_path / "at.jsonl")
    q.absorb_growth_rules([{
        "id": rid, "type": "domain_knowledge", "concept": "晚上好",
        "path": "/情感/问候", "confidence": 0.95, "source": "growth_distill",
        "verification_learning_id": vl_id,
    }])
    at_rows = [json.loads(l) for l in
               (tmp_path / "at.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    vl_rows = json.loads(vf.read_text(encoding="utf-8"))["entries"]
    assert at_rows[0]["verification_learning_id"] == vl_id
    assert at_rows[0]["rule_id"] == rid
    assert vl_rows[0]["id"] == vl_id and vl_rows[0]["absorb_trace_id"] == rid
    assert vl_rows[0]["applied"] is True
    # 双向可追溯：at→vl→at
    _back = [e for e in vl_rows if e["id"] == at_rows[0]["verification_learning_id"]]
    assert _back and _back[0]["absorb_trace_id"] == at_rows[0]["rule_id"]


def test_absorb_record_no_fake_link_when_unmatched(tmp_path):
    # 对应不上 verification_learning 时字段留 None（如实，不编造关联）
    q = QICA()
    q._m49317_absorb_trace_path = str(tmp_path / "at.jsonl")
    q.absorb_growth_rules([{
        "type": "domain_knowledge", "concept": "科技新闻",
        "path": "/技术/新闻", "confidence": 0.9, "source": "growth_distill",
    }])
    rows = [json.loads(l) for l in
            (tmp_path / "at.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows[0]["verification_learning_id"] is None


def test_disk_two_ledgers_linked():
    # 磁盘快照验收：≥1 条双向可追溯
    at = os.path.join("data", "self_index", "absorb_trace.jsonl")
    vl = "data/verification_learning.json"
    if not (os.path.exists(at) and os.path.exists(vl)):
        return
    at_rows = [json.loads(l) for l in open(at, encoding="utf-8").read().splitlines()
               if l.strip()]
    # 493.21 刀1 方案A：两本账天然不同粒度/不同来源，改为独立记录、不强求双向
    # ID 对齐，关联字段允许 None（493.18 的「强制 vl_ 前缀」口径已废止）。
    # 双向逻辑由本文件 test_two_ledgers_bidirectional_trace（tmp 隔离）覆盖，
    # 磁盘侧只验「absorb_trace 仍有真实规则」。
    assert at_rows, "absorb_trace 应有真实规则"
    assert all("verification_learning_id" in r for r in at_rows), \
        "交叉引用字段应保留（值允许 None）"


# ── 刀3：offline_local session 生产兜底（不污染磁盘）─────────────
def test_session_fallback_nonempty():
    s = ol._m49318_session_fallback()
    assert s and s.startswith("offline_local:")


def test_offline_local_shadow_session_never_null(monkeypatch, tmp_path):
    # 生产调用链：用真实降级判据的门控（offline_active=True），
    # 但把落盘重定向到 tmp，避免污染生产影子日志/状态文件。
    shadow = tmp_path / "shadow.jsonl"
    captured = []

    def _fake_record(_q, _ex, source_path=None, session_id=None, error_reason=None):
        captured.append(session_id)
        with open(shadow, "a", encoding="utf-8") as f:
            f.write(json.dumps({"query": _q, "source_path": source_path,
                                "session_id": session_id}) + "\n")

    monkeypatch.setattr(ol, "offline_active", lambda: True)
    monkeypatch.setattr(ol, "_cfg_on", lambda name: True)
    monkeypatch.setattr(fp, "record_self_query_shadow", _fake_record)
    monkeypatch.setattr(fp, "self_query_fastpath_explain",
                        lambda _q: {"answer": None, "confidence": None})
    # ① 显式 session_id → 原样保留；② 不传 → 兜底非空
    ol.offline_layered_answer("胃在哪", session_id="prod_session_49318")
    ol.offline_layered_answer("肺在哪", session_id=None)
    assert captured and all(s for s in captured), captured
    assert captured[0] == "prod_session_49318"
    assert captured[1].startswith("offline_local:")
    rows = [json.loads(l) for l in shadow.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    assert all(r["session_id"] for r in rows), rows


# ── 刀4：A/B 机制调研结论固化（无在线 A/B 可复用）─────────────────
def test_shadow_schema_has_no_ab_double_answer():
    # 493.18 调研结论：当时影子无 A/B 字段。
    # 493.19 刀2 已加 group/variant_id/answer_a/answer_b/winner 五个 schema 前置字段，
    # 但本批**不跑真 A/B**：这些字段的值必须全为 None（不填值、不做胜负判定）。
    # 老行（493.18 及以前）没有这些键，向后兼容、现有数据不受影响。
    p = os.path.join("data", "monitor", "self_query_shadow.jsonl")
    if not os.path.exists(p):
        return
    rows = [json.loads(l) for l in open(p, encoding="utf-8").read().splitlines()
            if l.strip()]
    _ab_fields = ("group", "variant_id", "answer_a", "answer_b", "winner")
    for r in rows:
        if any(k in r for k in _ab_fields):  # 493.19 新行
            assert all(r.get(k) is None for k in _ab_fields), \
                f"本批不跑真 A/B，5 字段应全为 None: {r}"


def test_param_ab_engine_is_segment_not_traffic_split():
    # 调研结论：ParamABTestEngine 的两组来自同一份历史日志前后半段分段，
    # 不是「按用户/会话分流 + 双路径执行」的在线 A/B。
    p = os.path.join("nucleus", "evolution", "ParamABTestEngine.py")
    if not os.path.exists(p):
        return
    src = open(p, encoding="utf-8").read()
    assert "def run_ab_test_in_process" in src
    # 分段口径存在（前半/后半），而非分组流量
    assert "_analyze_log_segment" in src or "分成两半" in src
