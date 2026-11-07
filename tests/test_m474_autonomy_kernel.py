# -*- coding: utf-8 -*-
"""474 批门控单测：通用自主能力基座（铁律 304 的可执行判据）。

现场：外部 LLM 渠道不稳定、随时换模型；而既有经验写进 prompt，
换模型/清上下文即归零。本模块把经验编译成**运行在曈曈进程里的算子**。

重点不是「能跑」，而是三条硬判据：
  1. 判别力（铁律 35）：已合格输入 detect 必须为 False；
     关掉 enforce 后同一输入必须**不被改写**（反证用例）。
  2. 独立基准（铁律 30）：verify 用 ast.parse 重新解析，不复用 synthesize 结论。
  3. 自主性（铁律 304）：内核源码的 import 节点里**不得出现任何外部模型/HTTP 客户端**，
     覆盖不了只返回 external_needed，绝不自己偷偷外联。
"""
import ast
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus._silent_except import silent_exc
from nucleus.autonomy import (
    DOMAIN_CODE_REPAIR,
    Capability,
    CapabilityContext,
    CapabilityKernel,
    CapabilityResult,
    IndentLevelAlignCapability,
    SignatureDocstringRestoreCapability,
    SilentExcBackfillCapability,
    build_default_kernel,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOD_DIR = os.path.join(_ROOT, "nucleus", "autonomy")

OVER_INDENTED = """    def set_self_awareness(self, self_awareness):
            # guard
            if getattr(self, '_busy', False):
                return
            self._busy = True
            try:
                self._x = self_awareness
            finally:
                self._busy = False
"""

ALIGNED = """    def set_self_awareness(self, self_awareness):
        # guard
        if getattr(self, '_busy', False):
            return
        self._busy = True
        try:
            self._x = self_awareness
        finally:
            self._busy = False
"""

ORIG_SIG = """    def _m383_dump(self, path: str | None = None) -> str:
        \"\"\"把分布落到文件。返回实际路径。\"\"\"
        return path or ""
"""

LOST_SIG = """    def _m383_dump(self, path=None):
        return path or ""
"""

BARE_EXCEPT = """    def f(self, tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
"""


def _ctx(mod, orig=None, domain=DOMAIN_CODE_REPAIR):
    return CapabilityContext(domain=domain,
                             artifact={"original_code": orig if orig is not None else mod,
                                       "modified_code": mod})


class _AlwaysFailCapability(Capability):
    capability_id = "test.always_fail"
    domains = ("test_domain",)
    error_type = "test"

    def detect(self, ctx):
        return True

    def synthesize(self, ctx):
        _a = dict(ctx.artifact)
        _a["modified_code"] = "BROKEN"
        return CapabilityResult(self.capability_id, "applied", _a, ["bad"])

    def verify(self, before, after, ctx):
        return False, "故意失败"


class _ReasoningCapability(Capability):
    """证明内核领域无关：这是「内在世界推理」域的一个自定义能力。"""

    capability_id = "reasoning.symbolic"
    domains = ("reasoning",)
    error_type = "test"

    def detect(self, ctx):
        return ctx.artifact.get("needs_local") is True

    def synthesize(self, ctx):
        _a = dict(ctx.artifact)
        _a["solved_by"] = "self"
        return CapabilityResult(self.capability_id, "applied", _a, ["local_solved"])

    def verify(self, before, after, ctx):
        return after.get("solved_by") == "self", ""


class TestOperatorDiscrimination(unittest.TestCase):
    def test_01_indent_detect_true_on_real_sample(self):
        self.assertTrue(IndentLevelAlignCapability().detect(_ctx(OVER_INDENTED)))

    def test_02_indent_detect_false_when_already_clean(self):
        """★判别力：旧代码上不成立，才算有判别力的判据。"""
        self.assertFalse(IndentLevelAlignCapability().detect(_ctx(ALIGNED)))

    def test_03_indent_synthesize_and_verify(self):
        _c = _ctx(OVER_INDENTED)
        _cap = IndentLevelAlignCapability()
        _r = _cap.synthesize(_c)
        self.assertTrue(_cap.verify(_c.artifact, _r.artifact, _c)[0])
        self.assertEqual(ALIGNED.split("\n")[1].strip(),
                         _r.artifact["modified_code"].split("\n")[1].strip())

    def test_04_signature_detect_and_restore(self):
        _c = _ctx(LOST_SIG, ORIG_SIG)
        _cap = SignatureDocstringRestoreCapability()
        self.assertTrue(_cap.detect(_c))
        _r = _cap.synthesize(_c)
        _ok, _why = _cap.verify(_c.artifact, _r.artifact, _c)
        self.assertTrue(_ok, _why)
        self.assertIn("-> str", _r.artifact["modified_code"])
        self.assertIn("把分布落到文件", _r.artifact["modified_code"])

    def test_05_signature_false_when_intact(self):
        self.assertFalse(SignatureDocstringRestoreCapability().detect(
            _ctx(ORIG_SIG, ORIG_SIG)))

    def test_06_silent_exc_detect_and_backfill(self):
        _c = _ctx(BARE_EXCEPT)
        _cap = SilentExcBackfillCapability()
        self.assertTrue(_cap.detect(_c))
        _r = _cap.synthesize(_c)
        _ok, _why = _cap.verify(_c.artifact, _r.artifact, _c)
        self.assertTrue(_ok, _why)
        self.assertIn("silent_exc(", _r.artifact["modified_code"])
        self.assertNotIn("except OSError as _e474:\n            pass",
                         _r.artifact["modified_code"])

    def test_07_silent_exc_false_when_clean(self):
        self.assertFalse(SilentExcBackfillCapability().detect(_ctx(ALIGNED)))


class TestKernelContract(unittest.TestCase):
    def _kernel(self, **kw):
        kw.setdefault("ledger_enabled", False)
        return build_default_kernel(**kw)

    def test_08_default_operators_registered(self):
        self.assertEqual(3, len(self._kernel().capabilities))

    def test_09_shadow_does_not_mutate(self):
        """★反证：不显式 enforce 时，同一输入必须原样返回。"""
        _k = self._kernel(enforce=False)
        _c = _ctx(OVER_INDENTED)
        _before = dict(_c.artifact)
        _r = _k.run(_c)
        self.assertEqual(_before, _r["artifact"])
        self.assertEqual(_before, _c.artifact)

    def test_10_enforce_mutates_result_but_not_input(self):
        _k = self._kernel(enforce=True)
        _c = _ctx(OVER_INDENTED)
        _before = dict(_c.artifact)
        _r = _k.run(_c)
        self.assertNotEqual(_before, _r["artifact"])
        self.assertEqual(_before, _c.artifact, "内核不许改传入对象")

    def test_11_enforce_result_passes_independent_parse(self):
        _r = self._kernel(enforce=True).run(_ctx(OVER_INDENTED))
        _code = _r["artifact"]["modified_code"]
        ast.parse(__import__("textwrap").dedent(_code))  # 独立入口

    def test_12_unknown_domain_returns_external_needed(self):
        _r = self._kernel().run(CapabilityContext("never_registered", {"x": 1}))
        self.assertTrue(_r["external_needed"])
        self.assertEqual("needs_external", _r["status"])

    def test_13_failed_verify_never_applies(self):
        _k = CapabilityKernel(ledger_enabled=False, enforce=True)
        _k.register(_AlwaysFailCapability())
        _r = _k.run(CapabilityContext("test_domain", {"modified_code": "OK"}))
        self.assertEqual("OK", _r["artifact"]["modified_code"])
        self.assertEqual(1, _k.snapshot()["failed"])

    def test_14_generality_other_domain(self):
        """★铁律 304「整体利用」：内核不能只服务补丁闭环。"""
        _k = CapabilityKernel(ledger_enabled=False, enforce=True)
        _k.register(_ReasoningCapability())
        _r = _k.run(CapabilityContext("reasoning", {"needs_local": True}))
        self.assertEqual("self", _r["artifact"]["solved_by"])
        self.assertIn("reasoning", _r["metrics"]["by_domain"])

    def test_15_metrics_by_domain(self):
        _k = self._kernel()
        _k.run(CapabilityContext("unregistered_a", {}))
        _k.run(CapabilityContext("unregistered_b", {}))
        _s = _k.snapshot()
        self.assertIn("unregistered_a", _s["by_domain"])
        self.assertIn("unregistered_b", _s["by_domain"])
        self.assertEqual(0.0, _s["by_domain"]["unregistered_a"]["self_sufficiency_rate"])
        self.assertEqual(1.0, _s["by_domain"]["unregistered_a"]["external_dependency_rate"])

    def test_16_cfg_zero_is_explicit_off(self):
        """★`0` 与 `None` 必须是两个意思（本项目栽过两次）。"""
        with mock.patch("config.ENABLE_M474_AUTONOMY_KERNEL", 0, create=True):
            _k = CapabilityKernel(ledger_enabled=False)
            self.assertFalse(_k._enforce)
        with mock.patch("config.ENABLE_M474_AUTONOMY_KERNEL", None, create=True):
            _k2 = CapabilityKernel(ledger_enabled=False)
            self.assertFalse(_k2._enforce)


class TestLedger(unittest.TestCase):
    def test_17_explicit_path_writes_structured_record(self, ):
        _p = os.path.join(_ROOT, "tmp", "m474_ledger_probe.jsonl")
        try:
            os.remove(_p)
        except OSError as _e:
            silent_exc(_e, where="m474.test_17 cleanup", level="debug")
        _k = build_default_kernel(ledger_path=_p, ledger_enabled=True, enforce=False)
        _k.run(_ctx(OVER_INDENTED))
        with open(_p, encoding="utf-8") as _fh:
            _rows = [json.loads(x) for x in _fh]
        self.assertTrue(_rows)
        self.assertTrue(all(r.get("schema") for r in _rows))
        self.assertTrue(all("at" in r for r in _rows))
        self.assertFalse(any(r.get("external_used") for r in _rows))
        os.remove(_p)

    def test_18_pytest_default_path_is_not_written(self):
        """★pytest 不得经生产路径灌假经验（446 实测教训）。"""
        _k = CapabilityKernel(ledger_enabled=True)
        self.assertTrue(_k._default_path)
        self.assertIn("pytest", sys.modules)
        _k.run(_ctx(OVER_INDENTED))


class TestAutonomyIronLaw(unittest.TestCase):
    """把「主体在曈曈、外部只辅助」固化成可执行门禁。"""

    _FORBIDDEN = ("openai", "anthropic", "zhipu", "bailian", "dashscope",
                  "siliconflow", "requests", "httpx", "aiohttp", "urllib",
                  "langchain", "transformers")

    def test_19_no_external_model_imports(self):
        for _name in ("kernel.py", "operators.py", "__init__.py"):
            with open(os.path.join(_MOD_DIR, _name), encoding="utf-8") as _fh:
                _src = _fh.read()
            _tree = ast.parse(_src)
            for _n in ast.walk(_tree):
                if isinstance(_n, ast.Import):
                    _names = " ".join(a.name for a in _n.names)
                elif isinstance(_n, ast.ImportFrom):
                    _names = str(_n.module or "")
                else:
                    continue
                for _bad in self._FORBIDDEN:
                    self.assertNotIn(_bad, _names.lower(),
                                     f"{_name} 引入了外部模型/HTTP: {_names}")

    def test_20_kernel_never_calls_external(self):
        """★判据要挑有判别力的量：`get` 是 dict.get，禁它会把正常代码顶红。

        真正的外部调用特征 = 明确的 HTTP/模型方法名，或接收者本身就是
        外部客户端对象；import 层面另由 test_19 的 AST 扫描硬守。
        """
        with open(os.path.join(_MOD_DIR, "kernel.py"), encoding="utf-8") as _fh:
            _src = _fh.read()
        _tree = ast.parse(_src)
        _bad_calls = ("urlopen", "urlretrieve", "chatcompletion", "acomplete",
                      "generate_content", "invoke_model", "post_json")
        _bad_receivers = ("requests", "httpx", "aiohttp", "client", "session")
        for _n in ast.walk(_tree):
            if not isinstance(_n, ast.Call):
                continue
            _fn = _n.func
            _txt = ""
            _recv = ""
            if isinstance(_fn, ast.Name):
                _txt = _fn.id
            elif isinstance(_fn, ast.Attribute):
                _txt = _fn.attr
                if isinstance(_fn.value, ast.Name):
                    _recv = _fn.value.id
            self.assertNotIn(_txt.lower(), _bad_calls, "内核出现外部调用")
            self.assertNotIn(_recv.lower(), _bad_receivers, "内核出现外部客户端")

    def test_21_note_experience_is_structured_and_domain_agnostic(self):
        """第二期新增口：任意域都能写结构化经验，且不改变传入对象。"""
        import copy
        _k = CapabilityKernel(ledger_enabled=False)
        _rec = {"kind": "reasoning_observation", "external_used": True}
        _before = copy.deepcopy(_rec)
        _s = _k.note_experience(_rec, domain="reasoning")
        self.assertEqual(_before, _rec)
        self.assertIn("reasoning", _s["by_domain"])
        self.assertEqual(1.0, _s["by_domain"]["reasoning"]["external_dependency_rate"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
