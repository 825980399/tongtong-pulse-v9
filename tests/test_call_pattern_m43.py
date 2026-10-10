# -*- coding: utf-8 -*-
"""主线第43批 T2（P0-250）门控测试：LLM 调用模式分析器。

覆盖：分布统计 / 四类可优化模式识别 / 污染剔除 / 覆盖率 / 建议生成 / IO 防御。
★ 全部使用构造记录 + 隔离目录。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import tempfile
import unittest

import config
from nucleus.llm.call_pattern_analyzer import (
    FAIL_REPEAT_MIN,
    REPEAT_MIN,
    analyze_and_report,
    analyze_day,
    analyze_records,
    analyzer_enabled,
    format_summary_line,
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
                    print("cleanup warn: {}: {}".format(type(e).__name__, e))
        try:
            os.rmdir(dp)
        except BaseException as e:
            print("cleanup warn: {}: {}".format(type(e).__name__, e))


def _rec(prompt="正常提问", channel="zhipu", model="glm-4-flash", status="success",
         origin="system_internal", duration=1.0, tokens=10, pv="",
         response="正常回答内容"):
    return {"trace_id": "x", "ts": 1.0, "origin": origin, "prompt": prompt,
            "response": response, "prompt_version": pv, "channel": channel,
            "model": model, "duration": duration, "tokens": tokens,
            "status": status, "error": "", "feedback": None}


class TestDistribution(unittest.TestCase):
    def test_01_empty_safe(self):
        for bad in (None, [], "x", 3):
            _r = analyze_records(bad)
            self.assertEqual(_r["status"], "empty")
            self.assertEqual(_r["patterns"], [])

    def test_02_origin_and_status_counts(self):
        _recs = [_rec(origin="user_query") for _ in range(3)] + \
                [_rec(origin="system_internal", status="failed") for _ in range(2)]
        _r = analyze_records(_recs)
        self.assertEqual(_r["distribution"]["origin"]["user_query"], 3)
        self.assertEqual(_r["distribution"]["status"]["failed"], 2)

    def test_03_coverage(self):
        _recs = [_rec(duration=0.0, tokens=0, pv="") for _ in range(4)]
        _r = analyze_records(_recs)
        self.assertEqual(_r["coverage"]["duration"], 0.0)
        self.assertEqual(_r["coverage"]["prompt_version"], 0.0)

    def test_04_coverage_full(self):
        _recs = [_rec(pv="v1") for _ in range(4)]
        _r = analyze_records(_recs)
        self.assertEqual(_r["coverage"]["prompt_version"], 1.0)


class TestPatterns(unittest.TestCase):
    def test_10_repeat_prompt_detected(self):
        _recs = [_rec(prompt="相同的意图分类提示词内容") for _ in range(REPEAT_MIN + 2)]
        _r = analyze_records(_recs)
        _names = [p["name"] for p in _r["patterns"]]
        self.assertIn("repeat_prompt", _names)
        _p = next(p for p in _r["patterns"] if p["name"] == "repeat_prompt")
        self.assertEqual(_p["redundant"], REPEAT_MIN + 1)

    def test_11_low_freq_not_repeat(self):
        _recs = [_rec(prompt="不同的问题%d" % i) for i in range(REPEAT_MIN * 2)]
        _r = analyze_records(_recs)
        self.assertNotIn("repeat_prompt", [p["name"] for p in _r["patterns"]])

    def test_12_failed_without_reason(self):
        _recs = [_rec(status="failed") for _ in range(4)]
        _r = analyze_records(_recs)
        _p = next((p for p in _r["patterns"] if p["name"] == "failed_without_reason"), None)
        self.assertIsNotNone(_p)
        self.assertEqual(_p["no_reason"], 4)

    def test_13_missing_prompt_version(self):
        _r = analyze_records([_rec() for _ in range(3)])
        self.assertIn("missing_prompt_version", [p["name"] for p in _r["patterns"]])

    def test_14_channel_scatter(self):
        _recs = [_rec(channel=c) for c in ("zhipu", "deepseek", "ark-ds-v4-flash",
                                           "ark-seed-evolving")
                 for _ in range(6)]
        _r = analyze_records(_recs)
        self.assertIn("channel_scatter", [p["name"] for p in _r["patterns"]])

    def test_15_fail_retry_no_dedup(self):
        _recs = [_rec(prompt="总是失败的完全相同提示词内容", status="failed")
                 for _ in range(FAIL_REPEAT_MIN + 1)]
        _r = analyze_records(_recs)
        self.assertIn("fail_retry_no_dedup", [p["name"] for p in _r["patterns"]])

    def test_16_at_least_three_pattern_classes(self):
        """★任务书要求：识别出至少 3 类可优化的调用模式。"""
        _recs = ([_rec(prompt="重复的分类提示词内容") for _ in range(REPEAT_MIN + 1)]
                 + [_rec(status="failed", channel="zhipu") for _ in range(4)]
                 + [_rec(channel=c) for c in ("zhipu", "deepseek", "ark-ds-v4-flash")]
                 + [_rec(channel=c) for c in ("zhipu", "deepseek", "ark-ds-v4-flash")])
        _r = analyze_records(_recs)
        self.assertGreaterEqual(len(_r["patterns"]), 3, _r["patterns"])

    def test_17_recommendations_present(self):
        _recs = [_rec(prompt="重复的分类提示词内容") for _ in range(REPEAT_MIN + 1)]
        _r = analyze_records(_recs)
        self.assertTrue(_r["recommendations"])
        for _x in _r["recommendations"]:
            self.assertIn("action", _x)
            self.assertIn("expected_gain", _x)
            self.assertIn("risk", _x)


class TestPurityFilter(unittest.TestCase):
    def test_20_exclude_suspect(self):
        _recs = [_rec(prompt="正常提问内容") for _ in range(8)]
        _recs += [_rec(prompt="hi", channel="c1", model="m1", response="模拟回复")
                  for _ in range(5)]
        _r_all = analyze_records(_recs, include_suspect=True)
        _r_pure = analyze_records(_recs, include_suspect=False)
        self.assertEqual(_r_all["scored"], 13)
        self.assertEqual(_r_pure["scored"], 8)

    def test_21_findings_mention_pollution(self):
        _recs = [_rec(prompt="hi", channel="c1", model="m1") for _ in range(5)]
        _r = analyze_records(_recs, include_suspect=True)
        self.assertTrue(any("非生产" in f or "污染" in f for f in _r["findings"]))


class TestIOAndWiring(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="m43cp_", dir=_TMP_ROOT)

    def tearDown(self):
        _safe_rmtree(self.dir)

    def test_30_summary_line_format(self):
        _line = format_summary_line(analyze_records([_rec() for _ in range(3)]))
        self.assertIn("[调用模式]", _line)
        self.assertIn("样本=", _line)
        self.assertIn("可优化模式=", _line)

    def test_31_save_report_rejects_production_in_test_env(self):
        self.assertIsNone(save_report({"status": "ok"}))

    def test_32_save_report_explicit_path(self):
        _p = os.path.join(self.dir, "p.json")
        self.assertEqual(save_report({"status": "ok"}, _p), _p)
        self.assertEqual(json.load(open(_p, encoding="utf-8"))["status"], "ok")

    def test_33_analyze_day_on_isolated_dir(self):
        import time as _t
        _d = os.path.join(self.dir, "t")
        os.makedirs(_d, exist_ok=True)
        _day = _t.strftime("%Y%m%d")
        with open(os.path.join(_d, "calls_{}.jsonl".format(_day)), "w", encoding="utf-8") as f:
            for i in range(10):
                f.write(json.dumps(_rec(prompt="问题%d" % i)) + "\n")
        _r = analyze_day(day=_day, trace_dir=_d)
        self.assertEqual(_r["scored"], 10)
        self.assertTrue(_r["file_exists"])

    def test_34_analyzer_enabled_reads_config(self):
        self.assertTrue(hasattr(config, "ENABLE_LLM_CALL_PATTERN_ANALYSIS"))
        self.assertTrue(analyzer_enabled())

    def test_35_analyze_and_report_switch_off(self):
        _bak = config.ENABLE_LLM_CALL_PATTERN_ANALYSIS
        try:
            config.ENABLE_LLM_CALL_PATTERN_ANALYSIS = False
            self.assertFalse(analyzer_enabled())
            self.assertIsNone(analyze_and_report())
        finally:
            config.ENABLE_LLM_CALL_PATTERN_ANALYSIS = _bak

    def test_36_analyze_and_report_explicit_paths(self):
        import time as _t
        _d = os.path.join(self.dir, "t2")
        os.makedirs(_d, exist_ok=True)
        _day = _t.strftime("%Y%m%d")
        with open(os.path.join(_d, "calls_{}.jsonl".format(_day)), "w", encoding="utf-8") as f:
            for i in range(10):
                f.write(json.dumps(_rec(prompt="q%d" % i)) + "\n")
        _out = os.path.join(self.dir, "o.json")
        _r = analyze_and_report(day=_day, trace_dir=_d, report_path=_out)
        self.assertIsNotNone(_r)
        self.assertTrue(os.path.isfile(_out))

    def test_37_tools_entry_exists(self):
        self.assertTrue(os.path.isfile(
            os.path.join(_ROOT, "tools", "run_call_pattern_analysis.py")))

    def test_38_design_doc_exists(self):
        self.assertTrue(os.path.isfile(
            os.path.join(_ROOT, "docs", "设计文档", "大模型调用精简方案_v1.0.md")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
