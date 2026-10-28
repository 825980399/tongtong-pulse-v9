# -*- coding: utf-8 -*-
"""第373批 刀5 · O3 双标确认通道 applied 时点归位（P1 · 与 372 刀3 同型）

票：`T-372-O3双标确认同型-1`

问题：`dispatch_o3_approved` 在沙箱验证成功后直接
      `_p["applied"] = True; _p["applied_at"] = time.time()`
—— 把「沙箱跑通」冒充「补丁已落到源码」。这与 372 刀3 修掉的主流程
`:2846-2857` 是**同一个错误**，只是发生在 O3 确认后派单这条独立通道上
（主流程改了，它没改）。

修法（与 372 刀3 同型、共用同一判据 `_m372_landed_ok`）：
  * 沙箱结果独立留痕（`sandbox_ok` / `sandbox_ok_at`）；
  * `applied=True` 只认 `landed is True`；
  * 未落地 ⇒ `o3_dispatch_verdict="sandbox_only"`（新增取值，消费方只认
    `=="applied"` 的等值判据，不会被误判为已应用）。

边界：`_LEGAL_TRANSITIONS` 不放宽、`set_status` 唯一闭环调用点不删（test_04/05 守）。
"""

import json
import re
from pathlib import Path

import pytest

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
        "id": "t_o3_1",
        "file": "a.py",
        "method": "_dead_method",
        "o3_dead_code": True,
        "o3_auto_dispatch_ready": True,
        "requires_confirmation": False,
        "status": "approved",
    }
    p.update(over)
    return p


