"""493.19 批单测：影子态主笔算子规则表 / GPU 超时退避 /
A/B schema 前置 / 长程 seed 转真实 / dry-run 落盘修复。

纪律：主笔 ENABLE_M493_SELF_QUERY_FASTPATH 仍 False；LEARNED_ALIASES 仍 False。
逻辑单测 tmp/fake 隔离，不污染生产磁盘；磁盘验收只读宽松断言。
"""
import json
import os
import subprocess
import time
import types

from nucleus.self_index import fastpath as fp
from nucleus.self_index import learned_alias as la
from nucleus.self_index import shadow_rules as sr
from organs.brain.PulseInnerWorld import PulseInnerWorld
from organs.senses.PulseTouch import PulseTouch


_RULES = [
    {"rule_id": "semantic_lesson:def0edf132", "concept": "晚上好",
     "path": "/情感/问候", "source": "growth_distill"},
    {"rule_id": "semantic_lesson:0f85394856", "concept": "数字化生命",
     "path": "/知识/数字化生命", "source": "growth_distill"},
    {"rule_id": "semantic_lesson:64e1b41c3a", "concept": "科技新闻",
     "path": "/技术/新闻", "source": "growth_distill"},
]


def _write_rules(tmp_path):
    p = tmp_path / "absorb_trace.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for r in _RULES:
            rec = dict(r, result="applied", type="domain_knowledge",
                       downstream_delta="d")
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        # 非 applied 的行应被过滤
        f.write(json.dumps({"rule_id": "x", "concept": "不该加载",
                            "path": "/x", "source": "growth_distill",
                            "result": "rejected"}, ensure_ascii=False) + "\n")
    return str(p)


# ── 刀1：影子规则表 + 对比路径 + explain 影子建议 ────────────────
def test_load_shadow_rules(tmp_path):
    rows = sr.load_shadow_rules(_write_rules(tmp_path))
    assert len(rows) == 3, rows  # result=applied 才加载
    assert {r["rule_id"] for r in rows} == {r["rule_id"] for r in _RULES}
    for r in rows:
        assert all(k in r for k in ("rule_id", "concept", "path", "source"))


def test_match_shadow_rules():
    assert sr.match_shadow_rules("晚上好 曈曈", _RULES)[0]["concept"] == "晚上好"
    assert sr.match_shadow_rules("聊点别的", _RULES) == []
    assert sr.match_shadow_rules("", _RULES) == []


def test_build_shadow_suggestion_primary_vs_shadow():
    sug = sr.build_shadow_suggestion("晚上好 曈曈", rules=_RULES)
    assert sug["primary_paths"] == ["/知识"]
    assert sug["shadow_paths"] == ["/情感/问候", "/知识"]
    assert sug["changed"] is True
    assert sug["rules"][0]["rule_id"] == "semantic_lesson:def0edf132"
    assert sr.build_shadow_suggestion("无关话题", rules=_RULES) is None


def test_shadow_suggestion_respects_real_primary_paths():
    # 传入真实主笔路径时，影子路径在其前面插入规则 path
    sug = sr.build_shadow_suggestion("科技新闻", primary_paths=["/技术/旧"], rules=_RULES)
    assert sug["primary_paths"] == ["/技术/旧"]
    assert sug["shadow_paths"][0] == "/技术/新闻"
    assert sug["changed"] is True


def test_record_shadow_compare(tmp_path):
    p = tmp_path / "compare.jsonl"
    sug = sr.build_shadow_suggestion("晚上好", rules=_RULES)
    assert sr.record_shadow_compare(sug, str(p)) is True
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows[0]["primary_paths"] == ["/知识"]
    assert rows[0]["shadow_paths"][0] == "/情感/问候"
    assert "ts" in rows[0]
    assert sr.record_shadow_compare(None, str(p)) is False  # 无建议不写


def test_explain_has_shadow_suggestion_but_answer_unchanged():
    # explain 带 shadow_suggestion 字段；主笔答案逻辑不受影响（有/无建议都能答）
    ex_hit = fp.self_query_fastpath_explain("晚上好 曈曈")
    assert "shadow_suggestion" in ex_hit
    assert ex_hit["shadow_suggestion"] is not None
    assert ex_hit["shadow_suggestion"]["changed"] is True
    assert ex_hit.get("answer") is not None  # 主笔照常答，未被影子替换
    ex_none = fp.self_query_fastpath_explain("胃在哪")
    assert "shadow_suggestion" in ex_none
    assert ex_none["shadow_suggestion"] is None  # 无规则命中不编建议


def test_shadow_suggestion_does_not_mutate_qica_rules(tmp_path):
    # 纯影子：不改 QICA._domain_knowledge（本模块不持有 QICA 实例/不写入）
    from nucleus.qica.QICA import QICA
    q = QICA()
    before = dict(q._domain_knowledge)
    sr.build_shadow_suggestion("晚上好", rules=_RULES)
    sr.record_shadow_compare(sr.build_shadow_suggestion("晚上好", rules=_RULES),
                             str(tmp_path / "compare.jsonl"))
    assert dict(q._domain_knowledge) == before


