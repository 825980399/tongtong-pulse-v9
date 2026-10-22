# -*- coding: utf-8 -*-
"""337 批回归：死锁筛选 / A4 口径 v2 / W1 仲裁 / typed_infer 登记 / K1 代次 / SC-3 空转。

隔离纪律（铁律158）：
  * 图相关用例**用 tmp_path 造的假图**，不读生产 `data/call_graph/`；
  * 不写任何生产路径；不需要图时全部断言走纯函数。
"""
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ══════════ 刀1：头部死锁 A+B ══════════
def test_m337_triage_excludes_applied():
    """方向A：`applied is True` 的补丁不进自动派单池。"""
    from nucleus.evolution.patch_execution_dispatch import (
        select_for_auto_triage, _LAST_TRIAGE_REJ,
    )
    hist = [{"id": "p_applied", "status": "approved", "risk_level": "低",
             "trust_score": 99, "method": "doRollover", "applied": True}]
    sel = select_for_auto_triage(hist, limit=1)
    assert sel == []
    assert _LAST_TRIAGE_REJ.get("applied") == 1


def test_m337_triage_cap_prefiltered():
    """方向B：cap 死的补丁在**筛选阶段**就被剔除（不靠选中后拦）。"""
    from nucleus.evolution.patch_execution_dispatch import (
        select_for_auto_triage, _LAST_TRIAGE_REJ, _DISPATCH_FAIL_CAP,
    )
    # 侧账里 fcef 的 fail_count 远大于 cap（真实数据 7）
    hist = [{"id": "patch_llm_1791967240_fcef", "status": "approved",
             "risk_level": "低", "trust_score": 99, "method": "doRollover",
             "applied": False}]
    sel = select_for_auto_triage(hist, limit=1)
    assert sel == []
    assert _LAST_TRIAGE_REJ.get("cap", 0) >= 1
    assert _DISPATCH_FAIL_CAP >= 1


def test_m337_last_attempt_ts_takes_max():
    """同 trust 时的第二排序键：取可用字段最大值。"""
    from nucleus.evolution.patch_execution_dispatch import _m337_last_attempt_ts
    assert _m337_last_attempt_ts({"saved_at": 10, "applied_at": 30}) == 30.0
    assert _m337_last_attempt_ts(
        {"saved_at": 5, "runtime_verify_result": {"executed_at": 99}}) == 99.0
    assert _m337_last_attempt_ts({}) == 0.0
    assert _m337_last_attempt_ts({"saved_at": "bad"}) == 0.0


def test_m337_boot_time_never_zero():
    """刀2：重启时刻取不到必须是 None（0 会让「图 mtime ≥ 0」恒真）。"""
    from nucleus.evolution.patch_execution_dispatch import _m337_boot_time
    v = _m337_boot_time()
    assert v is None or (isinstance(v, float) and v > 0)


# ══════════ 刀6：A4 口径 v2 ══════════
def _fake_graph():
    nodes, edges = [], []

    def _n(cls, name, file, line):
        nodes.append({"id": "%s:%s.%s" % (file, cls, name), "cls": cls,
                      "name": name, "file": file, "line": line, "type": "method"})

    _n("PulseInnerWorld", "core_a", "organs/brain/PulseInnerWorld.py", 10)
    _n("PulseInnerWorld", "core_b", "organs/brain/PulseInnerWorld.py", 20)
    _n("PulseCreative", "mk", "organs/brain/pulse_inner_world_creative.py", 5)
    _n("Other", "not_me", "organs/brain/other.py", 1)
    edges.append({"caller": "organs/brain/PulseInnerWorld.py:PulseInnerWorld.core_a",
                  "callee": "organs/brain/PulseInnerWorld.py:PulseInnerWorld.core_b"})
    return {"code_version": "test", "nodes": nodes, "edges": edges}


def test_m337_a4_scope_files_vs_cls():
    """文件口径收 Mixin 类，cls 口径不收；版本与 legacy 留档在位。"""
    from nucleus.self_awareness.CallGraphAnalyzer import CallGraphAnalyzer
    a = CallGraphAnalyzer()
    a._graph_data = _fake_graph()
    scope = a._A4_CLUSTER_SCOPE["files_included"]
    v2 = a.cluster_view("PulseInnerWorld", scope_files=scope)
    v1 = a.cluster_view("PulseInnerWorld")
    assert v1["method_count"] == 2            # 只有 PulseInnerWorld 自己的
    assert v2["method_count"] == 3            # 多了 creative mixin 的 1 个
    assert v2["method_count"] > v1["method_count"]


