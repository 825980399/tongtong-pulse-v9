# -*- coding: utf-8 -*-
"""主线第43批 T1（P1-255）门控测试：LLM 调用留存数据质量评估器。

覆盖：纯度判定（测试污染识别）/ 五维度评分 / 剔除污染 / IO 防御 / 接线。
★ 全部使用构造记录 + 隔离目录，不依赖生产数据。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import tempfile
import unittest

import config
from nucleus.llm.data_quality_evaluator import (
    SCORE_DIMENSIONS,
    WEIGHTS,
    channel_whitelist,
    classify_record,
    evaluate_and_report,
    evaluate_day,
    evaluate_records,
    evaluator_enabled,
    format_summary_line,
    load_records,
    save_report,
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
                    print("cleanup warn: %s: %s" % (type(e).__name__, e))
        try:
            os.rmdir(dp)
        except BaseException as e:
            print("cleanup warn: %s: %s" % (type(e).__name__, e))


def _rec(i=0, prompt="正常提问内容ABC", response="正常回答内容XYZ", channel="zhipu",
         model="glm-4-flash", status="success", duration=1.2, tokens=10,
         origin="user_query", error="", pv="v1"):
    return {"trace_id": "m40-%012d" % i, "ts": 1789274000.0 + i, "origin": origin,
            "prompt": prompt, "response": response, "prompt_version": pv,
            "channel": channel, "model": model, "duration": duration,
            "tokens": tokens, "status": status, "error": error, "feedback": None}


_WL = {"zhipu", "deepseek", "ark-ds-v4-flash"}


# ================================================================ 纯度判定
class TestClassifyRecord(unittest.TestCase):
    def test_01_stub_channel_is_suspect(self):
        self.assertEqual(classify_record(_rec(channel="c1", model="m1"), _WL), "suspect")
        self.assertEqual(classify_record(_rec(channel="m2", model="m2"), _WL), "suspect")

    def test_02_unknown_channel_is_suspect(self):
        self.assertEqual(classify_record(_rec(channel="not-a-real-channel"), _WL), "suspect")

    def test_03_real_channel_is_production(self):
        self.assertEqual(classify_record(_rec(channel="zhipu"), _WL), "production")

    def test_04_stub_response_is_suspect(self):
        self.assertEqual(classify_record(_rec(response="模拟回复"), _WL), "suspect")
        self.assertEqual(classify_record(_rec(response="第二个渠道成功"), _WL), "suspect")

    def test_05_high_freq_short_prompt_is_suspect(self):
        """★实测依据：`hi` 出现 96 次、间隔中位数 0.00s。"""
        _rc = {"hi": 96}
        self.assertEqual(classify_record(_rec(prompt="hi"), _WL, _rc), "suspect")

    def test_06_low_freq_short_prompt_is_production(self):
        _rc = {"hi": 3}
        self.assertEqual(classify_record(_rec(prompt="hi"), _WL, _rc), "production")

    def test_07_high_freq_long_prompt_is_production(self):
        _p = "请将以下搜索意图分类为以下5类之一（只返回类别名）" * 2
        _rc = {_p: 50}
        self.assertEqual(classify_record(_rec(prompt=_p), _WL, _rc), "production")

    def test_08_empty_channel_and_model_is_unknown(self):
        self.assertEqual(classify_record(_rec(channel="", model=""), _WL), "unknown")

    def test_09_non_dict_is_unknown(self):
        for bad in (None, "x", 3, []):
            self.assertEqual(classify_record(bad, _WL), "unknown")

    def test_10_channel_whitelist_from_config(self):
        _wl = channel_whitelist()
        self.assertIsInstance(_wl, set)
        self.assertTrue(_wl, "应从 config 动态推导出非空渠道池")


# ================================================================ 五维度
class TestDimensions(unittest.TestCase):
    def _ev(self, recs, **kw):
        return evaluate_records(recs, **kw)

    def test_20_completeness_full(self):
        _r = self._ev([_rec(i) for i in range(5)])
        self.assertEqual(_r["dimensions"]["completeness"], WEIGHTS["completeness"])

    def test_21_completeness_penalised_by_empty(self):
        _recs = [_rec(i, prompt="", channel="") for i in range(5)]
        _r = self._ev(_recs)
        self.assertLess(_r["dimensions"]["completeness"], WEIGHTS["completeness"])

    def test_22_completeness_ignores_response_on_failed(self):
        """failed 无响应是预期 → 不应扣 completeness。"""
        _recs = [_rec(i, status="failed", response="") for i in range(5)]
        _r = self._ev(_recs)
        self.assertEqual(_r["dimensions"]["completeness"], WEIGHTS["completeness"])

    def test_23_uniqueness_all_unique(self):
        _r = self._ev([_rec(i, prompt="完全不同的问题%d" % i) for i in range(10)])
        self.assertEqual(_r["dimensions"]["uniqueness"], WEIGHTS["uniqueness"])

    def test_24_uniqueness_all_duplicate(self):
        # 10 条同 prompt → 唯一 1 条 → 冗余比 1-1/10 = 0.9 → 得分 20*(1-0.9) = 2.0
        _r = self._ev([_rec(i, prompt="完全相同的问题内容") for i in range(10)])
        self.assertLess(_r["dimensions"]["uniqueness"], 5.0)
        self.assertGreaterEqual(_r["dimensions"]["uniqueness"], 0.0)
        self.assertAlmostEqual(_r["details"]["uniqueness"]["redundancy"], 0.9, places=4)

    def test_25_sanitization_clean(self):
        _r = self._ev([_rec(i) for i in range(3)])
        self.assertEqual(_r["dimensions"]["sanitization"], WEIGHTS["sanitization"])
        self.assertEqual(_r["details"]["sanitization"]["hits"], 0)

    def test_26_sanitization_detects_secret(self):
        _recs = [_rec(0, prompt="key=sk-abcdefghijklmnop1234")]
        _r = self._ev(_recs)
        self.assertGreater(_r["details"]["sanitization"]["hits"], 0)
        self.assertLess(_r["dimensions"]["sanitization"], WEIGHTS["sanitization"])

    def test_27_diversity_single_channel_low(self):
        _recs = [_rec(i, channel="zhipu", model="glm-4-flash", origin="user_query")
                 for i in range(10)]
        _r = self._ev(_recs)
        self.assertLess(_r["dimensions"]["diversity"], WEIGHTS["diversity"])

    def test_28_volume_low_when_few_records(self):
        _r = self._ev([_rec(0)])
        self.assertLess(_r["dimensions"]["volume"], WEIGHTS["volume"])

    def test_29_score_is_sum_of_dimensions(self):
        _r = self._ev([_rec(i) for i in range(60)])
        self.assertAlmostEqual(_r["score"],
                               round(sum(_r["dimensions"][k] for k in SCORE_DIMENSIONS), 2))
        self.assertEqual(set(_r["dimensions"]), set(SCORE_DIMENSIONS))


# ================================================================ 剔除污染
class TestEvaluateRecords(unittest.TestCase):
    def _mixed(self):
        _out = [_rec(i) for i in range(20)]
        _out += [_rec(100 + i, prompt="hi", channel="c1", model="m1",
                      response="模拟回复") for i in range(10)]
        _out += [_rec(200 + i, prompt="hi", channel="zhipu", model="glm-4-flash",
                      response="") for i in range(10)]
        return _out

    def test_30_drop_suspect_excludes_pollution(self):
        _r = evaluate_records(self._mixed(), drop_suspect=True)
        self.assertEqual(_r["total_records"], 40)
        self.assertEqual(_r["purity"]["suspect"], 20)
        self.assertEqual(_r["scored_records"], 20)
        self.assertEqual(_r["scope"], "production_only")

    def test_31_drop_disabled_uses_all(self):
        _r = evaluate_records(self._mixed(), drop_suspect=False)
        self.assertEqual(_r["scored_records"], 40)
        self.assertEqual(_r["scope"], "all_records")

    def test_32_purity_stats(self):
        _r = evaluate_records(self._mixed())
        _p = _r["purity"]
        self.assertEqual(_p["production"] + _p["suspect"], _p["total"])
        self.assertAlmostEqual(_p["purity"], 0.5, places=2)
        self.assertTrue(_p["suspect_samples"])

    def test_33_findings_mention_purity(self):
        _r = evaluate_records(self._mixed())
        self.assertTrue(any("非生产记录" in f for f in _r["findings"]))

    def test_34_empty_records_safe(self):
        for bad in ([], None, "x"):
            _r = evaluate_records(bad)
            self.assertEqual(_r["total_records"], 0)
            self.assertEqual(_r["score"], 0.0)

    def test_35_summary_line_format(self):
        _line = format_summary_line(evaluate_records([_rec(i) for i in range(60)]))
        self.assertIn("[数据质量]", _line)
        for _k in ("评分=", "完整率=", "重复率=", "脱敏遗漏="):
            self.assertIn(_k, _line)


# ================================================================ IO
class TestIO(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="m43dq_", dir=_TMP_ROOT)

    def tearDown(self):
        _safe_rmtree(self.dir)

    def test_40_load_records_skips_bad_lines(self):
        _p = os.path.join(self.dir, "calls_x.jsonl")
        with open(_p, "w", encoding="utf-8") as f:
            f.write(json.dumps(_rec(0)) + "\n")
            f.write("{not json\n")
            f.write("\n")
            f.write(json.dumps(_rec(1)) + "\n")
        _recs = load_records(_p)
        self.assertEqual(len(_recs), 2)

    def test_41_load_records_missing_file(self):
        self.assertEqual(load_records(os.path.join(self.dir, "nope.jsonl")), [])

    def test_42_save_report_rejects_production_in_test_env(self):
        self.assertIsNone(save_report({"score": 1}))

    def test_43_save_report_explicit_path(self):
        _p = os.path.join(self.dir, "r.json")
        self.assertEqual(save_report({"score": 88.5}, _p), _p)
        self.assertEqual(json.load(open(_p, encoding="utf-8"))["score"], 88.5)

    def test_44_evaluate_day_on_isolated_dir(self):
        _d = os.path.join(self.dir, "traces")
        os.makedirs(_d, exist_ok=True)
        import time as _t
        _day = _t.strftime("%Y%m%d")
        with open(os.path.join(_d, "calls_%s.jsonl" % _day), "w", encoding="utf-8") as f:
            for i in range(30):
                f.write(json.dumps(_rec(i)) + "\n")
        _r = evaluate_day(day=_day, trace_dir=_d)
        self.assertEqual(_r["total_records"], 30)
        self.assertTrue(_r["file_exists"])

    def test_45_evaluate_and_report_switch_off(self):
        _bak = config.ENABLE_LLM_DATA_QUALITY_EVAL
        try:
            config.ENABLE_LLM_DATA_QUALITY_EVAL = False
            self.assertFalse(evaluator_enabled())
            self.assertIsNone(evaluate_and_report())
        finally:
            config.ENABLE_LLM_DATA_QUALITY_EVAL = _bak

    def test_46_evaluate_and_report_explicit_paths(self):
        _d = os.path.join(self.dir, "t2")
        os.makedirs(_d, exist_ok=True)
        import time as _t
        _day = _t.strftime("%Y%m%d")
        with open(os.path.join(_d, "calls_%s.jsonl" % _day), "w", encoding="utf-8") as f:
            for i in range(30):
                f.write(json.dumps(_rec(i)) + "\n")
        _out = os.path.join(self.dir, "out.json")
        _r = evaluate_and_report(day=_day, trace_dir=_d, report_path=_out)
        self.assertIsNotNone(_r)
        self.assertTrue(os.path.isfile(_out))


# ================================================================ 接线
class TestWiring(unittest.TestCase):
    def test_50_config_switches(self):
        self.assertTrue(hasattr(config, "ENABLE_LLM_DATA_QUALITY_EVAL"))
        self.assertTrue(hasattr(config, "LLM_DATA_QUALITY_STUB_REPEAT_MIN"))
        self.assertTrue(hasattr(config, "LLM_DATA_QUALITY_STUB_LEN_MAX"))

    def test_51_call_recorder_has_test_guard(self):
        """★本批 T0：留存器必须有测试环境污染防御。"""
        _src = open(os.path.join(_ROOT, "nucleus", "llm", "call_recorder.py"),
                    encoding="utf-8").read()
        self.assertIn("_in_test_env", _src)
        self.assertIn("_is_production_trace_dir", _src)
        self.assertIn("_base_dir_override is None", _src)

    def test_52_daily_scheduler_wired(self):
        _src = open(os.path.join(_ROOT, "nucleus", "self_awareness", "DailyScheduler.py"),
                    encoding="utf-8").read()
        self.assertIn("data_quality_evaluator", _src)

    def test_53_tools_entry_exists(self):
        self.assertTrue(os.path.isfile(
            os.path.join(_ROOT, "tools", "run_data_quality_eval.py")))

    def test_54_readonly_no_write_to_留存(self):
        """L1 红线：评估器不得写 calls_*.jsonl。"""
        _src = open(os.path.join(_ROOT, "nucleus", "llm", "data_quality_evaluator.py"),
                    encoding="utf-8").read()
        self.assertNotIn('"calls_%s.jsonl", "a"', _src)
        self.assertNotIn("calls_%s.jsonl\", \"w\"", _src)
        self.assertIn("quality_report.json", _src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
