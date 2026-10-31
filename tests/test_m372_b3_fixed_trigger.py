# -*- coding: utf-8 -*-
"""372 刀3 定向单测：池 FIXED 触发源改由**落地成功**事件触发。

票 `T-371-池FIXED沙箱触发-1`（登记册 428）｜裁决 **A3**。

缺口（T0 实测）：
  `dispatch_runtime_verified_patches` 里 `if _v.get("success"): _m356_close_loop_fixed(_p)`，
  而 `_v["success"]` 来自**沙箱** motor；同一文件 :2657 的语义边界注释原文是
  「**沙箱执行成功 ≠ 源码落地**」，且沙箱"绝不 apply 到源码"
  ⇒ 审计池被标成 FIXED（已修）**早于**源码真被改。真写盘在
  `PatchManager.apply_all_pending` 的 `os.replace` 之后。

本刀：FIXED 只挂 `landed is True`；沙箱成功/失败另立留痕字段（不合并语义）。
"""
from __future__ import annotations

import os
import tempfile

import pytest

from nucleus import audit_problem_pool as _pool
from nucleus.evolution import patch_execution_dispatch as _disp
from nucleus.reasoning.PatchManager import PatchManager

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DISP_SRC = open("nucleus/evolution/patch_execution_dispatch.py", "rb").read().decode(
    "utf-8", "replace")


# ============================================================ ① 沙箱成功但未落地
def test_01_sandbox_success_without_landed_is_not_fixed():
    """沙箱成功（哪怕 applied=status 都像"成了"）不得算落地。"""
    for _p in (
        {"sandbox_ok": True},
        {"sandbox_ok": True, "applied": True},                     # 误标形态
        {"sandbox_ok": True, "status": "runtime_verified"},
        {"sandbox_ok": True, "applied": True, "status": "applied"},
        {"applied": True},                                          # 没有 landed 一律不算
        {"landed": False},
        {"landed": 1},                                              # ★只认 True，不认真值
        {"landed": "true"},                                         # 字符串不认（铁律153）
    ):
        assert _disp._m372_landed_ok(_p) is False, _p
    assert _disp._m372_landed_ok({"landed": True}) is True
    assert _disp._m372_landed_ok(None) is False

    # 触发点连线断言：set_status 封装只在 landed 判定之内被调用（不再无条件触发）
    _i = _DISP_SRC.index("            _p[\"sandbox_ok\"] = True")
    _seg = _DISP_SRC[_i:_i + 900]
    assert "if _m372_landed_ok(_p):" in _seg
    assert _seg.index("if _m372_landed_ok(_p):") < _seg.index("_m356_close_loop_fixed(_p)")


# ============================================================ ② 落地成功才置 FIXED
def test_02_landed_write_triggers_close_loop(monkeypatch):
    """真写盘成功点必须置 landed 并触发闭环回写（否则 FIXED 永不发生）。"""
    _calls = []
    monkeypatch.setattr(_disp, "_m356_close_loop_fixed",
                        lambda p: _calls.append(dict(p)) or {"state": "fixed"})

    import config
    monkeypatch.setitem(config.EVOLUTION_CONFIG, "auto_apply_enabled", True)
    # ★406刀4：关掉「diff 窄闸」，隔离出本用例要验的"落地成功才置 FIXED"。
    #   本用例补丁是 `return 'ORIG' → 'LANDED'` 的真实逻辑改动，窄闸在位时
    #   连 approved 都拿不到，落地链路根本走不到，就测不到 372刀3 的触发点。
    monkeypatch.setattr(config, "PATCH_M406_NARROW_GATE", False, raising=False)

    _tmp = tempfile.mkdtemp(prefix="m372b3_")
    os.makedirs(os.path.join(PROJECT_ROOT, "tmp"), exist_ok=True)
    _target = os.path.join(PROJECT_ROOT, "tmp", "m372b3_target_%s.py"
                           % next(tempfile._get_candidate_names()))
    _orig = "def probe_func():\n    return 'ORIG'\n"
    with open(_target, "w", encoding="utf-8") as f:
        f.write(_orig)

    _pm = PatchManager(project_root=PROJECT_ROOT)
    _pm._pending_file = os.path.join(_tmp, "pending.json")
    _pm._history_file = os.path.join(_tmp, "history.json")
    _pm._backup_manager = type("B", (), {
        "create_backup": lambda self, files=None, reason=None, source=None:
            os.path.join(tempfile.gettempdir(), "m372b3_bak")})()
    _pm._save_restart_counter = lambda c: True
    _pm._load_restart_counter = lambda: 0
    _pm._max_restart_count = 10 ** 9

    _p = {
        "id": "m372b3_land", "file": _target, "method": "probe_func",
        "original_code": _orig,
        "modified_code": "def probe_func():\n    return 'LANDED'\n",
        "risk_level": "低", "issue_type": "periodic_task_no_reentry",
        "source": "local_rule", "status": "verified", "trust_score": 90,
        "confidence": "high",
        "aesthetic_score": {"total": 90, "grade": "A", "syntax_valid": True},
        "runtime_verify_result": {"verified": True, "baseline": 3},
        "baseline_errors": 3,
    }
    _pm.save_pending_patch(_p)          # 由 B 通道升 approved（总开关已开）
    assert _p.get("status") == "approved", _p.get("status")
    assert not _calls, "★落地前不得触发闭环（沙箱/审批阶段不是落地）"

    _res = _pm.apply_all_pending(only_approved=True)
    assert _res.get("applied", 0) >= 1, _res
    # 本刀只对"落地才回写"负责，不锚回写次数（pending 可能有多份副本，属既有行为）：
    # 每次回写都必须带 landed=True 且 landed_at 与 applied_at 同源。
    assert _calls, "★真写盘成功必须触发闭环回写"
    for _c in _calls:
        assert _c.get("landed") is True, _c
        assert _c.get("landed_at") == _c.get("applied_at"), _c
    assert _calls[0].get("landed") is True
    assert _calls[0].get("landed_at") == _calls[0].get("applied_at"), (
        "landed_at 与 applied_at 必须同源，不造第二时间源")
    with open(_target, encoding="utf-8") as f:
        assert "LANDED" in f.read()


