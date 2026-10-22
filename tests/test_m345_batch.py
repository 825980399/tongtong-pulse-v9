# -*- coding: utf-8 -*-
"""第345批施工回归（7 刀 + 344 补1—补6 关键判据）

覆盖：
  d1 问题池落盘（dedup_key 弃行号 / severity 值域 / 状态机 / prune / restore）
  d2 回流分流（三类出口 / hub 条目级数据源 / 空出口也落文件）
  d3 输出端处理类型标识（四值域 + 显式优先 + 非 RESULT 不加字段）
  d4 时间感知注入（三档 source / prompt 前置 / 不伪造时刻）
  d5 愿景接成长记忆（换源阈值 / 空记忆退回单源）
  d6 派单机制治理（cap 豁免三条件 / A-18 变量化 / 并发验证顺序与台账）
  d7 重启连续性（三源 / 空不空壳 / unavailable≠empty / 块不加话术）
  补1 删后新孤儿第四条件    补2 注入面边界与留痕契约
  补3 域过滤（补丁池拒收 / 产物改道）  补4 作废台账锚（proc_gen+圈号+两档）
  补6 删除类整文件快照＋还原演练

纪律：
  * 全部自包含（tmp_path / provider 注入 / 合成数据），不依赖现场 data/（铁律179）
  * 不写生产日志、不改生产状态（铁律158/172）；模块 logger 一律接管
  * 断言用显式布尔/档位，不从理由字符串找关键字（铁律153）
"""

import io
import json
import logging
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture(autouse=True)
def _quiet_loggers():
    """把所有相关模块 logger 接管到 NullHandler，禁止合成指纹进 pulse.log（铁律172）。"""
    _names = ("pulse.module.nucleus.audit_problem_pool",
              "pulse.module.PatchExecutionDispatch",
              "pulse.module.PatchManager",
              "pulse.module.nucleus.cognitive.restart_continuity")
    _bak = {}
    for _n in _names:
        _lg = logging.getLogger(_n)
        _bak[_n] = (list(_lg.handlers), _lg.propagate)
        _lg.handlers[:] = [logging.NullHandler()]
        _lg.propagate = False
    yield
    for _n, (_h, _p) in _bak.items():
        _lg = logging.getLogger(_n)
        _lg.handlers[:] = _h
        _lg.propagate = _p


def _issue(f, m, t="dead_code", sev="low", line=10):
    return {"file": f, "method": m, "type": t, "severity": sev, "line": line}


# ============================================================ d1 问题池
def test_d1_dedup_key_ignores_line_number(tmp_path):
    """dedup_key 弃行号：同一问题换行号仍是同一个 key（335/336 踩过的坑）"""
    from nucleus import audit_problem_pool as p

    _k1 = p.make_dedup_key("a.py", "_m", "dead_code")
    _k2 = p.make_dedup_key("a.py", "_m", "dead_code")
    _k3 = p.make_dedup_key("a.py", "_m2", "dead_code")
    assert _k1 == _k2
    assert _k1 != _k3
    assert len(_k1) == 16
    # 行号根本没进签名：同 file/method/type 不同 line ⇒ 同 key
    _e1 = p.entry_from_issue(_issue("a.py", "_m", line=10))
    _e2 = p.entry_from_issue(_issue("a.py", "_m", line=999))
    assert _e1["dedup_key"] == _e2["dedup_key"]
    assert _e1["line"] == 10 and _e2["line"] == 999


def test_d1_severity_value_domain(tmp_path):
    """severity 值域外不做别名归一 ⇒ 判非法、留原值、强制清洗队列"""
    from nucleus import audit_problem_pool as p

    assert p.normalize_severity("low")[0] == "low"
    _v, _ok, _raw = p.normalize_severity("ERROR")
    assert _ok is False
    assert _raw == "ERROR"
    assert _v == "low"
    _e = p.entry_from_issue(_issue("x.py", "_x", sev="ERROR"))
    assert _e["severity_raw"] == "ERROR"
    assert _e["route"] == "cleanup_queue"
    assert bool(_e.get("severity_invalid")) is True


