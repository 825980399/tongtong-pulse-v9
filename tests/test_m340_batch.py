"""第 340 批 · 刀2/3/4/5 回归单测（A 层：口径收口之外的代码面）。

覆盖：
  · 刀3 min_severity 解耦（判死类独立档 / 非判死维持 medium）+ A+C 触发链（N=3）
  · 刀4 费用熔断①：单价单源、cost_estimate 非空、三个引擎写 `_m44_last_model`
  · 刀5 thread_spawn 基线署名（新字段在位 + 历史字段保留）
  · 刀2 去线程：PulseController 预热站点不再新建线程（源码级断言）

铁律：不改生产数据；状态类断言用进程内注入，不写盘。
"""
import io
import json
import logging
import os
import time
import warnings

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ============ 刀3：min_severity 解耦（判死行动面的钥匙） ============

def test_10_dead_code_low_passes_independent_tier():
    """判死类 severity=low **且**行动面已开 ⇒ 走独立档放行。"""
    from nucleus.self_inspector import _m340_severity_pass
    _ok, _why = _m340_severity_pass(
        {"issue_type": "dead_code", "severity": "low"},
        min_severity="medium", action_open=True)
    assert _ok is True and _why == "independent_dead_code"


def test_11_dead_code_low_blocked_when_action_closed():
    """行动面未开 ⇒ 判死类也拒，且拒收原因标 `action_closed`（可审计）。"""
    from nucleus.self_inspector import _m340_severity_pass
    _ok, _why = _m340_severity_pass(
        {"issue_type": "dead_code", "severity": "low"},
        min_severity="medium", action_open=False)
    assert _ok is False and _why == "action_closed"


def test_12_non_dead_code_still_blocked_by_medium():
    """★只解判死类，不解全局：其余类型的 low 仍被 medium 档拦。"""
    from nucleus.self_inspector import _m340_severity_pass
    _ok, _why = _m340_severity_pass(
        {"type": "long_method", "severity": "low"},
        min_severity="medium", action_open=True)
    assert _ok is False and _why == "below_min_severity"
    _ok2, _why2 = _m340_severity_pass(
        {"type": "long_method", "severity": "high"},
        min_severity="medium", action_open=False)
    assert _ok2 is True and _why2 == "pass_min_severity"


def test_13_judge_action_open_needs_three_ticks():
    """A+C 触发：三条件齐 **且连续 ≥3 tick** 才开；同一 tick 幂等。"""
    from nucleus.self_inspector import (_M340_JUDGE_ACTION_STATE,
                                        _m340_judge_action_open)
    _M340_JUDGE_ACTION_STATE.update({"streak": 0, "last_tick": None, "last_open": False})
    _r1 = _m340_judge_action_open(mode="review", sample="PASS", misjudge=0.0, tick_id="t1")
    _r2 = _m340_judge_action_open(mode="review", sample="PASS", misjudge=0.0, tick_id="t2")
    assert _r1["judge_action_open"] is False and _r2["judge_action_open"] is False
    _r3 = _m340_judge_action_open(mode="review", sample="PASS", misjudge=0.0, tick_id="t3")
    assert _r3["judge_action_open"] is True and _r3["verdict"] == "OPEN"
    # 同一 tick 重复调用不虚增
    _r3b = _m340_judge_action_open(mode="review", sample="PASS", misjudge=0.0, tick_id="t3")
    assert _r3b["reason"]["streak"] == _r3["reason"]["streak"]


def test_14_judge_action_breaks_on_any_of_three():
    """三取一连环：sample FAIL / misjudge 超线 / mode 非 review ⇒ 断链归零。"""
    from nucleus.self_inspector import (_M340_JUDGE_ACTION_STATE,
                                        _m340_judge_action_open)
    _M340_JUDGE_ACTION_STATE.update({"streak": 2, "last_tick": "x", "last_open": False})
    _a = _m340_judge_action_open(mode="review", sample="FAIL", misjudge=0.0, tick_id="t9")
    assert _a["judge_action_open"] is False and _a["reason"]["streak"] == 0
    _b = _m340_judge_action_open(mode="review", sample="PASS", misjudge=0.25, tick_id="t10")
    assert _b["verdict"] == "CLOSED"
    _c = _m340_judge_action_open(mode="judge", sample="PASS", misjudge=0.0, tick_id="t11")
    assert _c["verdict"] == "CLOSED"
    _d = _m340_judge_action_open(mode="review", sample=None, misjudge=None, tick_id="t12")
    assert _d["verdict"] == "UNAVAILABLE"


