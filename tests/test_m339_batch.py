# -*- coding: utf-8 -*-
"""★339批：审批分道 / 连通性四条件 / 判据域 / 内存自观测 / 分域回算 / K2 对表。

只测可复算的纯逻辑与路径契约；不跑活体、不写生产 `data/`（全部 tmp 隔离根）。
"""
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

NOW = 1_760_000_000.0


def _o3(_id="o3-a", **kw):
    r = {"id": _id, "file": "nucleus/demo.py", "method": "dead_fn",
         "issue_type": "dead_code_candidate", "o3_dead_code": True,
         "source": "o3_open_gate", "status": "pending",
         "risk_level": "高", "trust_score": 0,
         "requires_confirmation": True,
         "original_code": "def dead_fn():\n    pass\n",
         "modified_code": "def dead_fn():\n    pass\n",
         "saved_at": NOW - 3600.0, "created_at": NOW - 3600.0,
         "generated_at": NOW - 3600.0}
    r.update(kw)
    return r


FULL_BASIS = {"items": {"evidence_tier": "hard", "probe_out_of_graph": {},
                        "misjudge_sample": [], "god_file": False}}


# ============ 刀1：审批分道 ============
def test_m339_o3_identity_uses_explicit_fields():
    from nucleus.reasoning.PatchManager import PatchManager as PM
    assert PM._m339_is_o3({"o3_dead_code": True}) is True
    assert PM._m339_is_o3({"source": "o3_open_gate"}) is True
    assert PM._m339_is_o3({"issue_type": "dead_code"}) is True
    # llm / local_rule 不得被判成 O3（理由串里出现死代码字样也不算）
    assert PM._m339_is_o3({"source": "llm", "description": "dead_code 类"}) is False


def test_m339_review_items_requires_all_four():
    from nucleus.reasoning.PatchManager import PatchManager as PM
    ok, _why, miss = PM._m339_review_items_ok({"items": {"evidence_tier": "x"}})
    assert ok is False
    assert sorted(miss) == ["god_file", "misjudge_sample", "probe_out_of_graph"]
    ok2, _why2, miss2 = PM._m339_review_items_ok(FULL_BASIS)
    assert ok2 is True and miss2 == []
    ok3, why3, _m = PM._m339_review_items_ok(
        {"items": dict(FULL_BASIS["items"], god_file=True)})
    assert ok3 is False and "上帝文件" in why3
    assert PM._m339_review_items_ok(None)[0] is False


def test_m339_o3_dispatch_ready_gate_on_confirm_flags():
    from nucleus.evolution.patch_execution_dispatch import _m339_o3_dispatch_ready
    _base = {"o3_dead_code": True, "requires_confirmation": False,
             "o3_auto_dispatch_ready": True}
    assert _m339_o3_dispatch_ready(_base)[0] is True
    # llm 类永不自动：即便人工确认（approved）也不许进这条通道
    assert _m339_o3_dispatch_ready({"source": "llm", "status": "approved",
                                    "requires_confirmation": False,
                                    "o3_auto_dispatch_ready": True})[1] == "not_o3"
    # 未置显式标记 / 仍需确认 ⇒ 不派
    assert _m339_o3_dispatch_ready({"o3_dead_code": True})[1] == "not_confirmed"
    assert _m339_o3_dispatch_ready(
        {"o3_dead_code": True, "o3_auto_dispatch_ready": True,
         "requires_confirmation": True})[1] == "still_requires_confirmation"
    assert _m339_o3_dispatch_ready(dict(_base, applied=True))[1] == "applied"
    assert _m339_o3_dispatch_ready(dict(_base, status="expired_pending"))[1] == "expired"


def test_m339_o3_aging_downgrades_not_deletes(tmp_path):
    from nucleus.reasoning.PatchManager import PatchManager as PM
    pm = PM(str(tmp_path))
    _rows = [_o3("old", saved_at=NOW - 30 * 3600.0),
             _o3("new", saved_at=NOW - 1 * 3600.0)]
    _r = pm._m339_o3_aging(_rows, now=NOW)
    assert _r["expired"] == 1 and _r["ids"] == ["old"]
    # 降级不删除：条目仍在，原状态可回滚
    assert _rows[0]["status"] == "expired_pending"
    assert _rows[0]["acked_origin_status"] == "pending"
    assert _rows[0]["expired_revertible"] is True
    assert _rows[1]["status"] == "pending"