def test_d1_persist_and_restore_roundtrip(tmp_path):
    """落盘 → 读回：条目数一致、状态可改；无快照 ⇒ empty（不是 unavailable）"""
    from nucleus import audit_problem_pool as p

    _d = str(tmp_path / "audit")
    os.makedirs(_d, exist_ok=True)
    _now = time.time()
    _r = p.persist_pool({"issues": [_issue("a.py", "_a"), _issue("b.py", "_b")]},
                        audit_dir_path=_d, now=_now)
    assert _r["state"] == "ok"
    _rs = p.restore_pool(audit_dir_path=_d, now=_now)
    assert _rs["state"] == "ok"
    assert int(_rs["total"]) == 2
    _key = p.make_dedup_key("a.py", "_a", "dead_code")
    assert p.set_status(_key, "claimed", audit_dir_path=_d, now=_now) is not False
    _after = {e["id"]: e for e in p.load_pool(audit_dir_path=_d, now=_now)["entries"]}
    assert _after[_key]["status"] == "claimed"
    _empty = p.restore_pool(audit_dir_path=str(tmp_path / "nope"), now=_now)
    assert _empty["state"] == "empty"


def test_d1_terminal_status_rejected_as_transition(tmp_path):
    """get_identity 型：终态（wontfix）不能再被改成 verified"""
    from nucleus import audit_problem_pool as p

    _d = str(tmp_path / "audit2")
    os.makedirs(_d, exist_ok=True)
    _now = time.time()
    p.persist_pool({"issues": [_issue("c.py", "_c")]}, audit_dir_path=_d, now=_now)
    _k = p.make_dedup_key("c.py", "_c", "dead_code")
    _r1 = p.set_status(_k, "wontfix", audit_dir_path=_d, now=_now)
    assert _r1.get("changed") is True, _r1
    # 终态不可再流转：`set_status` 返回 dict（不是布尔），非法流转只拒不改
    _r2 = p.set_status(_k, "verified", audit_dir_path=_d, now=_now)
    assert _r2.get("state") == "illegal_transition" and _r2.get("changed") is False, _r2
    _e = {x["id"]: x for x in p.load_pool(audit_dir_path=_d, now=_now)["entries"]}[_k]
    assert _e["status"] == "wontfix"


# ============================================================ d2 回流分流
def test_d2_split_routes_three_exits_and_accepts_type_alias():
    """三类分流：审查报告原生字段名是 `type`，也必须能分对（曾漏读导致全落一桶）"""
    from nucleus import audit_problem_pool as p

    _entries = [
        {"issue_type": "dead_code", "severity": "low"},
        {"type": "long_method", "severity": "medium"},
        {"issue_type": "pollution", "severity": "low"},
        {"type": "silent_except", "severity": "low", "severity_invalid": True},
    ]
    _parts = p.split_routes(_entries)
    assert set(_parts) >= {"patch_pool", "optimize_list", "cleanup_queue"}
    # 族表口径：dead_code / long_method 属 code_defect ⇒ 补丁池；
    # pollution 属 pollution 类 ⇒ 清洗队列；severity 非法 ⇒ 也强制清洗队列
    assert len(_parts["patch_pool"]) == 2, [e.get("route") for e in _entries]
    assert len(_parts["cleanup_queue"]) == 2, [e.get("route") for e in _entries]
    assert _entries[0]["route"] == "patch_pool"
    assert _entries[3]["route"] == "cleanup_queue"


def test_d2_export_writes_empty_files(tmp_path):
    """出口 0 条也要写空文件：缺文件与"读到 0 条"必须可区分（铁律166）"""
    from nucleus import audit_problem_pool as p

    _d = str(tmp_path / "audit3")
    os.makedirs(_d, exist_ok=True)
    # export_routes 收的是**条目列表**（内部再分桶），不是已分好的 dict
    _r = p.export_routes([], audit_dir_path=_d)
    assert set(_r) == {"patch_pool", "optimize_list", "cleanup_queue"}, _r
    for _n in ("patch_pool.jsonl", "optimize_list.jsonl", "cleanup_queue.jsonl"):
        _fp = os.path.join(_d, "routes", _n)
        assert os.path.isfile(_fp), _fp
        assert io.open(_fp, encoding="utf-8").read() == ""
        assert int(_r[_n.replace(".jsonl", "")]["count"]) == 0