def test_15_resolve_gate_thresholds_untouched():
    """★`_RESOLVE_GATE_*` 阈值一字不动（0.50 / 0.70 / 0.35）。"""
    from nucleus.self_inspector import SelfInspector
    assert SelfInspector._RESOLVE_GATE_JUDGE == 0.50
    assert SelfInspector._RESOLVE_GATE_HIGH == 0.70
    assert SelfInspector._RESOLVE_GATE_REVIEW == 0.35


# ============ 刀4：费用熔断① model 透传 ============

def test_20_price_lookup_is_single_source():
    """`_compute_cost_estimate` 委派 `_m340_price_of`（函数体不再自己读表）。"""
    import inspect

    from nucleus.llm import call_recorder as _cr
    _src = inspect.getsource(_cr._compute_cost_estimate)
    assert "_m340_price_of(model)" in _src
    assert "_tbl.get(" not in _src          # 不再自己读表
    assert _cr._m340_price_of("deepseek-flash") == 0.0011
    assert _cr._m340_price_of("") is None
    assert _cr._m340_price_of("no-such-model") is None


def test_21_cost_estimate_non_null_with_model():
    """★改前 `cost_estimate` 恒 null 的根因就是 model 未透传；给真 model 即非空。"""
    from nucleus.llm import call_recorder as _cr
    _c = _cr._compute_cost_estimate("deepseek-flash", 1500)
    assert _c is not None and _c > 0
    assert abs(_c - 0.0011 * 1500 / 1000.0) < 1e-9
    assert _cr._compute_cost_estimate("", 1500) is None      # 空 model ⇒ 不猜价


def test_22_engines_write_last_model():
    """三个进化引擎都必须写 `_m44_last_model`（否则装饰器恒取空串）。"""
    for _rel in ("nucleus/evolution/LLMEvolutionEngine.py",
                 "nucleus/evolution/SelfReflectionEngine.py",
                 "nucleus/reasoning/SafeEvolutionExecutor.py"):
        _t = io.open(os.path.join(ROOT, _rel), encoding="utf-8").read()
        assert "_m44_last_model =" in _t, _rel


def test_23_decorator_forwards_last_model(monkeypatch):
    """装饰器把 `_m44_last_model` 透传进 `record_call`（整条链的最后一环）。"""
    from nucleus.llm import call_recorder as _cr
    _seen = {}
    monkeypatch.setattr(_cr, "record_call", lambda **kw: _seen.update(kw) or "tid")

    class _Stub:
        @_cr.trace_evolution_call(prompt_pos=1, version="evolution.stub.v1")
        def _call_llm(self, prompt):
            self._m44_last_model = "deepseek-flash"
            return "ok"

    _Stub()._call_llm("hello")
    assert _seen.get("model") == "deepseek-flash"


# ============ 刀5：thread_spawn 基线署名 ============

def test_30_reanchor_fields_present_and_history_kept():
    """新字段 `reanchor_by/reanchor_at` 在位，历史 `anchored_*` 原样保留。"""
    _p = os.path.join(ROOT, "tools", "ci", "baselines", "thread_spawn_baseline.json")
    _d = json.load(io.open(_p, encoding="utf-8"))
    assert _d.get("anchored_by") == "172刀0" and _d.get("anchored_at") == "2026-10-08"
    assert _d.get("reanchor_by") and _d.get("reanchor_at")
    _h = _d.get("reanchor_history") or []
    assert _h and _h[-1]["from"]["total_occurrences"] == 64
    assert _h[-1]["to"]["total_occurrences"] == _d["total_occurrences"]


def test_31_baseline_numbers_match_current_scan():
    """基线数字与当前扫描一致（刀2 重锚后 62/45）。"""
    _p = os.path.join(ROOT, "tools", "ci", "baselines", "thread_spawn_baseline.json")
    _d = json.load(io.open(_p, encoding="utf-8"))
    assert _d["total_occurrences"] == 62 and _d["file_count"] == 45


# ============ 刀2：去线程（源码级断言 + 保活不受伤） ============

