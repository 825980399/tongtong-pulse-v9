# -*- coding: utf-8 -*-
"""433 刀1：静默复算不得覆盖人工 override；`verify_applied_patches` 走单写口。

守护三件事：
  1) 带 ``problem_fixed_corrected`` 的补丁，``_m84_recompute_split`` **不得**改回
     ``problem_fixed`` —— 这正是「每 45 分钟一轮静默复算把人工修正抹掉」的路径；
  2) 无 override 时复算照常生效（防"锁过头"把整条链锁死）；
  3) ``verify_applied_patches`` 落盘走 ``_save_patch_list``（内含 427 状态守卫），
     不再用 ``_save_json`` 整份绕过。

★反证（铁律 30）：锁定用例的基准不取被测函数输出，而是**另一个入口**
  ``patch_verification_split.apply_split`` —— 它作用在同一份数据上会真的改掉
  ``problem_fixed``，故"没被改掉"只可能来自锁，而不是数据本身没变化。
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.evolution.patch_verification_split import (
    apply_split,
    is_split_override_locked,
)
from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor

SRC_SEE = os.path.join(ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")


def _src(path: str) -> str:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def _code_only(text: str) -> str:
    """剥掉整行注释 —— 注释里会**引用**被测代码，直接 count 会假绿。"""
    return "\n".join(_ln for _ln in text.split("\n")
                     if not _ln.lstrip().startswith("#"))


def _locked_patch() -> dict:
    """人工把 problem_fixed 修正为 False；按数据重算本会得出 True。"""
    return {"id": "m433-lock1", "file": "nucleus/demo_m433.py", "method": "m",
            "baseline_errors": 4, "post_apply_errors": 0,
            "problem_fixed": False, "problem_fixed_corrected": True,
            "problem_fixed_reason": "human_override"}


class _RecPM:
    """记录**走了哪个写口**的 PatchManager 替身（刻意同时实现两个口）。"""

    def __init__(self, history=None) -> None:
        self.history = history if history is not None else []
        self.calls: list = []

    def get_history_file(self) -> str:
        return "H"

    def get_pending_file(self) -> str:
        return "P"

    def load_json(self, path, default):
        return self.history if path == "H" else default

    def _save_json(self, path, data) -> bool:
        self.calls.append(("_save_json", path))
        return True

    def _save_patch_list(self, path, data) -> bool:
        self.calls.append(("_save_patch_list", path))
        return True

    def dedup_history_rows(self, rows):
        return rows

    def rollback_patch(self, pid: str):
        return {"ok": True}


def _bare_executor(manager) -> SafeEvolutionExecutor:
    ex = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
    ex._patch_manager = manager
    ex.verify_fix_from_logs = lambda p: {
        "verified": True, "post_apply_errors": 0, "after_fix": 0,
        "effectiveness": 1.0, "detail": "m433-test"}
    ex._learn_from_verification = lambda p, r: None
    ex._count_errors_for_location = lambda f, m, since=0: 0
    return ex


class TestOverrideLock:
    def test_01_locked_patch_is_not_recomputed(self):
        """带 override 锁 ⇒ 复算跳过，problem_fixed 保持人工值。"""
        _p = _locked_patch()
        ex = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        assert ex._m84_recompute_split(_p) is False, "锁命中应返回 False（=未复算）"
        assert _p["problem_fixed"] is False, "人工 override 被静默复算改回去了"
        assert _p["problem_fixed_reason"] == "human_override"
        assert "verification_split_version" not in _p, "锁命中不该写任何拆分字段"

    def test_02_apply_split_on_same_data_would_change_it(self):
        """★反证：另一个入口（apply_split）作用在同一份数据上**会**改掉它。"""
        _p = _locked_patch()
        assert is_split_override_locked(_p) is True
        apply_split(_p)
        assert _p["problem_fixed"] is True, (
            "反证失效：apply_split 本应把 4→0 判为已修复；若它不改，"
            "test_01 的『没被改掉』就不再是锁的功劳")

    def test_03_unlocked_patch_still_recomputes(self):
        """无锁 ⇒ 复算照常生效（防"锁过头"把整条链锁死）。"""
        _p = {"id": "m433-lock2", "baseline_errors": 4, "post_apply_errors": 0}
        ex = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        assert ex._m84_recompute_split(_p) is True
        assert _p["problem_fixed"] is True

    def test_04_lock_is_wired_inside_the_helper(self):
        """接线断言：锁判据必须**在** `_m84_recompute_split` 方法体内。"""
        _code = _code_only(_src(SRC_SEE))
        _head = "    def _m84_recompute_split(self, patch: dict[str, Any]) -> bool:"
        assert _head in _code
        _body = _code.split(_head, 1)[1].split("\n    def ", 1)[0]
        assert "is_split_override_locked" in _body, "未导入锁判据"
        assert "if _pvs84.is_split_override_locked(patch):" in _body, "方法体内缺锁短路"
        # 锁必须在 apply_split 之前生效（否则先改了再判，等于没判）
        assert (_body.index("is_split_override_locked(patch)")
                < _body.index("_pvs84.apply_split(patch)"))

    def test_05_six_writeback_call_sites_not_reduced(self):
        """6 个写回点必须都还在（只加不改；少于 6 说明误删了调用点）。"""
        _code = _code_only(_src(SRC_SEE))
        _n = _code.count("self._m84_recompute_split(_patch)")
        assert _n >= 6, f"写回点被减少了：{_n} < 6"


class TestSingleWritePort:
    def test_06_verify_applied_saves_via_save_patch_list(self):
        """`verify_applied_patches` 必须走 `_save_patch_list`，不再直写 `_save_json`。"""
        _p = {"id": "m433-sp1", "applied": True, "needs_runtime_verify": True,
              "runtime_verified": False, "applied_at": time.time() - 1000,
              "baseline_errors": 4, "file": "nucleus/demo_m433.py", "method": "m"}
        _pm = _RecPM(history=[_p])
        _res = _bare_executor(_pm).verify_applied_patches()

        assert _res["verified"] == 1
        assert ("_save_patch_list", "H") in _pm.calls, f"没走单写口：{_pm.calls}"
        assert ("_save_json", "H") not in _pm.calls, f"仍绕过单写口直写：{_pm.calls}"

    def test_07_source_no_save_json_for_history(self):
        """源码级：history 落盘那一段不得残留 `_save_json`（防回退）。"""
        _code = _code_only(_src(SRC_SEE))
        _head = "    def verify_applied_patches(self) -> dict[str, Any]:"
        _body = _code.split(_head, 1)[1].split("\n    def ", 1)[0]
        assert "_save_patch_list(" in _body
        assert "_save_json(" not in _body, "verify_applied_patches 里仍用 _save_json 直写"