def test_m339_o3_aging_switch_off_is_zero_change(tmp_path, monkeypatch):
    import config as _cfg
    from nucleus.reasoning.PatchManager import PatchManager as PM
    monkeypatch.setattr(_cfg, "ENABLE_PENDING_QUEUE_AGING_O3", False, raising=False)
    pm = PM(str(tmp_path))
    _rows = [_o3("old", saved_at=NOW - 30 * 3600.0)]
    _r = pm._m339_o3_aging(_rows, now=NOW)
    assert _r["enabled"] is False and _r["expired"] == 0
    assert _rows[0]["status"] == "pending"


def test_m339_llm_aging_switch_stays_off():
    """llm 类 aging 总开关必须仍是关闭（280批刀3 语义一字不改）。"""
    import config as _cfg
    assert bool(getattr(_cfg, "ENABLE_PENDING_QUEUE_AGING", False)) is False
    assert bool(getattr(_cfg, "ENABLE_PENDING_QUEUE_AGING_O3", True)) is True


def test_m339_approve_o3_patch_writes_sla_fields(tmp_path):
    from nucleus.reasoning.PatchManager import PatchManager as PM
    pm = PM(str(tmp_path))
    _p = os.path.join(str(tmp_path), "data", "patches", "pending_patches.json")
    with io.open(_p, "w", encoding="utf-8", newline="") as f:
        json.dump([_o3("x")], f)
    _r = pm.approve_o3_patch(0, "human:star", FULL_BASIS, judged_by="human")
    assert _r.get("ok") is True, _r
    _row = json.load(io.open(_p, encoding="utf-8"))[0]
    assert _row["approved_signer"] == "human:star"
    assert isinstance(_row["approved_at"], float)
    assert _row["approved_judged_by"] == "human"
    assert isinstance(_row["approved_basis"], dict)
    assert _row["o3_auto_dispatch_ready"] is True
    assert _row["requires_confirmation"] is False


def test_m339_approve_o3_patch_rejects_llm(tmp_path):
    from nucleus.reasoning.PatchManager import PatchManager as PM
    pm = PM(str(tmp_path))
    _p = os.path.join(str(tmp_path), "data", "patches", "pending_patches.json")
    with io.open(_p, "w", encoding="utf-8", newline="") as f:
        json.dump([{"id": "l", "file": "nucleus/a.py", "method": "m",
                    "source": "llm", "original_code": "x=1", "status": "pending"}], f)
    _r = pm.approve_o3_patch(0, "human:star", FULL_BASIS)
    assert _r.get("ok") is False and "非 O3" in _r.get("reason", "")


def test_m339_approve_patch_o3_requires_basis(tmp_path):
    from nucleus.reasoning.PatchManager import PatchManager as PM
    pm = PM(str(tmp_path))
    _p = os.path.join(str(tmp_path), "data", "patches", "pending_patches.json")
    with io.open(_p, "w", encoding="utf-8", newline="") as f:
        json.dump([_o3("x")], f)
    _r = pm.approve_patch(0, "human:star")           # 无 basis ⇒ fail-closed
    assert _r.get("ok") is False and _r.get("missing")


def test_m339_sla_summary_counts_unjudged(tmp_path):
    from nucleus.reasoning.PatchManager import PatchManager as PM
    pm = PM(str(tmp_path))
    _p = os.path.join(str(tmp_path), "data", "patches", "pending_patches.json")
    with io.open(_p, "w", encoding="utf-8", newline="") as f:
        json.dump([_o3("a", saved_at=NOW - 5 * 3600.0),
                   _o3("b", saved_at=NOW - 9 * 3600.0, status="expired_pending")], f)
    _s = pm.o3_pending_sla_summary(now=NOW)
    assert _s["ok"] is True and _s["unjudged"] == 1 and _s["expired"] == 1
    assert _s["oldest_hours"] == pytest.approx(5.0, abs=0.1)


