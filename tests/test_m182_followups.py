# -*- coding: utf-8 -*-
"""第182批 主任务书 刀1/刀2/刀4/刀5 门控测试（零停窗，全部开关默认关）。

刀1 认知反思盲区信号并入 181刀2 质量分升级触发源（PulseInnerWorld）
刀2 cognitive_reflection 注册进 PulseSubconscious 黑板消费表
刀4 181刀1 路由白名单消费侧接线（QICA）
刀5 config 阶段二三孤儿键删除
"""
import ast
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config

_IW_PATH = os.path.join(_ROOT, "organs", "brain", "PulseInnerWorld.py")
_SC_PATH = os.path.join(_ROOT, "organs", "brain", "PulseSubconscious.py")
_QI_PATH = os.path.join(_ROOT, "nucleus", "qica", "QICA.py")


def _src(path):
    return open(path, encoding="utf-8").read()


def _module_of(rel):
    import importlib
    return importlib.import_module(rel)


class _Switch:
    """临时翻转 config 属性（退出还原）。"""

    def __init__(self, **kv):
        self.kv = kv
        self._bak = {}

    def __enter__(self):
        for k, v in self.kv.items():
            self._bak[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self._bak.items():
            if v is None:
                if hasattr(config, k):
                    delattr(config, k)
            else:
                setattr(config, k, v)
        return False


class _StubOrgan:
    """刀1 被测替身：只提供既有数据源字段（_failed_domain_records / _last_reflection_insights）。"""

    def __init__(self, failed=None, insights=None):
        if failed is not None:
            self._failed_domain_records = dict(failed)
        if insights is not None:
            self._last_reflection_insights = list(insights)


# ══════════════════ A 组：刀1 反思盲区信号 ══════════════════
class TestD1ReflectionBlindSignal(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.iw = _module_of("organs.brain.PulseInnerWorld")

    def test_01_domains_from_failed_domain_records(self):
        """领域词来自既有 _failed_domain_records（count>=2 才入表）。"""
        with _Switch(ENABLE_INNER_WORLD_QUALITY_ESCALATION=True):
            o = _StubOrgan(failed={"代码": 3, "量子": 1})
            domains, flag = self.iw._m182_reflection_blind_domains(o)
        self.assertIn("代码", domains)
        self.assertNotIn("量子", domains)

    def test_02_domains_from_reflection_text(self):
        """反思文本解析：「最需要关注的是「X」」/「薄弱区: X」+ 低置信信号置位。"""
        with _Switch(ENABLE_INNER_WORLD_QUALITY_ESCALATION=True):
            o = _StubOrgan(
                failed={},
                insights=["最近35%的推理置信度较低。主动扫描发现了2个知识盲区——"
                          "其中最需要关注的是「代码」（薄弱区，仅1个节点）。"],
            )
            domains, flag = self.iw._m182_reflection_blind_domains(o)
        self.assertIn("代码", domains)
        self.assertTrue(flag, "反思文本含低置信/盲区陈述 -> flag 应置位")

    def test_03_switch_off_returns_empty_without_cache(self):
        """开关关闭 -> 空词表，且不写缓存（零额外开销）。"""
        with _Switch(ENABLE_INNER_WORLD_QUALITY_ESCALATION=False):
            o = _StubOrgan(failed={"代码": 3})
            domains, flag = self.iw._m182_reflection_blind_domains(o)
        self.assertEqual(domains, ())
        self.assertFalse(flag)
        self.assertFalse(hasattr(o, "_m182_blind_cache"), "开关关闭不得写缓存")

    def test_04_judge_off_never_escalates(self):
        """开关关闭 -> 即使命中盲区领域也恒 False（零行为变化）。"""
        with _Switch(ENABLE_INNER_WORLD_QUALITY_ESCALATION=False):
            self.assertFalse(self.iw._m181_should_escalate_on_quality(
                "代码里这个函数怎么写", "本地沉思结果", blind_domains=("代码",)))

    def test_05_judge_on_blind_domain_hit(self):
        """开关开启 + 问题命中盲区领域 -> 升级 LLM（True）。"""
        with _Switch(ENABLE_INNER_WORLD_QUALITY_ESCALATION=True):
            self.assertTrue(self.iw._m181_should_escalate_on_quality(
                "代码里这个函数怎么写", "本地沉思结果", blind_domains=("代码",)))
            # 未命中盲区领域、也不是关键词/结构/长问短答 -> False
            self.assertFalse(self.iw._m181_should_escalate_on_quality(
                "你好", "你好呀，有什么可以帮你", blind_domains=("量子",)))

    def test_06_call_site_passes_blind_domains(self):
        """调用点必须把盲区词表传进判据（AST：关键字实参 blind_domains）。"""
        tree = ast.parse(_src(_IW_PATH))
        hits = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
                if fn == "_m181_should_escalate_on_quality":
                    self.assertTrue(any(k.arg == "blind_domains" for k in node.keywords),
                                    "调用点未传 blind_domains")
                    hits += 1
        self.assertEqual(hits, 1, "判据调用点应恰好 1 处（不新建第二条升级线）")

    def test_07_no_second_escalation_line(self):
        """不新建第二条升级线：判据函数定义唯一、helper 定义唯一。"""
        tree = ast.parse(_src(_IW_PATH))
        defs = [n.name for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)
                and n.name in ("_m181_should_escalate_on_quality", "_m182_reflection_blind_domains")]
        self.assertEqual(sorted(defs), ["_m181_should_escalate_on_quality",
                                         "_m182_reflection_blind_domains"])

    def test_08_switch_is_shared_with_181_d2(self):
        """复用 181刀2 灰度键（不新建开关）。"""
        self.assertTrue(hasattr(config, "ENABLE_INNER_WORLD_QUALITY_ESCALATION"))
        self.assertFalse(config.ENABLE_INNER_WORLD_QUALITY_ESCALATION)


# ══════════════════ B 组：刀2 黑板类型注册 ══════════════════
class _StubBoard:
    def __init__(self, mapping):
        self._m = mapping
        self.seen = []

    def query(self, insight_type=None, **kw):
        self.seen.append(insight_type)
        return list(self._m.get(insight_type, []))


class TestD2SubconsciousRegistration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.mod = _module_of("organs.brain.PulseSubconscious")
        cls.src = _src(_SC_PATH)

    def test_10_query_registered_in_source(self):
        """AST：消费侧必须真的查询 cognitive_reflection 类型（仅加分支=死代码）。"""
        tree = ast.parse(self.src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for k in node.keywords:
                    if k.arg == "insight_type" and isinstance(k.value, ast.Constant):
                        if k.value.value == "cognitive_reflection":
                            found = True
        self.assertTrue(found, "PulseSubconscious 未查询 cognitive_reflection")

    def test_11_topic_branch_registered(self):
        """AST：elif 分支含 cognitive_reflection。"""
        tree = ast.parse(self.src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name):
                if node.left.id == "_insight_type":
                    for c in node.comparators:
                        if isinstance(c, ast.Constant) and c.value == "cognitive_reflection":
                            found = True
        self.assertTrue(found)

    def test_12_behavior_topic_from_cognitive_reflection(self):
        """行为：黑板只有 cognitive_reflection 洞察时，探索 topic 走新分支。"""
        o = self.mod.PulseSubconscious.__new__(self.mod.PulseSubconscious)
        o._insight_board = _StubBoard({
            "cognitive_reflection": [{
                "type": "cognitive_reflection",
                "related_dimension": "思考模式",
                "content": "最近推理置信度偏低",
                "source_loop": "self_reflection_loop",
            }],
        })
        o._interest_tags = []
        o._interest_weights = {}
        o._interest_lock = __import__("threading").RLock()
        o._log = lambda *a, **k: None
        o._detect_knowledge_gap = lambda: None
        o._generate_self_question = lambda: None
        o._generate_open_exploration = lambda: None
        import random as _rnd
        _bak = _rnd.random
        _rnd.random = lambda: 0.55      # 落入策略3(.5) 区间：knowledge_gap 返回 None -> board 分支
        try:
            topic = o._select_exploration_topic()
        finally:
            _rnd.random = _bak
        self.assertIsNotNone(topic, "认知反思洞察应产出探索 topic")
        self.assertIn("认知反思", topic)

    def test_13_other_types_unchanged(self):
        """既有两类的 topic 语义不变（零回归）。"""
        o = self.mod.PulseSubconscious.__new__(self.mod.PulseSubconscious)
        o._insight_board = _StubBoard({
            "weak_area_detected": [{
                "type": "weak_area_detected", "related_dimension": "量子",
                "content": "x", "source_loop": "s"}],
        })
        o._interest_tags = []
        o._interest_weights = {}
        o._interest_lock = __import__("threading").RLock()
        o._log = lambda *a, **k: None
        o._detect_knowledge_gap = lambda: None
        import random as _rnd
        _bak = _rnd.random
        _rnd.random = lambda: 0.55
        try:
            topic = o._select_exploration_topic()
        finally:
            _rnd.random = _bak
        self.assertEqual(topic, "量子 基础 概念 入门")


# ══════════════════ C 组：刀4 路由消费侧接线 ══════════════════
class TestD4RoutingConsume(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.mod = _module_of("nucleus.qica.QICA")
        cls.src = _src(_QI_PATH)

    def _obj(self):
        o = self.mod.QICA.__new__(self.mod.QICA)
        o._log = lambda *a, **k: None
        return o

    def test_20_switch_off_no_change(self):
        """开关默认关 -> anchor 原样返回（零行为变化）。"""
        with _Switch(ENABLE_ROUTING_LAYERED=False):
            a = {"task_type": "技术推理", "channel": "knowledge"}
            out = self._obj()._m182_apply_routing_layer(dict(a), "很长的问题" * 20)
        self.assertEqual(out, a)

    def test_21_local_intent_stays_local(self):
        with _Switch(ENABLE_ROUTING_LAYERED=True):
            out = self._obj()._m182_apply_routing_layer(
                {"task_type": "身份确认", "channel": "knowledge"}, "你是谁")
        self.assertEqual(out["channel"], "fast")
        self.assertEqual(out["routing_layer"], "local")

    def test_22_llm_intent_upgrades(self):
        with _Switch(ENABLE_ROUTING_LAYERED=True):
            out = self._obj()._m182_apply_routing_layer(
                {"task_type": "深度分析", "channel": "fast"}, "请分析一下")
        self.assertEqual(out["channel"], "deep")
        self.assertEqual(out["routing_layer"], "llm")

    def test_23_gray_intent_by_length_and_structure(self):
        """灰度档：长问题 / 推理结构命中 -> 升级；短问题 -> 维持本地。"""
        _min_len = int(getattr(config, "ROUTING_GRAY_MIN_LEN", 50))
        with _Switch(ENABLE_ROUTING_LAYERED=True):
            long_q = "x" * (_min_len + 5)
            out_long = self._obj()._m182_apply_routing_layer(
                {"task_type": "知识查询", "channel": "fast"}, long_q)
            out_short = self._obj()._m182_apply_routing_layer(
                {"task_type": "知识查询", "channel": "fast"}, "你好")
            out_struct = self._obj()._m182_apply_routing_layer(
                {"task_type": "知识查询", "channel": "fast"}, "已知A请给出结论")
        self.assertEqual(out_long["routing_layer"], "gray_upgrade")
        self.assertEqual(out_short["routing_layer"], "gray_local")
        self.assertEqual(out_struct["routing_layer"], "gray_upgrade")

    def test_24_unregistered_intent_untouched(self):
        with _Switch(ENABLE_ROUTING_LAYERED=True):
            out = self._obj()._m182_apply_routing_layer(
                {"task_type": "__not_in_whitelist__", "channel": "knowledge"}, "x")
        self.assertNotIn("routing_layer", out)
        self.assertEqual(out["channel"], "knowledge")

    def test_25_wired_into_classify_internal(self):
        """AST：classify_internal 内必须调用 _m182_apply_routing_layer。"""
        tree = ast.parse(self.src)
        hits = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "_m182_apply_routing_layer":
                hits.append(node.lineno)
        self.assertEqual(len(hits), 1, "接线点应恰好 1 处，实得 %s" % hits)

    def test_26_switch_default_off_in_config(self):
        self.assertFalse(getattr(config, "ENABLE_ROUTING_LAYERED", False))


# ══════════════════ D 组：刀5 三键删除 ══════════════════
class TestD5OrphanKeysDropped(unittest.TestCase):
    DROPPED = ("SELF_AWARENESS_DECISION_DEGRADE_THRESHOLD",
               "SELF_AWARENESS_DEGRADE",
               "SELF_AWARENESS_EVOLUTION_BOOST_ENABLED",
               "SELF_AWARENESS_EVOLUTION_BOOST_MAX")

    def test_30_dropped_keys_absent(self):
        for k in self.DROPPED:
            self.assertFalse(hasattr(config, k), "孤儿键仍存在: %s" % k)

    def test_31_mention_key_preserved(self):
        self.assertTrue(hasattr(config, "SELF_AWARENESS_MENTION_ENABLED"),
                        "第四键有消费方，必须保留")

    def test_32_no_reference_in_sources(self):
        """organs/nucleus/tools 全库无三键引用（删除安全）。"""
        hits = []
        for base in ("organs", "nucleus", "tools", "functions", "base"):
            for dp, dn, fn in os.walk(os.path.join(_ROOT, base)):
                if "__pycache__" in dp or ".bak" in dp:
                    continue
                for f in fn:
                    if not f.endswith(".py"):
                        continue
                    p = os.path.join(dp, f)
                    try:
                        t = open(p, encoding="utf-8", errors="replace").read()
                    except OSError:
                        continue
                    for k in self.DROPPED:
                        if k in t:
                            hits.append((os.path.relpath(p, _ROOT), k))
        self.assertEqual(hits, [], "删除后仍有引用: %s" % hits[:5])

    def test_33_config_word_searchable(self):
        """config 词面可检：删除留痕注释存在（便于后续追溯）。"""
        t = _src(os.path.join(_ROOT, "config.py"))
        self.assertIn("第182批刀5", t)
        for k in self.DROPPED:
            self.assertIn(k, t, "config 应保留删除留痕（键名出现在说明注释中）")


if __name__ == "__main__":
    unittest.main()
