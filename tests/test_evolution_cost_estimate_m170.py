# -*- coding: utf-8 -*-
"""第170批 C9 门控测试：进化费用追踪下半（cost_estimate 字段 + 计费常量表）。

覆盖：
* ``record()`` 输出含 ``cost_estimate`` 字段
* 空单价表 → null（**绝不猜价**）
* 单价表命中模型 → 按 ¥/1K tokens 正确估算
* 总开关关闭 → null
* 模型不在表 → null
* 由 ``usage`` 派生 tokens 也参与估算
* ``record_evolution_call`` / 装饰器透传 model → cost_estimate

★测试隔离：全部注入 ``tempfile.mkdtemp()`` 作为 ``base_dir``，**绝不写生产 data/**。
"""
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


# ---------------------------------------------------------------------------
# 公共夹具
# ---------------------------------------------------------------------------
class _RecBase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="m170_cost_")
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
# cost_estimate 字段语义
# ---------------------------------------------------------------------------
class TestCostEstimateField(_RecBase):
    def test_01_field_present_default_null(self):
        self._rec.record(origin=cr.ORIGIN_EVOLUTION_TASK, prompt="p", response="r",
                         model="deepseek-chat", tokens=1000)
        _rec = self._lines()[0]
        self.assertIn("cost_estimate", _rec)
        # 默认空单价表 → null（不猜价）
        self.assertIsNone(_rec["cost_estimate"])

    def test_02_empty_table_no_guess(self):
        self._set("EVOLUTION_LLM_PRICE_TABLE", {})
        self._rec.record(origin=cr.ORIGIN_EVOLUTION_TASK, prompt="p", response="r",
                         model="deepseek-chat", tokens=1000)
        self.assertIsNone(self._lines()[0]["cost_estimate"])

    def test_03_computed_when_price_configured(self):
        self._set("EVOLUTION_LLM_PRICE_TABLE", {"deepseek-chat": 0.001})  # ¥/1K
        self._rec.record(origin=cr.ORIGIN_EVOLUTION_TASK, prompt="p", response="r",
                         model="deepseek-chat", tokens=1000)
        # 0.001 * 1000 / 1000 = 0.001
        self.assertEqual(self._lines()[0]["cost_estimate"], 0.001)

    def test_04_computed_large_tokens(self):
        self._set("EVOLUTION_LLM_PRICE_TABLE", {"m": 0.004})
        self._rec.record(origin=cr.ORIGIN_EVOLUTION_TASK, prompt="p", response="r",
                         model="m", tokens=250000)
        # 0.004 * 250000 / 1000 = 1.0
        self.assertAlmostEqual(self._lines()[0]["cost_estimate"], 1.0, places=6)

    def test_05_switch_off_null(self):
        self._set("ENABLE_EVOLUTION_COST_ESTIMATE", False)
        self._set("EVOLUTION_LLM_PRICE_TABLE", {"deepseek-chat": 0.001})
        self._rec.record(origin=cr.ORIGIN_EVOLUTION_TASK, prompt="p", response="r",
                         model="deepseek-chat", tokens=1000)
        self.assertIsNone(self._lines()[0]["cost_estimate"])

    def test_06_model_not_in_table_null(self):
        self._set("EVOLUTION_LLM_PRICE_TABLE", {"deepseek-chat": 0.001})
        self._rec.record(origin=cr.ORIGIN_EVOLUTION_TASK, prompt="p", response="r",
                         model="unknown-model", tokens=1000)
        self.assertIsNone(self._lines()[0]["cost_estimate"])

    def test_07_usage_derived_tokens_counted(self):
        self._set("EVOLUTION_LLM_PRICE_TABLE", {"m": 0.002})
        self._rec.record(origin=cr.ORIGIN_EVOLUTION_TASK, prompt="p", response="r",
                         model="m",
                         usage={"prompt_tokens": 800, "completion_tokens": 200,
                                "total_tokens": 1000})
        self.assertAlmostEqual(self._lines()[0]["cost_estimate"], 0.002, places=6)

    def test_08_zero_tokens_null(self):
        self._set("EVOLUTION_LLM_PRICE_TABLE", {"m": 0.002})
        self._rec.record(origin=cr.ORIGIN_EVOLUTION_TASK, prompt="p", response="r",
                         model="m", tokens=0)
        self.assertIsNone(self._lines()[0]["cost_estimate"])

    def test_09_evolution_entry_propagates_cost(self):
        self._set("EVOLUTION_LLM_PRICE_TABLE", {"ev-model": 0.005})
        cr.record_evolution_call(prompt="p", response="r", model="ev-model",
                                 tokens=2000)
        _rec = self._lines()[0]
        self.assertEqual(_rec["origin"], cr.ORIGIN_EVOLUTION_TASK)
        # 0.005 * 2000 / 1000 = 0.01
        self.assertAlmostEqual(_rec["cost_estimate"], 0.01, places=6)

    def test_10_decorator_propagates_model(self):
        self._set("EVOLUTION_LLM_PRICE_TABLE", {"dec-model": 0.01})

        class _E:
            _m44_last_model = "dec-model"
            _last_llm_usage = None

            @cr.trace_evolution_call(prompt_pos=2, version="t.v1")
            def _call_llm(self, system, prompt, **kw):
                return "answer"

        _E()._call_llm("sys", "the-prompt")
        _rec = self._lines()[0]
        self.assertEqual(_rec["model"], "dec-model")
        # 装饰器未传 tokens（默认 0）→ 费用 null（模型已透传，费用待引擎补 tokens）
        self.assertIsNone(_rec["cost_estimate"])


if __name__ == "__main__":
    unittest.main()

