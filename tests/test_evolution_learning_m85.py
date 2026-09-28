# -*- coding: utf-8 -*-
"""第85批门控单测：本地学习尝试通道 / reprobe 接线 / real_fix_rate 口径 / 本地自动应用。

覆盖：
  M85-1  T-85a SafeEvolutionExecutor 本地学习尝试通道（保守修复 + 学习日志 + 不自动应用）
  M85-2  T-85b PatchManager.apply_all_pending 应用后运行时 active_reprobe 接线
  M85-3  T-85c patch_verification_split 的 real_fix_rate 分母改为「可判定补丁数」
  M85-4  T-85d 本地低风险补丁自动应用（source/confidence/risk_level 三条件）

★行尾：本文件 LF（与 tests/ 惯例一致）。
★先红后绿：改前上述符号/行为均不存在或行为相反 → 红。
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.evolution import patch_verification_split as PVS  # noqa: E402
from nucleus.reasoning.PatchManager import PatchManager  # noqa: E402
from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_SEE = os.path.join(_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
SRC_PM = os.path.join(_ROOT, "nucleus", "reasoning", "PatchManager.py")


def _src(path):
    with io.open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def _code_only(text):
    """剥离整行注释后再计数（注释里的示例符号不是调用点，铁律85）。"""
    return "\n".join(
        ln for ln in text.split("\n") if not ln.lstrip().startswith("#"))


# ===========================================================================
# M85-1  T-85a 本地学习尝试通道
# ===========================================================================
class TestM85ConservativeFix(unittest.TestCase):
    """保守修复策略（纯函数，直接调真实实现）。"""

    def test_01_except_log_adds_logging(self):
        _code = (
            "def f(self):\n"
            "    try:\n"
            "        g()\n"
            "    except Exception:\n"
            "        pass\n"
        )
        _strat, _mod = SafeEvolutionExecutor._m85_conservative_fix("long_method", _code)
        self.assertTrue(_strat, "bare except 应命中 except_log 策略")
        self.assertNotEqual(_mod, _code)
        self.assertIn("self._log(", _mod)
        self.assertIn("except", _mod)

    def test_02_http_timeout_added(self):
        _code = (
            "def f(self):\n"
            "    r = requests.get('http://x')\n"
            "    return r\n"
        )
        _strat, _mod = SafeEvolutionExecutor._m85_conservative_fix("ERROR", _code)
        self.assertTrue(_strat, "无 timeout 的 http 调用应命中 http_timeout 策略")
        self.assertIn("timeout=", _mod)
        self.assertIn("requests.get(", _mod)

    def test_03_existing_timeout_untouched(self):
        _code = "def f(self):\n    return requests.get('http://x', timeout=5)\n"
        _strat, _mod = SafeEvolutionExecutor._m85_conservative_fix("ERROR", _code)
        self.assertFalse(_strat, "已有 timeout 不应再改写")
        self.assertEqual(_mod, _code)

    def test_04_safe_code_no_fix(self):
        _code = "def f(self):\n    return 1 + 1\n"
        _strat, _mod = SafeEvolutionExecutor._m85_conservative_fix("long_method", _code)
        self.assertFalse(_strat, "无风险模式应返回 no_safe_fix")
        self.assertEqual(_mod, _code)

    def test_05_never_returns_broken_python(self):
        """改写结果必须仍是合法 Python（结构性安全断言）。"""
        import ast
        for _t, _c in [
            ("long_method", "def f(self):\n    try:\n        g()\n    except:\n        pass\n"),
            ("ERROR", "def f(self):\n    return requests.post('http://x', data={})\n"),
        ]:
            _s, _m = SafeEvolutionExecutor._m85_conservative_fix(_t, _c)
            if _s:
                ast.parse(_m)  # 不抛 SyntaxError 即通过


class TestM85LearningAttempt(unittest.TestCase):
    """通道主流程：入队 + 学习记录 + 不自动应用 + 灰度开关。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m85_learn_")
        self.executor = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        self.executor._project_root = self.tmp
        self.calls = {"verify": 0, "enqueue": 0, "last_patch": None}
        self._verify_pass = True
        self._executor_self = self.executor

        class _FakePM:
            def __init__(self, outer):
                self._outer = outer

            def has_pending_patch_for(self, *_a):
                return False

            def verify_in_copy(self, patch):
                self._outer.calls["verify"] += 1
                return {"passed": self._outer._verify_pass, "reason": "fake"}

            def save_pending_patch(self, patch):
                self._outer.calls["enqueue"] += 1
                self._outer.calls["last_patch"] = dict(patch)

        self.executor._patch_manager = _FakePM(self)
        # 记录器路径由 _project_root 推导 → 落在沙箱
        self.jsonl = os.path.join(self.tmp, "data", "patches",
                                  "local_learning_attempts.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _issue(self, _type="long_method"):
        return {"type": _type, "organ": "PulseLiver", "method": "f",
                "file": os.path.join(self.tmp, "x.py"), "description": "d"}

    def test_10_patch_enqueued_with_learning_mark(self):
        _code = ("def f(self):\n    try:\n        g()\n"
                 "    except Exception:\n        pass\n")
        _r = self.executor._m85_learning_attempt(
            self._issue(), os.path.join(self.tmp, "x.py"), "f", _code, "PulseLiver")
        self.assertEqual(_r.get("result"), "success")
        self.assertEqual(self.calls["enqueue"], 1, "验证通过应入队")
        _p = self.calls["last_patch"]
        self.assertTrue(_p.get("learning_attempt"), "补丁必须带 learning_attempt 标记")
        self.assertEqual(_p.get("source"), "local_learning")
        self.assertEqual(_p.get("risk_level"), "低")
        self.assertEqual(_p.get("confidence"), "high")

    def test_11_learning_patch_not_auto_applyable(self):
        """学习尝试补丁不得命中 T-85d 的自动应用条件（默认不自动应用）。"""
        _code = ("def f(self):\n    try:\n        g()\n"
                 "    except Exception:\n        pass\n")
        self.executor._m85_learning_attempt(
            self._issue(), os.path.join(self.tmp, "x.py"), "f", _code, "PulseLiver")
        _p = self.calls["last_patch"]
        self.assertFalse(
            PatchManager._m85_local_low_risk_auto_apply(_p),
            "学习尝试补丁必须仍走人工审批")

    def test_12_jsonl_recorded_each_attempt(self):
        _code = ("def f(self):\n    try:\n        g()\n"
                 "    except Exception:\n        pass\n")
        for _ in range(2):
            self.executor._m85_learning_attempt(
                self._issue(), os.path.join(self.tmp, "x.py"), "f", _code, "PulseLiver")
        self.assertTrue(os.path.isfile(self.jsonl), "学习日志必须落盘")
        with io.open(self.jsonl, encoding="utf-8") as fh:
            _rows = [json.loads(x) for x in fh if x.strip()]
        self.assertEqual(len(_rows), 2)
        _r = _rows[0]
        for _f in ("ts", "issue_type", "file", "method", "strategy", "result",
                   "llm_fallback"):
            self.assertIn(_f, _r, "学习记录缺字段 %s" % _f)
        self.assertEqual(_r["issue_type"], "long_method")
        self.assertEqual(_r["result"], "success")
        self.assertFalse(_r["llm_fallback"])

    def test_13_no_safe_fix_records_fallback(self):
        _r = self.executor._m85_learning_attempt(
            self._issue("Traceback"), os.path.join(self.tmp, "x.py"), "f",
            "def f(self):\n    return 1\n", "PulseLiver")
        self.assertEqual(_r.get("result"), "no_safe_fix")
        self.assertEqual(self.calls["enqueue"], 0, "无安全修复不得入队")
        with io.open(self.jsonl, encoding="utf-8") as fh:
            _r0 = json.loads(fh.readline())
        self.assertEqual(_r0["result"], "no_safe_fix")
        self.assertTrue(_r0["llm_fallback"], "无安全修复应记 llm_fallback=True")

    def test_14_verify_failed_records_failed(self):
        self._verify_pass = False
        _code = ("def f(self):\n    try:\n        g()\n"
                 "    except Exception:\n        pass\n")
        _r = self.executor._m85_learning_attempt(
            self._issue(), os.path.join(self.tmp, "x.py"), "f", _code, "PulseLiver")
        self.assertEqual(_r.get("result"), "failed")
        self.assertEqual(self.calls["enqueue"], 0, "验证未通过不得入队")
        with io.open(self.jsonl, encoding="utf-8") as fh:
            _r0 = json.loads(fh.readline())
        self.assertEqual(_r0["result"], "failed")
        self.assertTrue(_r0["llm_fallback"])

    def test_15_switch_off_zero_side_effect(self):
        self.executor._m85_learning_attempt_enabled = lambda: False
        _code = ("def f(self):\n    try:\n        g()\n"
                 "    except Exception:\n        pass\n")
        _r = self.executor._m85_learning_attempt(
            self._issue(), os.path.join(self.tmp, "x.py"), "f", _code, "PulseLiver")
        self.assertEqual(_r.get("result"), "disabled")
        self.assertEqual(self.calls["enqueue"], 0)
        self.assertEqual(self.calls["verify"], 0)
        self.assertFalse(os.path.exists(self.jsonl), "开关关闭不得写学习日志")

    def test_16_switch_default_on_without_config(self):
        """未配置时默认 True（任务书要求生效；不改 config.py）。"""
        _e = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        self.assertTrue(_e._m85_learning_attempt_enabled())

    def test_17_exception_never_escapes(self):
        """任何内部异常都不得逃出（不阻断进化主循环）。"""
        class _Boom:
            def has_pending_patch_for(self, *_a):
                raise RuntimeError("boom")

            def verify_in_copy(self, *_a):
                raise RuntimeError("boom")

            def save_pending_patch(self, *_a):
                raise RuntimeError("boom")

        self.executor._patch_manager = _Boom()
        _code = ("def f(self):\n    try:\n        g()\n"
                 "    except Exception:\n        pass\n")
        try:
            self.executor._m85_learning_attempt(
                self._issue(), os.path.join(self.tmp, "x.py"), "f", _code, "PulseLiver")
        except Exception as e:  # pragma: no cover
            self.fail("异常逃出学习通道: %s: %s" % (type(e).__name__, e))

    def test_18_wired_into_main_loop(self):
        """源码级接线断言：主循环内确实调用了学习通道。"""
        _c = _code_only(_src(SRC_SEE))
        self.assertIn("self._m85_learning_attempt(", _c,
                      "M85-1 未接线到 _process_issues 主循环")


# ===========================================================================
# M85-2  T-85b 运行时 reprobe 接线
# ===========================================================================
class TestM85ReprobeWiring(unittest.TestCase):

    def test_20_reprobe_called_after_apply(self):
        """应用成功分支（applied=True）之后、落 history 之前必须触发 reprobe。"""
        _c = _code_only(_src(SRC_PM))
        self.assertIn("apply_reprobe", _c, "M85-2 未接线 active_reprobe")
        _i_apply = _c.find('patch["applied"] = True')
        _i_rp = _c.find("apply_reprobe")
        _i_hist = _c.find('history.append(patch)', _i_apply)
        self.assertGreater(_i_apply, 0, "未找到 applied=True 写入点")
        self.assertGreater(_i_rp, _i_apply, "reprobe 必须在 applied=True 之后")
        self.assertGreater(_i_hist, _i_rp,
                           "reprobe 必须在落 history 之前（否则不落盘）")

    def test_21_reprobe_not_blocking(self):
        """假件抛异常时，应用判定不得受影响。"""
        self.assertTrue(callable(
            __import__("nucleus.evolution.patch_active_reprobe",
                       fromlist=["apply_reprobe"]).apply_reprobe))
        # 结构性断言：调用点被 try/except 包裹
        _c = _code_only(_src(SRC_PM))
        _i = _c.find("apply_reprobe")
        _win = _c[max(0, _i - 400):_i + 900]
        self.assertIn("try:", _win, "reprobe 调用点必须包 try（不阻塞主流程）")
        self.assertIn("except Exception", _win)

    def test_22_apply_reprobe_writes_verdict_on_real_patch(self):
        """真实调用 → patch 获得 reprobe_verdict（不再恒 null）。"""
        from nucleus.evolution.patch_active_reprobe import apply_reprobe
        _p = {
            "issue_type": "silent_exception",
            "original_code": ("def f(self):\n    try:\n        g()\n"
                              "    except Exception:\n        pass\n"),
            "modified_code": ("def f(self):\n    try:\n        g()\n"
                              "    except Exception as e:\n"
                              "        self._log(LogLevel.ERROR, f'异常: {e}')\n"),
        }
        _r = apply_reprobe(_p)
        self.assertIn("reprobe_verdict", _p, "apply_reprobe 必须原地写入 verdict")
        self.assertEqual(_p.get("reprobe_verdict"), _r.get("reprobe_verdict"))
        self.assertNotIn(_p.get("reprobe_verdict"),
                         (None, ""), "verdict 不得为空")

    def test_23_verdict_feeds_problem_fixed(self):
        """接线价值断言：写入 verdict 后 split 的 problem_fixed 变为可判定。"""
        _p = {
            "issue_type": "silent_exception",
            "original_code": ("def f(self):\n    try:\n        g()\n"
                              "    except Exception:\n        pass\n"),
            "modified_code": ("def f(self):\n    try:\n        g()\n"
                              "    except Exception as e:\n"
                              "        self._log(LogLevel.ERROR, f'异常: {e}')\n"),
        }
        self.assertIsNone(PVS.split_verification(_p)["problem_fixed"],
                          "无 verdict 时应为 None（基线 0 不可判定）")
        from nucleus.evolution.patch_active_reprobe import apply_reprobe
        apply_reprobe(_p)
        self.assertIsNotNone(PVS.split_verification(_p)["problem_fixed"],
                             "有 verdict 后 problem_fixed 必须可判定")


# ===========================================================================
# M85-3  T-85c real_fix_rate 口径
# ===========================================================================
class TestM85RealFixRateDenominator(unittest.TestCase):

    @staticmethod
    def _build():
        """构造 64 条：1 条修好(True) / 1 条未修好(False) / 62 条不可判定(None)。

        ★注意 backfill() 是**重算**语义（不读预置的 problem_fixed），
        故必须用 baseline/post_apply 组合让 split_verification 自然产出三态。
        """
        _ps = [
            {"issue_type": "x", "baseline_errors": 10, "post_apply_errors": 2},
            {"issue_type": "x", "baseline_errors": 10, "post_apply_errors": 10},
        ]
        for _ in range(62):
            _ps.append({"issue_type": "x"})
        return _ps

    def test_30_backfill_rate_uses_verifiable_denominator(self):
        _r = PVS.backfill(self._build(), apply=False)
        self.assertEqual(_r["total"], 64)
        self.assertEqual(_r["problem_fixed"]["true"], 1)
        self.assertEqual(_r["problem_fixed"]["false"], 1)
        self.assertAlmostEqual(_r["real_fix_rate"], 0.5, places=4,
                               msg="分母应为可判定数(2)，而非总数(64)")
        self.assertEqual(_r["verifiable_rate"], 0.0312)   # round(2/64, 4)
        self.assertEqual(_r["verifiable_count"], 2)

    def test_31_function_rate_uses_verifiable_denominator(self):
        _ps = [{"problem_fixed": True}, {"problem_fixed": False},
               {"problem_fixed": None}, {"problem_fixed": None}]
        self.assertAlmostEqual(PVS.real_fix_rate(_ps), 0.5, places=4,
                               msg="分母应为可判定数(2)")

    def test_32_all_none_is_zero_not_crash(self):
        _ps = [{"problem_fixed": None} for _ in range(5)]
        self.assertEqual(PVS.real_fix_rate(_ps), 0.0)
        _r = PVS.backfill(_ps, apply=False)
        self.assertEqual(_r["real_fix_rate"], 0.0)
        self.assertEqual(_r["verifiable_rate"], 0.0)

    def test_33_no_regression_unchanged(self):
        """口径调整不得影响其他字段（回归护栏）。"""
        _r = PVS.backfill(self._build(), apply=False)
        self.assertIn("old_claimed_rate", _r)
        self.assertIn("granularity_dist", _r)
        self.assertEqual(_r["no_regression"]["none"], 64)


# ===========================================================================
# M85-4  T-85d 本地低风险补丁自动应用
# ===========================================================================
class TestM85LocalAutoApply(unittest.TestCase):

    @staticmethod
    def _p(**kw):
        _b = {"file": "organs/body/PulseLiver.py", "method": "f",
              "source": "local_rule", "confidence": "high", "risk_level": "低"}
        _b.update(kw)
        return _b

    def test_40_local_low_risk_blocked_by_default(self):
        """★第96批 T-96b（N1-① 烛微第1期）：开关显式登记 config 且默认 False。

        原用例断言「缺省放行」。第96批把 ``local_auto_apply_enabled``
        显式登记为 False（此前**从未登记**，读取端兜底 True ⇒ 事实上默认开启，
        与本文件 :952 ``auto_apply_enabled=False`` 的安全语义矛盾）⇒ 缺省
        下非核心 local_rule 补丁**回到需人工审批**。
        ★同时保留「显式置 True 仍放行」⇒ 证明是**收开关**而非删除能力。
        """
        self.assertFalse(
            PatchManager._m85_local_low_risk_auto_apply(self._p()),
            "缺省（config 已登记 False）下不得自动放行")
        self.assertTrue(
            PatchManager._m85_local_low_risk_auto_apply(
                self._p(), {"local_auto_apply_enabled": True}),
            "显式开启后仍须放行（能力保留）")

    def test_41_llm_source_not_allowed(self):
        self.assertFalse(
            PatchManager._m85_local_low_risk_auto_apply(self._p(source="llm")),
            "LLM 补丁即使 auto_approved=true 也必须人工审批")

    def test_42_medium_confidence_not_allowed(self):
        self.assertFalse(
            PatchManager._m85_local_low_risk_auto_apply(self._p(confidence="medium")))

    def test_43_medium_risk_not_allowed(self):
        self.assertFalse(
            PatchManager._m85_local_low_risk_auto_apply(self._p(risk_level="中等")))

    def test_44_core_file_not_allowed(self):
        self.assertFalse(PatchManager._m85_local_low_risk_auto_apply(
            self._p(file="nucleus/reasoning/PatchManager.py")),
            "核心文件禁止自动应用（既有红线不得被本批绕过）")

    def test_45_learning_attempt_not_allowed(self):
        self.assertFalse(PatchManager._m85_local_low_risk_auto_apply(
            self._p(source="local_learning", learning_attempt=True)),
            "学习尝试补丁默认不自动应用")

    def test_46_switch_off_not_allowed(self):
        self.assertFalse(PatchManager._m85_local_low_risk_auto_apply(
            self._p(), {"local_auto_apply_enabled": False}))

    def test_47_wired_into_save_pending(self):
        """源码级接线断言：入队闸门确实调用了新判据。"""
        _c = _code_only(_src(SRC_PM))
        self.assertIn("_m85_local_low_risk_auto_apply", _c, "M85-4 未接线")
        self.assertGreaterEqual(_c.count("_m85_local_low_risk_auto_apply"), 2,
                                "判据需同时接入风险闸门与免签闸门")
        self.assertIn("def _m85_local_low_risk_auto_apply", _c)

    def test_48_auto_apply_enabled_semantics_unchanged(self):
        """既有总开关语义不得被本批改动（回归护栏）。"""
        self.assertFalse(PatchManager._m80_auto_apply_enabled({}))
        self.assertTrue(PatchManager._m80_auto_apply_enabled(
            {"auto_apply_enabled": True}))
        self.assertTrue(PatchManager._m80_gate_blocked(
            self._p(), {}), "总开关关闭时既有闸门必须仍为 blocked")


if __name__ == "__main__":
    unittest.main(verbosity=2)
