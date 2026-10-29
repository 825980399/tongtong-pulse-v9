# -*- coding: utf-8 -*-
"""388刀4：DL3 影子账本**开灯**确认（385刀2 落地，开关默认关）。

开灯前必须确认的两件事，本文件把它们钉死：
  ① `data/decisions/` 的写盘守卫放行（`explicit=True` 时 True）
  ② 开关开时真能写进账本（用 tmp 目录，绝不写生产 data/）
"""

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
from nucleus.decision import contracts, ledger, shadow  # noqa: E402


def _request():
    """最小合规 DecisionRequest（scope 必须 ∈ SCOPES，candidates 不许空）。"""
    return contracts.DecisionRequest(
        request_id="m388_d4_probe",
        ts=__import__("time").time(),
        proc_gen="test",
        scope="chat",
        candidates=[{"id": "c1"}],
        constraints={},
        signals={"l2": {}},
    )


def test_m388_d4_switch_is_on():
    """★核心：`ENABLE_DEC_SHADOW` 必须是 True（388刀4 开灯）。"""
    assert config.ENABLE_DEC_SHADOW is True
    assert shadow.enabled() is True


def test_m388_d4_write_guard_allows_ledger_dir():
    """★开灯前提：写盘守卫对 `data/decisions/` 放行（explicit=True）。"""
    from nucleus.data.write_guard import guard_write
    _ok = guard_write(os.path.join("data", "decisions", "dec_probe.jsonl"),
                      explicit=True, component="DecisionLedger")
    assert _ok is True, "守卫拒绝 ⇒ 开灯后账本永远写不出来"


def test_m388_d4_ledger_append_writes(tmp_path):
    """★核心（票面验收）：开关开时能把一行写进账本（可回读）。"""
    _p = str(tmp_path / "dec_test.jsonl")
    _req = _request()
    _res = contracts.shadow_result(
        _req,
        action="DEFER",
        reason_code="SHADOW_ONLY",
        evidence=[{"note": "388刀4 开灯验证"}],
        rollback=None,
    )
    _ok = ledger.append(_res, request=None, path=_p, explicit=True)
    assert _ok == "ok", "append 返回 %r（disabled/denied/duplicate/write_failed 都算失败）" % _ok
    _rows = list(ledger.iter_rows(_p))
    assert len(_rows) == 1, _rows
    assert _rows[0]["schema"] == "dec_ledger_row/385.v1"


def test_m388_d4_shadow_result_is_non_authoritative():
    """★红线：`shadow_result` 产出的行必须 **shadow=True**，执行器必须忽略。

    这条是 385刀2 三条红线的第一条 —— 开灯不改变执行行为，靠的就是它。
    """
    _r = contracts.shadow_result(_request(), action="DEFER",
                                 reason_code="SHADOW_ONLY",
                                 evidence=[{"e": 1}], rollback=None)
    assert _r.shadow is True, "影子结果必须标记 shadow=True"   # DecideResult 是 dataclass
    assert _r.action == "DEFER", "影子默认动作必须是 DEFER（不许 EXECUTE）"


def test_m388_d4_off_switch_still_possible():
    """★可回退：开关语义仍由 config 控制（关掉即零回归，不是硬编码）。"""
    _src = io.open(os.path.join(ROOT, "nucleus", "decision", "shadow.py"),
                   encoding="utf-8").read()
    assert "ENABLE_DEC_SHADOW" in _src, "开关读的是 config 键，不是硬编码常量"


def test_m388_d4_ledger_dir_is_under_data():
    """账本目录必须是 `data/decisions`（与 385刀2 契约一致，不能漂移）。"""
    assert os.path.join("data", "decisions") in ledger.LEDGER_DIR \
        or ledger.LEDGER_DIR.endswith("decisions"), ledger.LEDGER_DIR
