# -*- coding: utf-8 -*-
"""350 批隔离单测（刀1..刀7 核心不变量）。

纪律：
  · 全部走**路径注入**（root / audit_dir_path / state_path / base_dir），
    不读写生产 `data/`；
  · 不依赖现场数据 —— 真值由用例现造（铁律179）；
  · 每条"能过"的断言旁边都有一条**反向控制**（铁律168：对拍先造判别力）。
"""

import json
import os
import time

from nucleus import audit_problem_pool as ap
from nucleus.evolution import desc_norm as dn
from nucleus.evolution.criteria_window import (
    M350_LEDGER_CONCLUSIONS, write_ledger_conclusions,
)
from nucleus.evolution.patch_generator import (
    check_desc_dynamic, check_method_contract, check_reason_chain,
)
from nucleus.evolution.patch_lifecycle import clear_cancelled_pending
from nucleus.evolution.verify_feedback import (
    apply_feedback, feedback_key, map_verdict_to_entry,
)
from nucleus.evolution.zero_break_judge import death_false_positive_rate
from nucleus.mnemosyne import live_gate as lg


# ---------------------------------------------------------------- 刀2 / 刀3
def _hist(pg, values):
    return [{"proc_gen": pg, "heap_mb": v} for v in values]


_GOOD = dict(heap_mb=1500.0, heap_history=_hist("p1", [1400.0, 1500.0, 1600.0]),
             w1_verdict="alive",
             reload_stats={"hit_rate": 0.99, "denominator": 10},
             cold_index_writable={"writable": True, "index_entries": 5},
             proc_gen="p1")


def test_live_gate_four_conditions_all_pass():
    g = lg.live_gate_ok(**_GOOD)
    assert g["ok"] is True
    assert g["blocked_by"] == []
    # LIVE_ENABLED 默认 off ⇒ 即使四条件全立也只能 shadow
    assert lg.LIVE_ENABLED is False
    assert g["mode"] == "shadow"


def test_live_gate_each_condition_can_block_alone():
    """判别力：四个条件都必须能单独把门关掉（否则就是"三条抵一条"）。"""
    cases = {
        lg.COND_HEAP: dict(_GOOD, heap_history=_hist("p1", [900.0, 900.0, 900.0])),
        lg.COND_W1: dict(_GOOD, w1_verdict="stale"),
        lg.COND_RECALL: dict(_GOOD, reload_stats={"hit_rate": None, "denominator": 0}),
        lg.COND_COLD: dict(_GOOD, cold_index_writable={"writable": False,
                                                       "index_entries": 0}),
    }
    for cond, kw in cases.items():
        g = lg.live_gate_ok(**kw)
        assert g["ok"] is False, cond
        assert g["blocked_by"] == [cond], cond


def test_live_gate_heap_needs_three_beats_same_gen():
    """跨代次样本不能拼成"持续"（重启会把 heap 归零）。"""
    g = lg.live_gate_ok(**dict(_GOOD, heap_history=_hist("p0", [1900.0] * 3)))
    assert g["blocked_by"] == [lg.COND_HEAP]
    g2 = lg.live_gate_ok(**dict(_GOOD, heap_history=_hist("p1", [1900.0] * 2)))
    assert g2["blocked_by"] == [lg.COND_HEAP]


def test_live_gate_paused_category_forces_shadow():
    g = lg.live_gate_ok(**_GOOD, paused=["semantic_cache"])
    assert g["ok"] is True and g["mode"] == "shadow"


def test_escalation_skip_is_red():
    assert lg.assert_escalation_order(lg.STAGE_SHADOW, lg.STAGE_G2)["ok"] is True
    assert lg.assert_escalation_order(lg.STAGE_G2, lg.STAGE_G3)["ok"] is True
    skip = lg.assert_escalation_order(lg.STAGE_SHADOW, lg.STAGE_G3)
    assert skip["ok"] is False and skip["state"] == "stage_skip_forbidden"
    assert lg.assert_escalation_order(lg.STAGE_SHADOW, lg.STAGE_G4)["ok"] is False
    # 回退是安全动作，不拦
    assert lg.assert_escalation_order(lg.STAGE_G3, lg.STAGE_G2)["state"] == "rollback"


