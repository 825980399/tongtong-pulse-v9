# -*- coding: utf-8 -*-
"""407 刀2 判据：`_SKIP_TYPES` 在**打分之前**就排除，不是命中后再砍。

七组断言：
  A `exclude_types` 语义：`None` 与 `()` 都不排除；给定类型整类出局。
  B 打分前（AST）：类型判断的 `continue` 行号早于 `_calculate_feature_similarity`
    —— 与刀1 污染闸同一位置，不是"命中后再判"。
  C 埋点：`n_excluded_type` 是 int，且与 `n_excluded_polluted` **各自独立计数**。
  D 向后兼容：不传 `exclude_types` 的调用点行为不变（`n_excluded_type` 恒 0）。
  E `record()` 去重显式传 `()` —— 远程类型条目之间**仍能互相去重**。
  F 路由侧（AST）：`_SKIP_TYPES` 定义在 `search()` 调用**之前**，且作为
    `exclude_types` 实参传入；清单本身**一字未改**（任务书明令）。
  G 刀2 配套：`_derive_experience_default` 默认返回 None（不复用别人的问题当答案），
    开关打开才恢复 407 前行为。

★为什么 F 组要用 AST 而不是文本包含：
  `_SKIP_TYPES` 在 `_detect_experience_route` 里出现 4 次（注释 2 次 + 定义 + 判断），
  文本序比较必然误判"注释里那一行"；只有 AST 能定位到真正的赋值与真正那次 search 调用。
"""
import ast
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus._silent_except import silent_exc  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IW = os.path.join(_ROOT, "organs", "brain", "PulseInnerWorld.py")
_RX = os.path.join(_ROOT, "nucleus", "mnemosyne", "ReasoningExperience.py")

_CACHE = {}


def _tree(path):
    if path not in _CACHE:
        _CACHE[path] = ast.parse(io.open(path, encoding="utf-8", errors="replace").read())
    return _CACHE[path]


def _func(path, name):
    for n in ast.walk(_tree(path)):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def _mk(q):
    """造一条与 q 特征完全一致的经验（必拿满分）。"""
    from nucleus.mnemosyne.ReasoningExperience import ReasoningExperience
    return {
        "example_question": q,
        "derivation_type": "unknown",
        "features": ReasoningExperience().extract_structural_features(q),
        "source": "local",
        "timestamp": 1e18,
    }


class _Base(unittest.TestCase):
    def setUp(self):
        from nucleus.mnemosyne.ReasoningExperience import ReasoningExperience
        self._tmp = tempfile.mkdtemp(prefix="m407k2_")
        self.re = ReasoningExperience(data_dir=self._tmp)

    def tearDown(self):
        p = os.path.join(self._tmp, "reasoning_experience.json")
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError as _e:
                silent_exc(_e, where="tests/test_m407_k2_type_gate.py::tearDown")


# ------------------------------------------------------------------ A 语义
class TestA_ExcludeTypesSemantics(_Base):
    def test_01_none_means_no_filter(self):
        """`None`（默认）= 不排除，407 前行为。"""
        q = "什么是量子纠缠"
        e = _mk(q)
        e["derivation_type"] = "remote_api_confirmed"
        self.re._experiences = [e]
        r = self.re.search(q)
        self.assertIsNotNone(r)
        self.assertEqual(0, self.re.last_search_diagnostics["n_excluded_type"])

    def test_02_empty_tuple_means_no_filter(self):
        """`()` = 显式不排除，与 None 等价（语义更明确，供去重路径用）。"""
        q = "什么是量子纠缠"
        e = _mk(q)
        e["derivation_type"] = "remote_api_confirmed"
        self.re._experiences = [e]
        self.assertIsNotNone(self.re.search(q, exclude_types=()))
        self.assertEqual(0, self.re.last_search_diagnostics["n_excluded_type"])

    def test_03_type_excluded(self):
        """给定类型整类出局。"""
        q = "什么是量子纠缠"
        e = _mk(q)
        e["derivation_type"] = "remote_api_confirmed"
        self.re._experiences = [e]
        self.assertIsNone(self.re.search(q, exclude_types=("remote_api_confirmed",)))
        self.assertEqual(1, self.re.last_search_diagnostics["n_excluded_type"])

    def test_04_other_types_untouched(self):
        """只排给定类型：同池里的 `unknown` 条不受影响。"""
        q = "什么是量子纠缠"
        a = _mk(q)
        a["derivation_type"] = "remote_api_confirmed"
        b = _mk(q)
        b["example_question"] = "另一条 unknown"
        self.re._experiences = [a, b]
        r = self.re.search(q, exclude_types=("remote_api_confirmed", "remote_api"))
        self.assertIsNotNone(r)
        self.assertEqual("另一条 unknown", r["matched_example"])

    def test_05_missing_type_treated_as_empty(self):
        """没有 `derivation_type` 字段的旧记录不会被误排（取默认空串）。"""
        q = "什么是量子纠缠"
        e = _mk(q)
        e.pop("derivation_type")
        e["example_question"] = "无类型旧记录"
        self.re._experiences = [e]
        r = self.re.search(q, exclude_types=("remote_api_confirmed",))
        self.assertIsNotNone(r)
        self.assertEqual("无类型旧记录", r["matched_example"])

    def test_06_bad_value_does_not_crash(self):
        """传 None/列表/字符串都不许抛（检索是热路径，埋点不能变成新失败面）。"""
        q = "什么是量子纠缠"
        self.re._experiences = [_mk(q)]
        for bad in (None, (), [], ["remote_api"], "remote_api"):
            self.re.search(q, exclude_types=bad)