# ── 刀1附加 P2：GPU 探测连续超时退避 ─────────────────────────────
def _touch():
    t = PulseTouch.__new__(PulseTouch)
    t._gpu_timeout_streak = 0
    t._gpu_probe_backoff_until = 0.0
    t._gpu_usage_cache = {}
    return t


class _SyncThread:
    """同步执行 target 的假线程，供单测 deterministic 验证 _run()。"""

    def __init__(self, target=None, daemon=False):
        self._target = target

    def start(self):
        if self._target:
            self._target()


def test_gpu_3_timeouts_then_backoff(monkeypatch):
    import organs.senses.PulseTouch as pt
    t = _touch()
    monkeypatch.setattr(PulseTouch, "_M49319_TIMEOUT_STREAK_MAX", 3)
    monkeypatch.setattr(PulseTouch, "_M49319_BACKOFF_SEC", 600.0)
    monkeypatch.setattr(pt.threading, "Thread", _SyncThread)

    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired("nvidia-smi", 5)

    monkeypatch.setattr(subprocess, "run", _timeout)
    for i in range(3):
        t._gpu_probe_once()
        if i < 2:
            assert not t._gpu_backoff_active(), f"第{i+1}次不应退避"
    assert t._gpu_timeout_streak == 3
    assert t._gpu_backoff_active() is True, "连续 3 次超时进入退避"


def test_gpu_backoff_skips_new_probe(monkeypatch):
    import organs.senses.PulseTouch as pt
    t = _touch()
    t._gpu_probe_backoff_until = time.time() + 600.0
    created = []

    class _CountThread(_SyncThread):
        def __init__(self, target=None, daemon=False):
            created.append(True)
            super().__init__(target, daemon)

    monkeypatch.setattr(pt.threading, "Thread", _CountThread)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=0, stdout="10, 20"))
    t._gpu_probe_once()
    assert not created, "退避期不应创建探测线程 / 启动 nvidia-smi"


def test_gpu_success_resets_counter(monkeypatch):
    import organs.senses.PulseTouch as pt
    t = _touch()
    monkeypatch.setattr(PulseTouch, "_M49319_TIMEOUT_STREAK_MAX", 3)
    monkeypatch.setattr(PulseTouch, "_M49319_BACKOFF_SEC", 600.0)
    t._gpu_timeout_streak = 3
    t._gpu_probe_backoff_until = time.time() + 600.0
    # 到期放行一次，成功
    t._gpu_probe_backoff_until = 0.0
    t._gpu_note_success()
    assert t._gpu_timeout_streak == 0
    assert t._gpu_probe_backoff_until == 0.0
    assert t._gpu_backoff_active() is False


def test_gpu_full_success_via_probe_resets(monkeypatch):
    import organs.senses.PulseTouch as pt
    t = _touch()
    monkeypatch.setattr(pt.threading, "Thread", _SyncThread)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=0, stdout="42, 55"))
    t._gpu_timeout_streak = 2
    t._gpu_probe_once()
    assert t._gpu_timeout_streak == 0, "成功一次重置计数器"
    assert t._gpu_usage_cache.get("usage_percent") == 42.0
    assert not t._gpu_backoff_active()


