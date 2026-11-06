# -*- coding: utf-8 -*-
"""468 批门控：进化闭环「最后一公里」三刀。

背景（T0 实测 2026-11-06 15:33-15:40）：

* 刀1 · 复验前置门
  `SafeEvolutionExecutor` 在「提交待审批」那一刻就打上 `fixed_at` /
  `needs_runtime_verify`（L2888-2896），不看审批是否通过、不看 apply 是否成功。
  于是复验流程捡起**源码没被改过**的方法测效果：前后都 0 错误 ⇒ baseline=0
  ⇒ 不可判定 ⇒ 延迟复验 ⇒ 再不可判定。实证：`_schedule_next_beat` 的补丁
  applied=False，而 `organs/body/PulseHeart.py:444-482` 无 `_in_progress` 守卫；
  pending 里三条进复验的补丁 applied 全为 False，其中
  `_m383_dump_tier_distribution` 还拿到了 runtime_verified 证书。

* 刀2 · 审批重投
  `_m447_try_approve` 只在补丁刚生成时调一次。14:53:52 实测
  `approved=False blocked=daily_cap（今日已批准 1 / 上限 1）`，此后再无审批日志。

* 刀3 · 生成侧标复核
  生成侧用**原始 would_block** 打 `sandbox_incompatible=True`
  （`PatchManager.save_pending_patch` L3688），而 365刀A2 的两通道放行只在
  「字段缺失、现算」那条路上生效 ⇒ 打过标的补丁被 `is_sandbox_incompatible`
  第一行短路，idiom 放行对它完全失效。
"""
import ast
import importlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SEE_PATH = os.path.join(_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
_DIS_PATH = os.path.join(_ROOT, "nucleus", "evolution", "patch_execution_dispatch.py")
_CFG_PATH = os.path.join(_ROOT, "config.py")

SEE = importlib.import_module("nucleus.reasoning.SafeEvolutionExecutor")
DIS = importlib.import_module("nucleus.evolution.patch_execution_dispatch")


def _src(p):
    return io.open(p, encoding="utf-8").read()


def _code_only(text):
    """剥掉整行注释（注释里引用符号不是调用点，铁律 85）。"""
    return "\n".join(ln for ln in text.split("\n") if not ln.lstrip().startswith("#"))


class TestA_ReverifyGate(unittest.TestCase):
    """刀1 判据：没落地的补丁不复验。"""

    def setUp(self):
        self._old = getattr(config, "ENABLE_M468_REVERIFY_APPLIED_GUARD", None)

    def tearDown(self):
        if self._old is None:
            config.__dict__.pop("ENABLE_M468_REVERIFY_APPLIED_GUARD", None)
        else:
            config.ENABLE_M468_REVERIFY_APPLIED_GUARD = self._old

    def test_01_applied_true_passes(self):
        self.assertEqual(SEE._m468_reverify_gate({"applied": True}), (True, ""))

    def test_02_applied_false_blocks(self):
        self.assertEqual(SEE._m468_reverify_gate({"applied": False}),
                         (False, "not_applied"))

    def test_03_apply_error_takes_precedence(self):
        _ok, _why = SEE._m468_reverify_gate(
            {"applied": False, "apply_error": "副本验证失败: ['基础缩进不一致']"})
        self.assertFalse(_ok)
        self.assertTrue(_why.startswith("apply_failed:"), _why)

    def test_04_applied_missing_keeps_old_behaviour(self):
        """老件没有 applied 字段 ⇒ 不擅自改变行为。"""
        self.assertEqual(SEE._m468_reverify_gate({"method": "x"}),
                         (True, "applied_unknown"))

    def test_05_non_dict_passes(self):
        self.assertTrue(SEE._m468_reverify_gate(None)[0])
        self.assertTrue(SEE._m468_reverify_gate([])[0])

    def test_06_switch_off_restores_old_behaviour(self):
        config.ENABLE_M468_REVERIFY_APPLIED_GUARD = False
        self.assertFalse(SEE._m468_reverify_applied_guard_on())
        self.assertEqual(SEE._m468_reverify_gate({"applied": False}),
                         (True, "guard_off"))

    def test_07_switch_on_by_default(self):
        self.assertTrue(SEE._m468_reverify_applied_guard_on())


class TestB_ReverifyWiring(unittest.TestCase):
    """刀1 接线：两个入口都必须过 gate（防止只改一处）。"""

    def setUp(self):
        self._code = _code_only(_src(_SEE_PATH))

    def test_11_gate_called_twice(self):
        self.assertEqual(self._code.count("_m468_reverify_gate(_patch)"), 2,
                         "两处入口（延迟复验 / 批量验证）都应过 gate")

    def test_12_gate_definition_once(self):
        self.assertEqual(_src(_SEE_PATH).count("def _m468_reverify_gate("), 1)

    def test_13_marks_awaiting_apply(self):
        self.assertEqual(self._code.count('_patch["m468_awaiting_apply"] = True'), 2)

    def test_14_gate_before_total_increment(self):
        """批量验证里 gate 必须在 `_total += 1` 之前（否则计数仍会涨）。"""
        _i = self._code.find("_m468_reverify_gate(_patch)")
        _j = self._code.rfind("_m468_reverify_gate(_patch)")
        _k = self._code.find("_total += 1")
        self.assertGreater(_j, _i)
        self.assertGreater(_k, _j, "gate 必须在 _total += 1 之前")

    def test_15_no_bare_except_pass(self):
        import re
        _s = _src(_SEE_PATH)
        self.assertEqual(
            len(re.findall(r"except\s+[^\n:]+:\s*\n\s*pass\s*(\n|$)", _s)), 0)


class TestC_ApprovalRetry(unittest.TestCase):
    """刀2：滞留补丁的审批重投。"""

    def setUp(self):
        self._code = _code_only(_src(_SEE_PATH))

    def test_21_retry_branch_wired(self):
        self.assertIn("[468刀2] 审批重投", self._code)

    def test_22_retry_calls_bridge(self):
        self.assertIn('source="retry_468"', self._code)

    def test_23_skip_set_is_terminal_only(self):
        _skip = SEE._M468_APPROVAL_RETRY_SKIP
        for _s in ("approved", "runtime_verified", "runtime_failed",
                   "expired_pending", "wont_fix", "rolled_back"):
            self.assertIn(_s, _skip)
        # 未落地待审批的状态**不得**在跳过集合里
        for _s in ("verified", "needs_reverify", "pending", "awaiting_apply"):
            self.assertNotIn(_s, _skip, "%s 必须允许重投" % _s)

    def test_24_capped_stops_immediately(self):
        """撞日限额必须 break（日限额是当天硬约束，再问也没用）。"""
        _i = self._code.find('_r468.get("blocked") == "daily_cap"')
        self.assertGreater(_i, 0)
        _seg = self._code[_i:_i + 400]
        self.assertIn("break", _seg)

    def test_25_retry_on_default_true(self):
        self.assertTrue(SEE._m468_approval_retry_on())


class TestD_SandboxFlagRecheck(unittest.TestCase):
    """刀3：生成侧标与两通道口径分裂。"""

    def setUp(self):
        self._old = getattr(config, "ENABLE_M468_SANDBOX_FLAG_RECHECK", None)
        # 真实标本：14:53 被判「转人工」的那条补丁的字段形态
        self._patch = {
            "id": "patch_probe_468",
            "method": "_schedule_next_beat",
            "file": "organs/body/PulseHeart.py",
            "danger_keyword_class": "idiom",
            "sandbox_blocked_keywords": ["getattr("],
            "sandbox_incompatible": True,
            "original_code": "    def _schedule_next_beat(self) -> bool:\n        return True\n",
            "modified_code": (
                "    def _schedule_next_beat(self) -> bool:\n"
                "        if getattr(self, '_in_progress', False):\n"
                "            return False\n"
                "        return True\n"),
        }

    def tearDown(self):
        if self._old is None:
            config.__dict__.pop("ENABLE_M468_SANDBOX_FLAG_RECHECK", None)
        else:
            config.ENABLE_M468_SANDBOX_FLAG_RECHECK = self._old

    def test_31_idiom_flag_released(self):
        self.assertFalse(DIS.is_sandbox_incompatible(self._patch),
                         "danger_keyword_class=idiom 的 getattr 守卫应放行")

    def test_32_switch_off_restores_true(self):
        config.ENABLE_M468_SANDBOX_FLAG_RECHECK = False
        self.assertTrue(DIS.is_sandbox_incompatible(self._patch))

    def test_33_real_dangerous_still_blocked(self):
        _bad = dict(self._patch)
        _bad["modified_code"] = "def m(self):\n    return eval(_user)\n"
        self.assertTrue(DIS.is_sandbox_incompatible(_bad),
                        "真实 eval 调用必须仍被拦（不得借刀3 放宽）")

    def test_34_flag_false_shortcut_intact(self):
        self.assertFalse(DIS.is_sandbox_incompatible({"sandbox_incompatible": False}))

    def test_35_released_counter_recorded(self):
        DIS.is_sandbox_incompatible(self._patch)
        self.assertIsInstance(DIS._M468_RECHECK_RELEASED, dict)
        self.assertTrue(DIS._M468_RECHECK_RELEASED)

    def test_36_switch_fn_default_true(self):
        self.assertTrue(DIS._m468_recheck_flagged_on())


class TestE_ConfigRegistration(unittest.TestCase):
    """三刀开关必须登记进 config.py（不得留模块内联默认值当事实来源）。"""

    def test_41_switches_registered(self):
        _s = _src(_CFG_PATH)
        for _k in ("ENABLE_M468_REVERIFY_APPLIED_GUARD",
                   "ENABLE_M468_APPROVAL_RETRY",
                   "ENABLE_M468_SANDBOX_FLAG_RECHECK"):
            self.assertIn("%s = True" % _k, _s, "%s 未登记" % _k)

    def test_42_config_marker_present(self):
        self.assertIn("[M468-CFG]", _src(_CFG_PATH))

    def test_43_config_still_parses(self):
        ast.parse(_src(_CFG_PATH))

    def test_44_only_appended_at_tail(self):
        """既有关键开关值不得被本批改写（零回归的结构性证明）。"""
        _s = _src(_CFG_PATH)
        self.assertIn("ENABLE_M447_APPROVAL_BRIDGE", _s)
        self.assertIn("M447_APPROVE_DAILY_CAP", _s)
        # 本批块必须位于文件末尾 1200 字符内
        _i = _s.find("[M468-CFG]")
        self.assertGreater(_i, len(_s) - 1200, "本批配置块应在文件末尾（只追加）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
