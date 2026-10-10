# -*- coding: utf-8 -*-
"""主线第42批 T2（P0-250）门控测试：补丁质量评估器。

覆盖：效果重算判据 / 假通过识别 / 标签与评分 / 汇总与结论 / IO 防御 / 接线。
★ 全部使用构造数据 + 隔离目录，不依赖生产 patch_history 的具体内容。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import tempfile
import unittest

import config
from nucleus.evolution.patch_quality_evaluator import (
    DEFAULT_REPORT_PATH,
    LABELS,
    WEIGHTS,
    evaluate_and_report,
    evaluate_history,
    evaluate_patch,
    evaluator_enabled,
    format_summary_line,
    load_history,
    real_effectiveness,
    save_report,
    summarize,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def _pytest_tmp_root():
    """★主线第46批 T4（P2-301/307）：测试隔离目录改用【系统临时目录】。

    背景：旧实现 ``mkdtemp(prefix=..., dir=<项目>/tmp)`` 会在项目 ``tmp/`` 下
    持续留下隔离目录（``m41_t2_*`` / ``m43dq_*`` / ``_m27_*`` ...），tearDown
    清不干净时就变成"历史残留"，并与清理工具互相打架。

    现在改为系统临时目录下的 ``pulse_pytest/``：
      * 不污染项目 ``tmp/`` → 清理判据不再需要为它们开特例
      * 由操作系统回收 → 残留不再累积
      * 可用环境变量 ``PULSE_TEST_TMP_ROOT`` 覆盖（调试/隔离用）
    """
    _env = os.environ.get("PULSE_TEST_TMP_ROOT")
    if _env:
        _d = _env
    else:
        # ★与项目同盘：Windows 跨盘 shutil.move 会 copy+unlink →
        #   触发沙箱删除配额（m41 实测教训）。故不用 tempfile.gettempdir()，
        #   改用项目根下的 .pytest_tmp/（同盘 + 不污染 tmp/）。
        _d = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".pytest_tmp")
    os.makedirs(_d, exist_ok=True)
    return _d


_TMP_ROOT = _pytest_tmp_root()


def _safe_rmtree(path):
    if not os.path.isdir(path):
        return
    for dp, dn, fn in os.walk(path, topdown=False):
        for i in range(0, len(fn), 50):
            for f in fn[i:i + 50]:
                try:
                    os.remove(os.path.join(dp, f))
                except BaseException as e:
                    print("cleanup warn: {}: {}".format(type(e).__name__, e))
        try:
            os.rmdir(dp)
        except BaseException as e:
            print("cleanup warn: {}: {}".format(type(e).__name__, e))


def _patch(**kw):
    """构造最小补丁记录。"""
    base = {"id": "p1", "file": "a/b.py", "method": "m", "applied": True}
    base.update(kw)
    return base


# ==================================================================== 效果重算
class TestRealEffectiveness(unittest.TestCase):
    def test_01_positive(self):
        self.assertAlmostEqual(real_effectiveness(10, 2), 0.8)

    def test_02_zero_when_equal(self):
        self.assertEqual(real_effectiveness(4, 4), 0.0)

    def test_03_negative_on_regression(self):
        self.assertLess(real_effectiveness(4, 8), 0)

    def test_04_no_baseline_is_none(self):
        """★核心修正：0/0 必须不可判定，绝不返回 1.0。"""
        self.assertIsNone(real_effectiveness(0, 0))

    def test_05_no_baseline_with_new_errors(self):
        self.assertEqual(real_effectiveness(0, 5), -1.0)

    def test_06_missing_baseline(self):
        self.assertIsNone(real_effectiveness(None, 3))
        self.assertIsNone(real_effectiveness(None, None))

    def test_07_rejects_bool(self):
        self.assertIsNone(real_effectiveness(True, False))


# ==================================================================== 单条评估
class TestEvaluatePatch(unittest.TestCase):
    def test_10_fake_pass_detected(self):
        """★任务书要求：能识别 eff=0.00 的假通过补丁（PulseLiver 形态）。"""
        _p = _patch(baseline_errors=4, post_apply_errors=4,
                    runtime_verified=True,
                    runtime_verify_result={"verified": True, "baseline": 4,
                                           "after_fix": 4, "effectiveness": 0.0,
                                           "detail": "修复前错误=4, 修复后错误=4"})
        _r = evaluate_patch(_p)
        self.assertEqual(_r["label"], "fake_pass")
        self.assertEqual(_r["real_effectiveness"], 0.0)
        self.assertEqual(_r["claimed_effectiveness"], 0.0)
        # 60 = 验证/回归/稳定 满分但**效果维为 0**；假通过不得进入良好区间
        self.assertLess(_r["score"], 70)

    def test_11_fake_pass_on_regression(self):
        """无基线却出现错误 ⇒ 真实效果 -1，也是假通过（且回归维扣 0）。"""
        _p = _patch(baseline_errors=0, post_apply_errors=4,
                    runtime_verified=True,
                    runtime_verify_result={"verified": True, "baseline": 0,
                                           "after_fix": 4, "effectiveness": 0.5,
                                           "detail": "x"})
        _r = evaluate_patch(_p)
        self.assertEqual(_r["label"], "fake_pass")
        self.assertEqual(_r["real_effectiveness"], -1.0)
        self.assertEqual(_r["scores"]["regression"], 0.0)
        self.assertEqual(_r["scores"]["effectiveness"], 0.0)

    def test_12_unverifiable_when_no_baseline(self):
        _p = _patch(baseline_errors=0, post_apply_errors=0,
                    runtime_verified=True,
                    runtime_verify_result={"verified": True, "baseline": 0,
                                           "after_fix": 0, "effectiveness": 1.0,
                                           "detail": "修复前错误=0, 修复后错误=0"})
        _r = evaluate_patch(_p)
        self.assertEqual(_r["label"], "unverifiable")
        self.assertIsNone(_r["real_effectiveness"])
        self.assertEqual(_r["claimed_effectiveness"], 1.0)     # 补丁自称满分
        # 不可验证 → 效果维只能拿中性分，绝不能拿满分
        self.assertEqual(_r["scores"]["effectiveness"], 20.0)

    def test_13_not_applied(self):
        _p = _patch(applied=False, reason="信任分数不足")
        _r = evaluate_patch(_p)
        self.assertEqual(_r["label"], "not_applied")

    def test_14_good_label(self):
        _p = _patch(baseline_errors=10, post_apply_errors=0,
                    runtime_verified=True,
                    runtime_verify_result={"verified": True, "baseline": 10,
                                           "after_fix": 0, "effectiveness": 1.0,
                                           "detail": "ok"})
        _r = evaluate_patch(_p)
        self.assertEqual(_r["label"], "good")
        self.assertGreaterEqual(_r["score"], 75.0)
        self.assertAlmostEqual(_r["real_effectiveness"], 1.0)

    def test_15_mediocre_range(self):
        # 真实效果 0.1 → 效果维 22；无 detail → 验证 15；无回归 20；单次 15 → 72
        _p = _patch(baseline_errors=10, post_apply_errors=9,
                    runtime_verified=True,
                    runtime_verify_result={"verified": True, "baseline": 10,
                                           "after_fix": 9, "effectiveness": 0.1})
        _r = evaluate_patch(_p)
        self.assertEqual(_r["label"], "mediocre")
        self.assertGreaterEqual(_r["score"], 50.0)
        self.assertLess(_r["score"], 75.0)

    def test_16_score_is_sum_of_dimensions(self):
        _p = _patch(baseline_errors=10, post_apply_errors=2, runtime_verified=True,
                    runtime_verify_result={"verified": True, "detail": "d"})
        _r = evaluate_patch(_p)
        _s = _r["scores"]
        self.assertAlmostEqual(_r["score"], round(sum(_s.values()), 2))
        self.assertEqual(set(_s), set(WEIGHTS))

    def test_17_non_dict_is_safe(self):
        for bad in (None, "x", 42, []):
            _r = evaluate_patch(bad)
            self.assertEqual(_r["label"], "bad")
            self.assertEqual(_r["score"], 0.0)

    def test_18_stability_decreases_with_repeats(self):
        _p = _patch(runtime_verified=True, baseline_errors=1, post_apply_errors=0,
                    runtime_verify_result={"verified": True, "detail": "d"})
        _r1 = evaluate_patch(_p, repeats=1)
        _r3 = evaluate_patch(_p, repeats=3)
        self.assertGreater(_r1["scores"]["stability"], _r3["scores"]["stability"])
        self.assertEqual(_r3["scores"]["stability"], 0.0)


# ==================================================================== 全量评估
class TestEvaluateHistory(unittest.TestCase):
    def _hist(self):
        return [
            _patch(id="a", baseline_errors=0, post_apply_errors=0, runtime_verified=True,
                   runtime_verify_result={"verified": True, "baseline": 0, "after_fix": 0,
                                          "effectiveness": 1.0, "detail": "d"}),
            _patch(id="b", baseline_errors=4, post_apply_errors=4, runtime_verified=True,
                   runtime_verify_result={"verified": True, "baseline": 4, "after_fix": 4,
                                          "effectiveness": 0.0, "detail": "d"}),
            _patch(id="c", applied=False),
            _patch(id="d", file="x/y.py", method="n", baseline_errors=10, post_apply_errors=1,
                   runtime_verified=True,
                   runtime_verify_result={"verified": True, "detail": "d"}),
        ]

    def test_20_counts(self):
        _s = evaluate_history(self._hist())["summary"]
        self.assertEqual(_s["unverifiable"], 1)
        self.assertEqual(_s["fake_pass"], 1)
        self.assertEqual(_s["not_applied"], 1)
        self.assertEqual(_s["good"], 1)
        self.assertEqual(_s["total"], 4)
        self.assertEqual(_s["real_effectiveness_computable"], 2)

    def test_21_patches_all_scored(self):
        _ev = evaluate_history(self._hist())
        self.assertEqual(len(_ev["patches"]), 4)
        for _x in _ev["patches"]:
            self.assertIsInstance(_x["score"], float)
            self.assertIn(_x["label"], LABELS)

    def test_22_sorted_by_score_asc(self):
        _sc = [x["score"] for x in evaluate_history(self._hist())["patches"]]
        self.assertEqual(_sc, sorted(_sc))

    def test_23_findings_mention_fake_pass(self):
        _ev = evaluate_history(self._hist())
        _txt = " ".join(_ev["findings"])
        self.assertIn("假通过", _txt)
        self.assertIn("无法验证", _txt)

    def test_24_repeats_tracked(self):
        _h = [_patch(id="a", runtime_verified=True, baseline_errors=1, post_apply_errors=0,
                     runtime_verify_result={"verified": True, "detail": "d"}),
              _patch(id="b", runtime_verified=True, baseline_errors=1, post_apply_errors=0,
                     runtime_verify_result={"verified": True, "detail": "d"})]
        _ev = evaluate_history(_h)
        self.assertTrue(all(x["repeats"] == 2 for x in _ev["patches"]))
        self.assertIn("重复修复", " ".join(_ev["findings"]))

    def test_25_empty_and_bad_input(self):
        for bad in ([], None, "x", 3):
            _ev = evaluate_history(bad)
            self.assertEqual(_ev["patches"], [])
            self.assertEqual(_ev["summary"]["total"], 0)

    def test_26_summarize_empty(self):
        _s = summarize([])
        for _k in LABELS:
            self.assertEqual(_s[_k], 0)
        self.assertEqual(_s["avg_score"], 0.0)

    def test_27_summary_line_format(self):
        _line = format_summary_line(evaluate_history(self._hist())["summary"])
        self.assertIn("[补丁评估]", _line)
        for _k in ("总补丁=", "优质=", "中等=", "劣质=", "假通过="):
            self.assertIn(_k, _line)


# ==================================================================== IO
class TestIO(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="m42q_", dir=_TMP_ROOT)

    def tearDown(self):
        _safe_rmtree(self.dir)

    def test_30_save_report_rejects_production_in_test_env(self):
        """★pytest 环境 + 默认（生产）路径 → 拒写。"""
        self.assertIsNone(save_report({"x": 1}))
        _prod = os.path.abspath(DEFAULT_REPORT_PATH).replace("\\", "/").lower()
        _root = os.path.abspath(_ROOT).replace("\\", "/").lower()
        self.assertTrue(_prod.startswith(_root + "/data/"), "默认路径必须在生产 data/ 下")

    def test_31_save_report_explicit_path_writes(self):
        _p = os.path.join(self.dir, "r.json")
        _out = save_report({"a": 1}, _p)
        self.assertEqual(_out, _p)
        self.assertEqual(json.load(open(_p, encoding="utf-8"))["a"], 1)

    def test_32_load_history_missing(self):
        self.assertEqual(load_history(os.path.join(self.dir, "nope.json")), [])

    def test_33_load_history_bad_json(self):
        _p = os.path.join(self.dir, "bad.json")
        open(_p, "w", encoding="utf-8").write("{not json")
        self.assertEqual(load_history(_p), [])

    def test_34_load_history_non_list(self):
        _p = os.path.join(self.dir, "d.json")
        json.dump({"a": 1}, open(_p, "w", encoding="utf-8"))
        self.assertEqual(load_history(_p), [])

    def test_35_evaluate_and_report_switch_off(self):
        _bak = config.ENABLE_PATCH_QUALITY_EVALUATOR
        try:
            config.ENABLE_PATCH_QUALITY_EVALUATOR = False
            self.assertFalse(evaluator_enabled())
            self.assertIsNone(evaluate_and_report())
        finally:
            config.ENABLE_PATCH_QUALITY_EVALUATOR = _bak

    def test_36_evaluate_and_report_with_explicit_paths(self):
        _h = os.path.join(self.dir, "h.json")
        json.dump([_patch(baseline_errors=4, post_apply_errors=4, runtime_verified=True,
                          runtime_verify_result={"verified": True, "detail": "d"})],
                  open(_h, "w", encoding="utf-8"))
        _out = os.path.join(self.dir, "out.json")
        _ev = evaluate_and_report(history_path=_h, report_path=_out)
        self.assertEqual(_ev["summary"]["fake_pass"], 1)
        self.assertEqual(_ev["report_path"], _out)
        self.assertTrue(os.path.isfile(_out))


# ==================================================================== 接线
class TestWiring(unittest.TestCase):
    def test_40_config_switches(self):
        self.assertTrue(hasattr(config, "ENABLE_PATCH_QUALITY_EVALUATOR"))
        self.assertTrue(hasattr(config, "ENABLE_EXPERIENCE_RETRIEVER_OBSERVE"))

    def test_41_daily_scheduler_calls_evaluator(self):
        _src = open(os.path.join(_ROOT, "nucleus", "self_awareness", "DailyScheduler.py"),
                    encoding="utf-8").read()
        self.assertIn("evaluate_and_report(", _src)
        self.assertIn("patch_quality", _src)

    def test_42_tools_entry_exists(self):
        self.assertTrue(os.path.isfile(
            os.path.join(_ROOT, "tools", "run_patch_quality_eval.py")))

    def test_43_readonly_no_write_in_patch_history(self):
        """评估器源码不得出现对 patch_history 的写操作。"""
        _src = open(os.path.join(_ROOT, "nucleus", "evolution",
                                 "patch_quality_evaluator.py"), encoding="utf-8").read()
        self.assertNotIn("patch_history.json\", \"w", _src)
        self.assertNotIn("HISTORY_PATH, \"w", _src)
        self.assertIn("DEFAULT_REPORT_PATH", _src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