def _run(tmp_path, monkeypatch, patches, verdict):
    _pend = tmp_path / "pending_patches.json"
    _pend.write_text(json.dumps(patches, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "verify_patches_concurrent",
                        lambda _sb, _batch, **_kw: [dict(verdict) for _ in _batch])
    return mod.dispatch_o3_approved(
        limit=1, pending_path=str(_pend), sandbox=_FakeSandbox(),
        project_root=str(tmp_path))


def _read_back(tmp_path, idx=0):
    _d = json.loads((tmp_path / "pending_patches.json").read_text(encoding="utf-8"))
    if isinstance(_d, dict):
        _d = next(v for v in _d.values() if isinstance(v, list))
    return _d[idx]


def test_01_sandbox_ok_without_landed_does_not_set_applied(tmp_path, monkeypatch):
    """★核心：沙箱成功但未落地 ⇒ **不得**置 applied。"""
    mod._M373_O3_SANDBOX_ONLY["n"] = 0
    _run(tmp_path, monkeypatch, [_patch()], {"success": True, "executed_at": 1.0})
    p = _read_back(tmp_path)
    assert p.get("applied") is not True, "沙箱成功即置 applied（本刀要修的就是这个）"
    assert "applied_at" not in p, "沙箱成功即写 applied_at（落地时点被提前）"
    assert p.get("sandbox_ok") is True, "沙箱结果未独立留痕"
    assert p.get("sandbox_ok_at") == 1.0
    assert p.get("o3_dispatch_verdict") == "sandbox_only", \
        f"实得 {p.get('o3_dispatch_verdict')!r}（期望 sandbox_only）"
    assert mod._M373_O3_SANDBOX_ONLY["n"] == 1, "未计入 o3_sandbox_only 计数"


def test_02_landed_sets_applied_and_keeps_real_landed_time(tmp_path, monkeypatch):
    """已落地 ⇒ 置 applied，且 applied_at 取**既有落地时刻**，不是派单时刻。"""
    _t_landed = 1700000000.0
    _run(tmp_path, monkeypatch,
         [_patch(landed=True, landed_at=_t_landed)],
         {"success": True, "executed_at": 1.0})
    p = _read_back(tmp_path)
    assert p.get("applied") is True
    assert p.get("o3_dispatch_verdict") == "applied"
    assert p.get("applied_at") == _t_landed, (
        f"applied_at={p.get('applied_at')} 未采用既有落地时刻 {_t_landed}（被派单时刻冒充）")


def test_03_verify_failed_never_applied(tmp_path, monkeypatch):
    """沙箱失败 ⇒ verify_failed，连 sandbox_ok 都不该有。"""
    _run(tmp_path, monkeypatch, [_patch()], {"success": False, "error": "boom"})
    p = _read_back(tmp_path)
    assert p.get("applied") is not True
    assert p.get("o3_dispatch_verdict") == "verify_failed"
    assert "sandbox_ok" not in p


#: `_LEGAL_TRANSITIONS` 在 373 刀5 动工时的**冻结快照**（逐边抄自
#: `nucleus/audit_problem_pool.py:107-117`）。
#: 语义：本刀只改 `applied` 的时点，**不许**为了让补丁状态流转顺畅去放宽状态机。
#: 增一条边或删一条边都必须在这里显式改快照 = 一次显式裁决。
#: ★前提勘误（2026-10-27 实测）：初版考题写「open 不得直达 fixed」——但
#:   `open→fixed` 是 352 以来**既有**的合法边（允许直接判修复），把既有设计
#:   当成「本刀放开的」是考题前提错误；改为快照对比后判别力更强：
#:   多一条边、少一条边、改一条边都红。
_LEGAL_TRANSITIONS_SNAPSHOT_373 = {
    # ★385刀1：383刀4 **受控新增** STATUS_STALE（O3 判死超时转 stale，非结案），
    #   此处同步基线。守卫本意是「不许为流转顺畅放宽既有边」；新增**状态**属
    #   功能扩展，必须显式更新基线并留痕 —— 否则守卫会永久红，等于坏掉的警报器。
    "open": ("claimed", "fixed", "verified", "wontfix", "target_drifted", "stale"),
    "claimed": ("fixed", "open", "wontfix", "target_drifted"),
    "fixed": ("verified", "claimed", "wontfix", "target_drifted"),
    "verified": ("open", "wontfix"),
    "wontfix": ("open",),
    "target_drifted": ("open", "wontfix"),
    # stale 留两条回升通道：回 open（有人要处理了）/ 直接 wontfix。
    #   **不给 fixed**：它本来就没人在修。
    "stale": ("open", "wontfix"),
}


def test_04_legal_transitions_not_loosened():
    """守 `_LEGAL_TRANSITIONS` 不放宽（状态机合法流转表逐边未动）。"""
    from nucleus.audit_problem_pool import _LEGAL_TRANSITIONS
    assert isinstance(_LEGAL_TRANSITIONS, dict) and _LEGAL_TRANSITIONS
    got = {str(k): tuple(v) for k, v in _LEGAL_TRANSITIONS.items()}
    _extra = {k: [x for x in v if x not in _LEGAL_TRANSITIONS_SNAPSHOT_373.get(k, ())]
              for k, v in got.items()}
    _extra = {k: v for k, v in _extra.items() if v}
    _lost = {k: [x for x in v if x not in got.get(k, ())]
             for k, v in _LEGAL_TRANSITIONS_SNAPSHOT_373.items()}
    _lost = {k: v for k, v in _lost.items() if v}
    assert not _extra and not _lost, (
        f"状态机表被改动（新增边={_extra} 丢失边={_lost}）"
        "——本刀不得为流转顺畅放宽 `_LEGAL_TRANSITIONS`")


def test_05_set_status_single_closeout_call_site_kept():
    """356 闭环语义不破坏：`set_status` 的唯一闭环调用点仍在。"""
    src = _SRC.read_text(encoding="utf-8")
    assert "_m356_close_loop_fixed" in src, "356 闭环调用点被删"
    # 主流程仍是「landed 才 FIXED」（372 刀3 修法不许被本刀顺手改回去）
    assert "if _m372_landed_ok(_p):" in src


def test_06_o3_channel_no_longer_applies_on_sandbox_success():
    """源码级反回归：O3 段不得再出现「success ⇒ 无条件 applied=True」形态。"""
    src = _SRC.read_text(encoding="utf-8")
    # 抓 O3 通道那一段（从 o3_dispatched_at 到 o3_dispatch_verdict = verify_failed）
    m = re.search(r"if _v\.get\(\"success\"\):(.{0,2200}?)o3_dispatch_verdict\"\] = \"verify_failed\"",
                  src, re.DOTALL)
    assert m, "O3 派单段锚点未找到（结构可能已变，需复核）"
    seg = m.group(1)
    assert "_m372_landed_ok(_p)" in seg, "O3 段未接落地判据"
    assert 'o3_dispatch_verdict"] = "sandbox_only"' in seg, "O3 段缺 sandbox_only 分支"
    # 关键否定：不允许在**没有 landed 判定**的情况下直接写 applied
    assert not re.search(r"if _v\.get\(\"success\"\):\s*\n\s*_p\[\"applied\"\] = True", src), \
        "O3 段仍是「沙箱成功即 applied=True」"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