def test_advance_stage_needs_seven_day_hold(tmp_path):
    sp = str(tmp_path / "gate.json")
    now = time.time()
    assert lg.advance_stage(lg.STAGE_G2, state_path=sp, now=now)["state"] == "advanced"
    deny = lg.advance_stage(lg.STAGE_G3, state_path=sp,
                            reload_stats={"hit_rate": 0.99}, now=now + 10)
    assert deny["state"] == "hold_not_met"
    st = lg._load_state(sp)
    st["hit_ok_since_ts"] = now - 8 * 86400
    lg._save_state(st, sp)
    ok = lg.advance_stage(lg.STAGE_G3, state_path=sp,
                          reload_stats={"hit_rate": 0.99}, now=now + 20)
    assert ok["state"] == "advanced"


def test_rollback_three_lines(tmp_path):
    now = time.time()
    assert lg.evaluate_rollback([], now=now)["action"] == lg.ACT_NONE
    # ① 同节点 30min 内释放-回载 ≥3
    r1 = lg.evaluate_rollback([
        {"kind": "release", "node_id": "n1", "category": "semantic_cache", "ts": now - 9},
        {"kind": "recall_ok", "node_id": "n1", "ts": now - 6},
        {"kind": "recall_ok", "node_id": "n1", "ts": now - 4},
        {"kind": "recall_ok", "node_id": "n1", "ts": now - 2},
    ], now=now)
    assert r1["action"] == lg.ACT_PAUSE
    # 反向控制：窗口外不触发
    r1b = lg.evaluate_rollback([
        {"kind": "release", "node_id": "n2", "ts": now - 9999},
        {"kind": "recall_ok", "node_id": "n2", "ts": now - 9990},
        {"kind": "recall_ok", "node_id": "n2", "ts": now - 9980},
        {"kind": "recall_ok", "node_id": "n2", "ts": now - 9970},
    ], now=now)
    assert r1b["action"] == lg.ACT_NONE
    # ② 一次回载失败即回 shadow
    assert lg.evaluate_rollback(
        [{"kind": "recall_fail", "node_id": "n3", "ts": now}],
        now=now)["action"] == lg.ACT_SHADOW
    # ③ 驱逐未写冷索引 ⇒ 不允许 live
    assert lg.evaluate_rollback(
        [{"kind": "evict", "node_id": "n4", "ts": now, "cold_index_written": False}],
        now=now)["action"] == lg.ACT_SHADOW


def test_pause_category_idempotent(tmp_path):
    sp = str(tmp_path / "gate.json")
    a = lg.pause_category("semantic_cache", "why", state_path=sp)
    b = lg.pause_category("semantic_cache", "why", state_path=sp)
    assert a["changed"] is True and b["changed"] is False
    assert b["repeat"] == 1
    assert lg.paused_categories(sp) == ["semantic_cache"]
    assert lg.resume_category("semantic_cache", state_path=sp)["changed"] is True


# ---------------------------------------------------------------- 刀6 归一
def test_desc_norm_same_identity_for_floating_readings():
    a = "死代码（硬=59.3%/全=66.2%）nucleus/field/InfoField.py:0"
    b = "死代码（硬=59.4%/全=66.2%）nucleus/field/InfoField.py:0"
    assert dn.semantic_identity(a) == dn.semantic_identity(b)
    # 反向控制：真正变了的内容不能被抹平
    c = b.replace("InfoField", "aibot_logger")
    assert dn.semantic_identity(c) != dn.semantic_identity(a)


def test_desc_dynamic_gate_rejects_readings():
    assert check_desc_dynamic({"desc": "覆盖率 硬=59.3%"})["ok"] is False
    assert check_desc_dynamic({"desc": "死代码候选：无引用"})["ok"] is True