def test_d2_hub_discovery_separates_new_from_refeed():
    """hub 条目级口径：真新增（first_seen）≠ 回流虚增（每轮重复投递）"""
    from nucleus import audit_problem_pool as p

    _t0 = time.time()

    class _FakeHub:
        """hub 侧容器：`get_m335_strategies()` 返回 `{"strategies": {key: rec}}`。"""

        def get_m335_strategies(self):
            return {"strategies": {
                "k1": {"issue_type": "dead_code", "first_seen": _t0 - 100,
                       "last_seen": _t0},
                "k2": {"issue_type": "long_method", "first_seen": _t0 - 50,
                       "last_seen": _t0},
                "k3": {"issue_type": "dead_code", "first_seen": _t0 - 9000,
                       "last_seen": _t0},
            }}

    _s = p.hub_discovery_stats(_t0 - 1000, hub=_FakeHub(), rounds=6,
                               reflow_per_round=15)
    assert _s["new_by_first_seen"] == 2           # 第 3 条 first_seen 在窗口外
    assert _s["reflow_equivalent"] == 90          # 6 轮 × 15 条
    assert _s["inflation_ratio"] > 1.0


# ============================================================ d3 处理类型标识
def test_d3_processing_type_four_values_and_explicit_wins():
    from nucleus import processing_type as pt

    _t, _src = pt.processing_type_of("arithmetic")
    assert _t == "self_rule" and _src == "method_table"
    _t2, _s2 = pt.processing_type_of("deep_think")
    assert _t2 == "llm_generated"
    _t3, _s3 = pt.processing_type_of("arithmetic", explicit="hybrid")
    assert _t3 == "hybrid" and _s3 == "explicit"
    _t4, _ = pt.processing_type_of("no_such_method_xyz")
    assert _t4 == "hybrid"                        # 兜底
    # 四值域只在函数返回值上成立（模块没有 PROCESSING_TYPES 常量）
    _seen = {pt.processing_type_of(m)[0] for m in
             ("arithmetic", "deep_think", "no_such_method_xyz")}
    assert _seen <= {"self_rule", "llm_generated", "hybrid", "sandbox_result"}


def test_d3_annotate_payload_no_crash_on_odd_input():
    from nucleus import processing_type as pt

    # 非 dict 一律返回 None（不猜、不包壳）
    assert pt.annotate_payload(None) is None
    assert pt.annotate_payload("x") is None
    _d = {"method": "arithmetic"}
    _out = pt.annotate_payload(_d)
    assert _out["processing_type"] == "self_rule"


# ============================================================ d4 时间注入
def test_d4_context_time_three_sources(monkeypatch):
    from nucleus.chronos import time_context as tc

    # 显式 now ⇒ source=injected（隔离验证固定时刻，不读真钟）
    _inj = tc.context_time(1_700_000_000.0)
    assert _inj["source"] == "injected"
    assert _inj["ts"] == 1_700_000_000.0
    assert _inj["text"]
    # TimeCore 不可用且未给 now ⇒ 墙钟兜底并如实标源（不假装拿到框架时间）
    monkeypatch.setattr(tc, "_from_time_core", lambda: None)
    _fb = tc.context_time()
    assert _fb["source"] == "wall_clock_fallback"
    assert _fb["text"]
    # TimeCore 可用 ⇒ source=time_core
    monkeypatch.setattr(tc, "_from_time_core",
                        lambda: {"ts": 1_700_000_100.0, "wall_clock": "2023-11-15 06:15:00"})
    _tc = tc.context_time()
    assert _tc["source"] == "time_core"
    assert _tc["ts"] == 1_700_000_100.0


def test_d4_annotate_prompt_prepends_and_keeps_original():
    from nucleus.chronos import time_context as tc

    _p, _info = tc.annotate_llm_prompt("原始问题", now=1_700_000_000.0)
    assert _p.endswith("原始问题")
    assert _p.startswith("当前时间：")
    assert _info["ts"] == 1_700_000_000.0
    _p2, _i2 = tc.annotate_llm_prompt("", now=1_700_000_000.0)
    assert _p2 == "" and _i2 == {}


