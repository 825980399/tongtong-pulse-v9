# -*- coding: utf-8 -*-
"""408 刀1 判据：错答案路（把经验库的 example_question 当答案）已**整条删除**。

背景（407 实测）：`ReasoningExperience.search()` 返回的 `matched_example` 是
`example_question[:80]` —— **另一个人的问题**；而经验池 128 条的全部键里
没有任何字段承载答案。407 只加了开关（默认 False），408 把路删掉。

五组断言：
  A 符号不存在：类上没有 `_derive_experience_default` / `_m407_reuse_example_answer`，
    config 上没有对应开关（连"打开"的入口都没有）。
  B 路由行为：unknown 类型命中 → **不产出答案** → 走 `_fallback_to_lung_model()`；
    状态里不会出现 `experience_default`。
  C 正控：deductive 类型命中且算子产出答案时，路由**仍能产出答案** ——
    证明 B 组不是"任何输入都掉兜底"的恒真断言。
  D 全仓口径：生产代码里 `experience_default` 0 命中（tests/tmp/备份目录除外）。
  E 结构（AST）：`_use_default` 分支体**不产出答案**（无 `_derive_*` 调用、
    不给 `_experience_result` 赋值），但必须保留 `self._log` 留痕。
    ★只看 `_if.body`，不看 `orelse` —— `elif` 链在 AST 里挂在 `orelse`，
      走 `ast.walk(_if)` 会把别的类型分支的 `_derive_*()` 算进来。
"""
from __future__ import annotations

import ast
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus._silent_except import silent_exc  # noqa: E402  (须先插 sys.path)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IW = os.path.join(_ROOT, "organs", "brain", "PulseInnerWorld.py")

#: 生产面扫描时跳过的目录前缀（相对项目根）
_SKIP_PREFIXES = ("tests/", "tmp/", "data/", ".bak", ".release-tmp", ".workbuddy/",
                  "__pycache__", "docs/")


def _iw_src():
    return open(_IW, "rb").read().decode("utf-8", "replace")


class _RXFake:
    """假的经验检索器：`search()` 恒定返回同一条 unknown 经验。"""

    last_search_diagnostics = {"best_score": 0.6803, "threshold": 0.5,
                               "n_experiences": 128, "hit": True,
                               "n_excluded_polluted": 98, "n_excluded_type": 16}

    def __init__(self, match):
        self._match = match

    def search(self, question, exclude_types=None):  # noqa: ARG002
        return self._match


class _Ctx:
    question = "帮我把这次经历整理成三条要点"
    guidance = None
    user_name = "测试"
    correlation_id = "m408k1"
    payload = {}
    empathetic_note = ""
    _reasoning_start_time = 0.0
    _question_complexity = 0.3
    _memory_context = {}


def _mk_iw(logs):
    from organs.brain.PulseInnerWorld import PulseInnerWorld
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw._log = lambda lvl, msg: logs.append((lvl, msg))
    iw._inference_count = 0
    iw.node_pool = None
    iw._emit = lambda *a, **k: None
    iw._cache_inference = lambda *a, **k: None
    iw._trace_inference = lambda *a, **k: None
    iw._enhance_answer = lambda answer, **k: answer
    return iw


# ------------------------------------------------------------------ A
class TestA_SymbolsGone(unittest.TestCase):
    def test_01_derive_method_absent(self):
        from organs.brain.PulseInnerWorld import PulseInnerWorld
        self.assertFalse(hasattr(PulseInnerWorld, "_derive_experience_default"))

    def test_02_switch_method_absent(self):
        from organs.brain.PulseInnerWorld import PulseInnerWorld
        self.assertFalse(hasattr(PulseInnerWorld, "_m407_reuse_example_answer"))

    def test_03_config_switch_absent(self):
        import config
        self.assertFalse(hasattr(config, "EXPERIENCE_REUSE_EXAMPLE_AS_ANSWER"),
                         "config 里还有该开关 —— 等于留了一个'打开错答案'的入口")

    def test_04_source_text_clean(self):
        _s = _iw_src()
        for _sym in ("_derive_experience_default", "_m407_reuse_example_answer",
                     "EXPERIENCE_REUSE_EXAMPLE_AS_ANSWER"):
            self.assertNotIn(_sym, _s, "%s 仍在 PulseInnerWorld 源码/注释里" % _sym)


