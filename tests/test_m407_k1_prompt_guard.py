# -*- coding: utf-8 -*-
"""407 刀1 判据：代码学习 prompt 不入经验库 + 存量污染条不打分。

六组断言：
  A 入库闸门：模板 prompt（强指纹 / 弱指纹三件套）→ 不入库；真实提问 → 正常入库。
  B 闸门开关：`EXPERIENCE_PROMPT_GUARD=False` ⇒ 回到 407 前来者不拒。
  C 污染闸在**打分之前**：造「污染条本可以拿 1.0 分」+「真经验」两条，
    闸开 ⇒ 真经验被选中；闸关 ⇒ 污染条赢。证明排除发生在排序前，不是命中后砍。
  D 诊断埋点：`n_excluded_polluted` 是 int，且**未标记记录零影响**（零回归）。
  E 口径一致：`_m407_is_clean` 与 `experience_cleanup.is_retrievable` 逐样本同结论
    —— 两个模块的判断不能各说各话。
  F 结构（AST）：闸门是 `record()` 的**第一个 if**，且在 `extract_structural_features`
    之前 —— 否则先算特征再拦，等于白算一遍特征。

★为什么 C 组不锁具体分数：
  特征相似度是浮点加权和，任何一次 `FEATURE_WEIGHTS` 校准都会让精确值漂移；
  判据要钉的是「**谁被选中**」这个语义，不是「分数等于多少」。
"""
import ast
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus._silent_except import silent_exc  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RX = os.path.join(_ROOT, "nucleus", "mnemosyne", "ReasoningExperience.py")

_TPL = ("请分析以下Python方法，并返回一个JSON格式的结果（只返回JSON，不要其他文字）：\n"
        "方法名: PulseFoo\n代码: def foo(): return 1\n返回格式: {\"type\": \"...\"}")


def _read(p):
    return io.open(p, encoding="utf-8", errors="replace").read()


_TREE = None


def _tree():
    global _TREE
    if _TREE is None:
        _TREE = ast.parse(_read(_RX))
    return _TREE


def _func(name):
    for n in ast.walk(_tree()):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


class _Base(unittest.TestCase):
    def setUp(self):
        from nucleus.mnemosyne.ReasoningExperience import ReasoningExperience
        self._cls = ReasoningExperience
        self._tmp = tempfile.mkdtemp(prefix="m407k1_")
        self.re = ReasoningExperience(data_dir=self._tmp)

    def tearDown(self):
        for f in ("reasoning_experience.json",):
            p = os.path.join(self._tmp, f)
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError as _e:
                    silent_exc(_e, where="tests/test_m407_k1_prompt_guard.py::tearDown")


# ------------------------------------------------------------------ A 入库闸
class TestA_IngestGate(_Base):
    def test_01_strong_fingerprint_rejected(self):
        """强指纹：`请分析以下Python方法` 开头 ⇒ 不入库。"""
        self.re.record(_TPL, "remote_api_confirmed", source="quality_feedback")
        self.assertEqual([], self.re._experiences)

    def test_02_second_strong_fingerprint(self):
        """强指纹独立生效：只带 `返回一个JSON格式的结果` 也拦。"""
        self.re.record("随便一句 返回一个JSON格式的结果 就这样", "remote_api_confirmed")
        self.assertEqual([], self.re._experiences)

    def test_03_weak_fingerprint_triplet(self):
        """弱指纹三件套同时出现 ⇒ 即便模板措辞改了也拦。"""
        q = "看看这段。方法名: bar\n代码: x=1\n返回格式: yaml"
        self.re.record(q, "remote_api_confirmed")
        self.assertEqual([], self.re._experiences)

    def test_04_real_question_ingested(self):
        """真实提问必须正常入库 —— 闸门不能把正常流量也拦了。"""
        self.re.record("请分析以下两种架构的差异，并给出取舍", "inductive", source="local")
        self.assertEqual(1, len(self.re._experiences))
        self.assertEqual("请分析以下两种架构的差异，并给出取舍",
                         self.re._experiences[0]["example_question"])

    def test_05_single_weak_fingerprint_not_rejected(self):
        """只出现一个弱指纹（`方法名:`）不算污染 —— 用户可能真在描述表结构。"""
        self.re.record("方法名: 应该怎么命名比较好？", "inductive")
        self.assertEqual(1, len(self.re._experiences))

    def test_06_guard_hit_counter(self):
        """拦截计数可观测（埋点：累计拦下多少条）。"""
        self.re.record(_TPL, "remote_api_confirmed")
        self.re.record(_TPL, "remote_api_confirmed")
        self.assertEqual(2, getattr(self.re, "_m407_prompt_guard_hits", 0))

    def test_07_empty_and_none_safe(self):
        """空串 / None 不炸、也不被误判成污染。"""
        from nucleus.mnemosyne.ReasoningExperience import is_code_learning_prompt
        self.assertFalse(is_code_learning_prompt(""))
        self.assertFalse(is_code_learning_prompt(None))
        self.assertFalse(is_code_learning_prompt(12345))


