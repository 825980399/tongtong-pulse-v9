# -*- coding: utf-8 -*-
"""★378 批门控单测（逐刀追加）。

被测面全部为**真实实现**，不复制逻辑；「改前行为」用内联固化算法对照
（铁律58.3：先红证据要能永久复现，不依赖 .bak_batchN）。
"""
import io
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

from nucleus.evolution import patch_execution_dispatch as ped  # noqa: E402
from nucleus.evolution import zero_break_judge as zb  # noqa: E402

_WID = "o3_selftest_378"


# ===================================================================== 刀1
def _legacy_watch(patch_id, *, pending_rows=None, history_rows=None, results=None):
    """★378刀1 的**改前**算法（内联固化）。

    改前：`_rec["applied"] = _res.get("success") is True` —— 本拍沙箱成功即当"已落地"。
    用途：证明「改前确实少报一个缺口」，而不是嘴上说。
    """
    _pend = None
    for _r in pending_rows or []:
        if isinstance(_r, dict) and str(_r.get("id") or "") == patch_id:
            _pend = _r
    _hist = None
    for _r in history_rows or []:
        if isinstance(_r, dict) and str(_r.get("id") or "") == patch_id:
            _hist = _r
    _res = None
    for _r in results or []:
        if isinstance(_r, dict) and str(_r.get("patch_id") or "") == patch_id:
            _res = _r
    _rec = {}
    if isinstance(_pend, dict):
        _rec.update(_pend)
    if isinstance(_hist, dict):
        for _k in ("status", "verified", "applied", "executed"):
            if _k in _hist and _k not in _rec:
                _rec[_k] = _hist[_k]
    if isinstance(_res, dict):
        _rec["executed"] = 1
        _rec["applied"] = _res.get("success") is True      # ← 改前那一行
    return zb.judge_zero_break(_rec, project_root=ROOT)


def test_m378_d1_sandbox_success_is_not_applied():
    """★核心：沙箱执行成功 ≠ 已落地 ⇒ `applied_true` 必须仍为假。"""
    _w = zb.watch_candidate(_WID,
                            pending_rows=[{"id": _WID, "status": "pending",
                                           "file": "nucleus/audit_problem_pool.py"}],
                            history_rows=[],
                            results=[{"patch_id": _WID, "success": True}],
                            project_root=ROOT)
    assert _w["checks"]["applied_true"]["ok"] is False
    assert _w["checks"]["applied_true"]["got"] is None
    assert _w["checks"]["executed_ge_1"]["ok"] is True
    # 本拍结果仍可见，只是不算「落地」
    assert _w["sandbox_ok"] is True


def test_m378_d1_missing_lists_two_gaps_not_one():
    """★反向：改前只报 1 个缺口，改后必须报 2 个（applied_true + ledger_verified）。"""
    _args = dict(pending_rows=[{"id": _WID, "file": "nucleus/x.py"}],
                 history_rows=[],
                 results=[{"patch_id": _WID, "success": True}])
    _now = zb.watch_candidate(_WID, project_root=ROOT, **_args)
    _old = _legacy_watch(_WID, **_args)
    assert _old["half_through"] == ["ledger_verified"], "改前口径应与历史一致"
    assert set(_now["missing"]) == {"applied_true", "ledger_verified"}
    assert len(_now["missing"]) == len(_old["half_through"]) + 1
    # 两版 verdict 都是 half_through（executed 成立），差别在"缺口数"而非"结论"
    assert _old["verdict"] == _now["verdict"] == "half_through"


def test_m378_d1_ledger_applied_still_counts():
    """正控：台账里真写了 applied=True，判据仍要认（不能把功能改死）。"""
    _hist = [{"id": _WID, "status": "applied_verified", "verified": True,
              "executed": 1, "applied": True, "file": "nucleus/x.py"}]
    _w = zb.watch_candidate(_WID, pending_rows=[{"id": _WID}],
                            history_rows=_hist,
                            results=[{"patch_id": _WID, "success": True}],
                            project_root=ROOT)
    assert _w["checks"]["applied_true"]["ok"] is True
    assert _w["zero_broken"] is True