# ============ 刀2：连通性四条件 ============
def _cc(tmp_path, pending, trace, proc_gen="pid1@T"):
    from nucleus.self_inspector import _m339_consumer_connected
    _pp = os.path.join(str(tmp_path), "data", "reports")
    os.makedirs(_pp, exist_ok=True)
    with io.open(os.path.join(_pp, "patch_dispatch_trace.jsonl"), "w",
                 encoding="utf-8", newline="") as f:
        for _r in trace:
            f.write(json.dumps(_r, ensure_ascii=False) + "\n")
    return _m339_consumer_connected(pending, project_root=str(tmp_path),
                                    proc_gen=proc_gen)


def test_m339_consumer_connected_partial_at_each_stage(tmp_path):
    _e = _o3("a")
    # 缺 1：无 O3 条目
    assert _cc(tmp_path, [{"id": "l", "source": "llm"}], [])["partial_at"] == "段1"
    # 缺 2：无同代次 dispatched
    assert _cc(tmp_path, [_e], [{"proc_gen": "old", "patch_ids": ["a"]}])["partial_at"] == "段2"
    # 缺 3：有 dispatched、无 verify 结果
    assert _cc(tmp_path, [_e], [{"proc_gen": "pid1@T", "patch_ids": ["a"]}])["partial_at"] == "段3"
    # 缺 4：有 verify、未沉淀也未过期
    _e3 = dict(_e, runtime_verify_result={"verified": False})
    assert _cc(tmp_path, [_e3], [{"proc_gen": "pid1@T", "patch_ids": ["a"]}])["partial_at"] == "段4"
    # 全立
    _e4 = dict(_e3, applied=True)
    _r = _cc(tmp_path, [_e4], [{"proc_gen": "pid1@T", "patch_ids": ["a"]}])
    assert _r["connected"] is True and _r["partial_at"] is None


def test_m339_consumer_connected_old_trace_without_proc_gen_not_counted(tmp_path):
    _r = _cc(tmp_path, [_o3("a")], [{"patch_ids": ["a"]}])
    assert _r["conditions"]["c2_dispatched_same_gen"] is False


# ============ 刀3：判据域 + 白名单 ============
def test_m339_domain_excludes_tests():
    from nucleus.self_inspector import _m339_in_domain
    for _p in ("tests/test_a.py", "tests/sub/b.py", "nucleus/a/tests/b.py",
               "nucleus/test_helper.py", "organ/test_x.py"):
        assert _m339_in_domain(_p) is False, _p
    for _p in ("nucleus/reasoning/PatchManager.py", "organs/core/X.py",
               "tools/ci/y.py", "main.py", "config.py"):
        assert _m339_in_domain(_p) is True, _p
    assert _m339_in_domain("") is False


def test_m339_intake_whitelist_blocks_fixture_paths():
    from nucleus.self_inspector import _m339_intake_whitelisted
    assert _m339_intake_whitelisted("ExampleOrgan.py") is False
    assert _m339_intake_whitelisted("tests/ExampleOrgan.py") is False
    assert _m339_intake_whitelisted("nucleus/demo.py") is True
    assert _m339_intake_whitelisted("config.py") is True
    assert _m339_intake_whitelisted("") is False


def test_m339_intake_candidates_rejects_domain_and_whitelist():
    from nucleus.self_inspector import _m335_o3_intake_candidates
    _items = [
        {"file": "tests/test_a.py", "method": "m1", "evidence_tier": "hard_strict"},
        {"file": "ExampleOrgan.py", "method": "m2", "evidence_tier": "hard_strict"},
        {"file": "nucleus/ok.py", "method": "m3", "evidence_tier": "hard_strict"},
    ]
    _r = _m335_o3_intake_candidates(items=_items, limit=1)
    assert _r["rejected"]["domain"] == 1
    assert _r["rejected"]["whitelist"] == 1
    assert [c["method"] for c in _r["candidates"]] == ["m3"]


# ============ 刀4：内存自观测钩子 ============
def test_m339_memory_probe_writes_six_header_fields(tmp_path, monkeypatch):
    import config as _cfg
    from organs.core.PulseMetricsCollector import PulseMetricsCollector as PMC
    _h = __import__("time").localtime().tm_hour
    monkeypatch.setattr(_cfg, "M339_MEMORY_PROBE_HOURS", (_h,), raising=False)
    _o = object.__new__(PMC)
    _o._logger = __import__("logging").getLogger("t339")
    _o._m337_image_cache = {"ts": 0.0, "mb": 100.0}
    _r = _o._m339_memory_probe(heap_mb=1300.0, out_dir=str(tmp_path))
    assert _r["ran"] is True, _r
    _files = [f for f in os.listdir(str(tmp_path)) if f.startswith("top_types_")]
    assert len(_files) == 1
    _d = json.load(io.open(os.path.join(str(tmp_path), _files[0]), encoding="utf-8"))
    for _k in ("proc_gen", "code_version", "criteria_version", "heap_mb",
               "image_mb", "top20"):
        assert _k in _d, _k
    assert _d["image_mb"] == 100.0