# ------------------------------------------------------------------ B 开关回退
class TestB_SwitchRollback(_Base):
    def test_10_switch_off_allows_ingest(self):
        """`EXPERIENCE_PROMPT_GUARD=False` ⇒ 完全回到 407 前（来者不拒）。"""
        with patch("config.EXPERIENCE_PROMPT_GUARD", False, create=True):
            self.re.record(_TPL, "remote_api_confirmed")
        self.assertEqual(1, len(self.re._experiences))

    def test_11_switch_default_is_true(self):
        """默认必须是**开**：闸门默认关闭等于没做这一刀。"""
        import config
        self.assertTrue(bool(getattr(config, "EXPERIENCE_PROMPT_GUARD", False)))

    def test_12_switch_off_restores_polluted_gate(self):
        """污染闸也受既有开关控制（复用 `ENABLE_EXPERIENCE_CLEANUP_FILTER`）。"""
        self.re._experiences = [{
            "example_question": "旧问题", "derivation_type": "unknown",
            "features": self.re.extract_structural_features("旧问题"),
            "polluted": True, "source": "local", "timestamp": 1e18,
        }]
        with patch("config.ENABLE_EXPERIENCE_CLEANUP_FILTER", False, create=True):
            self.re.search("旧问题")
        self.assertEqual(0, self.re.last_search_diagnostics["n_excluded_polluted"])