def test_m378_d1_sandbox_failure_still_not_applied():
    """负控：本拍失败 ⇒ 更不能算落地（改前改后一致，防改出新洞）。"""
    _w = zb.watch_candidate(_WID, pending_rows=[{"id": _WID}],
                            history_rows=[],
                            results=[{"patch_id": _WID, "success": False}],
                            project_root=ROOT)
    assert _w["checks"]["applied_true"]["ok"] is False
    assert _w["sandbox_ok"] is False
    assert _w["verdict"] == "half_through"


# ===================================================================== 刀2
class _FakeSandbox:
    is_running = True

    @staticmethod
    def on_pulse(*_a, **_kw):
        return {"status": "ok"}


def _o3(**over):
    """满足 `_m339_o3_dispatch_ready` 全部门槛的最小 O3 条目。"""
    _p = {"id": "t_o3_378", "file": "a.py", "method": "_dead_method",
          "o3_dead_code": True, "o3_auto_dispatch_ready": True,
          "requires_confirmation": False, "status": "approved"}
    _p.update(over)
    return _p


def _dispatch(tmp_path, monkeypatch, patches, verdict=None):
    """跑一次派单；返回 (结果, 沙箱被调用次数)。"""
    _pend = tmp_path / "pending_patches.json"
    _pend.write_text(json.dumps(patches, ensure_ascii=False), encoding="utf-8")
    _calls = []

    def _fake(_sb, _batch, **_kw):
        _calls.append(len(_batch))
        return [dict(verdict or {"patch_id": "t_o3_378", "success": True})
                for _ in _batch]

    monkeypatch.setattr(ped, "verify_patches_concurrent", _fake)
    _r = ped.dispatch_o3_approved(limit=1, pending_path=str(_pend),
                                 sandbox=_FakeSandbox(),
                                 project_root=str(tmp_path))
    return _r, sum(_calls)


def test_m378_d2_frozen_within_ttl_no_sandbox_call(tmp_path, monkeypatch):
    """★核心：沙箱刚成功过 ⇒ TTL 内**一次沙箱都不跑**。"""
    monkeypatch.setattr(config, "ENABLE_M378_SANDBOX_TTL_FREEZE", True,
                        raising=False)
    _r, _n = _dispatch(tmp_path, monkeypatch,
                       [_o3(sandbox_ok=True, sandbox_ok_at=time.time() - 60)])
    assert _r["status"] == "no_candidate"
    assert _r["rejected"]["sandbox_ttl"] == 1
    assert _n == 0, "冻结期间不得调用沙箱"


def test_m378_d2_expired_ttl_redispatches(tmp_path, monkeypatch):
    """反控：超 TTL 必须照旧重派（否则补丁永远卡在沙箱态）。"""
    monkeypatch.setattr(config, "ENABLE_M378_SANDBOX_TTL_FREEZE", True,
                        raising=False)
    _r, _n = _dispatch(tmp_path, monkeypatch,
                       [_o3(sandbox_ok=True,
                            sandbox_ok_at=time.time() - (ped._M378_SANDBOX_TTL_SEC + 60))])
    assert _r["status"] == "ok" and _r["dispatched"] == 1
    assert _n == 1
    assert _r["rejected"]["sandbox_ttl"] == 0


def test_m378_d2_switch_off_restores_old_behaviour(tmp_path, monkeypatch):
    """灰度：开关关闭 ⇒ 逐字回到"每拍重派"（零回归路径）。"""
    monkeypatch.setattr(config, "ENABLE_M378_SANDBOX_TTL_FREEZE", False,
                        raising=False)
    _r, _n = _dispatch(tmp_path, monkeypatch,
                       [_o3(sandbox_ok=True, sandbox_ok_at=time.time() - 60)])
    assert _r["dispatched"] == 1 and _n == 1
    assert _r["rejected"]["sandbox_ttl"] == 0


def test_m378_d2_failure_is_not_frozen(tmp_path, monkeypatch):
    """负控：沙箱失败过 ⇒ 不冻结（失败要立刻可重试，别被 TTL 锁死）。"""
    monkeypatch.setattr(config, "ENABLE_M378_SANDBOX_TTL_FREEZE", True,
                        raising=False)
    _r, _n = _dispatch(tmp_path, monkeypatch,
                       [_o3(sandbox_ok=False, sandbox_ok_at=time.time() - 60)])
    assert _r["dispatched"] == 1 and _n == 1