def test_40_prewarm_no_longer_spawns_thread():
    """PulseController 的预热站点不再 `threading.Thread(`；保活循环仍在。"""
    _t = io.open(os.path.join(ROOT, "organs", "motor", "PulseController.py"),
                 encoding="utf-8").read()
    assert "_ensure_headless_browser()" in _t
    assert _t.count("threading.Thread(") == 1      # 仅剩 _keepalive_loop
    assert "_keepalive_loop" in _t                  # 保活站点未被误删


def test_41_boot_checks_site_left_intact():
    """★PulseSystemManager:155 本批**未改**（改同步会重入 T-112c 的 L0 超时事故面）。"""
    _t = io.open(os.path.join(ROOT, "organs", "core", "PulseSystemManager.py"),
                 encoding="utf-8").read()
    assert 'name="PulseSystemBootChecks"' in _t
    assert "threading.Thread(" in _t


# ============ 刀7：费用熔断② 额度 50/200 + 日切/周切留痕 ============

def _eb():
    from nucleus.evolution import evolution_budget as _m
    return _m


def _seed(tmp_path, day_key, day_used, week_key, week_used):
    """写一份**注入路径**的侧账（铁律158：绝不落生产 data/）。"""
    _m = _eb()
    _p = str(tmp_path / "budget.json")
    _m.save_budget({"version": "335.1",
                    "day": {"key": day_key, "used": day_used, "calls": 3},
                    "week": {"key": week_key, "used": week_used, "calls": 3}}, _p)
    return _p


def test_50_budget_limits_are_50_and_200(tmp_path):
    """额度按 340 任务书 §刀7 落地：50 元/日、200 元/周，且视为「已配置」。"""
    _m = _eb()
    _lim = _m._m335_limits()
    assert _lim["day"] == 50.0, "日额度应为 50，实际 %r" % (_lim["day"],)
    assert _lim["week"] == 200.0, "周额度应为 200，实际 %r" % (_lim["week"],)
    _st = _m.budget_state(now=time.time(), path=str(tmp_path / "empty.json"))
    assert _st["configured"] is True
    assert _st["tripped"] is False and _st["state"] == "ok"


def test_51_day_roll_logs_and_resets_to_zero(tmp_path, caplog):
    """日切必须留痕，且切后**从 0 起算**（不是把上期 used 累加进来）。"""
    _m = _eb()
    _p = _seed(tmp_path, "20200101", 12.5, _m._m335_week_key(time.time()), 12.5)
    with caplog.at_level(logging.INFO):
        _r = _m.record_cost(0.5, ts=time.time(), path=_p)
    assert _r["ok"] is True
    assert abs(float(_r["day_used"]) - 0.5) < 1e-9, "切后应从 0 起算，实际 %r" % (_r["day_used"],)
    _hit = [x.getMessage() for x in caplog.records if "预算日切" in x.getMessage()]
    assert len(_hit) == 1, "日切行应恰好 1 条，实际 %d：%r" % (len(_hit), _hit)
    assert "上期=20200101" in _hit[0]
    assert "上期used=12.5000" in _hit[0]
    assert "limit=50.00" in _hit[0]
    assert "proc_gen=" in _hit[0]


def test_52_week_roll_logs_only_week(tmp_path, caplog):
    """日 key 未变、周 key 变了 ⇒ 只打周切，且 limit=200.00。"""
    _m = _eb()
    _p = _seed(tmp_path, _m._m335_day_key(time.time()), 1.0, "2020-W01", 33.0)
    with caplog.at_level(logging.INFO):
        _r = _m.record_cost(0.25, ts=time.time(), path=_p)
    _day_hit = [x.getMessage() for x in caplog.records if "预算日切" in x.getMessage()]
    _week_hit = [x.getMessage() for x in caplog.records if "预算周切" in x.getMessage()]
    assert _day_hit == [], "日 key 未变不该打日切：%r" % (_day_hit,)
    assert len(_week_hit) == 1, "周切行应恰好 1 条：%r" % (_week_hit,)
    assert "limit=200.00" in _week_hit[0]
    assert "上期used=33.0000" in _week_hit[0]
    assert abs(float(_r["day_used"]) - 1.25) < 1e-9, "同日应累加：%r" % (_r["day_used"],)
    assert abs(float(_r["week_used"]) - 0.25) < 1e-9, "跨周应归零再计：%r" % (_r["week_used"],)