# ============================================================ d5 愿景换源
def test_d5_reroute_after_threshold():
    from nucleus.cognitive import growth_memory as gm

    gm.reset()
    _r1 = gm.note_dedup_skip("同一方向", threshold=3)
    _r2 = gm.note_dedup_skip("同一方向", threshold=3)
    _r3 = gm.note_dedup_skip("同一方向", threshold=3)
    assert _r1["streak"] == 1 and _r1["reroute"] is False
    assert _r3["streak"] == 3 and _r3["reroute"] is True
    assert gm.should_reroute() is True
    _c = gm.consume_reroute()
    assert _c["was_set"] is True
    assert gm.should_reroute() is False
    gm.reset()


def test_d5_empty_growth_memory_is_empty_not_ok():
    from nucleus.cognitive import growth_memory as gm

    gm.reset()
    gm.set_growth_provider(lambda: {})
    _m = gm.read_growth_memory()
    assert _m["state"] in ("empty", "unavailable")
    assert _m["state"] != "ok"
    gm.reset()


# ============================================================ d6 派单机制
def test_d6_env_failure_vs_logic_failure():
    """只按证据字段判环境缺陷；SyntaxError 属补丁自身错，不得豁免"""
    from nucleus.evolution import patch_execution_dispatch as d

    _env = {"id": "a", "o3_dead_code": True,
            "runtime_verify_result": {"detail": "NameError: name 'Any' is not defined"}}
    assert d._m345_env_failure_reason(_env) == "env_typing_or_import"
    _syn = {"id": "b", "o3_dead_code": True,
            "runtime_verify_result": {"detail": "SyntaxError: invalid syntax"}}
    assert d._m345_env_failure_reason(_syn) == ""
    _ast = {"id": "c", "o3_dead_code": True,
            "runtime_verify_result": {"detail": "ast_delete:target_def_not_found"}}
    assert d._m345_env_failure_reason(_ast) == "ast_delete_verifier"


def test_d6_cap_exempt_three_conditions():
    from nucleus.evolution import patch_execution_dispatch as d

    _env = {"o3_dead_code": True,
            "runtime_verify_result": {"detail": "NameError: name 'Any' is not defined"}}
    assert d._m345_may_exempt_cap(dict(_env), 2, 2)[0] is True
    assert d._m345_may_exempt_cap(dict(_env, o3_cap_exempt_used=2), 2, 2)[0] is False
    _plain = dict(_env)
    _plain.pop("o3_dead_code")
    assert d._m345_may_exempt_cap(_plain, 2, 2)[0] is False
    _logic = {"o3_dead_code": True,
              "runtime_verify_result": {"detail": "AssertionError: x"}}
    assert d._m345_may_exempt_cap(_logic, 2, 2)[0] is False


def test_d6_a18_caps_are_real_variables():
    """A-18：安全闸是变量而不是格式串字面量 ⇒ 改它日志会变"""
    from nucleus.evolution import patch_execution_dispatch as d

    assert isinstance(d._DISPATCH_HARD_CAP, int)
    assert isinstance(d._DISPATCH_BLAST_CAP, int)
    assert d._DISPATCH_HARD_CAP == 1 and d._DISPATCH_BLAST_CAP == 1
    _src = io.open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "nucleus", "evolution", "patch_execution_dispatch.py"),
        encoding="utf-8", newline="").read()
    assert "_DISPATCH_HARD_CAP = 1" in _src.replace("\r\n", "\n")
    # 日志实参必须是变量：格式串后紧跟常量名（不是数字字面量）
    assert "hard_cap=%d blast_cap=%d" in _src


