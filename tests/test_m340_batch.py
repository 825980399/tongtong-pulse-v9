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
import os

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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