def test_53_no_repeat_roll_log_within_same_key(tmp_path, caplog):
    """同一 key 内连记两笔 ⇒ 日切行只出现一次（防刷屏 / 防误读成反复重置）。"""
    _m = _eb()
    _p = _seed(tmp_path, "20200101", 5.0, _m._m335_week_key(time.time()), 5.0)
    _t = time.time()
    with caplog.at_level(logging.INFO):
        _m.record_cost(0.1, ts=_t, path=_p)
        _m.record_cost(0.2, ts=_t, path=_p)
    _hit = [x.getMessage() for x in caplog.records if "预算日切" in x.getMessage()]
    assert len(_hit) == 1, "日切行应只 1 条，实际 %d" % len(_hit)


def test_54_trip_only_at_or_above_limit(tmp_path):
    """边界：49.99 不熔断 / 50.0 熔断 / 200.0 周熔断；is_tripped 是纯读。"""
    _m = _eb()
    _now = time.time()
    _dk = _m._m335_day_key(_now)
    _wk = _m._m335_week_key(_now)

    _p = _seed(tmp_path, _dk, 49.99, _wk, 49.99)
    _st = _m.budget_state(now=_now, path=_p)
    assert _st["tripped"] is False and _st["state"] == "ok", "49.99 不该熔断：%r" % (_st,)

    _p2 = _seed(tmp_path, _dk, 50.0, _wk, 50.0)
    _st2 = _m.budget_state(now=_now, path=_p2)
    assert _st2["tripped"] is True and _st2["state"] == "tripped"
    assert "日预算超限" in _st2["reason"]
    assert _m.is_tripped(now=_now, path=_p2) is True

    _p3 = _seed(tmp_path, _dk, 0.0, _wk, 200.0)
    _st3 = _m.budget_state(now=_now, path=_p3)
    assert _st3["tripped"] is True and "周预算超限" in _st3["reason"]