def test_d6_concurrent_verify_keeps_order_and_ledger(tmp_path):
    """并发验证：结果按传入序返回、台账按 patch_id+attempt 双键、串并结论一致"""
    from nucleus.evolution import patch_execution_dispatch as d

    class _SB:
        is_running = True

        def on_pulse(self, evt):
            _tid = str((evt.get("payload") or {}).get("task_id") or "")
            time.sleep(0.02 if _tid.endswith("slow") else 0.005)
            return {"success": True, "error": ""}

    _patches = [{"id": "p-slow", "modified_code": "print(1)", "o3_dispatch_attempts": 0},
                {"id": "p-b", "modified_code": "print(2)", "o3_dispatch_attempts": 0},
                {"id": "p-c", "modified_code": "print(3)", "o3_dispatch_attempts": 0}]
    _par = d.verify_patches_concurrent(_SB(), _patches, max_workers=2,
                                       project_root=str(tmp_path))
    assert [v["patch_id"] for v in _par] == ["p-slow", "p-b", "p-c"]
    assert all(v["success"] for v in _par)
    _led = os.path.join(str(tmp_path), "data", "patches", "patch_verify_attempts.jsonl")
    _rows = [json.loads(x) for x in io.open(_led, encoding="utf-8") if x.strip()]
    assert len(_rows) == 3
    assert {(r["patch_id"], r["attempt"]) for r in _rows} == {
        ("p-slow", 1), ("p-b", 1), ("p-c", 1)}
    # 空批 ⇒ 空结果
    assert d.verify_patches_concurrent(_SB(), [], project_root=str(tmp_path)) == []


# ============================================================ d7 重启连续性
def test_d7_restart_context_three_sources_and_terminal_filter(tmp_path):
    from nucleus.cognitive import restart_continuity as rc

    rc.reset()
    rc.set_issue_provider(lambda: {"state": "ok", "items": [
        {"issue_type": "dead_code", "file": "a.py", "method": "_a",
         "severity": "low", "status": "open", "last_seen_ts": 10},
        {"issue_type": "dead_code", "file": "b.py", "method": "_b",
         "severity": "low", "status": "verified", "last_seen_ts": 99},
    ]})
    rc.set_vision_provider(lambda: ["接通闭环"])
    rc.set_self_aware_provider(lambda: ["health_level=good"])
    _c = rc.collect_restart_context()
    assert _c["state"] == "ok"
    assert _c["counts"]["issues"] == 1          # verified 被滤掉
    assert _c["total"] == 3
    _blk = rc.format_continuity_block(_c, now_text="2026-10-22 22:00")
    assert _blk.startswith("【此前存在的事实 · 重启恢复】")
    assert "以这些记录为准" in _blk
    assert "你" not in _blk
    rc.reset()


def test_d7_no_fact_means_no_shell_prompt():
    """无事实 ⇒ prompt 原样返回（injected=False），不加空壳；unavailable ≠ empty"""
    from nucleus.cognitive import restart_continuity as rc

    rc.reset()
    rc.set_issue_provider(lambda: {"state": "ok", "items": []})
    rc.set_vision_provider(lambda: [])
    rc.set_self_aware_provider(lambda: [])
    _c = rc.collect_restart_context()
    assert _c["state"] == "empty"
    _p, _i = rc.annotate_prompt("原文", ctx=_c)
    assert _p == "原文" and _i["injected"] is False

    def _boom():
        raise RuntimeError("down")

    rc.set_issue_provider(_boom)
    rc.set_vision_provider(_boom)
    rc.set_self_aware_provider(_boom)
    _c2 = rc.collect_restart_context()
    assert _c2["state"] == "unavailable"
    assert _c2["state"] != "empty"
    rc.reset()


# ============================================================ 344 补1/补2/补3
def test_supp1_post_delete_orphan_blocks(tmp_path):
    """补1：删掉独占引用下游成员的 def ⇒ 拒入（删一个、造一个新的）"""
    from nucleus.evolution import patch_execution_dispatch as d

    _src = ("class A:\n"
            "    def helper(self):\n        return 1\n\n"
            "    def owned(self):\n        return self.helper()\n\n\n"
            "class B:\n"
            "    def sib(self):\n        return 2\n\n"
            "    def other(self):\n        return 3\n")
    _p = tmp_path / "sample.py"
    _p.write_text(_src, encoding="utf-8")
    _bad = d.verify_ast_delete({"id": "t1", "file": "sample.py", "method": "owned",
                                "line": 5}, project_root=str(tmp_path))
    assert _bad["success"] is False
    assert _bad["error"] == "ast_delete:post_delete_orphans"
    assert [o["member"] for o in _bad["new_orphans"]] == ["helper"]
    _ok = d.verify_ast_delete({"id": "t2", "file": "sample.py", "method": "other",
                               "line": 12}, project_root=str(tmp_path))
    assert _ok["success"] is True


