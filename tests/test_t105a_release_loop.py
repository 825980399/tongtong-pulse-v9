# -*- coding: utf-8 -*-
"""★第105批 T-105a（P0）：进化闭环复通 —— 放行判据**时序负控** + 污染字段免疫。

任务书原文（T-105a）：
    「单测补时序负控：
        * 断言『入队时 runtime_verify_result 不存在 ⇒ 不放行』；
        * 断言『验证后复查 ⇒ 放行』。」

根因回顾（第105批 T0 实证）：
    * 旧放行判据读顶层 ``runtime_verified`` —— 该字段由 ``verify_submitted_patches``
      对所有处理过的补丁**无条件置 True**（含失败补丁），属污染字段；
      真验证结果在嵌套 ``runtime_verify_result.verified``。
    * 旧逻辑仅在「入队时」调用放行判据，彼时 ``runtime_verify_result`` 尚不存在
      ⇒ 判据恒假；验证写回后又无人复查 ⇒ 闭环断裂（实测 30 条 rv=True 仍 0 放行）。
    * 第105批修复：①放行判据改读嵌套字段 + 要求 baseline_errors>0；
      ②``verify_submitted_patches`` 验证写回后**补回边**复查放行（调用
      ``PatchManager._m105_try_release_low_risk``）。

本文件固化「改后的正确行为」，是 T-105a 复通的可执行验收证据：
  A. 入队时仅有污染字段（无嵌套 verified）⇒ 绝不放行（断旧污染路径）。
  B. 入队时嵌套 verified=True + baseline>0 ⇒ 立刻放行（approved + auto_released）。
  C. 验证后复查（直接驱动补回边同款助手）⇒ 放行。
  D. 顶层污染字段 runtime_verified=True 即使嵌套 verified=False 也不放行（双保险）。
"""
import io
import os
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from nucleus.reasoning.PatchManager import PatchManager  # noqa: E402


class _DummyBackup:
    def create_backup(self, files=None, reason=None, source=None):
        return os.path.join(tempfile.gettempdir(), "dummy_backup_t105a")


def _make_pm(tmp):
    pm = PatchManager(project_root=PROJECT_ROOT)
    pm._pending_file = os.path.join(tmp, "pending_patches.json")
    pm._history_file = os.path.join(tmp, "patch_history.json")
    pm._backup_manager = _DummyBackup()
    # 隔离重启计数持久化（与生产 WriteGuard 无关，专测落地链路）
    pm._save_restart_counter = lambda c: True
    pm._load_restart_counter = lambda: 0
    pm._max_restart_count = 10 ** 9
    return pm


def _make_target(tmp):
    os.makedirs(os.path.join(PROJECT_ROOT, "tmp"), exist_ok=True)
    return os.path.join(PROJECT_ROOT, "tmp", "t105a_loop_target_%s.py" % __import__("uuid").uuid4().hex)


def _low_risk_patch(target, **kw):
    _p = {
        "id": "t105a_loop",
        "file": target,
        "method": "probe_func",
        "original_code": "def probe_func():\n    return 1\n",
        "modified_code": "def probe_func():\n    return 2\n",
        "risk_level": "低",
        "source": "llm",
        "status": "pending",
        "trust_score": 90,
        "aesthetic_score": {"total": 90, "grade": "A", "syntax_valid": True},
    }
    _p.update(kw)
    return _p


def test_A_enqueue_without_nested_verify_not_released():
    """★A：入队时仅有污染字段 runtime_verified=True（无嵌套 verified）⇒ 绝不放行。"""
    tmp = tempfile.mkdtemp(prefix="t105a_A_")
    target = _make_target(tmp)
    with io.open(target, "w", encoding="utf-8") as f:
        f.write("def probe_func():\n    return 1\n")
    pm = _make_pm(tmp)
    # 仅顶层污染字段，嵌套 runtime_verify_result 不存在
    _p = _low_risk_patch(target, runtime_verified=True)
    pm.save_pending_patch(_p)
    assert _p.get("status") != "approved", \
        "★污染字段 runtime_verified=True 不得触发放行（判据须读嵌套 verified）"
    assert not _p.get("auto_released"), "★未通过嵌套验证者不得 auto_released"