def test_reason_chain_requires_three_parts():
    assert check_reason_chain({"reason": "补 bug"})["ok"] is False
    assert check_reason_chain(
        {"reason": "触发行:x.py:12 现象:空 desc 进池 依据:兜底桶统计"})["ok"] is True


def test_method_contract_requires_full_method():
    # 锚点级片段（无顶层 def）⇒ 拒
    assert check_method_contract({"method": "f",
                                  "modified_code": "    return 1"})["ok"] is False
    # def 名与 method 不符 ⇒ 拒
    assert check_method_contract({"method": "g",
                                  "modified_code": "def f():\n    return 1\n"
                                  })["ok"] is False
    # 整方法 ⇒ 过
    ok = check_method_contract({"method": "f",
                                "original_code": "def f():\n    return 0\n",
                                "modified_code": "def f():\n    x = 1\n    return x\n"})
    assert ok["ok"] is True and ok["mode"] == "method"


# ---------------------------------------------------------------- 刀1 回灌
def test_feedback_key_stable():
    assert feedback_key("p", 1.0, True) == feedback_key("p", 1.0, True)
    assert feedback_key("p", 1.0, True) != feedback_key("p", 1.0, False)


def test_map_verdict_by_pool_prefix_and_unmap():
    entries = [{"dedup_key": "abc", "file": "a/b.py", "issue_type": "dead_code"}]
    m = map_verdict_to_entry({"patch_id": "pool_abc"}, entries)
    assert m["state"] == "ok" and m["by"] == "pool_prefix"
    m2 = map_verdict_to_entry({"patch_id": "pool_zzz"}, entries)
    assert m2["state"] == "unmap"
    m3 = map_verdict_to_entry({"patch_id": "other", "file": "c/d.py",
                               "issue_type": "dead_code"}, entries)
    assert m3["state"] == "unmap"


def test_apply_feedback_idempotent_and_none_keeps_status(tmp_path):
    base = str(tmp_path)
    adir = os.path.join(base, "reports", "audit")
    now = time.time()
    rep = {"issues": [
        {"issue_type": "dead_code_candidate", "severity": "high",
         "file": "nucleus/x.py", "line": 10, "method": "mA",
         "message": "死代码候选A"},
        {"issue_type": "dead_code_candidate", "severity": "high",
         "file": "nucleus/y.py", "line": 20, "method": "mB",
         "message": "死代码候选B"},
    ]}
    ap.persist_pool(rep, root=base, audit_dir_path=adir, now=now)
    keys = [e["dedup_key"] for e in ap.load_pool(base, adir, now)["entries"]]
    for k in keys:
        ap.set_status(k, "claimed", root=base, audit_dir_path=adir, now=now)
    verdicts = {
        "pool_" + keys[0]: {"patch_id": "pool_" + keys[0], "passed": True,
                            "verified_at": now, "dims": {"function_ok": True},
                            "reason": "修好"},
        "pool_" + keys[1]: {"patch_id": "pool_" + keys[1], "passed": None,
                            "verified_at": now, "dims": {}, "reason": "没测出来"},
    }
    r1 = apply_feedback(verdicts, ap.load_pool(base, adir, now)["entries"],
                        ledger={"applied": {}}, pool=ap, now=now,
                        root=base, audit_dir_path=adir)
    assert r1["stats"]["written"] == 2
    assert r1["stats"]["undetermined"] == 1
    after = {e["dedup_key"]: e for e in ap.load_pool(base, adir, now)["entries"]}
    # passed=True 且原态 claimed ⇒ 经 fixed 到 verified（状态机不允许跨级）
    assert after[keys[0]]["status"] == "verified"
    assert after[keys[0]]["verify_feedback"]["passed"] is True
    # passed=None ⇒ 只记反馈，**不改状态**
    assert after[keys[1]]["status"] == "claimed"
    assert after[keys[1]]["verify_feedback"]["passed"] is None

    # 幂等：同一份 verdict 再来一次 ⇒ 全部按 dup 跳过
    led = {"applied": {}}
    apply_feedback(verdicts, ap.load_pool(base, adir, now)["entries"],
                   ledger=led, pool=ap, now=now, root=base, audit_dir_path=adir)
    r2 = apply_feedback(verdicts, ap.load_pool(base, adir, now)["entries"],
                        ledger=led, pool=ap, now=now, root=base, audit_dir_path=adir)
    assert r2["stats"]["written"] == 0
    assert r2["stats"]["skipped_dup"] == 2