# ------------------------------------------------------------------ C 打分前排除
class TestC_GateBeforeScoring(_Base):
    def _seed(self, q):
        """造两条：污染条特征与查询**完全一致**（本该拿满分），真经验略弱但**过得了阈值**。

        ★为什么真经验只错开两个特征位：若把它做成分数极低，`search()` 会因
          `best_score < 0.5` 返回 None，C 组就变成在测阈值而不是在测闸门。
          这里让真经验 = 17.8 权重里错开 4.0（`has_conflict_signal` 2.0 +
          `question_type_hint` 2.0），分数 ≈0.775 —— 既能过阈值，又低于污染条的 1.0。
        """
        f_full = self.re.extract_structural_features(q)
        f_weak = dict(f_full)
        f_weak["has_conflict_signal"] = not f_full.get("has_conflict_signal", False)
        f_weak["question_type_hint"] = "general"
        self.re._experiences = [
            {"example_question": _TPL, "derivation_type": "unknown",
             "features": f_full, "polluted": True, "source": "local",
             "timestamp": 1e18},
            {"example_question": q, "derivation_type": "unknown",
             "features": f_weak, "source": "local", "timestamp": 1e18},
        ]
        return f_full, f_weak

    def test_20_polluted_excluded_before_scoring(self):
        """闸开：污染条被排除，best_score 不含它那一份。"""
        q = "规则1 → 规则2 推演一下会不会冲突"
        self._seed(q)
        with patch("config.ENABLE_EXPERIENCE_CLEANUP_FILTER", True, create=True):
            self.re.search(q)
        d = self.re.last_search_diagnostics
        self.assertEqual(1, d["n_excluded_polluted"])
        self.assertEqual(2, d["n_experiences"])

    def test_21_clean_experience_wins_when_gate_on(self):
        """闸开：污染条即便能拿更高分也赢不了 —— 它根本没进比赛。"""
        q = "规则1 → 规则2 推演一下会不会冲突"
        self._seed(q)
        with patch("config.ENABLE_EXPERIENCE_CLEANUP_FILTER", True, create=True):
            r = self.re.search(q)
        self.assertIsNotNone(r)
        self.assertEqual(q, r["matched_example"])

    def test_22_polluted_wins_when_gate_off(self):
        """闸关（对照）：污染条立刻夺回第一 —— 证明 C 组的胜负真由闸门决定。"""
        q = "规则1 → 规则2 推演一下会不会冲突"
        self._seed(q)
        with patch("config.ENABLE_EXPERIENCE_CLEANUP_FILTER", False, create=True):
            r = self.re.search(q)
        self.assertIsNotNone(r)
        self.assertEqual(_TPL[:80], r["matched_example"])

    def test_23_is_cleaned_false_also_excluded(self):
        """`is_cleaned=False`（本项目清洗口径的另一半）同样出局。"""
        q = "什么是量子纠缠"
        self.re._experiences = [{
            "example_question": q, "derivation_type": "unknown",
            "features": self.re.extract_structural_features(q),
            "is_cleaned": False, "source": "local", "timestamp": 1e18,
        }]
        with patch("config.ENABLE_EXPERIENCE_CLEANUP_FILTER", True, create=True):
            self.assertIsNone(self.re.search(q))
        self.assertEqual(1, self.re.last_search_diagnostics["n_excluded_polluted"])


# ------------------------------------------------------------------ D 埋点 + 零回归
class TestD_DiagnosticsAndNoRegression(_Base):
    def test_30_field_present_and_int(self):
        """诊断快照必须带 `n_excluded_polluted` 且是 int（埋点要能直接入库统计）。"""
        self.re.search("什么是量子纠缠")
        d = self.re.last_search_diagnostics
        self.assertIn("n_excluded_polluted", d)
        self.assertIsInstance(d["n_excluded_polluted"], int)

    def test_31_unmarked_records_untouched(self):
        """未标记的记录：排除数恒 0 —— 零回归的结构性保证。"""
        q = "什么是量子纠缠"
        self.re._experiences = [{
            "example_question": q, "derivation_type": "unknown",
            "features": self.re.extract_structural_features(q),
            "source": "local", "timestamp": 1e18,
        }]
        self.re.search(q)
        self.assertEqual(0, self.re.last_search_diagnostics["n_excluded_polluted"])

    def test_32_snapshot_reset_each_search(self):
        """每轮 search 都复位，不留上一轮脏读数。"""
        self.re._experiences = [{"example_question": "x", "derivation_type": "unknown",
                                 "features": {}, "polluted": True, "timestamp": 1e18}]
        with patch("config.ENABLE_EXPERIENCE_CLEANUP_FILTER", True, create=True):
            self.re.search("问题一")
            self.assertEqual(1, self.re.last_search_diagnostics["n_excluded_polluted"])
            self.re.search("问题二")
        self.assertEqual(1, self.re.last_search_diagnostics["n_excluded_polluted"])

    def test_33_threshold_untouched(self):
        """★任务书明令：不碰阈值 0.5（406 已裁决）。"""
        self.assertEqual(0.5, self.re._min_confidence)


