# -*- coding: utf-8 -*-
"""389刀4：重启计数桶 —— 只读核实 + 幂等重置（**不改共用桶语义**）。

T0 核实（票面前提不成立）
========================
票面：「计数器已经记了 1 次（今天下午自动重启过一次）」，实测：

    data/patches/restart_count.txt      = "0"    （mtime 2026-10-29 14:08）
    data/patches/restart_blocked_at.txt = 旧值   （mtime 2026-10-03 14:53）

⇒ 计数当前已是 0，**无需重置**（本刀没有写生产文件）。

这个桶是什么（别改错东西）
--------------------------
`restart_count.txt` 是**补丁自动应用 + 内存自动重启共用**的棘轮桶（176批段2 明文"共用"）：
`counter > 3` ⇒ `memory_restart_bucket_state().locked`，锁 `MEMORY_RESTART_COOLDOWN_HOURS`
（387刀1 改为 6h）小时。内存重启走 `bump_restart_counter_for_memory_restart()`，
与补丁重启**同一个计数**。

★由此的设计风险（**本刀不擅自改**，留裁决）：
  4 次内存重启 ⇒ 补丁自动应用被锁 6h；反之亦然。要分开必须同时改
  `apply_all_pending` 与 `memory_restart_bucket_state` 两处判定 ⇒ 单独成批。

本测试钉住：三态读数、桶判定、幂等重置、**重置不动冷却起点**、未锁时 remain=0。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import m389_check_restart_counter as T  # noqa: E402


def test_m389_d4_read_counter_three_states(tmp_path):
    _p = tmp_path / "restart_count.txt"
    assert T.read_counter(str(_p)) == (0, "absent")
    _p.write_text("2", encoding="utf-8")
    assert T.read_counter(str(_p)) == (2, "ok")
    _p.write_text("not-an-int", encoding="utf-8")
    assert T.read_counter(str(_p)) == (0, "unreadable"), "读不动必须与「从没记过」分开"
    _p.write_text("", encoding="utf-8")
    assert T.read_counter(str(_p)) == (0, "ok"), "空文件按 0（合法空态）"


def test_m389_d4_read_blocked_at_tolerates_garbage(tmp_path):
    _p = tmp_path / "restart_blocked_at.txt"
    assert T.read_blocked_at(str(_p)) == 0.0
    _p.write_text("abc", encoding="utf-8")
    assert T.read_blocked_at(str(_p)) == 0.0
    _p.write_text("1791010393.5", encoding="utf-8")
    assert T.read_blocked_at(str(_p)) == 1791010393.5


def test_m389_d4_bucket_not_locked_under_limit():
    """≤ 上限 ⇒ 未锁，且 remain 必须是 0（不是"整个冷却窗口"）。"""
    _r = T.bucket_state(counter=3, blocked_at=0.0, cooldown_h=6.0, max_n=3)
    assert _r["locked"] is False
    assert _r["remain_sec"] == 0, "未锁时给 remain=21600 会被读成'还要等6小时'"


def test_m389_d4_bucket_locked_over_limit_without_blocked_at():
    _r = T.bucket_state(counter=4, blocked_at=0.0, cooldown_h=6.0, max_n=3,
                        now=1000.0)
    assert _r["locked"] is True
    assert _r["remain_sec"] == 6 * 3600, "没有锁死起点 ⇒ 按整个窗口算"


def test_m389_d4_bucket_releases_after_cooldown():
    """冷却期满 ⇒ 不锁（与 `apply_all_pending` 的放行分支一致）。"""
    _r = T.bucket_state(counter=9, blocked_at=1000.0, cooldown_h=6.0, max_n=3,
                        now=1000.0 + 6 * 3600 + 1)
    assert _r["locked"] is False and _r["remain_sec"] == 0


def test_m389_d4_bucket_remaining_counts_down():
    _r = T.bucket_state(counter=5, blocked_at=1000.0, cooldown_h=6.0, max_n=3,
                        now=1000.0 + 3600)
    assert _r["locked"] is True
    assert _r["remain_sec"] == 5 * 3600


def test_m389_d4_reset_is_idempotent_and_verifies(tmp_path):
    """★核心：非 0 → 0；已是 0 再重置 ⇒ `changed=False`（幂等，不重复写）。"""
    _p = str(tmp_path / "restart_count.txt")
    with open(_p, "w", encoding="utf-8") as _f:
        _f.write("3")
    _r1 = T.reset_counter(_p, allow_prod=True)
    assert _r1["ok"] is True and _r1["changed"] is True, _r1
    assert _r1["before"] == 3 and _r1["after"] == 0
    assert open(_p, encoding="utf-8").read() == "0"

    _r2 = T.reset_counter(_p, allow_prod=True)
    assert _r2["ok"] is True and _r2["changed"] is False
    assert _r2["reason"] == "already_zero"


def test_m389_d4_reset_requires_allow_prod(tmp_path):
    """★没 --allow-prod 一律不写（票面要求"重置"，但不能悄悄改生产）。"""
    _p = str(tmp_path / "restart_count.txt")
    with open(_p, "w", encoding="utf-8") as _f:
        _f.write("2")
    _r = T.reset_counter(_p, allow_prod=False)
    assert _r["ok"] is False and _r["reason"] == "need_allow_prod"
    assert open(_p, encoding="utf-8").read() == "2", "被拒时必须原样"


def test_m389_d4_reset_does_not_touch_blocked_at(tmp_path):
    """★重置只清计数，**不动冷却起点** —— 清掉等于把棘轮提前放行。"""
    _d = tmp_path
    _cp = str(_d / "restart_count.txt")
    _bp = str(_d / "restart_blocked_at.txt")
    with open(_cp, "w", encoding="utf-8") as _f:
        _f.write("7")
    with open(_bp, "w", encoding="utf-8") as _f:
        _f.write("1791010393.5")
    T.reset_counter(_cp, allow_prod=True)
    assert T.read_blocked_at(_bp) == 1791010393.5, "冷却起点被改了"


def test_m389_d4_reset_unreadable_is_refused(tmp_path):
    _p = str(tmp_path / "restart_count.txt")
    with open(_p, "w", encoding="utf-8") as _f:
        _f.write("abc")
    _r = T.reset_counter(_p, allow_prod=True)
    assert _r["ok"] is False and _r["reason"] == "unreadable_before_reset"


def test_m389_d4_default_paths_point_at_prod_patch_dir():
    assert T.counter_path("").endswith(os.path.join("data", "patches",
                                                    "restart_count.txt"))
    assert T.blocked_at_path("").endswith(os.path.join("data", "patches",
                                                       "restart_blocked_at.txt"))


def test_m389_d4_shared_bucket_risk_is_documented():
    """★设计风险必须写在工具里（下一批要看得到"为什么不能随便改成两个桶"）。"""
    _p = os.path.join(ROOT, "tools", "m389_check_restart_counter.py")
    _s = open(_p, encoding="utf-8").read()
    assert "共用" in _s
    assert "apply_all_pending" in _s and "memory_restart_bucket_state" in _s


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