# ============================================================ ③ 356 闭环链路仍合法
def test_03_pool_state_chain_still_legal():
    """不改状态机：审计池 open→claimed→fixed 仍合法（本刀不许放宽/收紧流转表）。"""
    _T = _pool._LEGAL_TRANSITIONS
    assert _pool.STATUS_FIXED in _T[_pool.STATUS_CLAIMED], "claimed→fixed 必须合法"
    assert _pool.STATUS_VERIFIED in _T[_pool.STATUS_FIXED], "fixed→verified 必须合法"
    assert _pool.STATUS_OPEN in _T[_pool.STATUS_VERIFIED]
    # 流转表逐项等于基线（本刀只改触发源，不动状态机）
    assert _T == {
        _pool.STATUS_OPEN: (_pool.STATUS_CLAIMED, _pool.STATUS_FIXED,
                            _pool.STATUS_VERIFIED, _pool.STATUS_WONTFIX,
                            _pool.STATUS_TARGET_DRIFTED),
        _pool.STATUS_CLAIMED: (_pool.STATUS_FIXED, _pool.STATUS_OPEN,
                               _pool.STATUS_WONTFIX, _pool.STATUS_TARGET_DRIFTED),
        _pool.STATUS_FIXED: (_pool.STATUS_VERIFIED, _pool.STATUS_CLAIMED,
                             _pool.STATUS_WONTFIX, _pool.STATUS_TARGET_DRIFTED),
        _pool.STATUS_VERIFIED: (_pool.STATUS_OPEN, _pool.STATUS_WONTFIX),
        _pool.STATUS_WONTFIX: (_pool.STATUS_OPEN,),
        _pool.STATUS_TARGET_DRIFTED: (_pool.STATUS_OPEN, _pool.STATUS_WONTFIX),
    }
    # 唯一 set_status 调用点仍在既有封装内（不新增第二个真实调用点）。
    # ★注意：不能数裸 "set_status(" —— 注释/docstring 里也有这个词；只数带模块前缀的调用。
    assert _DISP_SRC.count("_app.set_status(") == 1, (
        "★闭环回写只许有一个 set_status 真实调用点，实得 %d"
        % _DISP_SRC.count("_app.set_status("))


# ============================================================ ④ 沙箱留痕不丢
def test_04_sandbox_traces_preserved():
    """沙箱成功/失败独立留痕，不被 landed 语义吞掉。"""
    assert '_p["sandbox_ok"] = True' in _DISP_SRC
    assert '_p["sandbox_ok_at"] = _v.get("executed_at") or time.time()' in _DISP_SRC
    assert "_M372_SANDBOX_ONLY[" in _DISP_SRC
    # 计数是观测面：不得参与判定（landed 判定函数里不许出现 sandbox 字样）
    import inspect
    _src = inspect.getsource(_disp._m372_landed_ok)
    _body = "\n".join(_l for _l in _src.splitlines()
                      if not _l.strip().startswith("#") and '"""' not in _l
                      and not _l.strip().startswith("只认")
                      and not _l.strip().startswith("为什么")
                      and not _l.strip().startswith("其语义")
                      and not _l.strip().startswith("⇒")
                      and not _l.strip().startswith("（铁律"))
    assert "sandbox" not in _body.lower(), _body
    assert "_M372_SANDBOX_ONLY" not in _body


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
