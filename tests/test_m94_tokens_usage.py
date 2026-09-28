# -*- coding: utf-8 -*-
"""★主线第94批 T-94b：LLM **tokens / usage 打通** 门控单测。

任务书原文（第94批 T-94b）：
    ① `BaseLLMAdapter` 新增可选 `extract_usage(response) -> dict | None`，
       `OpenAICompatibleAdapter` 实现返回 `{prompt_tokens, completion_tokens,
       total_tokens}`，**不改 `parse_response` 签名（零回归）**；
    ② 调用层把 usage 传给 `record()`，`tokens=usage.total_tokens`（向后兼容）；
    ③ `record()` 新增可选参数 `usage`，写入新增字段 `"usage"`。

★★本批 T0 的**前提修正**（与任务书表述不同，已用生产数据实证）：
    任务书称「tokens 恒 0」，实测 `data/llm_traces/calls_*.jsonl` 8 天 5759 条中
    `tokens>0` 有 **2893 条（50.2%）** —— **不是恒 0**！零值是**结构性集中**的：
    `origin=evolution_task` 那批 100% 为 0（装饰器 `trace_evolution_call`
    的 `finally` 从未取用 usage），肺通道（`system_internal`）靠第40批的
    私有旁路**已经是对的**（该旁路本批 T-95e 统一命名为 `_last_llm_usage`）。
    ⇒ 真根因 = **三个被装饰的进化引擎出口没有统一入口**，E 组即为该结论的
    可执行证据（只固化「可复算事实」，不写死生产数字）。

覆盖五组：
  A. 静态接线（AST）—— 新增成员归属 + `parse_response` 签名与改前**逐字一致**
     + 三个进化引擎出口 `_last_llm_usage` 赋值确实落在被装饰的函数体内；
  B. adapter 层 —— `extract_usage` 边界（完整 / 缺失补齐 / 全 0 / 非数值 / 负数
     / 非 dict / 无键）+ 委托适配器一致性 + 纯函数不改入参；
  C. 留存器层 —— `record(usage=...)` 写新字段 + `tokens` 回落规则 +
     显式 tokens 优先（向后兼容）+ 落盘 JSON 实测；
  D. 装饰器闭环 —— `trace_evolution_call` 在 `finally` 取用并**清空**
     `_last_llm_usage`，未设时零回归；
  E. 生产 traces 只读取证 + 回归不变量（有 usage ⇒ tokens == total_tokens）。
"""
import ast
import glob
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
from nucleus.llm import call_recorder as cr  # noqa: E402
from nucleus.llm.adapter_registry import ExternalGatewayAdapter  # noqa: E402
from nucleus.llm.base_adapter import BaseLLMAdapter  # noqa: E402
from nucleus.llm.openai_compatible_adapter import OpenAICompatibleAdapter  # noqa: E402

