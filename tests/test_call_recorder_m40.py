# -*- coding: utf-8 -*-
"""第40批 T1 门控测试：LLM 调用留存管道（数据结构 / 脱敏 / 滚窗 / 并发 / 开关）。

★测试隔离：全部使用 ``tempfile.mkdtemp()`` 注入 base_dir，**绝不写生产
``data/llm_traces/``**。
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from nucleus.llm import call_recorder as cr  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="m40_t1_")
        self._orig = getattr(config, "ENABLE_LLM_CALL_RECORDER", True)
        config.ENABLE_LLM_CALL_RECORDER = True
        self.r = cr.LLMCallRecorder(base_dir=self._dir)

    def tearDown(self):
        config.ENABLE_LLM_CALL_RECORDER = self._orig
        shutil.rmtree(self._dir, ignore_errors=True)

    def _lines(self, prefix="calls"):
        _fp = self.r._path_for(time.time(), prefix=prefix)
        if not os.path.isfile(_fp):
            return []
        with open(_fp, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]


class TestDataStructure(_Base):
    def test_01_all_fields_present(self):
        tid = self.r.record(origin=cr.ORIGIN_USER_QUERY, prompt="p", response="r",
                            prompt_version="v1", channel="ark", model="m",
                            duration=1.5, tokens=9, status=cr.STATUS_SUCCESS)
        self.assertTrue(tid and tid.startswith("m40-"))
        _rec = self._lines()[0]
        for _k in ("ts", "origin", "prompt", "response", "prompt_version",
                   "channel", "model", "duration", "tokens", "status",
                   "feedback", "trace_id"):
            self.assertIn(_k, _rec)

    def test_02_jsonl_append(self):
        for _i in range(5):
            self.r.record(origin=cr.ORIGIN_USER_QUERY, prompt="p%d" % _i,
                          response="r%d" % _i)
        self.assertEqual(len(self._lines()), 5)

    def test_03_stats_counters(self):
        self.r.record(origin=cr.ORIGIN_USER_QUERY, prompt="a", response="b")
        self.assertEqual(self.r.stats()["written"], 1)
        self.assertEqual(self.r.stats()["failed"], 0)

    def test_04_four_origins_covered(self):
        for _o in cr.ORIGINS:
            self.r.record(origin=_o, prompt="p", response="r")
        _got = {x["origin"] for x in self._lines()}
        self.assertEqual(_got, set(cr.ORIGINS))
        self.assertEqual(len(cr.ORIGINS), 4)


class TestSanitize(_Base):
    def test_10_sk_key(self):
        self.assertNotIn("sk-abcdefghijklmnop1234",
                         cr.sanitize_text("k=sk-abcdefghijklmnop1234"))

    def test_11_bearer(self):
        _s = cr.sanitize_text("Bearer abcdefghijklmnopqrstuvwx")
        self.assertNotIn("abcdefghijklmnopqrstuvwx", _s)

    def test_12_api_key_assign(self):
        _s = cr.sanitize_text("api_key=verysecretkey1234")
        self.assertNotIn("verysecretkey1234", _s)

    def test_13_aws(self):
        _s = cr.sanitize_text("AKIAIOSFODNN7EXAMPLE")
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", _s)

    def test_14_sanitize_applied_on_record(self):
        self.r.record(origin=cr.ORIGIN_USER_QUERY,
                      prompt="key=sk-abcdefghijklmnop9999", response="ok")
        self.assertNotIn("sk-abcdefghijklmnop9999",
                         self._lines()[0]["prompt"])

    def test_15_sanitize_can_be_disabled(self):
        config.LLM_TRACE_SANITIZE = False
        try:
            _r = cr.LLMCallRecorder(base_dir=self._dir)
            _r.record(origin=cr.ORIGIN_USER_QUERY,
                      prompt="key=sk-abcdefghijklmnop9999", response="")
            self.assertIn("sk-abcdefghijklmnop9999", self._lines()[0]["prompt"])
        finally:
            config.LLM_TRACE_SANITIZE = True

    def test_16_normal_text_not_mangled(self):
        _s = cr.sanitize_text("请解释 token 是什么，以及如何使用它")
        self.assertEqual(_s, "请解释 token 是什么，以及如何使用它")


class TestTruncate(_Base):
    def test_20_truncate(self):
        config.LLM_TRACE_MAX_TEXT_LEN = 20
        try:
            _r = cr.LLMCallRecorder(base_dir=self._dir)
            _r.record(origin=cr.ORIGIN_USER_QUERY, prompt="x" * 100, response="")
            _p = self._lines()[0]["prompt"]
            self.assertLess(len(_p), 100)
            self.assertIn("[truncated", _p)
        finally:
            config.LLM_TRACE_MAX_TEXT_LEN = 8000

    def test_21_zero_means_unlimited(self):
        config.LLM_TRACE_MAX_TEXT_LEN = 0
        try:
            _r = cr.LLMCallRecorder(base_dir=self._dir)
            _r.record(origin=cr.ORIGIN_USER_QUERY, prompt="y" * 500, response="")
            self.assertEqual(len(self._lines()[0]["prompt"]), 500)
        finally:
            config.LLM_TRACE_MAX_TEXT_LEN = 8000


class TestRotation(_Base):
    def test_30_old_files_removed(self):
        config.LLM_TRACE_RETENTION_DAYS = 90
        try:
            # 造 1 个 200 天前的旧文件 + 1 个 10 天前的新文件
            _now = time.time()
            _old = os.path.join(self._dir, "calls_%s.jsonl"
                                % self.r._day_str(_now - 200 * 86400))
            _new = os.path.join(self._dir, "calls_%s.jsonl"
                                % self.r._day_str(_now - 10 * 86400))
            for _fp in (_old, _new):
                with open(_fp, "w", encoding="utf-8") as f:
                    f.write("{}\n")
            _t = _now - 200 * 86400
            os.utime(_old, (_t, _t))
            _t2 = _now - 10 * 86400
            os.utime(_new, (_t2, _t2))
            self.r.record(origin=cr.ORIGIN_USER_QUERY, prompt="x", response="y")
            self.assertFalse(os.path.isfile(_old), "200 天前文件应被清理")
            self.assertTrue(os.path.isfile(_new), "10 天前文件应保留")
        finally:
            config.LLM_TRACE_RETENTION_DAYS = 90

    def test_31_retention_zero_no_cleanup(self):
        config.LLM_TRACE_RETENTION_DAYS = 0
        try:
            _r = cr.LLMCallRecorder(base_dir=self._dir)
            _old = os.path.join(self._dir, "calls_20000101.jsonl")
            with open(_old, "w", encoding="utf-8") as f:
                f.write("{}\n")
            _r.record(origin=cr.ORIGIN_USER_QUERY, prompt="x", response="y")
            self.assertTrue(os.path.isfile(_old))
        finally:
            config.LLM_TRACE_RETENTION_DAYS = 90


class TestSwitchAndConcurrency(_Base):
    def test_40_switch_off_zero_io(self):
        config.ENABLE_LLM_CALL_RECORDER = False
        try:
            _d = tempfile.mkdtemp(prefix="m40_t1_off_")
            shutil.rmtree(_d, ignore_errors=True)
            _r = cr.LLMCallRecorder(base_dir=_d)
            self.assertIsNone(_r.record(origin=cr.ORIGIN_USER_QUERY,
                                        prompt="x", response="y"))
            self.assertFalse(os.path.exists(_d), "开关关闭不得创建目录")
            self.assertFalse(_r.record_feedback("m40-x", "ok"))
        finally:
            config.ENABLE_LLM_CALL_RECORDER = True

    def test_41_concurrent_lines_intact(self):
        def _work(_i):
            for _j in range(10):
                self.r.record(origin=cr.ORIGIN_USER_QUERY,
                              prompt="t%d-%d" % (_i, _j), response="r")
        _ths = [threading.Thread(target=_work, args=(i,)) for i in range(5)]
        for _t in _ths:
            _t.start()
        for _t in _ths:
            _t.join()
        _ls = self._lines()
        self.assertEqual(len(_ls), 50, "并发写不得丢行/串行")
        self.assertEqual(len({x["trace_id"] for x in _ls}), 50)

    def test_42_path_outside_cwd(self):
        self.assertIn("m40_t1_", self.r.base_dir())
        self.assertTrue(os.path.isdir(self._dir))


class TestFeedback(_Base):
    def test_50_feedback_sidecar(self):
        _tid = self.r.record(origin=cr.ORIGIN_USER_QUERY, prompt="p",
                             response="r")
        self.assertTrue(self.r.record_feedback(_tid, "accepted"))
        _fb = self._lines(prefix="feedback")
        self.assertEqual(len(_fb), 1)
        self.assertEqual(_fb[0]["trace_id"], _tid)
        self.assertEqual(_fb[0]["feedback"], "accepted")

    def test_51_feedback_empty_trace_id(self):
        self.assertFalse(self.r.record_feedback("", "x"))

    def test_52_main_record_feedback_none(self):
        self.r.record(origin=cr.ORIGIN_USER_QUERY, prompt="p", response="r")
        self.assertIsNone(self._lines()[0]["feedback"])


class TestSingleton(unittest.TestCase):
    def setUp(self):
        cr.reset_call_recorder()

    def tearDown(self):
        cr.reset_call_recorder()

    def test_60_singleton(self):
        _a = cr.get_call_recorder()
        _b = cr.get_call_recorder()
        self.assertIs(_a, _b)

    def test_61_reset(self):
        _a = cr.get_call_recorder()
        cr.reset_call_recorder()
        self.assertIsNot(cr.get_call_recorder(), _a)


if __name__ == "__main__":
    unittest.main()