def test_m337_a4_criteria_version_and_legacy():
    from nucleus.self_awareness.CallGraphAnalyzer import CallGraphAnalyzer
    assert CallGraphAnalyzer._A4_CRITERIA_VERSION == "337.a4.v2"
    sc = CallGraphAnalyzer._A4_CLUSTER_SCOPE
    assert sc["scope_mode"] == "files_included"
    assert sc["manual_baseline"] == sc["graph_baseline"] == 455
    assert sc["gap_baseline"] == 0
    # 旧口径必须留档，供"跨版本禁止比较"识别
    assert sc["legacy_a4_v1"]["criteria_version"] == "317.a4.v1"
    assert sc["legacy_a4_v1"]["graph_baseline"] == 339


# ══════════ 刀8：W1 仲裁 ══════════
def test_m337_a6_arbitrate_three_way():
    from nucleus.world_model.external_heartbeat import _m337_a6_arbitrate
    assert _m337_a6_arbitrate("PASS", {"verdict": "alive"})["verdict"] == "PASS"
    assert _m337_a6_arbitrate("PASS", {"verdict": "stale"})["verdict"] == "provisional"
    assert _m337_a6_arbitrate("PASS", {"verdict": "unavailable"})["verdict"] == "UNAVAILABLE"
    # 取不到 W1 ⇒ unavailable（不猜 alive）
    assert _m337_a6_arbitrate("PASS", {})["verdict"] == "UNAVAILABLE"


def test_m337_w1_consumers_landed():
    from nucleus.world_model.external_heartbeat import w1_consumers
    rows = {r["name"]: r for r in w1_consumers()["consumers"]}
    assert rows["A6 外部证据面"]["landed"] is True
    assert rows["O3 抽验 A"]["landed"] is True
    assert rows["A6 外部证据面"].get("judge")


def test_m337_w1_brief_never_alive_on_error():
    """取数异常必须落 unavailable，不能猜 alive（仲裁方向会反）。"""
    from nucleus.world_model import external_heartbeat as eh
    _orig = eh.external_heartbeat_verdict

    def _boom(*a, **k):
        raise RuntimeError("boom")

    eh.external_heartbeat_verdict = _boom
    try:
        r = eh.w1_verdict_brief()
        assert r["verdict"] == "unavailable"
        assert "boom" in r["basis"] or "RuntimeError" in r["basis"]
    finally:
        eh.external_heartbeat_verdict = _orig


# ══════════ 刀9：typed_infer 登记 ══════════
def test_m337_typed_infer_default_off(monkeypatch):
    from nucleus.self_awareness.CallGraphAnalyzer import CallGraphAnalyzer
    monkeypatch.delenv("TT_TYPED_INFER", raising=False)
    assert CallGraphAnalyzer._m322_typed_enabled() is False
    monkeypatch.setenv("TT_TYPED_INFER", "1")
    assert CallGraphAnalyzer._m322_typed_enabled() is True


def test_m337_typed_only_registry_hit():
    import nucleus.self_inspector as si
    assert si._m337_typed_only("nucleus/llm/semantic_cache.py", "to_dict") is True
    assert si._m337_typed_only(
        "D:/x/nucleus/reporting/report_envelope.py", "has_p0") is True
    # 未登记的方法名不豁免（防"按名全赦"）
    assert si._m337_typed_only("nucleus/logger.py", "to_dict") is False
    assert len(si._M337_TYPED_ONLY_METHODS) == 4


# ══════════ 刀10：K1 gen_consistent ══════════
def test_m337_k1_mark_three_states():
    from nucleus.reasoning import SafeEvolutionExecutor as se
    r1 = se._m337_k1_mark({"proc_gen": "pid1@x", "verdict": "达成", "reason": ""}, None)
    assert r1["gen_consistent"] is None
    r2 = se._m337_k1_mark({"proc_gen": "pid1@x", "verdict": "达成", "reason": ""}, "pid1@x")
    assert r2["gen_consistent"] is True and r2["verdict"] == "达成"
    r3 = se._m337_k1_mark({"proc_gen": "pid1@x", "verdict": "达成", "reason": "ok"}, "pid2@y")
    assert r3["gen_consistent"] is False
    assert r3["verdict"] != "达成"          # 跨代次残留不可信
    assert "跨代次" in r3["reason"]