# ------------------------------------------------------------------ B
class TestB_RouteFallsBack(unittest.TestCase):
    MATCH = {
        "derivation_type": "unknown",
        "confidence": 0.68,
        "source": "local_fallback",
        "matched_example": "我需要更多的一手资料和实践来建立扎实的认知",
    }

    def _run(self, match):
        logs = []
        iw = _mk_iw(logs)
        calls = []
        iw._fallback_to_lung_model = lambda ctx, g=None, **kw: (
            calls.append(1), {"status": "inference_fallback_to_lung", "answer": None})[1]
        with patch("nucleus.mnemosyne.ReasoningExperience.get_reasoning_experience",
                   lambda *a, **k: _RXFake(match)):
            res = iw._detect_experience_route(_Ctx())
        return res, logs, calls

    def test_10_unknown_type_returns_no_answer(self):
        res, logs, calls = self._run(dict(self.MATCH))
        self.assertIsInstance(res, dict)
        self.assertIsNone(res.get("answer"), "命中 unknown 经验后仍产出了答案")
        self.assertEqual(1, len(calls), "未走 _fallback_to_lung_model")
        self.assertTrue(any("408刀1" in str(m) for _, m in logs),
                        "缺少「仅作参考、不当答案」留痕")

    def test_11_status_never_experience_default(self):
        res, _logs, _calls = self._run(dict(self.MATCH))
        self.assertNotEqual("experience_default", res.get("status"))
        self.assertEqual("experience_failed_to_lung", res.get("status"))

    def test_12_hit_is_still_traced(self):
        """命中仍要留痕（谁被命中），否则"经验路由还活着吗"没法观测。"""
        res, logs, _calls = self._run(dict(self.MATCH))
        self.assertIsInstance(res, dict)
        _all = " ".join(str(m) for _l, m in logs)
        self.assertIn("经验匹配类型", _all)


# ------------------------------------------------------------------ C 正控
class TestC_PositiveControl(unittest.TestCase):
    """★正控：别的类型仍能产出答案 —— 证明 B 组不是恒真。"""

    MATCH = {"derivation_type": "deductive", "confidence": 0.9,
             "source": "local", "matched_example": "规则1→A→B"}

    def test_20_deductive_still_answers(self):
        logs = []
        iw = _mk_iw(logs)
        iw._derive_deductive_chain = lambda q: "推导结论：B"
        calls = []
        iw._fallback_to_lung_model = lambda ctx, g=None, **kw: calls.append(1) or {}
        with patch("nucleus.mnemosyne.ReasoningExperience.get_reasoning_experience",
                   lambda *a, **k: _RXFake(dict(self.MATCH))):
            res = iw._detect_experience_route(_Ctx())
        self.assertEqual({"status": "experience_deductive", "answer": "推导结论：B"}, res)
        self.assertEqual([], calls, "能产出答案时不得再走兜底")


# ------------------------------------------------------------------ D 全仓口径
class TestD_NoExperienceDefaultAnywhere(unittest.TestCase):
    def _iter_prod(self):
        for _dp, _dn, _fn in os.walk(_ROOT):
            _rel = os.path.relpath(_dp, _ROOT).replace("\\", "/")
            if _rel != "." and any(_rel.startswith(p.rstrip("/")) for p in _SKIP_PREFIXES):
                _dn[:] = []
                continue
            _dn[:] = [d for d in _dn if not d.startswith(".bak") and d not in
                      ("__pycache__", ".git", ".release-tmp", "node_modules", "tmp")]
            for _f in _fn:
                if _f.endswith(".py"):
                    yield os.path.join(_dp, _f)

    def test_30_zero_hits_in_production(self):
        hits = []
        for _p in self._iter_prod():
            try:
                _t = open(_p, encoding="utf-8", errors="replace").read()
            except OSError as _e:
                # 单文件读不了（占用/权限/正在被删）不该让整仓扫描失败，
                # 但也不许静默 —— 走统一可见化（cw2 门禁要求）。
                silent_exc(_e, "tests/test_m408_k1_no_bad_answer.py 扫描读文件")
                continue
            if "experience_default" in _t:
                hits.append(os.path.relpath(_p, _ROOT).replace("\\", "/"))
        self.assertEqual([], hits, "生产代码仍出现 experience_default：%s" % hits)

    def test_31_scanner_is_not_vacuous(self):
        """★0 命中正控：扫描器必须真的能扫到文件（否则"0 命中"没意义）。"""
        self.assertGreater(len(list(self._iter_prod())), 200)