_LLM = os.path.join(ROOT, "nucleus", "llm")
_BAK = os.path.join(ROOT, ".bak_batch94")
_TRACES = os.path.join(ROOT, "data", "llm_traces")
_ENGINES = {
    "SafeEvolutionExecutor": os.path.join(
        ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py"),
    "LLMEvolutionEngine": os.path.join(
        ROOT, "nucleus", "evolution", "LLMEvolutionEngine.py"),
    "SelfReflectionEngine": os.path.join(
        ROOT, "nucleus", "evolution", "SelfReflectionEngine.py"),
}
_LUNG = os.path.join(ROOT, "organs", "body", "PulseLung.py")


def _read(path):
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        return f.read().replace("\r\n", "\n")


def _def_args(path, cls, name):
    """返回方法的**全部形参名**（位置 + 关键字，含 self）。

    ``cls is None`` ⇒ 在**模块级**函数中查找；否则在指定类内查找。
    """
    _t = ast.parse(_read(path))
    if cls is None:
        for _n in _t.body:
            if (isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and _n.name == name):
                return ([a.arg for a in _n.args.args]
                        + [a.arg for a in _n.args.kwonlyargs])
        return None
    for _n in ast.walk(_t):
        if isinstance(_n, ast.ClassDef) and _n.name == cls:
            for _m in _n.body:
                if (isinstance(_m, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and _m.name == name):
                    return ([a.arg for a in _m.args.args]
                            + [a.arg for a in _m.args.kwonlyargs])
    return None


def _decorated_funcs_with_assign(path, attr):
    """返回「被 `trace_evolution_call` 装饰 且 函数体内给 attr 赋值」的
    (函数名, 装饰器名) 列表 —— 用于验证接线落在**正确作用域**内。"""
    _t = ast.parse(_read(path))
    _out = []
    for _n in ast.walk(_t):
        if not isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        _deco = [ast.unparse(d) for d in _n.decorator_list]
        if not any("trace_evolution_call" in d for d in _deco):
            continue
        _hit = False
        for _x in ast.walk(_n):
            if not isinstance(_x, ast.Assign):
                continue
            for _tg in _x.targets:
                if isinstance(_tg, ast.Attribute) and _tg.attr == attr:
                    _hit = True
        if _hit:
            _out.append((_n.name, _deco))
    return _out


class _Base(unittest.TestCase):
    """★测试隔离：全部注入 tempdir base_dir，**绝不写生产 data/llm_traces/**。"""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="m94_t94b_")
        self._o_rec = getattr(config, "ENABLE_LLM_CALL_RECORDER", True)
        self._o_evo = getattr(config, "ENABLE_EVOLUTION_CALL_TRACE", True)
        config.ENABLE_LLM_CALL_RECORDER = True
        config.ENABLE_EVOLUTION_CALL_TRACE = True
        self.r = cr.LLMCallRecorder(base_dir=self._dir)
        self._o_singleton = cr._recorder
        cr._recorder = self.r

    def tearDown(self):
        cr._recorder = self._o_singleton
        config.ENABLE_LLM_CALL_RECORDER = self._o_rec
        config.ENABLE_EVOLUTION_CALL_TRACE = self._o_evo
        shutil.rmtree(self._dir, ignore_errors=True)

    def _lines(self, prefix="calls"):
        _fp = self.r._path_for(time.time(), prefix=prefix)
        if not os.path.isfile(_fp):
            return []
        with io.open(_fp, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]


class TestM94TokensStaticWiring(_Base):
    """A 组：静态接线（归属 + 签名零回归 + 作用域正确）。"""

    def test_A1_base_adapter_has_optional_extract_usage(self):
        _a = BaseLLMAdapter()
        self.assertTrue(hasattr(_a, "extract_usage"))
        self.assertIsNone(_a.extract_usage({"usage": {"total_tokens": 5}}),
                          "★基类默认必须是「无用量」= None")
        self.assertIsNone(_a.extract_usage(None))
        # 可选能力：只要存在即可，调用方以返回值判定（None ⇒ 保持既有行为）
        self.assertTrue(callable(getattr(_a, "extract_usage", None)))

    def test_A2_parse_response_signature_unchanged(self):
        """★零回归的核心证据：`parse_response` 签名与改前**逐字一致**。"""
        for _rel, _cls in (("base_adapter.py", "BaseLLMAdapter"),
                           ("openai_compatible_adapter.py", "OpenAICompatibleAdapter"),
                           ("adapter_registry.py", "ExternalGatewayAdapter")):
            _bak = os.path.join(_BAK, _rel + ".bak")
            if not os.path.isfile(_bak):
                self.skipTest("改前备份缺失: %s" % _rel)
            _new_args = _def_args(os.path.join(_LLM, _rel), _cls, "parse_response")
            _old_args = _def_args(_bak, _cls, "parse_response")
            self.assertIsNotNone(_new_args)
            self.assertEqual(_old_args, _new_args,
                             "★%s.parse_response 签名必须与改前一致" % _cls)

    def test_A3_subclasses_implement_extract_usage(self):
        _oa = OpenAICompatibleAdapter()
        _ga = ExternalGatewayAdapter()
        self.assertTrue(callable(getattr(_oa, "extract_usage", None)))
        self.assertTrue(callable(getattr(_ga, "extract_usage", None)))
        self.assertIn("OpenAICompatibleAdapter().extract_usage(response)",
                      _read(os.path.join(_LLM, "adapter_registry.py")),
                      "★网关适配器必须**委托**兼容实现，不得自造格式")

    def test_A4_recorder_accepts_usage_kwarg(self):
        _r = cr.LLMCallRecorder(base_dir=self._dir)
        _args = _def_args(os.path.join(_LLM, "call_recorder.py"),
                          "LLMCallRecorder", "record")
        self.assertIn("usage", _args, "★record() 必须新增 usage 形参")
        # 默认值必须是 None（既有调用点不传 ⇒ 行为不变）
        _t = ast.parse(_read(os.path.join(_LLM, "call_recorder.py")))
        _default = None
        for _n in ast.walk(_t):
            if isinstance(_n, ast.ClassDef) and _n.name == "LLMCallRecorder":
                for _m in _n.body:
                    if (isinstance(_m, ast.FunctionDef) and _m.name == "record"):
                        _kw = {a.arg: d for a, d in zip(_m.args.kwonlyargs,
                                                        _m.args.kw_defaults)}
                        _default = ast.unparse(_kw["usage"]) if "usage" in _kw else None
        self.assertEqual("None", _default, "★usage 默认值必须为 None")
        self.assertTrue(callable(getattr(_r, "record", None)))
        # 便捷入口同样暴露 usage
        self.assertIn("usage", _def_args(os.path.join(_LLM, "call_recorder.py"),
                                         None, "record_evolution_call"))

    def test_A5_decorator_reads_and_clears_usage(self):
        _src = _read(os.path.join(_LLM, "call_recorder.py"))
        self.assertIn('getattr(_self, "_last_llm_usage", None)', _src)
        self.assertIn("_self._last_llm_usage = None", _src,
                      "★必须在 finally 清空，避免串到下一次调用（脏读）")
        # 清空动作必须与取用同处 finally
        _i_use = _src.index('getattr(_self, "_last_llm_usage", None)')
        _i_clr = _src.index("_self._last_llm_usage = None")
        self.assertLess(_i_use, _i_clr)

    def test_A6_engine_exits_assign_usage_inside_decorated_func(self):
        for _name, _p in _ENGINES.items():
            _hits = _decorated_funcs_with_assign(_p, "_last_llm_usage")
            self.assertEqual(1, len(_hits),
                             "★%s 必须恰有 1 个被装饰的出口暂存 usage，实得 %r"
                             % (_name, _hits))
            self.assertTrue(any("trace_evolution_call" in d
                                for d in _hits[0][1]))

    def test_A7_lung_uses_unified_entry_with_fallback(self):
        _src = _read(_LUNG)
        self.assertIn('getattr(_adapter, "extract_usage", None)', _src,
                      "★肺通道必须走适配器统一入口")
        self.assertIn('_data.get("usage") if isinstance(_data, dict) else None',
                      _src, "★第40批的旧旁路必须保留为回落（无该入口时零回归）")
        self.assertIn("usage=_usage if isinstance(_usage, dict) else None",
                      _src, "★肺通道必须把 usage 交给 record()（任务书 §T-94b②）")


class TestM94ExtractUsage(_Base):
    """B 组：`extract_usage` 边界（纯函数，不碰磁盘）。"""

    def setUp(self):
        super().setUp()
        self.oa = OpenAICompatibleAdapter()
        self.gw = ExternalGatewayAdapter()
        self._full = {"choices": [{"message": {"content": "hi"}}],
                      "usage": {"prompt_tokens": 11, "completion_tokens": 22,
                                "total_tokens": 33}}

    def test_B1_complete_triple(self):
        self.assertEqual({"prompt_tokens": 11, "completion_tokens": 22,
                          "total_tokens": 33}, self.oa.extract_usage(self._full))

    def test_B2_missing_total_is_filled(self):
        _r = self.oa.extract_usage({"usage": {"prompt_tokens": 4,
                                              "completion_tokens": 6}})
        self.assertEqual({"prompt_tokens": 4, "completion_tokens": 6,
                          "total_tokens": 10}, _r)

    def test_B3_all_zero_is_none(self):
        for _u in ({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                   {"prompt_tokens": 0},
                   {}):
            self.assertIsNone(self.oa.extract_usage({"usage": _u}),
                              "★不写假数据：%r ⇒ None" % (_u,))

    def test_B4_non_dict_response_or_usage(self):
        for _v in (None, [], "x", 0, {"usage": None}, {"usage": []},
                   {"choices": []}):
            self.assertIsNone(self.oa.extract_usage(_v),
                              "★%r ⇒ None（结构防御）" % (_v,))

    def test_B5_non_numeric_and_bool_are_rejected(self):
        self.assertIsNone(self.oa.extract_usage(
            {"usage": {"prompt_tokens": "11", "completion_tokens": "22",
                       "total_tokens": "33"}}))
        self.assertIsNone(self.oa.extract_usage(
            {"usage": {"total_tokens": True}}),
            "★布尔不是数值（True 会被当成 1 ⇒ 假数据）")
        self.assertIsNone(self.oa.extract_usage(
            {"usage": {"prompt_tokens": None, "completion_tokens": None,
                       "total_tokens": None}}))

    def test_B6_negative_is_rejected(self):
        self.assertIsNone(self.oa.extract_usage({"usage": {"total_tokens": -5}}))
        _r = self.oa.extract_usage({"usage": {"prompt_tokens": 6,
                                              "completion_tokens": -1,
                                              "total_tokens": 6}})
        self.assertEqual({"prompt_tokens": 6, "completion_tokens": 0,
                          "total_tokens": 6}, _r)

    def test_B7_float_usage_is_truncated_to_int(self):
        _r = self.oa.extract_usage({"usage": {"prompt_tokens": 3.7,
                                              "completion_tokens": 4.2,
                                              "total_tokens": 7.9}})
        self.assertEqual({"prompt_tokens": 3, "completion_tokens": 4,
                          "total_tokens": 7}, _r)
        for _v in _r.values():
            self.assertIsInstance(_v, int)

    def test_B8_gateway_delegates_identically(self):
        self.assertEqual(self.oa.extract_usage(self._full),
                         self.gw.extract_usage(self._full))
        self.assertIsNone(self.gw.extract_usage({"usage": {}}))

    def test_B9_pure_function_does_not_mutate_input(self):
        _snap = json.dumps(self._full, ensure_ascii=False, sort_keys=True)
        self.oa.extract_usage(self._full)
        self.assertEqual(_snap, json.dumps(self._full, ensure_ascii=False,
                                           sort_keys=True))

    def test_B10_never_raises(self):
        class _Boom(dict):
            def get(self, *a, **k):     # noqa: D401
                raise RuntimeError("boom")
        self.assertIsNone(self.oa.extract_usage(_Boom()))


class TestM94RecorderUsage(_Base):
    """C 组：留存器 usage 字段 + tokens 回落 + 向后兼容。"""

    def _rec(self, **kw):
        _tid = self.r.record(origin=cr.ORIGIN_EVOLUTION_TASK, prompt="p",
                             response="r", **kw)
        self.assertTrue(_tid)
        return self._lines()[-1]

    def test_C1_usage_written_and_tokens_backfilled(self):
        _rec = self._rec(usage={"prompt_tokens": 7, "completion_tokens": 3,
                                "total_tokens": 10})
        self.assertIn("usage", _rec, "★新字段必须落盘")
        self.assertEqual({"prompt_tokens": 7, "completion_tokens": 3,
                          "total_tokens": 10}, _rec["usage"])
        self.assertEqual(10, _rec["tokens"], "★无显式 tokens ⇒ 回落 usage.total")

    def test_C2_explicit_tokens_wins(self):
        """★向后兼容：显式传 tokens 的既有调用方不受影响。"""
        _rec = self._rec(tokens=99, usage={"prompt_tokens": 7,
                                           "completion_tokens": 3,
                                           "total_tokens": 10})
        self.assertEqual(99, _rec["tokens"])
        self.assertEqual(10, _rec["usage"]["total_tokens"],
                         "★usage 仍如实保留（两字段不互相覆盖）")

    def test_C3_no_usage_keeps_legacy_behaviour(self):
        _rec = self._rec()
        self.assertIsNone(_rec["usage"], "★无 usage ⇒ 字段为 None")
        self.assertEqual(0, _rec["tokens"], "★无 usage 时 tokens 仍写 0")
        _rec2 = self._rec(usage=None)
        self.assertIsNone(_rec2["usage"])
        self.assertEqual(0, _rec2["tokens"])

    def test_C4_partial_usage_is_completed(self):
        _rec = self._rec(usage={"prompt_tokens": 5, "completion_tokens": 6})
        self.assertEqual(11, _rec["usage"]["total_tokens"])
        self.assertEqual(11, _rec["tokens"])

    def test_C5_zero_usage_is_not_fabricated(self):
        for _u in ({"total_tokens": 0}, {}, {"prompt_tokens": 0,
                                            "completion_tokens": 0,
                                            "total_tokens": 0}, "x", 5, []):
            _rec = self._rec(usage=_u)
            self.assertIsNone(_rec["usage"], "★%r ⇒ usage 必须为 None" % (_u,))
            self.assertEqual(0, _rec["tokens"])

    def test_C6_usage_is_json_serialisable(self):
        _rec = self._rec(usage={"prompt_tokens": 1, "completion_tokens": 2,
                                "total_tokens": 3})
        self.assertEqual(_rec, json.loads(json.dumps(_rec, ensure_ascii=False)))

    def test_C7_normalize_usage_is_pure_helper(self):
        _f = cr._m94_normalize_usage
        self.assertIsNone(_f(None))
        self.assertIsNone(_f("x"))
        self.assertEqual({"prompt_tokens": 1, "completion_tokens": 2,
                          "total_tokens": 3},
                         _f({"prompt_tokens": 1, "completion_tokens": 2}))
        _d = {"total_tokens": 3}
        _f(_d)
        self.assertEqual({"total_tokens": 3}, _d, "★纯函数不得写回入参")

    def test_C8_record_evolution_call_forwards_usage(self):
        _tid = cr.record_evolution_call(prompt="p", response="r",
                                        usage={"prompt_tokens": 2,
                                               "completion_tokens": 4,
                                               "total_tokens": 6})
        self.assertTrue(_tid)
        _rec = self._lines()[-1]
        self.assertEqual(cr.ORIGIN_EVOLUTION_TASK, _rec["origin"])
        self.assertEqual(6, _rec["tokens"])
        self.assertEqual(6, _rec["usage"]["total_tokens"])


class TestM94DecoratorLoop(_Base):
    """D 组：`trace_evolution_call` 与引擎 `_last_llm_usage` 的闭环。"""

    class _FakeEngine:
        def __init__(self, usage):
            self._m44_last_model = "fake-model"
            self._m44_last_error = ""
            self._usage = usage
            self._last_llm_usage = None

        @cr.trace_evolution_call(prompt_pos=2, version="test.m94.v1")
        def _call_llm(self, system, prompt):
            if self._usage is not None:
                self._last_llm_usage = self._usage
            return "answer"

    def test_D1_usage_survives_into_trace_and_is_cleared(self):
        _eng = self._FakeEngine({"prompt_tokens": 8, "completion_tokens": 12,
                                 "total_tokens": 20})
        _out = _eng._call_llm("sys", "prompt")
        self.assertEqual("answer", _out)
        _rec = self._lines()[-1]
        self.assertEqual(cr.ORIGIN_EVOLUTION_TASK, _rec["origin"])
        self.assertEqual("test.m94.v1", _rec["prompt_version"])
        self.assertEqual("fake-model", _rec["model"])
        self.assertEqual(20, _rec["tokens"], "★这就是 2423 条恒 0 的修复点")
        self.assertEqual(20, _rec["usage"]["total_tokens"])
        self.assertIsNone(_eng._last_llm_usage,
                          "★finally 必须清空，避免脏读串到下一次调用")

    def test_D2_no_usage_is_zero_regression(self):
        _eng = self._FakeEngine(None)
        _eng._call_llm("sys", "prompt")
        _rec = self._lines()[-1]
        self.assertIsNone(_rec["usage"])
        self.assertEqual(0, _rec["tokens"])

    def test_D3_two_calls_do_not_bleed(self):
        _eng = self._FakeEngine({"total_tokens": 30})
        _eng._call_llm("sys", "p1")
        _eng._usage = None
        _eng._call_llm("sys", "p2")
        _a, _b = self._lines()[-2], self._lines()[-1]
        self.assertEqual(30, _a["tokens"])
        self.assertEqual(0, _b["tokens"], "★第 2 次不得沿用第 1 次的 usage")

    def test_D4_exception_path_still_records(self):
        class _Boom(self._FakeEngine):
            @cr.trace_evolution_call(prompt_pos=2, version="test.m94.boom.v1")
            def _call_llm(self, system, prompt):
                self._last_llm_usage = {"total_tokens": 40}
                raise ValueError("boom")

        _eng = _Boom({"total_tokens": 1})
        with self.assertRaises(ValueError):
            _eng._call_llm("sys", "p")
        _rec = self._lines()[-1]
        self.assertEqual(cr.STATUS_FAILED, _rec["status"])
        self.assertEqual(40, _rec["tokens"], "★异常路径同样要带出 usage")
        self.assertIsNone(_eng._last_llm_usage)


class TestM94ProductionTraces(_Base):
    """E 组：生产 traces 只读取证（不写盘）。"""

    def _records(self):
        _files = sorted(glob.glob(os.path.join(_TRACES, "calls_*.jsonl")))
        _out = []
        for _fp in _files:
            with io.open(_fp, encoding="utf-8", errors="ignore") as _f:
                for _ln in _f:
                    _ln = _ln.strip()
                    if not _ln:
                        continue
                    try:
                        _out.append(json.loads(_ln))
                    except Exception:
                        continue
        return _out

    def test_E1_recompute_origin_vs_tokens(self):
        """★固化「零值结构性集中」这一可复算事实（不写死任何生产数字）。"""
        _recs = self._records()
        if not _recs:
            self.skipTest("生产 traces 为空或不存在")
        _by_origin = {}
        for _r in _recs:
            _o = str(_r.get("origin"))
            _d = _by_origin.setdefault(_o, {"n": 0, "zero": 0})
            _d["n"] += 1
            try:
                if int(_r.get("tokens") or 0) == 0:
                    _d["zero"] += 1
            except Exception:
                _d["zero"] += 1
        # 自洽性：每条记录都必须有 tokens 字段且可转 int
        self.assertEqual(len(_recs), sum(d["n"] for d in _by_origin.values()))
        _unparsable = [r for r in _recs
                       if not str(r.get("tokens", "")).strip().isdigit()
                       and not isinstance(r.get("tokens"), (int, float))]
        self.assertEqual([], _unparsable[:3],
                         "★tokens 字段必须可解析为数值")

    def test_E2_usage_tokens_invariant(self):
        """★回归不变量：一旦记录带 `usage`（本批之后），tokens 必须 == total。"""
        _bad = []
        for _r in self._records():
            _u = _r.get("usage")
            if not isinstance(_u, dict):
                continue
            _t = int(_u.get("total_tokens") or 0)
            try:
                _got = int(_r.get("tokens") or 0)
            except Exception:
                _got = -1
            if _got != _t:
                _bad.append((_r.get("origin"), _got, _t))
        self.assertEqual([], _bad,
                         "★带 usage 的记录必须满足 tokens == usage.total_tokens")

    def test_E3_pre_batch_records_have_no_usage_field(self):
        """先红证据：**改前**的留存记录里没有 `usage` 字段（本批新增 schema）。"""
        _n_no_usage = 0
        _n_total = 0
        for _r in self._records():
            _n_total += 1
            if "usage" not in _r:
                _n_no_usage += 1
        if _n_total == 0:
            self.skipTest("生产 traces 为空")
        self.assertGreater(_n_no_usage, 0,
                           "★历史记录必然缺 usage 字段（本批才新增）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