# ── 刀2：A/B schema 前置（5 字段默认 None）────────────────────────
def test_shadow_row_ab_schema_default_none(monkeypatch, tmp_path):
    # chdir 到 tmp：record_self_query_shadow 的相对路径 data/monitor/... 落到 tmp
    monkeypatch.chdir(tmp_path)
    os.makedirs(os.path.join("data", "monitor"), exist_ok=True)
    fp.record_self_query_shadow("胃在哪", {"answer": "胃 在 x", "organ": "胃",
                                          "confidence": 0.9}, session_id="s")
    shadow = tmp_path / "data" / "monitor" / "self_query_shadow.jsonl"
    rows = [json.loads(l) for l in shadow.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    _ab = ("group", "variant_id", "answer_a", "answer_b", "winner")
    assert rows, "应写入影子行"
    for r in rows:
        assert all(k in r for k in _ab), r
        assert all(r.get(k) is None for k in _ab), r


def test_ab_fields_accept_optional_values(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    os.makedirs(os.path.join("data", "monitor"), exist_ok=True)
    monkeypatch.setattr(fp, "_shadow_proc_gen", lambda: 1.0)
    fp.record_self_query_shadow(
        "胃在哪", {"answer": "胃 在 x", "organ": "胃", "confidence": 0.9},
        session_id="s", group="A", variant_id="v1", answer_a="a", answer_b="b",
        winner=None)
    shadow = tmp_path / "data" / "monitor" / "self_query_shadow.jsonl"
    r = json.loads(shadow.read_text(encoding="utf-8").splitlines()[0])
    assert r["group"] == "A" and r["variant_id"] == "v1"
    assert r["answer_a"] == "a" and r["answer_b"] == "b" and r["winner"] is None


def test_disk_shadow_old_rows_untouched():
    # 磁盘快照：老行没有 A/B 字段（向后兼容，现有数据不受影响）
    p = os.path.join("data", "monitor", "self_query_shadow.jsonl")
    if not os.path.exists(p):
        return
    rows = [json.loads(l) for l in open(p, encoding="utf-8").read().splitlines()
            if l.strip()]
    old = [r for r in rows if "session_id" not in r]  # 493.10 老 schema
    assert all("group" not in r for r in old), "老行不应被回填 A/B 字段"


# ── 刀3：长程 seed 转真实 ─────────────────────────────────────────
def test_disk_seed_memories_no_marker():
    p = os.path.join("data", "context", "conversation_memory.json")
    if not os.path.exists(p):
        return
    d = json.load(open(p, encoding="utf-8"))
    mems = [m for u, part in d.get("users", {}).items()
            for m in part.get("memories", [])]
    organ_mems = [m for m in mems if m.get("organ")]
    assert len(organ_mems) >= 5
    assert all("_m49318_seed" not in m for m in organ_mems), \
        "5 条记忆不应再有验证种子标记"
    assert all(m.get("_source") == "manual_seed" for m in organ_mems)


def test_get_last_organ_still_works_after_seed_conversion():
    p = os.path.join("data", "context", "conversation_memory.json")
    if not os.path.exists(p):
        return
    d = json.load(open(p, encoding="utf-8"))
    mems = [m for u, part in d.get("users", {}).items()
            for m in part.get("memories", [])]
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw.set_conversation_memory(mems)
    r = iw.get_last_organ("小林")
    assert r and r["organ"] == "胃", r
    assert iw.get_last_organ("陌生人") is None


def test_qica_context_organ_still_binds():
    from nucleus.qica.QICA import QICA
    q = QICA()
    a = q.classify_internal({"content": "它呢", "user_name": "小林",
                             "context_organ": "胃", "context_organ_age": 3,
                             "context_organ_confidence": 0.9})
    assert a.get("anaphora_resolved", {}).get("organ") == "胃"


# ── 刀4：dry-run 落盘修复 ─────────────────────────────────────────
def test_maybe_trigger_backfills_once(monkeypatch):
    # 首次写影子触发一次全量回填（修「抽出来了但没落盘」），100 门槛仍在
    calls = []
    monkeypatch.setattr(la, "extract_and_append_dryrun",
                        lambda *a, **k: calls.append(1) or 0)
    monkeypatch.setattr(la, "_cfg_on", lambda name: True)
    la._DRYRUN_BACKFILL_DONE = False
    la._DRYRUN_COUNTER = 0
    for _ in range(99):
        la._maybe_trigger_dryrun()
    assert len(calls) == 1, "首次回填恰好一次（不重复）"
    # 第 100 次仍走原门槛
    la._maybe_trigger_dryrun()
    assert len(calls) == 2
    assert la._DRYRUN_COUNTER == 0
    # 复位，避免污染其它测试
    la._DRYRUN_BACKFILL_DONE = False
    la._DRYRUN_COUNTER = 0


def test_extract_appends_grows_file_rows(tmp_path, monkeypatch):
    # 3 个不同 query 的影子行 → 落盘 3 行（去重口径真实上限）
    shadow = tmp_path / "shadow.jsonl"
    dryrun = tmp_path / "dryrun.jsonl"
    rows = [
        {"query": q, "would_answer": "胃 在 x", "action_id": "organ.location",
         "organ": "胃", "confidence": 0.9, "skipped_by_guard": False}
        for q in ("胃在哪", "胃在身体的哪个位置", "肺在哪")
    ]
    with open(shadow, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    monkeypatch.setattr(la, "_dryrun_candidates_path", lambda: str(dryrun))
    monkeypatch.setattr(la, "_cfg_on", lambda name: True)
    la._SEED_VETO_DONE = True  # 跳过 veto 初始化（不依赖真实 veto 文件）
    n = la.extract_and_append_dryrun(shadow_path=str(shadow))
    assert n == 3, n
    on_disk = [json.loads(l) for l in dryrun.read_text(encoding="utf-8").splitlines()
               if l.strip()]
    assert len(on_disk) == 3
    # 再跑一次：去重生效，不重复落盘
    assert la.extract_and_append_dryrun(shadow_path=str(shadow)) == 0
    la._SEED_VETO_DONE = False


def test_dryrun_backfill_no_promote(tmp_path, monkeypatch):
    # 回填只写候选文件，不写 overlay / online_answer_delta 恒 0（不 promote）
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
    overlay = tmp_path / "overlay.json"
    assert not overlay.exists()
    la.extract_and_append_dryrun(shadow_path=str(shadow))
    assert dryrun.exists() and not overlay.exists(), "不应写 overlay"
    la._SEED_VETO_DONE = False
