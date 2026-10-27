# -*- coding: utf-8 -*-
"""第374批 刀2 · O3 通道闭环缺口（P1 · 与 373 刀5 同通道的下一环）

票：`T-373-O3通道闭环缺口-1`

问题：373 刀5 把 O3 通道的 `applied` / `applied_at` 归位到「真落地」事件上，
      但**只置了标记，没回写审计池**：
        `dispatch_o3_approved` 的落地分支缺 `_m356_close_loop_fixed(_p)`。
      ⇒ 主通道（`:2857`）落地后池条目 `set_status(FIXED)`，O3 通道落地后
        条目永远停在 `claimed/open`。破零要的「首条 FIXED 落在 O3 通道上」
        因此永远不发生。

修法：O3 落地分支补同一函数调用（复用同一函数、同一状态机、同一唯一写口），
      不给 O3 单开一条写路径。

边界：
  * `_LEGAL_TRANSITIONS` 不放宽（test_05 快照逐边对比）；
  * 356 闭环语义不破坏：只有「真落地」才 FIXED（test_02/03 守 sandbox_only /
    verify_failed 两条否定路径）；
  * 观测不阻断主流程：无 dedup_key 只记数 + warning，派单照旧成功（test_04）。
"""
import json
import re
from pathlib import Path

import pytest

import nucleus.audit_problem_pool as app
import nucleus.evolution.patch_execution_dispatch as mod

_SRC = Path("nucleus/evolution/patch_execution_dispatch.py")


class _FakeSandbox:
    is_running = True

    @staticmethod
    def on_pulse(*_a, **_kw):
        return {"status": "ok"}


def _patch(**over):
    """满足 `_m339_o3_dispatch_ready` 全部门槛的最小 O3 条目。"""
    p = {
        "id": "t_o3_close_1",
        "file": "a.py",
        "method": "_dead_method",
        "o3_dead_code": True,
        "o3_auto_dispatch_ready": True,
        "requires_confirmation": False,
        "status": "approved",
        "dedup_key": "k" * 16,
    }
    p.update(over)
    return p


def _spy(monkeypatch):
    """监听唯一写口 `audit_problem_pool.set_status`。"""
    calls = []

    def _fake(dedup_key, new_status, root=None, audit_dir_path=None, now=None, note=""):
        calls.append({"key": dedup_key, "status": new_status, "note": note})
        return {"state": new_status, "changed": True}

    monkeypatch.setattr(app, "set_status", _fake)
    return calls


