# -*- coding: utf-8 -*-
"""★378 批门控单测（逐刀追加）。

被测面全部为**真实实现**，不复制逻辑；「改前行为」用内联固化算法对照
（铁律58.3：先红证据要能永久复现，不依赖 .bak_batchN）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

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