# ------------------------------------------------------------------ E AST
class TestE_UseDefaultBranch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(_iw_src())
        cls.fn = None
        for n in ast.walk(cls.tree):
            if isinstance(n, ast.FunctionDef) and n.name == "_detect_experience_route":
                cls.fn = n
        assert cls.fn is not None, "找不到 _detect_experience_route"

    def _use_default_if(self):
        """定位**「调用对应的推理算子」那一段**里的 `if _use_default:`。

        ★不能取"第一个 `if _use_default:`" —— 上面还有一处同名的日志分支
          （`unknown/空类型 → 使用默认内容相似度匹配`）。用「同一 body 里的
          前一条语句是 `_experience_result = None`」定位，语义上就是那段派发。
        """
        for _node in ast.walk(self.fn):
            _body = getattr(_node, "body", None)
            if not isinstance(_body, list):
                continue
            for _i in range(len(_body) - 1):
                _a, _b = _body[_i], _body[_i + 1]
                if not isinstance(_a, ast.Assign) or not isinstance(_b, ast.If):
                    continue
                _tg = [t.id for t in _a.targets if isinstance(t, ast.Name)]
                if "_experience_result" not in _tg:
                    continue
                if isinstance(_b.test, ast.Name) and _b.test.id == "_use_default":
                    return _b
        return None

    def _branch_nodes(self, _if):
        """只取 `if _use_default:` 的**真分支体**，不含 `orelse`。

        ★★这是本文件最容易写错的一处：Python 的 `if/elif` 链在 AST 里是
          `If(orelse=[If(test=...), If(...)])` 嵌套 —— 那 7 个
          `elif _exp_type == "deductive" / "inductive" / ...` 分支全挂在
          `orelse` 里。`ast.walk(_if)` 会一并走进去，把**别的类型分支**里的
          `self._derive_*()` 算成"`_use_default` 分支体里的调用"，
          于是判据恒红（408 首跑实测：报出 7 条 `_derive_*`）。
          ⇒ 判"这个分支体里有什么"必须只看 `_if.body`。
        """
        _out = []
        for _n in _if.body:
            _out.append(_n)
            _out.extend(ast.walk(_n))
        return _out

    def test_40_branch_produces_no_answer(self):
        """派发分支体里不许**产出答案**：既无 `_derive_*` 调用，也无 `_experience_result` 赋值。

        ★比"整个分支体不许有 Call"更精确 —— 分支里必须保留一条 `_log` 留痕，
          否则"命中了但不说话"就没法观测（那也是一种静默失败）。
        """
        _if = self._use_default_if()
        self.assertIsNotNone(_if, "派发段的 `_use_default` 分支没了？")
        _nodes = self._branch_nodes(_if)
        _calls = [ast.unparse(n)[:80] for n in _nodes if isinstance(n, ast.Call)]
        _derives = [c for c in _calls if "_derive_" in c]
        self.assertEqual([], _derives, "分支体里出现推理算子调用：%s" % _derives)
        _assigned = [t.id for n in _nodes if isinstance(n, ast.Assign)
                     for t in n.targets if isinstance(t, ast.Name)]
        self.assertNotIn("_experience_result", _assigned,
                         "分支仍在给 `_experience_result` 赋值 —— 错答案路回来了")

    def test_41_branch_still_logs(self):
        """分支体里仍要留痕 —— 删调用不等于删观测。"""
        _if = self._use_default_if()
        self.assertIsNotNone(_if)
        _nodes = self._branch_nodes(_if)
        self.assertTrue(any(isinstance(n, ast.Attribute) and n.attr == "_log"
                            for n in _nodes),
                        "分支体里没有 _log 调用")

    def test_42_other_derivers_intact(self):
        """其余 7 个推理算子分支一字未动（防"顺手删干净"）。"""
        _names = set()
        for n in ast.walk(self.fn):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                _names.add(n.func.attr)
        for _need in ("_derive_deductive_chain", "_derive_inductive_from_samples",
                      "_derive_self_analogy", "_derive_conflict_resolution",
                      "_derive_multi_variable", "_derive_long_term_evolution",
                      "_derive_meta_reflection", "_fallback_to_lung_model"):
            self.assertIn(_need, _names, "推理算子分支被误删：%s" % _need)


if __name__ == "__main__":
    unittest.main(verbosity=2)
