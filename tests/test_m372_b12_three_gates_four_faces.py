# -*- coding: utf-8 -*-
"""372 刀12 定向单测：三闸改造四面（A/B 只读定位 · D 纯文案 · E 口径注记）。

票 `T-371-三闸改造子刀前置复核-1`｜P1｜裁决：本批**只做 A/B/D/E 四低风险面**，
候选 C（首次落盘，改生产写路径）不进 372，待烛微复核后单独报小林拍板。

四例对应任务书四项：
  ��� 三闸锚点仍在（函数/常量签名未变）
  ② D 面文案与**实现语义**一致（三取与，不是三取一）
  ③ A/B 面定位结论有源码/日志证据支撑
  ④ 生产面零 diff（除 D 面 docstring）
"""
from __future__ import annotations

import inspect
import io
import re

import pytest

from nucleus.mnemosyne import PulseNodePool as _poolmod
from nucleus import self_inspector as _si


def _src(fp):
    return io.open(fp, encoding="utf-8", errors="replace").read()


# ============================================================ ① 锚点仍在
def test_01_three_gate_anchors_present():
    """三闸锚点（函数 + 常量 + 状态机入口）签名未变，本批没动结构。"""
    _f = _si._m340_judge_action_open
    _params = list(inspect.signature(_f).parameters)
    assert _params[:4] == ["mode", "sample", "misjudge", "tick_id"], _params
    assert hasattr(_si, "_M340_JUDGE_ACTION_MIN_TICKS")
    assert hasattr(_si, "_O3_MISJUDGE_LINE")

    # 闸③：写侧三件仍在（路径 / 记账 / 落盘）
    for _n in ("_m349_reload_stats_path", "_m349_note_reload",
               "_m349_persist_reload_stats", "_m349_reload_state"):
        assert hasattr(_poolmod.PulseNodePool, _n), _n
    # 闸③读侧：live_gate 与采集器仍在
    from nucleus.mnemosyne import live_gate
    assert hasattr(live_gate, "live_gate_ok")


# ============================================================ ② D 面文案与实现一致
def test_02_docstring_matches_implementation():
    """D 面：文案说"三取与"，实现就必须是三个 and（不是 or）。"""
    _doc = inspect.getdoc(_si._m340_judge_action_open) or ""
    assert "三取与" in _doc, "D 面文案应订正为三取与"
    assert "三项全满足" in _doc
    # 历史口径要留痕（说明为什么原来是错的），但必须明说它相反
    assert "三取一" in _doc and "相反" in _doc

    _body = inspect.getsource(_si._m340_judge_action_open)
    assert "_all_ok = _mode_ok and _sample_ok and _mis_ok" in _body, (
        "★实现必须是三个 and；若改成 or，文案就成了新的谎言")
    # 不许出现 or 形态的三取一
    assert not re.search(r"_all_ok\s*=\s*_mode_ok\s+or", _body)


# ============================================================ ③ A/B 定位有证据
def test_03_ab_findings_have_source_evidence():
    """A 面：sample 降 provisional 的**唯一**开关是 W1 心跳；B 面：写侧零调用。"""
    _s = _src("nucleus/self_inspector.py")
    # A 面锚点：只在 w1 != alive 时降级
    assert '_w1b.get("verdict") != "alive"' in _s
    assert '"provisional"' in _s
    # ★373刀3：降级理由加带成因（stale_kind）⇒ 格式串从 "W1=%s ⇒" 变为 "W1=%s%s ⇒"。
    #   判据**不放宽**：仍要求「W1 值 + 降 provisional」在同一格式串里，并新增一条 ——
    #   成因变量必须真的被拼进去（否则等于加了字段却没人用，正是 337/349 踩过的坑）。
    assert "W1=%s%s ⇒ 抽样结论降 provisional" in _s
    assert '_wk = _w1b.get("stale_kind")' in _s
    # 走的是统一薄封装（不重复实现阈值）
    assert "from nucleus.world_model.external_heartbeat import (" in _s
    assert "w1_verdict_brief as _w1v" in _s

    # B 面锚点：写侧唯一入口在 evict / cold recall 两条路径
    _p = _src("nucleus/mnemosyne/PulseNodePool.py")
    _calls = [m.start() for m in re.finditer(r"self\._m349_note_reload\(", _p)]
    assert len(_calls) >= 4, "写侧应有 evict + 3 处 recall 调用点，实得 %d" % len(_calls)
    # 节流与原子写仍在（说明不是 persist 实现坏了）
    assert "_M349_RELOAD_PERSIST_GAP" in _p
    assert "os.replace(_tmp, _p)" in _p


# ============================================================ ④ 生产面零 diff
def test_04_production_surface_untouched():
    """本批只改 D 面 docstring：不得新增/删除任何判据常量与阈值。"""
    _s = _src("nucleus/self_inspector.py")
    # 阈值常量一字不动（值级断言，防有人顺手调门槛）
    assert _si._O3_MISJUDGE_LINE == 0.10
    assert _si._M340_JUDGE_ACTION_MIN_TICKS == 3
    _p = _src("nucleus/mnemosyne/PulseNodePool.py")
    assert "_m349_persist_reload_stats(force=True)" not in _p, (
        "★候选 C（首次立即落盘）明确不进 372，不得出现")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