# ---------------------------------------------------------------- 刀4 撤单
def test_clear_cancelled_pending_idempotent():
    pending = [{"id": "o3_x", "status": "approved", "o3_dead_code": True,
                "o3_auto_dispatch_ready": True},
               {"id": "o3_y", "status": "approved", "o3_dead_code": True,
                "o3_auto_dispatch_ready": True}]
    hist = [{"id": "o3_x", "status": "wont_fix",
             "wont_fix_reason": "74号裁定：协议接口不可删"}]
    r1 = clear_cancelled_pending(pending, hist)
    assert r1["cleared"] == 1 and r1["rows"][0]["id"] == "o3_x"
    assert pending[0]["pending_cleared"] is True
    assert pending[0]["o3_auto_dispatch_ready"] is False
    # 反向控制：不在 history 里的那条不能被清理
    assert pending[1].get("pending_cleared") is None
    r2 = clear_cancelled_pending(pending, hist)
    assert r2["cleared"] == 0 and r2["already_cleared"] == 1


def test_o3_dispatch_ready_reports_pending_cleared():
    from nucleus.evolution.patch_execution_dispatch import _m339_o3_dispatch_ready
    p = {"id": "o3_x", "o3_dead_code": True, "o3_auto_dispatch_ready": False,
         "requires_confirmation": False, "status": "approved",
         "pending_cleared": True}
    ok, why = _m339_o3_dispatch_ready(p)
    assert ok is False and why == "pending_cleared"


# ---------------------------------------------------------------- 刀5 误报率
def test_death_false_positive_rate_discriminating():
    rows = [{"id": "a", "judged": "dead", "dead_code_true": False},
            {"id": "b", "judged": "dead", "dead_code_true": True}]
    r = death_false_positive_rate(rows)
    assert r["death_false_positive_rate"] == 0.5
    assert r["fp_ids"] == ["a"]
    # 无样本 ⇒ None（不是 0）
    assert death_false_positive_rate([])["death_false_positive_rate"] is None
    # 真值不可判的不进分母
    r2 = death_false_positive_rate([{"id": "c", "judged": "dead",
                                     "dead_code_true": None}])
    assert r2["death_judged_n"] == 0 and r2["undetermined_truth"] == 1


# ---------------------------------------------------------------- 刀7 台账
def test_ledger_conclusions_idempotent(tmp_path):
    p = str(tmp_path / "criteria_ledger.jsonl")
    r1 = write_ledger_conclusions(p)
    assert set(r1["added"]) == set(M350_LEDGER_CONCLUSIONS)
    r2 = write_ledger_conclusions(p)
    assert r2["added"] == []
    rows = [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]
    assert len(rows) == len(M350_LEDGER_CONCLUSIONS)
    for row in rows:
        assert row.get("conclusion") and row.get("boundary")


def test_domain_checklist_passes_and_flags_drift():
    from nucleus.evolution.LogAnalyzer import LogAnalyzer

    class _S:
        M350_DOMAIN_CHECKLIST = LogAnalyzer.M350_DOMAIN_CHECKLIST

        def _log(self, *a, **k):
            pass

    good = {"both": ["config.py", "main.py", "nucleus", "organs", "tools"],
            "only_tool": ["base", "functions", "pulse_doctor.py", "pulses",
                          "somatics", "utils"],
            "only_wl": ["scripts"], "wl_source": "config"}
    assert LogAnalyzer._m350_domain_checklist(_S(), good)["ok"] is True
    bad = dict(good, only_tool=good["only_tool"] + ["scripts"])
    r = LogAnalyzer._m350_domain_checklist(_S(), bad)
    assert r["ok"] is False and r["unexpected"]["only_tool"] == ["scripts"]