# ------------------------------------------------------------------ B 打分前
class TestB_BeforeScoring(unittest.TestCase):
    def test_10_type_gate_before_similarity(self):
        """AST：类型闸的 `continue` 必须早于相似度计算。"""
        fn = _func(_RX, "search")
        self.assertIsNotNone(fn)
        loop = None
        for n in ast.walk(fn):
            if isinstance(n, ast.For):
                ids = {x.id for x in ast.walk(n) if isinstance(x, ast.Name)}
                if "_m407_typed" in ids:
                    loop = n
                    break
        self.assertIsNotNone(loop, "打分循环里没有 _m407_typed 计数")
        conts = [x.lineno for x in ast.walk(loop) if isinstance(x, ast.Continue)]
        sims = [x.lineno for x in ast.walk(loop)
                if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
                and x.func.attr == "_calculate_feature_similarity"]
        self.assertTrue(conts, "循环里没有 continue")
        self.assertTrue(sims, "循环里没有相似度计算")
        self.assertLess(min(conts), min(sims), "类型闸晚于算分 ⇒ 变成命中后砍")

    def test_11_both_gates_in_same_loop(self):
        """污染闸与类型闸在**同一个**打分循环里（不是两遍遍历）。"""
        fn = _func(_RX, "search")
        for n in ast.walk(fn):
            if isinstance(n, ast.For):
                ids = {x.id for x in ast.walk(n) if isinstance(x, ast.Name)}
                if "_m407_polluted" in ids:
                    self.assertIn("_m407_typed", ids, "两个闸不在同一循环")
                    return
        self.fail("找不到打分循环")

    def test_12_no_second_pass(self):
        """只允许一个「遍历 _experiences 打分」的循环（避免 O(2n) 与口径漂移）。"""
        fn = _func(_RX, "search")
        n_loops = 0
        for n in ast.walk(fn):
            if isinstance(n, ast.For):
                seg = ast.get_source_segment(
                    io.open(_RX, encoding="utf-8", errors="replace").read(), n) or ""
                if "self._experiences" in seg:
                    n_loops += 1
        self.assertEqual(1, n_loops, "遍历经验库的循环有 %d 个" % n_loops)


