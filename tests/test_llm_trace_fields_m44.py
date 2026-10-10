# -*- coding: utf-8 -*-
"""第44批 T1 门控测试：LLM 留存字段补全（prompt_version + failed error）。

覆盖：
* ``LLMCallRecorder`` 字段默认值 / 开关回退 / 截断
* ``format_error`` 规范化
* PulseLung 埋点透传（prompt_version + 失败 error）
* 进化引擎装饰器 ``trace_evolution_call``
* 历史回填工具 ``tools/backfill_llm_trace_fields.py``

★测试隔离：全部注入 ``tempfile.mkdtemp()`` 作为 ``base_dir``，
**绝不写生产 ``data/llm_traces/``**（recorder 自带生产目录守卫，双保险）。
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.llm import call_recorder as cr  # noqa: E402
from organs.body.PulseLung import PulseLung  # noqa: E402


# ---------------------------------------------------------------------------
# 公共夹具
# ---------------------------------------------------------------------------
class _RecBase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="m44_t1_")
        self._rec = cr.LLMCallRecorder(base_dir=self._dir)
        self._orig_get = cr.get_call_recorder
        cr.get_call_recorder = lambda: self._rec
        self._saved = {}

    def tearDown(self):
        cr.get_call_recorder = self._orig_get
        for _k, _v in self._saved.items():
            setattr(config, _k, _v)
        shutil.rmtree(self._dir, ignore_errors=True)

    def _set(self, name, value):
        """改 config 并登记还原。"""
        if name not in self._saved:
            self._saved[name] = getattr(config, name, None)
        setattr(config, name, value)

    def _lines(self):
        _fp = self._rec._path_for(time.time())
        if not os.path.isfile(_fp):
            return []
        with open(_fp, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]


# ---------------------------------------------------------------------------
# 1) prompt_version 默认值 / 开关
# ---------------------------------------------------------------------------
class TestPromptVersion(_RecBase):
    def test_01_default_is_unknown(self):
        self._rec.record(origin=cr.ORIGIN_USER_QUERY, prompt="p", response="r")
        self.assertEqual(self._lines()[0]["prompt_version"], "unknown")

    def test_02_explicit_kept(self):
        self._rec.record(origin=cr.ORIGIN_USER_QUERY, prompt="p", response="r",
                         prompt_version="lung.dialog.v3")
        self.assertEqual(self._lines()[0]["prompt_version"], "lung.dialog.v3")

    def test_03_blank_becomes_unknown(self):
        self._rec.record(origin=cr.ORIGIN_USER_QUERY, prompt="p", response="r",
                         prompt_version="   ")
        self.assertEqual(self._lines()[0]["prompt_version"], "unknown")

    def test_04_switch_off_reproduces_old_behavior(self):
        self._set("ENABLE_LLM_TRACE_PROMPT_VERSION", False)
        self._rec.record(origin=cr.ORIGIN_USER_QUERY, prompt="p", response="r")
        # 修复前行为：缺省写空串（零回归）
        self.assertEqual(self._lines()[0]["prompt_version"], "")

    def test_05_custom_default_value(self):
        self._set("LLM_TRACE_DEFAULT_PROMPT_VERSION", "v0.0")
        self._rec.record(origin=cr.ORIGIN_USER_QUERY, prompt="p", response="r")
        self.assertEqual(self._lines()[0]["prompt_version"], "v0.0")

    def test_06_stripped_but_preserved(self):
        self._rec.record(origin=cr.ORIGIN_USER_QUERY, prompt="p", response="r",
                         prompt_version="  v9  ")
        self.assertEqual(self._lines()[0]["prompt_version"], "v9")

    def test_07_stats_exposes_new_fields(self):
        _s = self._rec.stats()
        for _k in ("prompt_version_enabled", "default_prompt_version",
                   "error_capture_enabled", "error_max_len"):
            self.assertIn(_k, _s)
        self.assertTrue(_s["prompt_version_enabled"])
        self.assertEqual(_s["default_prompt_version"], "unknown")
        self.assertEqual(_s["error_max_len"], 500)


# ---------------------------------------------------------------------------
# 2) failed 记录 error
# ---------------------------------------------------------------------------
class TestErrorField(_RecBase):
    def test_10_failed_without_error_gets_placeholder(self):
        self._rec.record(origin=cr.ORIGIN_USER_QUERY, prompt="p", response="",
                         status=cr.STATUS_FAILED)
        self.assertEqual(self._lines()[0]["error"], cr.DEFAULT_FAILED_ERROR)

    def test_11_failed_with_error_kept(self):
        self._rec.record(origin=cr.ORIGIN_USER_QUERY, prompt="p", response="",
                         status=cr.STATUS_FAILED, error="TimeoutError: read timeout")
        self.assertEqual(self._lines()[0]["error"], "TimeoutError: read timeout")

    def test_12_success_without_error_stays_empty(self):
        self._rec.record(origin=cr.ORIGIN_USER_QUERY, prompt="p", response="r")
        self.assertEqual(self._lines()[0]["error"], "")

    def test_13_error_truncated_to_500(self):
        self._rec.record(origin=cr.ORIGIN_USER_QUERY, prompt="p", response="",
                         status=cr.STATUS_FAILED, error="X" * 1200)
        _err = self._lines()[0]["error"]
        self.assertTrue(_err.startswith("X" * 500))
        self.assertIn("[truncated", _err)
        self.assertLess(len(_err), 600)

    def test_14_error_capture_switch_off_old_behavior(self):
        self._set("ENABLE_LLM_TRACE_ERROR_CAPTURE", False)
        self._rec.record(origin=cr.ORIGIN_USER_QUERY, prompt="p", response="",
                         status=cr.STATUS_FAILED)
        self.assertEqual(self._lines()[0]["error"], "")

    def test_15_error_max_len_configurable(self):
        self._set("LLM_TRACE_ERROR_MAX_LEN", 20)
        self._rec.record(origin=cr.ORIGIN_USER_QUERY, prompt="p", response="",
                         status=cr.STATUS_FAILED, error="Y" * 100)
        self.assertTrue(self._lines()[0]["error"].startswith("Y" * 20))

    def test_16_timeout_status_also_gets_placeholder(self):
        self._rec.record(origin=cr.ORIGIN_USER_QUERY, prompt="p", response="",
                         status=cr.STATUS_TIMEOUT)
        self.assertEqual(self._lines()[0]["error"], cr.DEFAULT_FAILED_ERROR)


# ---------------------------------------------------------------------------
# 3) format_error
# ---------------------------------------------------------------------------
class TestFormatError(unittest.TestCase):
    def test_20_exception_formatted(self):
        self.assertEqual(cr.format_error(ValueError("boom")), "ValueError: boom")

    def test_21_str_passthrough(self):
        self.assertEqual(cr.format_error("plain text"), "plain text")

    def test_22_none_empty(self):
        self.assertEqual(cr.format_error(None), "")

    def test_23_truncated(self):
        _s = cr.format_error("Z" * 900, limit=100)
        self.assertEqual(len(_s), 100)

    def test_24_sanitized(self):
        _s = cr.format_error("key=sk-abcdefghijklmnop1234 failed")
        self.assertNotIn("sk-abcdefghijklmnop1234", _s)

    def test_25_default_limit_500(self):
        _s = cr.format_error(RuntimeError("q" * 2000))
        self.assertLessEqual(len(_s), 500)


# ---------------------------------------------------------------------------
# 4) 进化引擎入口
# ---------------------------------------------------------------------------
class TestEvolutionEntry(_RecBase):
    def test_30_origin_and_default_version(self):
        cr.record_evolution_call(prompt="p", response="r")
        _l = self._lines()
        self.assertEqual(len(_l), 1)
        self.assertEqual(_l[0]["origin"], cr.ORIGIN_EVOLUTION_TASK)
        self.assertTrue(_l[0]["prompt_version"])
        self.assertEqual(_l[0]["status"], cr.STATUS_SUCCESS)

    def test_31_switch_off_zero_io(self):
        self._set("ENABLE_EVOLUTION_CALL_TRACE", False)
        self.assertIsNone(cr.record_evolution_call(prompt="p", response="r"))
        self.assertEqual(self._lines(), [])

    def test_32_decorator_records_success(self):
        class _E:
            _m44_last_error = ""

            @cr.trace_evolution_call(prompt_pos=2, version="t.v1")
            def _call_llm(self, system, prompt, **kw):
                return "answer"

        self.assertEqual(_E()._call_llm("sys", "the-prompt"), "answer")
        _l = self._lines()
        self.assertEqual(len(_l), 1)
        self.assertEqual(_l[0]["response"], "answer")
        self.assertEqual(_l[0]["prompt_version"], "t.v1")
        self.assertEqual(_l[0]["prompt"], "the-prompt")
        self.assertEqual(_l[0]["origin"], cr.ORIGIN_EVOLUTION_TASK)

    def test_33_decorator_uses_engine_error_detail(self):
        class _E:
            def __init__(self):
                self._m44_last_error = "missing_api_config: 未配置"

            @cr.trace_evolution_call(prompt_pos=2)
            def _call_llm(self, system, prompt, **kw):
                return None

        _E()._call_llm("s", "p")
        _l = self._lines()
        self.assertEqual(_l[0]["status"], cr.STATUS_FAILED)
        self.assertIn("missing_api_config", _l[0]["error"])

    def test_34_decorator_reraises_and_records(self):
        class _E:
            _m44_last_error = ""

            @cr.trace_evolution_call(prompt_pos=2)
            def _call_llm(self, system, prompt, **kw):
                raise TimeoutError("read timeout")

        with self.assertRaises(TimeoutError):
            _E()._call_llm("s", "p")
        _l = self._lines()
        self.assertEqual(_l[0]["status"], cr.STATUS_FAILED)
        self.assertEqual(_l[0]["error"], "TimeoutError: read timeout")

    def test_35_decorator_survives_recorder_failure(self):
        cr.get_call_recorder = lambda: None
        _r = self._rec

        class _E:
            _m44_last_error = ""

            @cr.trace_evolution_call(prompt_pos=2)
            def _call_llm(self, system, prompt, **kw):
                return "ok"

        self.assertEqual(_E()._call_llm("s", "p"), "ok")
        self.assertEqual(len(_r._path_for(time.time())) > 0, True)

    def test_36_evolution_engines_are_wired(self):
        """三个进化引擎的 ``_call_llm`` 均已挂上埋点装饰器（源码级核实）。"""
        import io
        _targets = {
            os.path.join(_ROOT, "nucleus", "evolution", "LLMEvolutionEngine.py"): 1,
            os.path.join(_ROOT, "nucleus", "evolution", "SelfReflectionEngine.py"): 1,
            os.path.join(_ROOT, "nucleus", "reasoning",
                         "SafeEvolutionExecutor.py"): 1,
        }
        for _fp, _n in _targets.items():
            with io.open(_fp, "r", encoding="utf-8") as f:
                _t = f.read()
            self.assertEqual(_t.count("@trace_evolution_call("), _n,
                             "{} 装饰器数不符".format(os.path.basename(_fp)))


# ---------------------------------------------------------------------------
# 5) PulseLung 接线
# ---------------------------------------------------------------------------
_CHANNELS = [{"name": "t-ch", "model": "m1", "api_url": "http://x", "api_key": "k"}]


def _make_lung(reply="模拟回复", raise_exc=None):
    _l = PulseLung.__new__(PulseLung)
    _l._log = lambda *a, **k: None
    _l._gateway_channel = lambda: None
    _l._is_advanced_task = lambda m: False
    _l._prefer_cheap_channels = lambda c, is_background=False: c
    _l._prefer_paid_channels = lambda c, caller=None: c
    _l._get_channel_health = lambda: None
    _l._channel_concurrency = lambda: None
    _l._update_channel_health = lambda *a, **k: None
    _l._record_model_result = lambda *a, **k: None
    _l._adjust_channel_concurrency = lambda *a, **k: None
    _l._m32_apply_quota_policy = lambda c: c
    _l._current_call_is_background = False
    _l._m40_dep_tracking_enabled = lambda: False
    _l._m41_cache_observe_enabled = lambda: False
    if raise_exc is not None:
        def _boom(ch, prompt, **kw):
            raise raise_exc
        _l._call_channel = _boom
    else:
        _l._call_channel = lambda ch, prompt, **kw: reply
    return _l


class TestPulseLungWiring(_RecBase):
    def setUp(self):
        super().setUp()
        self._orig_ch = config.get_active_channels
        config.get_active_channels = lambda: list(_CHANNELS)
        self._s1 = getattr(config, "ENABLE_LUNG_CALL_TRACE", True)
        config.ENABLE_LUNG_CALL_TRACE = True

    def tearDown(self):
        config.get_active_channels = self._orig_ch
        config.ENABLE_LUNG_CALL_TRACE = self._s1
        super().tearDown()

    def test_40_prompt_version_passed_through(self):
        _l = _make_lung("ok")
        _l._call_via_channels("q", "m1", caller="user_dialog",
                              prompt_version="lung.entry.v9")
        self.assertEqual(self._lines()[0]["prompt_version"], "lung.entry.v9")

    def test_41_missing_version_becomes_unknown(self):
        _l = _make_lung("ok")
        _l._call_via_channels("q", "m1", caller="user_dialog")
        self.assertEqual(self._lines()[0]["prompt_version"], "unknown")

    def test_42_failed_channel_records_exception_detail(self):
        _l = _make_lung(raise_exc=TimeoutError("read timeout"))
        self.assertIsNone(_l._call_via_channels("q", "m1", caller="user_dialog"))
        _l0 = self._lines()[0]
        self.assertEqual(_l0["status"], "failed")
        self.assertEqual(_l0["error"], "TimeoutError: read timeout")

    def test_43_empty_reply_gets_placeholder_error(self):
        _l = _make_lung(None)
        _l._call_via_channels("q", "m1", caller="user_dialog")
        self.assertEqual(self._lines()[0]["status"], "failed")
        self.assertEqual(self._lines()[0]["error"], cr.DEFAULT_FAILED_ERROR)

    def test_44_remote_api_threads_version(self):
        """`_call_remote_api` **签名不变**，版本号经一次性属性转交。"""
        _l = _make_lung("ok")
        _l._channels_config = lambda: {"enabled": True}
        _l._current_task_is_background = False
        _l._m44_prompt_version = "lung.chat.v2"
        _l._call_remote_api("q", "m1")
        self.assertEqual(self._lines()[0]["prompt_version"], "lung.chat.v2")

    def test_47_prompt_version_attr_is_one_shot(self):
        """一次性消费：转交后属性清零，避免下一次调用串味。"""
        _l = _make_lung("ok")
        _l._channels_config = lambda: {"enabled": True}
        _l._current_task_is_background = False
        _l._m44_prompt_version = "lung.chat.v2"
        _l._call_remote_api("q", "m1")
        self.assertEqual(getattr(_l, "_m44_prompt_version", ""), "")

    def test_48_remote_api_signature_unchanged(self):
        """★零回归：既有测试以精确签名假件打桩，故签名不得新增形参。"""
        import inspect
        _sig = inspect.signature(PulseLung._call_remote_api)
        self.assertEqual(list(_sig.parameters),
                         ["self", "prompt", "model", "enable_thinking"])

    def test_45_version_constants_present(self):
        for _n in ("_M44_PROMPT_VERSION_ENTRY", "_M44_PROMPT_VERSION_RETRY",
                   "_M44_PROMPT_VERSION_SEMANTIC", "_M44_PROMPT_VERSION_CHAT"):
            self.assertTrue(isinstance(getattr(PulseLung, _n), str))
            self.assertTrue(getattr(PulseLung, _n).startswith("lung."))

    def test_46_trace_call_accepts_prompt_version(self):
        """``_m40_trace_call`` 形参已扩展（源码级核实）。"""
        import inspect
        _sig = inspect.signature(PulseLung._m40_trace_call)
        self.assertIn("prompt_version", _sig.parameters)


# ---------------------------------------------------------------------------
# 6) 回填工具
# ---------------------------------------------------------------------------
def _load_backfill():
    _p = os.path.join(_ROOT, "tools", "backfill_llm_trace_fields.py")
    _spec = importlib.util.spec_from_file_location("m44_backfill_tool", _p)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod


class TestBackfillTool(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="m44_bf_")
        self._fp = os.path.join(self._dir, "calls_20260101.jsonl")
        _recs = [
            {"trace_id": "a", "prompt_version": "", "status": "failed",
             "error": "", "response": "", "prompt": "p1"},
            {"trace_id": "b", "prompt_version": "", "status": "success",
             "error": "", "response": "r", "prompt": "p2"},
            {"trace_id": "c", "prompt_version": "v1", "status": "failed",
             "error": "TimeoutError: x", "response": "", "prompt": "p3"},
        ]
        with open(self._fp, "w", encoding="utf-8") as f:
            for _r in _recs:
                f.write(json.dumps(_r, ensure_ascii=False) + "\n")
        self._bf = _load_backfill()

    def tearDown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    def _read(self, path=None):
        with open(path or self._fp, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def test_50_plan_counts(self):
        _p = self._bf.plan_file(self._fp)
        self.assertEqual(_p["total"], 3)
        self.assertEqual(_p["prompt_version_fill"], 2)   # a, b
        self.assertEqual(_p["error_fill"], 1)            # a
        self.assertEqual(_p["bad_lines"], 0)

    def test_51_dry_run_does_not_write(self):
        _before = self._read()
        self._bf.run(self._dir, apply=False)
        self.assertEqual(self._read(), _before)
        self.assertFalse(os.path.exists(self._fp + self._bf.BACKUP_SUFFIX))

    def test_52_apply_fills_fields_and_backs_up(self):
        _rep = self._bf.run(self._dir, apply=True)
        self.assertEqual(_rep["total_prompt_version_fill"], 2)
        self.assertEqual(_rep["total_error_fill"], 1)
        self.assertTrue(os.path.exists(self._fp + self._bf.BACKUP_SUFFIX))
        _after = self._read()
        self.assertEqual(_after[0]["prompt_version"], "unknown")
        self.assertEqual(_after[0]["error"], self._bf.BACKFILL_NO_DETAIL)
        self.assertEqual(_after[1]["prompt_version"], "unknown")
        # 已完整的记录保持原值
        self.assertEqual(_after[2]["prompt_version"], "v1")
        self.assertEqual(_after[2]["error"], "TimeoutError: x")
        # 业务字段未被改动
        self.assertEqual([x["trace_id"] for x in _after], ["a", "b", "c"])
        self.assertEqual([x["prompt"] for x in _after], ["p1", "p2", "p3"])

    def test_53_apply_is_idempotent(self):
        self._bf.run(self._dir, apply=True)
        _snap = self._read()
        self._bf.run(self._dir, apply=True)
        self.assertEqual(self._read(), _snap)

    def test_54_response_used_as_error_source(self):
        _fp2 = os.path.join(self._dir, "calls_20260102.jsonl")
        with open(_fp2, "w", encoding="utf-8") as f:
            f.write(json.dumps({"trace_id": "d", "prompt_version": "",
                                "status": "failed", "error": "",
                                "response": "HTTP 429 rate limited"},
                               ensure_ascii=False) + "\n")
        self._bf.run(self._dir, apply=False)
        _p = self._bf.plan_file(_fp2)
        self.assertEqual(_p["error_fill"], 1)
        self.assertIn("backfilled_from_response",
                      _p["samples"][0]["set_error"])

    def test_55_backfill_errors_are_truncated(self):
        _fp3 = os.path.join(self._dir, "calls_20260103.jsonl")
        with open(_fp3, "w", encoding="utf-8") as f:
            f.write(json.dumps({"trace_id": "e", "prompt_version": "",
                                "status": "failed", "error": "",
                                "response": "R" * 900},
                               ensure_ascii=False) + "\n")
        self._bf.run(self._dir, apply=True)
        _rec = self._read(_fp3)[0]
        self.assertLessEqual(len(_rec["error"]), 260)
        self.assertIn("backfilled_from_response", _rec["error"])


if __name__ == "__main__":
    unittest.main()