def test_B_enqueue_nested_verified_and_baseline_released():
    """★B：入队时嵌套 verified=True + baseline>0 ⇒ 立刻放行（闭环入口打通）。"""
    tmp = tempfile.mkdtemp(prefix="t105a_B_")
    target = _make_target(tmp)
    with io.open(target, "w", encoding="utf-8") as f:
        f.write("def probe_func():\n    return 1\n")
    pm = _make_pm(tmp)
    _p = _low_risk_patch(
        target,
        runtime_verify_result={"verified": True, "baseline": 3},
        baseline_errors=3,
    )
    pm.save_pending_patch(_p)
    assert _p.get("status") == "approved", _p.get("status")
    assert _p.get("auto_released") is True, "★嵌套 verified 合格补丁须 auto_released"
    assert _p.get("release_reason") == "low_risk_release:T-101a"


def test_C_post_verify_recheck_releases():
    """★C：验证后复查 ⇒ 放行（直接驱动补回边同款助手 _m105_try_release_low_risk）。

    对应 ``SafeEvolutionExecutor.verify_submitted_patches`` 尾部补回边循环：
    遍历 status∈{pending, runtime_verified} 的条目，对满足 T-101a 判据者置 approved。
    """
    tmp = tempfile.mkdtemp(prefix="t105a_C_")
    target = _make_target(tmp)
    pm = _make_pm(tmp)
    # 模拟「验证写回后」状态：status=pending/runtime_verified + 嵌套 verified + baseline>0
    _p = {
        "id": "t105a_recheck", "file": target, "method": "probe_func",
        "risk_level": "低", "source": "llm", "status": "runtime_verified",
        "runtime_verify_result": {"verified": True, "baseline": 2},
        "baseline_errors": 2,
    }
    _ok = pm._m105_try_release_low_risk(_p)
    assert _ok is True, "★验证后复查须放行 T-101a 合格补丁"
    assert _p.get("status") == "approved"
    assert _p.get("auto_released") is True

    # 幂等：已 approved 者再次复查不得重复放行
    _again = pm._m105_try_release_low_risk(_p)
    assert _again is False, "★已 approved 须幂等，不得重复放行"


def test_D_polluted_top_level_ignored_when_nested_false():
    """★D：顶层污染字段 runtime_verified=True 即使嵌套 verified=False 也不放行（双保险）。"""
    tmp = tempfile.mkdtemp(prefix="t105a_D_")
    target = _make_target(tmp)
    pm = _make_pm(tmp)
    _p = {
        "id": "t105a_polluted", "file": target, "method": "probe_func",
        "risk_level": "低", "source": "llm", "status": "pending",
        "runtime_verified": True,  # 污染字段，模拟旧数据
        "runtime_verify_result": {"verified": False},  # 真验证：未通过
    }
    _ok = pm._m105_try_release_low_risk(_p)
    assert _ok is False, "★嵌套 verified=False 须否决（顶层污染字段不可翻案）"
    assert _p.get("status") != "approved"


def test_E_baseline_zero_routes_to_human():
    """★E：嵌套 verified=True 但 baseline_errors<=0（效果不可判定）⇒ 不放行（转人工）。"""
    tmp = tempfile.mkdtemp(prefix="t105a_E_")
    target = _make_target(tmp)
    pm = _make_pm(tmp)
    _p = {
        "id": "t105a_nobase", "file": target, "method": "probe_func",
        "risk_level": "低", "source": "llm", "status": "pending",
        "runtime_verify_result": {"verified": True, "baseline": 0},
        "baseline_errors": 0,
    }
    _ok = pm._m105_try_release_low_risk(_p)
    assert _ok is False, "★baseline<=0 效果不可判定，须转人工队列而非自动放行"
    assert _p.get("status") != "approved"


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