def _run(tmp_path, monkeypatch, patches, verdict):
    _pend = tmp_path / "pending_patches.json"
    _pend.write_text(json.dumps(patches, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "verify_patches_concurrent",
                        lambda _sb, _batch, **_kw: [dict(verdict) for _ in _batch])
    # ★数据面隔离：这两条路径都按**模块文件位置**解析（不吃 project_root），
    #   不桩掉就会真写仓库 data/reports/ 与 data/patches/（活体目录）。
    monkeypatch.setattr(mod, "_m324_append_trace", lambda _rec: "")
    monkeypatch.setattr(mod, "_m326_save_fail_ledger", lambda *_a, **_kw: True)
    return mod.dispatch_o3_approved(
        limit=1, pending_path=str(_pend), sandbox=_FakeSandbox(),
        project_root=str(tmp_path))


def _read_back(tmp_path, idx=0):
    _d = json.loads((tmp_path / "pending_patches.json").read_text(encoding="utf-8"))
    if isinstance(_d, dict):
        _d = next(v for v in _d.values() if isinstance(v, list))
    return _d[idx]


# ---------------------------------------------------------------- ① 核心
def test_01_landed_closes_loop_with_fixed(tmp_path, monkeypatch):
    """★核心：O3 真落地 ⇒ 审计池 `set_status(FIXED)` 必须被调用（且用本条的 dedup_key）。"""
    calls = _spy(monkeypatch)
    _t_landed = 1700000000.0
    _run(tmp_path, monkeypatch,
         [_patch(landed=True, landed_at=_t_landed)],
         {"success": True, "executed_at": 1.0})

    assert len(calls) == 1, f"应当恰好回写一次池状态，实得 {calls}"
    assert calls[0]["status"] == app.STATUS_FIXED, f"写的是 {calls[0]['status']!r} 而非 fixed"
    assert calls[0]["key"] == "k" * 16, "闭环用错了 key（应与本条 dedup_key 一致）"
    assert "t_o3_close_1" in calls[0]["note"], "闭环 note 未带 patch id（事后无法归因）"

    p = _read_back(tmp_path)
    assert p.get("applied") is True and p.get("o3_dispatch_verdict") == "applied"


def test_02_sandbox_only_does_not_close_loop(tmp_path, monkeypatch):
    """沙箱成功但未落地 ⇒ 不得回写 FIXED（否则又是"沙箱跑通冒充已修"）。"""
    calls = _spy(monkeypatch)
    _run(tmp_path, monkeypatch, [_patch()], {"success": True, "executed_at": 1.0})
    assert calls == [], f"未落地却写了池状态：{calls}"
    assert _read_back(tmp_path).get("o3_dispatch_verdict") == "sandbox_only"


def test_03_verify_failed_does_not_close_loop(tmp_path, monkeypatch):
    """沙箱失败 ⇒ 不得回写 FIXED。"""
    calls = _spy(monkeypatch)
    _run(tmp_path, monkeypatch, [_patch(landed=True, landed_at=1.0)],
         {"success": False, "error": "boom"})
    assert calls == [], f"验证失败却写了池状态：{calls}"


def test_04_missing_dedup_key_only_counts(tmp_path, monkeypatch):
    """键缺失 ⇒ 只记数 + 留痕，**不阻断派单**（观测不得阻断主流程）。"""
    calls = _spy(monkeypatch)
    mod._M365_CLOSE_LOOP_MISS["no_dedup_key"] = 0
    _p = _patch(landed=True, landed_at=1.0)
    _p.pop("dedup_key")
    _run(tmp_path, monkeypatch, [_p], {"success": True, "executed_at": 1.0})
    assert mod._M365_CLOSE_LOOP_MISS["no_dedup_key"] == 1, "键缺失未计数"
    assert calls == [], "键缺失不该调 set_status"
    assert _read_back(tmp_path).get("applied") is True, "键缺失不得把派单判失败"


# ---------------------------------------------------------------- ② 边界
_LEGAL_TRANSITIONS_SNAPSHOT_374 = {
    "open": ("claimed", "fixed", "verified", "wontfix", "target_drifted"),
    "claimed": ("fixed", "open", "wontfix", "target_drifted"),
    "fixed": ("verified", "claimed", "wontfix", "target_drifted"),
    "verified": ("open", "wontfix"),
    "wontfix": ("open",),
    "target_drifted": ("open", "wontfix"),
}


def test_05_legal_transitions_not_loosened():
    """状态机逐边未动（增/删/改任一边都红）。"""
    got = {str(k): tuple(v) for k, v in app._LEGAL_TRANSITIONS.items()}
    assert got == _LEGAL_TRANSITIONS_SNAPSHOT_374, (
        f"`_LEGAL_TRANSITIONS` 被改动：\n实得={got}")


def test_06_no_second_write_path():
    """不给 O3 单开写口：模块内 `set_status(` 只出现在 `_m356_close_loop_fixed` 里。"""
    src = _SRC.read_text(encoding="utf-8")
    assert len(re.findall(r"_app\.set_status\(", src)) == 1, "出现第二个池写入口"
    assert len(re.findall(r"\b_m356_close_loop_fixed\(", src)) == 3, (
        "闭环函数调用点数量变化（定义 1 + main 1 + O3 1 应为 3）")


def test_07_o3_segment_calls_close_loop_only_after_landed():
    """源码级：O3 段的闭环调用必须落在 `if _m372_landed_ok(_p):` 分支内。"""
    src = _SRC.read_text(encoding="utf-8")
    i = src.index("def dispatch_o3_approved")
    seg = src[i:i + 12000]
    assert "_m372_landed_ok(_p)" in seg, "O3 段缺落地判据"
    _i_landed = seg.index("if _m372_landed_ok(_p):")
    _i_close = seg.index("_m356_close_loop_fixed(_p)")
    _i_else = seg.index("o3_dispatch_verdict\"] = \"sandbox_only\"")
    assert _i_landed < _i_close < _i_else, (
        "闭环调用不在 landed 分支内（顺序应为 landed → close_loop → sandbox_only）")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