def test_m339_memory_probe_slot_idempotent(tmp_path, monkeypatch):
    import config as _cfg
    from organs.core.PulseMetricsCollector import PulseMetricsCollector as PMC
    _h = __import__("time").localtime().tm_hour
    monkeypatch.setattr(_cfg, "M339_MEMORY_PROBE_HOURS", (_h,), raising=False)
    _o = object.__new__(PMC)
    _o._logger = __import__("logging").getLogger("t339b")
    _o._m337_image_cache = {"ts": 0.0, "mb": 100.0}
    assert _o._m339_memory_probe(heap_mb=100.0, out_dir=str(tmp_path))["ran"] is True
    _r2 = _o._m339_memory_probe(heap_mb=100.0, out_dir=str(tmp_path))
    assert _r2["ran"] is False and "slot_done" in _r2["reason"]
    assert len([f for f in os.listdir(str(tmp_path))
                if f.startswith("top_types_")]) == 1


def test_m339_memory_probe_freq_config_at_most_two():
    import config as _cfg
    assert 1 <= len(tuple(_cfg.M339_MEMORY_PROBE_HOURS)) <= 2


# ============ 刀5：分域 + 回算 ============
def test_m339_by_version_dir_has_code_and_criteria(tmp_path):
    from nucleus.self_inspector import dump_per_item_by_version
    _p = dump_per_item_by_version([{"file": "nucleus/a.py", "method": "m"}],
                                 "339.c1", project_root=str(tmp_path),
                                 code_version="335.cg.v1")
    _rel = os.path.relpath(_p, str(tmp_path)).replace("\\", "/")
    assert "by_version/335.cg.v1_339.c1/" in _rel
    _d = json.load(io.open(_p, encoding="utf-8"))
    assert _d["code_version"] == "335.cg.v1"
    assert _d["domain_dir"] == "335.cg.v1_339.c1"


def test_m339_by_version_unknown_code_falls_back(tmp_path):
    from nucleus.self_inspector import dump_per_item_by_version
    _p = dump_per_item_by_version([{"file": "nucleus/a.py"}], "339.c1",
                                 project_root=str(tmp_path), code_version="unknown")
    _rel = os.path.relpath(_p, str(tmp_path)).replace("\\", "/")
    assert "by_version/339.c1/" in _rel and "unknown_" not in _rel


def test_m339_recompute_double_reports_and_never_writes_business(tmp_path):
    from nucleus.review_recompute import run_recompute
    _hist = []
    for _i in range(20):
        _r = {"id": "p%02d" % _i, "applied": True, "applied_at": NOW - 10 * 86400}
        if _i >= 8:
            _r["runtime_verify_result"] = {"verified": True}
        if _i >= 14:
            _r["patch_evidence"] = {"risk": {}, "trust": {}, "blast": {}}
        _hist.append(_r)
    _sample = {"items": [{"file": "nucleus/a.py", "method": "m",
                          "evidence_tier": "hard_strict"},
                         {"file": "tests/test_a.py", "method": "t",
                          "evidence_tier": "hard_strict"}]}
    _hp = os.path.join(str(tmp_path), "data", "patches", "patch_history.json")
    os.makedirs(os.path.dirname(_hp), exist_ok=True)
    with io.open(_hp, "w", encoding="utf-8", newline="") as f:
        json.dump(_hist, f)
    _before = io.open(_hp, "rb").read()
    _r = run_recompute(scope="incremental", project_root=str(tmp_path), now=NOW,
                       criteria_version="339.c1", code_version="c1",
                       patch_history=_hist, sample=_sample,
                       out_path=os.path.join(str(tmp_path), "rc", "a.json"),
                       state_path=os.path.join(str(tmp_path), "rc", "_state.json"))
    _b = _r["blocks"]
    assert _b["A_judgeability"]["old"] == 8
    assert _b["A_judgeability"]["new"] == 14
    assert _b["B_dead_code_hard"]["old"] == 2
    assert _b["B_dead_code_hard"]["new"] == 1          # tests/ 被域排除
    assert _b["C_legacy_applied"]["legacy_tagged"] == 20
    assert _b["C_legacy_applied"]["recomputed"] == 0
    assert _r["wrote_business_data"] is False
    assert io.open(_hp, "rb").read() == _before