# ------------------------------------------------------------------ E 口径一致
class TestE_SameVerdictAsCleanup(unittest.TestCase):
    SAMPLES = [
        {},
        {"polluted": True},
        {"polluted": False},
        {"polluted": None},
        {"is_cleaned": False},
        {"is_cleaned": True},
        {"is_cleaned": None},
        {"polluted": True, "is_cleaned": True},
        {"polluted": False, "is_cleaned": False},
        {"polluted": 1},            # 非 True 的"真值"，两处都不能拦
        {"polluted": "yes"},        # 字符串，同上
        {"pollution_risk": "high"},  # 只有风险标注、没有布尔标记 ⇒ 不拦
    ]

    def test_40_verdict_identical(self):
        """`_m407_is_clean` 与 `experience_cleanup.is_retrievable` 逐样本同结论。"""
        from nucleus.mnemosyne.ReasoningExperience import _m407_is_clean
        from nucleus.data.experience_cleanup import is_retrievable
        for s in self.SAMPLES:
            self.assertEqual(is_retrievable(s), _m407_is_clean(s),
                             "口径分叉: %r" % (s,))

    def test_41_non_dict_both_false(self):
        """非 dict 输入两处都判 False（不让 None 混过闸门）。"""
        from nucleus.mnemosyne.ReasoningExperience import _m407_is_clean
        from nucleus.data.experience_cleanup import is_retrievable
        for bad in (None, [], "x", 7):
            self.assertEqual(is_retrievable(bad), _m407_is_clean(bad))


# ------------------------------------------------------------------ F 结构 AST
class TestF_Structural(unittest.TestCase):
    def test_50_gate_is_first_if_of_record(self):
        """闸门必须是 `record()` 的**第一个 if**（任何写点都被覆盖）。"""
        fn = _func("record")
        self.assertIsNotNone(fn, "record() 没找到")
        first_if = None
        for st in fn.body:
            if isinstance(st, ast.If):
                first_if = st
                break
        self.assertIsNotNone(first_if, "record() 里没有 if —— 闸门丢了？")
        names = {n.id for n in ast.walk(first_if.test) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(first_if.test) if isinstance(n, ast.Attribute)}
        self.assertTrue(
            "is_code_learning_prompt" in names or
            any("prompt_guard" in a for a in attrs),
            "record() 第一个 if 不是代码学习闸门，实际: %s" % sorted(names | attrs))

    def test_51_gate_before_feature_extraction(self):
        """闸门行号必须早于 `extract_structural_features` 调用 —— 否则白算一遍特征。"""
        fn = _func("record")
        gate_line = None
        feat_line = None
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Attribute) and f.attr == "extract_structural_features":
                    feat_line = n.lineno if feat_line is None else min(feat_line, n.lineno)
                if isinstance(f, ast.Name) and f.id == "is_code_learning_prompt":
                    gate_line = n.lineno if gate_line is None else min(gate_line, n.lineno)
        self.assertIsNotNone(gate_line, "record() 里没有 is_code_learning_prompt 调用")
        self.assertIsNotNone(feat_line, "record() 里没有 extract_structural_features 调用")
        self.assertLess(gate_line, feat_line,
                        "闸门(%d) 晚于 特征提取(%d)" % (gate_line, feat_line))

    def test_52_polluted_gate_inside_scoring_loop(self):
        """污染闸必须在打分 for 循环体内，且 `continue` 在算分之前。"""
        fn = _func("search")
        self.assertIsNotNone(fn)
        target = None
        for n in ast.walk(fn):
            if isinstance(n, ast.For):
                ids = {x.id for x in ast.walk(n) if isinstance(x, ast.Name)}
                if "_m407_gate" in ids:
                    target = n
                    break
        self.assertIsNotNone(target, "search() 的打分循环里没有污染闸")
        sim = [x.lineno for x in ast.walk(target)
               if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
               and x.func.attr == "_calculate_feature_similarity"]
        cont = [x.lineno for x in ast.walk(target) if isinstance(x, ast.Continue)]
        self.assertTrue(sim, "循环里没有相似度计算？")
        self.assertTrue(cont, "循环里没有 continue？")
        self.assertLess(min(cont), min(sim), "continue 晚于算分 ⇒ 变成命中后砍")


if __name__ == "__main__":
    unittest.main(verbosity=2)