def test_m378_d2_age_helper_never_raises():
    """时点缺失/不可读 ⇒ None（= 不冻结），绝不抛。"""
    assert ped._m378_sandbox_age_sec({}) is None
    assert ped._m378_sandbox_age_sec({"sandbox_ok": True}) is None
    assert ped._m378_sandbox_age_sec({"sandbox_ok": True, "sandbox_ok_at": "abc"}) is None
    assert ped._m378_sandbox_age_sec({"sandbox_ok": False, "sandbox_ok_at": 1.0}) is None
    assert ped._m378_sandbox_age_sec(None) is None


# ===================================================================== 刀3
def _src(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as _f:
        return _f.read()


def test_m378_d3_no_id_truncation_left():
    """★核心：同类截断一处不留（任务书点 1 处，实测同型 4 处）。"""
    _s = _src("nucleus/reasoning/SafeEvolutionExecutor.py")
    assert "patch.get('id', '')[:16]" not in _s
    assert _s.count("patch.get('id', '')}") == 4


def test_m378_d3_legacy_truncation_loses_identity():
    """反向：内联固化旧格式，证明同秒批量补丁的前 16 字符**必然相撞**。

    这就是"改全 id"的理由 —— 不是美观问题，是事后**查不到是哪一个**。
    """
    def _legacy(pid):
        return pid[:16] + "..."

    _a = "patch_multi_1788000000_017"
    _b = "patch_multi_1788000000_018"
    assert _a != _b
    assert _legacy(_a) == _legacy(_b) == "patch_multi_1788...", "旧格式两者不可区分"
    # 全 id 可区分
    assert _a != _b and len(_a) > 16


# ===================================================================== 刀4
def test_m378_d4_acceptance_line_is_decoupled_from_g4():
    """★核心：验收线读独立键（2000），分档线仍读 G4（7000）⇒ 两者**不同源**。"""
    from nucleus.mnemosyne import release_policy as rp
    assert config.MEMORY_PEAK_ACCEPTANCE_MB == 2000.0
    assert rp.acceptance_line_mb() == 2000.0
    assert rp.thresholds()["g4_heap_mb"] == 7000.0
    assert rp.acceptance_line_mb() != rp.thresholds()["g4_heap_mb"], \
        "验收线与分档线若相等，说明又并回一个键了"


def test_m378_d4_guard_default_restores_d3_line():
    """默认线回到 2.0GB：1999 过 / 2001 不过（边界两侧各一例，铁律275）。"""
    from nucleus.mnemosyne import release_policy as rp
    assert rp.heap_peak_guard(1999.0)["verdict"] == "pass"
    assert rp.heap_peak_guard(2001.0)["verdict"] == "fail"
    assert rp.heap_peak_guard(2001.0)["line_mb"] == 2000.0


def test_m378_d4_judge_default_restores_d3_line():
    from nucleus.mnemosyne import release_policy as rp
    _d = rp.judge_pass_line(12000.0, 1999.0)
    assert _d["heap_line_mb"] == 2000.0 and _d["verdict"] == "pass"
    assert rp.judge_pass_line(12000.0, 2001.0)["verdict"] == "fail"


def test_m378_d4_explicit_override_still_works():
    """显式传线仍优先（按季度口径临时放宽的能力不能被这次改动拿走）。"""
    from nucleus.mnemosyne import release_policy as rp
    assert rp.heap_peak_guard(6000.0, line_mb=7000.0)["verdict"] == "pass"
    assert rp.judge_pass_line(12000.0, 6000.0, heap_line_mb=7000.0)["verdict"] == "pass"


def test_m378_d4_legacy_coupling_would_pass_2500():
    """反向：内联固化**改前**口径（默认线吃 G4=7000）⇒ 2500MB 会被判 pass。

    这就是本刀要堵的洞：不显式传 2000 时，验收尺子被分档提额顺手放宽。
    """
    _legacy_line = 7000.0
    assert (2500.0 <= _legacy_line) is True, "改前口径下 2500MB 判 pass（不该）"
    from nucleus.mnemosyne import release_policy as rp
    assert rp.heap_peak_guard(2500.0)["verdict"] == "fail", "改后应判 fail"