def test_m339_recompute_promotes_full_on_criteria_change(tmp_path):
    from nucleus.review_recompute import run_recompute
    _sp = os.path.join(str(tmp_path), "rc", "_state.json")
    _kw = dict(project_root=str(tmp_path), now=NOW, code_version="c1",
               patch_history=[], sample={"items": []},
               state_path=_sp,
               out_path=os.path.join(str(tmp_path), "rc", "x.json"))
    assert run_recompute(criteria_version="v1", **dict(
        _kw, out_path=os.path.join(str(tmp_path), "rc", "v1.json")))["scope"] == "incremental"
    _r2 = run_recompute(criteria_version="v2", **dict(
        _kw, out_path=os.path.join(str(tmp_path), "rc", "v2.json")))
    assert _r2["scope"] == "full"
    assert _r2["prev_criteria_version"] == "v1"


# ============ 刀6：K2 对表脚本 ============
def test_m339_k2_script_slices_and_concludes(tmp_path):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import k2_bench_339 as K
    _items = K.build_items(64, seed=325)
    _ab = K.assert_blocks(_items)
    assert _ab["A1-1_slice_conserved"] and _ab["A1-2_no_dup"]
    assert _ab["A4-1_slice_construction_deterministic"]
    _s = [K.run_serial(_items) for _ in range(2)]
    _p = [K.run_parallel(_items, 3) for _ in range(2)]
    assert all(r["reviewed"] == 64 for r in _s + _p)
    assert all(r["applied"] == 0 for r in _s + _p)     # 未开 runner
    _c = K._conclude(_s, _p)
    assert _c["input_same_domain"] is True
    assert _c["output_equal"] is True
    # ★342刀3 契约同步（E-2 裁决）：verdict 改由**安全面**决定，
    #   吞吐降幅降级为报告字段。旧值 PASS_throughput/FAIL_throughput 不再产出，
    #   只在 `verdict_legacy_throughput` 里保留供历史数据对表。
    assert _c["verdict"] in ("PASS_safety", "FAIL_output_mismatch")
    assert _c["safety_pass"] is True
    assert _c["verdict_basis"].startswith("safety_only")
    assert _c["throughput_report_only"] is True
    assert _c["throughput_line_enforced"] is False
    assert _c["throughput_pct"] == _c["drop_pct"]
    assert _c["throughput_band"] in ("gil_noise", "above_noise", "regression", "unknown")
    assert _c["verdict_legacy_throughput"] in ("PASS_throughput", "FAIL_throughput")


def test_m339_k2_live_mode_stub_removed():
    """**契约已更新**（340 刀8）：原用例 `..._refused` 断言 live 恒返回 2，
    那是「live 还是桩」时代的契约；340 刀8 把 live 落地后该断言必然失败，
    故改锁新契约（桩文案已移除 + live 构件在位 + 无留样才 fail-fast 返回 2）。

    真跑由 tests/test_m340_batch.py::test_62 覆盖（读盘+AST+probe 全真）。
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import k2_bench_339 as K
    assert hasattr(K, "build_live_items") and hasattr(K, "run_serial_live")
    assert hasattr(K, "run_parallel_live") and hasattr(K, "warmup_live")
    _src = io.open(os.path.join(ROOT, "tools", "k2_bench_339.py"),
                   encoding="utf-8", errors="replace").read()
    assert "需在 10-22 04:00 重启窗之后执行" not in _src, "live 桩文案应已移除"
    # 无留样 ⇒ 构件返回 ok=False（fail-fast，不静默空跑）
    _r = K.build_live_items(3, os.path.join(ROOT, "__no_such_root__"))
    assert _r["ok"] is False and _r["items"] == []
    assert "无 per-item 留样" in (_r.get("reason") or "")