def test_55_budget_consumers_are_only_dispatch_and_recorder():
    """只读巡检 / 心跳 / W1 结构上不受影响：业务码里只有 2 个 import 消费点。

    ★为什么用 AST 判 import 面而不是 grep 子串（铁律154）：`config.py` 里
    `# 侧账：data/evolution/evolution_budget.json` 只是注释，子串扫会误判成消费点。
    扫描面 = 业务目录 + 顶层入口脚本（排除 tests/tmp/data/tools/docs）。
    """
    import ast as _ast

    def _imports_budget(src):
        try:
            _tree = _ast.parse(src)
        except SyntaxError as _e:
            warnings.warn("消费点审计跳过无法解析的文件：%s" % (_e,))
            return False
        for _n in _ast.walk(_tree):
            if isinstance(_n, _ast.Import):
                for _a in _n.names:
                    if "evolution_budget" in _a.name:
                        return True
            elif isinstance(_n, _ast.ImportFrom):
                if _n.module and "evolution_budget" in _n.module:
                    return True
            elif isinstance(_n, _ast.Call):
                _fn = _n.func
                _nm = _fn.attr if isinstance(_fn, _ast.Attribute) else (
                    _fn.id if isinstance(_fn, _ast.Name) else "")
                if _nm in ("import_module", "__import__"):
                    for _a in _n.args:
                        if isinstance(_a, _ast.Constant) and \
                                "evolution_budget" in str(_a.value):
                            return True
        return False

    _allow = {"nucleus/evolution/patch_execution_dispatch.py",
              "nucleus/llm/call_recorder.py"}
    _scan_dirs = ["base", "functions", "hardware", "nucleus", "organs",
                  "pulses", "somatics", "utils"]
    _found = set()
    for _sd in _scan_dirs:
        for _d, _dn, _fn in os.walk(os.path.join(ROOT, _sd)):
            _dn[:] = [_x for _x in _dn if _x != "__pycache__"]
            for _f in _fn:
                if not _f.endswith(".py"):
                    continue
                _full = os.path.join(_d, _f)
                _rel = os.path.relpath(_full, ROOT).replace("\\", "/")
                try:
                    _s = io.open(_full, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                if _imports_budget(_s):
                    _found.add(_rel)
    for _f in ("main.py", "config.py", "pulse_doctor.py"):
        _full = os.path.join(ROOT, _f)
        if os.path.isfile(_full):
            _s = io.open(_full, encoding="utf-8", errors="replace").read()
            if _imports_budget(_s):
                _found.add(_f)
    assert _found == _allow, "import 面变了：多出=%r 缺少=%r" % (
        sorted(_found - _allow), sorted(_allow - _found))
    # 反向兜底：只读巡检 / 心跳 / W1 三个面**必须**不在消费集合里
    for _p in ("main.py", "pulse_doctor.py"):
        assert _p not in _found, "%s 不该直接消费预算账（只读面）" % _p


# ============ 刀8：K2 live 对表（真实任务集 / 真实取证） ============

def _k2():
    import importlib.util
    _p = os.path.join(ROOT, "tools", "k2_bench_339.py")
    _spec = importlib.util.spec_from_file_location("k2_bench_339_under_test", _p)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod


def test_60_live_items_come_from_real_sample_and_are_deterministic():
    """任务集来自生产留样，且**同参数取同一批**（换批=换考题）。"""
    _k = _k2()
    _r = _k.build_live_items(5, ROOT)
    assert _r["ok"] is True, "构建失败：%r" % (_r.get("reason"),)
    assert _r["taken"] == 5 and len(_r["items"]) == 5
    assert all(_x["file"] and _x["method"] for _x in _r["items"])
    assert os.path.isfile(_r["source"]), "来源留样应为真实文件：%r" % (_r["source"],)
    _r2 = _k.build_live_items(5, ROOT)
    assert [x["id"] for x in _r["items"]] == [x["id"] for x in _r2["items"]]


def test_61_verdict_fp_discriminates():
    """★铁律168：指纹判据必须**能失败** —— 改一条裁决就变，不改就不变。"""
    _k = _k2()
    _base = [{"id": "a", "reviewed": 1, "report": True, "applied": 0,
              "digest": "aaa", "candidate": False},
             {"id": "b", "reviewed": 1, "report": True, "applied": 0,
              "digest": "bbb", "candidate": True}]
    _a1 = _k._live_agg([dict(x) for x in _base], 0.0)
    _a2 = _k._live_agg([dict(x) for x in _base], 0.0)
    assert _a1["verdict_fp"] == _a2["verdict_fp"], "同一批裁决指纹应相同"
    _changed = [dict(x) for x in _base]
    _changed[1]["digest"] = "bbc"
    _b = _k._live_agg(_changed, 0.0)
    assert _b["verdict_fp"] != _a1["verdict_fp"], "改一条裁决指纹必须变（否则判据空转）"
    assert _a1["reviewed"] == _b["reviewed"] == 2
    assert _a1["applied"] == _b["applied"] == 0


def test_62_live_serial_and_parallel_report_same_output():
    """真跑一小批：串行/并发三列产出相等 + 逐条裁决指纹相等（读盘+AST+probe 全真）。"""
    _k = _k2()
    _src = _k.build_live_items(4, ROOT)
    if not _src.get("ok"):
        pytest.skip("无 per-item 留样：%r" % (_src.get("reason"),))
    _k.warmup_live(ROOT)  # 计时单列，不计入任何一轮
    _s = _k.run_serial_live(_src["items"], ROOT)
    _p = _k.run_parallel_live(_src["items"], workers=3, project_root=ROOT)
    for _c in ("reviewed", "report", "applied", "candidates"):
        assert _s[_c] == _p[_c], "产出列 %s 不等：串行=%r 并发=%r" % (_c, _s[_c], _p[_c])
    assert _s["verdict_fp"] == _p["verdict_fp"], "逐条裁决指纹不等"
    assert _s["n"] == _p["n"] == 4
    assert _s["wall_sec"] > 0 and _p["wall_sec"] > 0


def test_63_live_log_line_and_assert_blocks_present():
    """契约：`[340K2]` 留痕行与 live 专用断言块在位（可 grep 验收）。"""
    _src = io.open(os.path.join(ROOT, "tools", "k2_bench_339.py"),
                   encoding="utf-8", errors="replace").read()
    assert "[340K2] live 对表" in _src
    assert "verdict_fp_equal" in _src
    assert "A4-3_live_source_ok" in _src
    assert "A4-4_probe_index_warm" in _src
    assert "item_count_field" in _src, "任务集元信息（池/取数）须落盘可核"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