# ══════════ 刀7：SC-3 空转 ══════════
def test_m337_sc3_empty_is_unavailable(tmp_path):
    """total==0 必须 UNAVAILABLE，**不得** rate=0.0 冒充通过。"""
    from nucleus.world_model.expectation_ledger import sc3_consistency
    (tmp_path / "data" / "world_model").mkdir(parents=True)
    (tmp_path / "data" / "world_model" / "expectation_ledger.jsonl").write_text(
        "", encoding="utf-8")
    r = sc3_consistency(project_root=str(tmp_path))
    assert r.get("total") == 0
    assert r.get("rate") is None
    assert r.get("status") == "UNAVAILABLE"
    assert r.get("ruler_broken") is None


# ══════════ 刀4：heap/image ══════════
def test_m337_image_mb_not_fake_zero():
    """镜像取不到必须是 None（0 会把 heap 放大成整个 RSS）。"""
    from organs.core.PulseMetricsCollector import PulseMetricsCollector
    c = PulseMetricsCollector.__new__(PulseMetricsCollector)
    v = c._m337_image_mb()
    assert v is None or (isinstance(v, float) and v > 0)
    # 缓存命中路径不应把 None 变成 0
    v2 = c._m337_image_mb()
    assert v2 == v


# ══════════ 刀11：重锚护栏 ══════════
def test_m337_thread_gate_passes_now():
    """门禁由红转绿且归因在基线里（★342刀5 重锚后：62/45 → **59/44**）。

    ★本用例与基线**强耦合**：`thread_spawn_baseline.json` 每被重锚一次，
    本断言就必须同步一次 —— 这是**设计如此**（基线门禁的全部价值就是
    "红线只能被有记录地挪动"）。
    342刀5 去掉了 `PulseSubconscious` 两处 Timer 包线程（480/484）
    ＋ `function_loader` 一处聚合装配线程（106）
    ⇒ occurrences −3 / files −1（function_loader 归零退出 file_count），
    归因 2 条、合计恰为 −3/−1；L475 的硬件探针线程经济证据否决后**保留**。
    """
    import subprocess
    _p = subprocess.run(
        [sys.executable, os.path.join(_ROOT, "tools", "ci",
                                      "check_thread_spawn_baseline.py")],
        capture_output=True, text=True, cwd=_ROOT, timeout=120)
    assert _p.returncode == 0, _p.stdout + _p.stderr
    _bp = os.path.join(_ROOT, "tools", "ci", "baselines", "thread_spawn_baseline.json")
    with open(_bp, encoding="utf-8") as fh:
        b = json.load(fh)
    assert b["total_occurrences"] == 59 and b["file_count"] == 44
    attrs = b.get("attribution") or []
    assert len(attrs) == 2
    assert sum(a["delta_occurrences"] for a in attrs) == -3
    # （340 时代那条 `delta_files == 0` 已由上面 `== -1` 取代：342刀5 起 function_loader
    #   的 spawn 归零会退出 file_count，文件数**必须**跟着降 1 —— 留着旧断言就是自相矛盾。）
    # ★340刀5：重锚必须留当日署名（历史 anchored_* 保留）
    assert b.get("anchored_by") == "172刀0" and b.get("anchored_at") == "2026-10-08"
    assert b.get("reanchor_by") == "342刀5" and b.get("reanchor_at") == time.strftime("%Y-%m-%d")
    _h = b.get("reanchor_history") or []
    # 历史**累积**：头一条 from 仍是 172刀0 的 64，最近一条 from 是本次重锚前的 62
    assert len(_h) >= 2
    assert _h[0]["from"]["total_occurrences"] == 64
    assert _h[-1]["from"]["total_occurrences"] == 62
    assert (_h[-1].get("prev_attribution") or [])  # 337 的历史归因须留存（不丢追溯）