def test_supp2_injection_surface_bounded():
    """补2：注入面只到注解层；缺 import 的真错不被兜住"""
    from nucleus.security import sandbox_limits as sl

    assert sl.SANDBOX_ANNOTATION_MODULES == ("typing", "collections.abc")
    for _m in ("sys", "os", "json", "subprocess"):
        assert _m not in sl.SANDBOX_ANNOTATION_SYMBOLS
    _wrap = sl.build_child_wrapper("_x = 1\n")
    assert "_ann_hit" in _wrap
    assert sl.ANN_MARKER in _wrap
    assert sl.parse_injected_symbols("noise\n%s:Any,List\n" % sl.ANN_MARKER) == \
        ["Any", "List"]
    assert sl.parse_injected_symbols("noise only") == []


def test_supp3_domain_guard_patch_pool_and_artifacts(tmp_path, monkeypatch):
    """补3：隔离产物不进补丁池；有隔离声明时产物改道"""
    from nucleus import domain_guard as dg

    assert dg.is_isolation_path("tmp/p342_root/d1_iso.py")[0] is True
    assert dg.is_isolation_path("nucleus/evolution/x.py")[0] is False
    _ok, _why = dg.guard_patch_entry({"id": "p", "file": "tmp/x.py"})
    assert _ok is False and _why.startswith("prefix:")
    _keep, _ = dg.guard_patch_entry({"id": "q", "file": "nucleus/y.py"})
    assert _keep is True
    _dir, _r = dg.guard_artifact_dir("data/reports/memory")
    assert (_dir, _r) == ("data/reports/memory", "")
    monkeypatch.setenv("PULSE_ISOLATION_ROOT", str(tmp_path))
    _dir2, _r2 = dg.guard_artifact_dir("data/reports/memory")
    assert _r2 == "isolation_declared"
    assert os.path.abspath(_dir2).startswith(str(tmp_path))


# ============================================================ 344 补4/补6
def test_supp4_voided_ledger_has_anchor(tmp_path):
    """补4：作废台账带 proc_gen + round_no；覆盖不到 ⇒ unavailable 不猜 0"""
    from nucleus.evolution import criteria_window as cw

    _led = str(tmp_path / "voided.jsonl")
    _now = time.time()
    cw.note_converge_voided("pid1", file="a.py", method="m", path=_led, now=_now)
    _row = json.loads(io.open(_led, encoding="utf-8").readline())
    assert str(_row["proc_gen"]) not in ("", "unknown")
    assert isinstance(_row["round_no"], int) and _row["round_no"] > 0
    _cnt = cw.count_converge_voided(_now - 60, _now + 60, ledger_path=_led,
                                    log_paths=[])
    assert _cnt["state"] == "ok" and _cnt["count"] == 1
    assert _cnt["tier"] == "ledger_anchored"
    _far = cw.count_converge_voided(_now - 7200, _now - 7100, ledger_path=_led,
                                    log_paths=[])
    assert _far["state"] == "unavailable" and _far["count"] is None


def test_supp6_snapshot_and_restore_drill(tmp_path):
    """补6：删除类补丁必须有整文件快照＋还原演练，且快照不落生产 data/"""
    import importlib.util

    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _spec = importlib.util.spec_from_file_location(
        "o3_truth_check_t", os.path.join(_root, "tools", "audit", "o3_truth_check.py"))
    _m = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_m)
    _tgt = tmp_path / "repo" / "nucleus"
    _tgt.mkdir(parents=True)
    (_tgt / "x.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _d = _m.deletion_snapshot_drill(str(tmp_path / "repo"), "nucleus/x.py",
                                    snapshot_root=str(tmp_path / "snaps"),
                                    work_dir=str(tmp_path / "drill"))
    assert _d["snapshot"]["state"] == "ok"
    assert _d["snapshot"]["in_production_data"] is False
    assert _d["restore_drill"]["sha_match"] is True
    assert _d["deletion_ready"] is True