# ------------------------------------------------------------------ C 埋点
class TestC_Diagnostics(_Base):
    def test_20_field_present_and_int(self):
        self.re.search("什么是量子纠缠")
        d = self.re.last_search_diagnostics
        self.assertIn("n_excluded_type", d)
        self.assertIsInstance(d["n_excluded_type"], int)

    def test_21_independent_counters(self):
        """污染闸与类型闸各自计数：一条同时满足两者时只算**污染**（先判污染）。"""
        q = "什么是量子纠缠"
        e = _mk(q)
        e["derivation_type"] = "remote_api_confirmed"
        e["polluted"] = True
        self.re._experiences = [e]
        with patch("config.ENABLE_EXPERIENCE_CLEANUP_FILTER", True, create=True):
            self.re.search(q, exclude_types=("remote_api_confirmed",))
        d = self.re.last_search_diagnostics
        self.assertEqual(1, d["n_excluded_polluted"])
        self.assertEqual(0, d["n_excluded_type"], "同一行被两个闸重复计数")

    def test_22_counters_add_up(self):
        """剩余 = 总 - 污染 - 类型（埋点口径必须能自圆）。"""
        q = "什么是量子纠缠"
        a = _mk(q)
        a["polluted"] = True
        b = _mk(q)
        b["derivation_type"] = "remote_api_confirmed"
        c = _mk(q)
        c["derivation_type"] = "inductive"
        self.re._experiences = [a, b, c]
        with patch("config.ENABLE_EXPERIENCE_CLEANUP_FILTER", True, create=True):
            self.re.search(q, exclude_types=("remote_api_confirmed",))
        d = self.re.last_search_diagnostics
        self.assertEqual(3, d["n_experiences"])
        self.assertEqual(1, d["n_excluded_polluted"])
        self.assertEqual(1, d["n_excluded_type"])
        self.assertEqual(d["n_experiences"],
                         d["n_excluded_polluted"] + d["n_excluded_type"] + 1)


# ------------------------------------------------------------------ D 向后兼容
class TestD_BackwardCompatible(_Base):
    def test_30_default_call_unchanged(self):
        """不传参的调用点：排除类型恒 0，返回值与显式传 () 完全一致。"""
        q = "什么是量子纠缠"
        self.re._experiences = [_mk(q)]
        r1 = self.re.search(q)
        d1 = dict(self.re.last_search_diagnostics)
        r2 = self.re.search(q, exclude_types=())
        d2 = dict(self.re.last_search_diagnostics)
        self.assertEqual(r1, r2)
        self.assertEqual(d1, d2)
        self.assertEqual(0, d1["n_excluded_type"])

    def test_31_threshold_and_polluted_gate_untouched(self):
        """刀2 不动阈值、不动污染闸开关。"""
        self.assertEqual(0.5, self.re._min_confidence)
        import config
        self.assertTrue(bool(getattr(config, "ENABLE_EXPERIENCE_CLEANUP_FILTER", False)))


# ------------------------------------------------------------------ E 去重路径
class TestE_DedupUnaffected(_Base):
    def test_40_remote_entries_dedup_each_other(self):
        """`record()` 去重显式传 `()`：远程类型条目仍互相去重（不因路由排它而重复入库）。"""
        q = "规则1 → 规则2 的推演链条是否成立"
        self.re.record(q, "remote_api_confirmed", source="quality_feedback", confidence=0.9)
        self.re.record(q, "remote_api_confirmed", source="quality_feedback", confidence=0.9)
        self.assertEqual(1, len(self.re._experiences),
                         "去重失效：同一问题被记了 %d 条" % len(self.re._experiences))

    def test_41_record_calls_search_with_empty_tuple(self):
        """AST：`record()` 里的去重调用必须显式传 `exclude_types=()`（不能跟路由默认值）。"""
        fn = _func(_RX, "record")
        found = False
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "search":
                kws = {k.arg: k.value for k in n.keywords}
                self.assertIn("exclude_types", kws, "去重调用没传 exclude_types")
                self.assertIsInstance(kws["exclude_types"], ast.Tuple)
                self.assertEqual(0, len(kws["exclude_types"].elts))
                found = True
        self.assertTrue(found, "record() 里没有 search 调用")


# ------------------------------------------------------------------ F 路由侧
def _exp_search_calls(fn):
    """只挑对 `_reasoning_exp.search(...)` 的调用。

    ★为什么不能只按 `attr == "search"` 筛：本函数里有 6 处 `re.search(...)`
      （复盘独占 / 冲突独占 / multi_variable 强信号），按 attr 筛必然把它们
      一起捞进来，然后行号比较就变成拿"定义"去和"第一个正则"比。
    """
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "search" \
                and isinstance(n.func.value, ast.Name) \
                and n.func.value.id == "_reasoning_exp":
            out.append(n)
    return out


class TestF_RouterWiring(unittest.TestCase):
    def _skip_assign_line(self):
        fn = _func(_IW, "_detect_experience_route")
        lines = [n.lineno for n in ast.walk(fn)
                 if isinstance(n, ast.Assign)
                 for t in n.targets
                 if isinstance(t, ast.Name) and t.id == "_SKIP_TYPES"]
        return min(lines) if lines else None

    def test_50_defined_before_search(self):
        """`_SKIP_TYPES` 必须在 search() 调用**之前**定义，才能注入。"""
        d = self._skip_assign_line()
        fn = _func(_IW, "_detect_experience_route")
        c = [n.lineno for n in _exp_search_calls(fn)]
        self.assertIsNotNone(d, "_SKIP_TYPES 没有赋值了？")
        self.assertTrue(c, "没有 _reasoning_exp.search 调用")
        self.assertEqual(1, len(c), "_reasoning_exp.search 调用有 %d 处" % len(c))
        self.assertLess(d, min(c), "定义(%s) 晚于调用(%s)" % (d, min(c)))

    def test_51_passed_as_exclude_types(self):
        """必须作为 `exclude_types=` 实参传进去（不是别的位置参数）。"""
        fn = _func(_IW, "_detect_experience_route")
        calls = _exp_search_calls(fn)
        self.assertTrue(calls, "找不到 _reasoning_exp.search 调用")
        for n in calls:
            kws = {k.arg: k.value for k in n.keywords}
            self.assertIn("exclude_types", kws, "search() 调用没传 exclude_types")
            self.assertIsInstance(kws["exclude_types"], ast.Name)
            self.assertEqual("_SKIP_TYPES", kws["exclude_types"].id)

    def test_52_list_unchanged(self):
        """★任务书明令：**不改 `_SKIP_TYPES` 清单本身**。"""
        fn = _func(_IW, "_detect_experience_route")
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id == "_SKIP_TYPES":
                        vals = [e.value for e in n.value.elts]
                        self.assertEqual(["remote_api_confirmed", "remote_api"], vals)
                        return
        self.fail("_SKIP_TYPES 赋值没找到")

    def test_53_downstream_guard_still_there(self):
        """下游那道 `if _exp_type in _SKIP_TYPES: return None` 必须保留（双保险）。"""
        fn = _func(_IW, "_detect_experience_route")
        ok = False
        for n in ast.walk(fn):
            if isinstance(n, ast.If) and isinstance(n.test, ast.Compare):
                left = n.test.left
                if isinstance(left, ast.Name) and left.id == "_exp_type":
                    for c in n.test.comparators:
                        if isinstance(c, ast.Name) and c.id == "_SKIP_TYPES":
                            ok = True
        self.assertTrue(ok, "下游类型闸被误删 —— 检索层被绕过时就没兜底了")


# ------------------------------------------------------------------ G 刀2 配套
class TestG_NoExampleAsAnswer(unittest.TestCase):
    MATCH = {
        "derivation_type": "unknown",
        "confidence": 0.68,
        "source": "local_fallback",
        "matched_example": "我需要更多的一手资料和实践来建立扎实的认知",
    }

    def _fake(self):
        from organs.brain.PulseInnerWorld import PulseInnerWorld

        class _F:
            pass

        f = _F()
        f._m407_reuse_example_answer = PulseInnerWorld._m407_reuse_example_answer
        f._log = lambda *a, **k: None
        return PulseInnerWorld, f

    def test_60_default_returns_none(self):
        """默认：不复用别人的问题当答案。"""
        IW, f = self._fake()
        with patch("config.EXPERIENCE_REUSE_EXAMPLE_AS_ANSWER", False, create=True):
            self.assertIsNone(IW._derive_experience_default(f, "什么是量子纠缠", self.MATCH))

    def test_61_switch_on_restores_old_behaviour(self):
        """开关打开 ⇒ 恢复 407 前行为（对照排查用）。"""
        IW, f = self._fake()
        with patch("config.EXPERIENCE_REUSE_EXAMPLE_AS_ANSWER", True, create=True):
            self.assertEqual(self.MATCH["matched_example"],
                             IW._derive_experience_default(f, "什么是量子纠缠", self.MATCH))

    def test_62_empty_match_still_none(self):
        """空 match 一律 None —— 与开关无关。"""
        IW, f = self._fake()
        with patch("config.EXPERIENCE_REUSE_EXAMPLE_AS_ANSWER", True, create=True):
            self.assertIsNone(IW._derive_experience_default(f, "x", {}))
            self.assertIsNone(IW._derive_experience_default(f, "x", None))

    def test_63_config_default_is_false(self):
        """config 里的默认值必须是 False（默认要选"不出事"的那一边）。"""
        import config
        self.assertIs(False, getattr(config, "EXPERIENCE_REUSE_EXAMPLE_AS_ANSWER", True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
