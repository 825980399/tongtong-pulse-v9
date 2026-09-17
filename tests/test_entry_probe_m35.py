# -*- coding: utf-8 -*-
"""主线第35批 门控测试：v2 入口预判（P2-203）+「把/将」分隔符可选（P2-204）+ docstring（T5）。

覆盖：
  T1  预判开关双向 / 六分支行为 / ★模型调用次数（核心验收）/ 源码接线
  T4  无逗号变体识别 / 关闭即回归 / 负样本不误判
  T5  两个目标方法的 docstring 三段齐备
  T2  承重 noqa 未被误删 + 死规则 noqa 未残留在非热文件
"""
import collections
import io
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IW_PATH = os.path.join(_ROOT, "organs", "brain", "PulseInnerWorld.py")
_IW_SRC = io.open(_IW_PATH, encoding="utf-8").read()


class _Switch:
    """临时切换 config 顶层开关。"""

    def __init__(self, **kw):
        self._kw = kw

    def __enter__(self):
        self._old = {k: getattr(config, k) for k in self._kw}
        for k, v in self._kw.items():
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._old.items():
            setattr(config, k, v)
        return False


class _Node:
    def __init__(self, value, keywords, trust):
        self.value = value
        self.keywords = keywords
        self.trust_score = trust


class _FakePool:
    def __init__(self, nodes):
        self._nodes = list(nodes)

    def query(self, evol_level=None, importance=None, source_organ=None,
              space_path_prefix=None, limit=100):
        return list(self._nodes)[:limit]


_WEAK_POOL = _FakePool([_Node("无关内容甲乙丙", ["别的"], 50.0) for _ in range(20)])
_STRONG_POOL = _FakePool(
    [_Node("微服务架构的可维护性", ["微服务", "架构"], 70.0) for _ in range(12)])
_HIT_TEXT = "这是一段足够长的可用检索结果，用于验证质量门可以通过并包含天气关键词。"


def _mk_iw(pool=None, retrieve=None, model_result=None):
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw.node_pool = pool
    iw._logs = []
    iw._log = lambda level, msg: iw._logs.append(str(msg))
    iw._knowledge_retrieve = (lambda q: retrieve)
    iw._model_calls = []

    def _model(original_question="", branch_name="", branch_prompt="", **kw):
        iw._model_calls.append(branch_name)
        return model_result

    iw._generate_branch_with_model = _model
    return iw


_Q = "帮我查一下今天的天气，然后提醒我带伞。"
_QK = "对比微服务架构与单体架构在可维护性和性能上的优劣"
_PLAN = [{"name": "a", "prompt": "x", "fallback": ""},
         {"name": "b", "prompt": "y", "fallback": ""}]


class TestProbeSwitch(unittest.TestCase):
    def test_01_switch_default_on(self):
        self.assertTrue(PulseInnerWorld._m35_entry_probe_on())
        self.assertTrue(getattr(config, "ENABLE_MULTI_STEP_ENTRY_PROBE", False))

    def test_02_config_thresholds_exist(self):
        self.assertEqual(config.MULTI_STEP_ENTRY_PROBE_SCAN_LIMIT, 800)
        self.assertEqual(config.MULTI_STEP_ENTRY_PROBE_MIN_NODES, 5)
        self.assertEqual(config.MULTI_STEP_ENTRY_PROBE_MIN_TRUST, 30.0)

    def test_03_switch_off_returns_none(self):
        with _Switch(ENABLE_MULTI_STEP_ENTRY_PROBE=False):
            self.assertFalse(PulseInnerWorld._m35_entry_probe_on())
            iw = _mk_iw(_WEAK_POOL, None)
            self.assertIsNone(PulseInnerWorld._m35_entry_probe(iw, _Q, _PLAN, ["天气", "提醒"]))


class TestProbeBranches(unittest.TestCase):
    def test_10_domain_weak_and_probe_miss_blocks(self):
        """★核心：领域弱 + 探针未命中 → False（跳过 v2）。"""
        iw = _mk_iw(_WEAK_POOL, None)
        self.assertIs(PulseInnerWorld._m35_entry_probe(iw, _Q, _PLAN, ["天气", "提醒"]), False)
        self.assertTrue(any("知识支撑不足" in m for m in iw._logs))

    def test_11_domain_strong_passes(self):
        iw = _mk_iw(_STRONG_POOL, None)
        self.assertIsNone(PulseInnerWorld._m35_entry_probe(iw, _QK, _PLAN, ["服务", "架构"]))

    def test_12_domain_weak_but_probe_hit_passes(self):
        """保守：探针命中即放行，不误拦。"""
        iw = _mk_iw(_WEAK_POOL, _HIT_TEXT)
        self.assertIsNone(PulseInnerWorld._m35_entry_probe(iw, _Q, _PLAN, ["天气"]))

    def test_13_empty_pool_is_uncertain(self):
        iw = _mk_iw(_FakePool([]), None)
        self.assertIsNone(PulseInnerWorld._m35_entry_probe(iw, _Q, _PLAN, ["天气"]))

    def test_14_none_pool_is_uncertain(self):
        iw = _mk_iw(None, None)
        self.assertIsNone(PulseInnerWorld._m35_entry_probe(iw, _Q, _PLAN, ["天气"]))

    def test_15_pool_exception_is_uncertain(self):
        class _Boom:
            def query(self, **kw):
                raise RuntimeError("boom")

        iw = _mk_iw(_Boom(), None)
        self.assertIsNone(PulseInnerWorld._m35_entry_probe(iw, _Q, _PLAN, ["天气"]))

    def test_16_retrieve_exception_is_uncertain(self):
        iw = _mk_iw(_WEAK_POOL, None)

        def _boom(q):
            raise RuntimeError("retrieve boom")

        iw._knowledge_retrieve = _boom
        self.assertIsNone(PulseInnerWorld._m35_entry_probe(iw, _Q, _PLAN, ["天气"]))

    def test_17_empty_core_words_is_uncertain(self):
        iw = _mk_iw(_WEAK_POOL, None)
        # 无核心词 → 领域无法判定 → 保守放行
        self.assertIsNone(PulseInnerWorld._m35_entry_probe(iw, _Q, _PLAN, []))

    def test_18_no_model_call_inside_probe(self):
        """预判本身**不得**发起模型调用。"""
        iw = _mk_iw(_WEAK_POOL, None)
        PulseInnerWorld._m35_entry_probe(iw, _Q, _PLAN, ["天气"])
        self.assertEqual(iw._model_calls, [])


class TestModelCallCount(unittest.TestCase):
    """★T1 核心验收：预判拦截后模型调用次数为 0，否则每步 1 次。"""

    _Q = "请分析深度学习的原理和应用场景，并与传统机器学习进行对比"

    def _run(self, probe_result):
        iw = _mk_iw(_WEAK_POOL, None)
        iw._m35_entry_probe = lambda *a, **k: probe_result
        out = PulseInnerWorld._multi_step_execute_v2(iw, self._Q)
        return out, iw._model_calls

    def test_20_blocked_by_probe_zero_model_calls(self):
        out, calls = self._run(False)
        self.assertIsNone(out)
        self.assertEqual(len(calls), 0, "预判拦截后不得有任何模型兜底调用")

    def test_21_not_blocked_falls_through_to_per_step_calls(self):
        iw = _mk_iw(_WEAK_POOL, None)
        _plan = PulseInnerWorld._generate_task_plan(iw, self._Q) or []
        self.assertGreaterEqual(len(_plan), 2, "本用例需多步计划")
        iw._m35_entry_probe = lambda *a, **k: None
        out = PulseInnerWorld._multi_step_execute_v2(iw, self._Q)
        self.assertIsNone(out)          # 全步失败 → P2-196 返回 None
        self.assertEqual(len(iw._model_calls), len(_plan),
                         "每步各 1 次模型兜底调用")


class TestSourceWiring(unittest.TestCase):
    def test_30_probe_called_once_in_v2(self):
        self.assertEqual(_IW_SRC.count("if self._m35_entry_probe(question, _plan, _core_words) is False:"), 1)
        self.assertNotIn("_m35_entry_probe(question, _plan", _IW_SRC[:0])  # 占位保持可读

    def test_31_probe_before_step_loop(self):
        _i_probe = _IW_SRC.index("if self._m35_entry_probe(question, _plan, _core_words) is False:")
        _i_loop = _IW_SRC.index("_step_results: list[dict] = []")
        self.assertLess(_i_probe, _i_loop, "预判必须在步骤循环之前")

    def test_32_switch_method_defined(self):
        self.assertIn("def _m35_entry_probe_on()", _IW_SRC)
        self.assertIn("def _m35_entry_probe(self, question: str, plan: list, core_words: list):", _IW_SRC)


class TestBaChainLoose(unittest.TestCase):
    _NO_COMMA = [
        "打开设置把蓝牙关掉。",
        "打开微信把这段话发给他。",
        "复制这段代码把它粘贴到新文件。",
        "登录后台把渠道参数改掉。",
        "打开系统设置把自动更新关闭。",
    ]
    _NEG = [
        "如何理解把函数作为参数传递的机制？",
        "配置中心的设计原则有哪些？",
        "删除操作在数据库中是如何保证一致性的？",
        "复制语义与移动语义在 C++ 中的区别是什么？",
    ]

    def test_40_switch_default_on(self):
        self.assertTrue(PulseInnerWorld._m35_ba_chain_loose_on())
        self.assertTrue(getattr(config, "ENABLE_MULTI_STEP_BA_CHAIN_LOOSE", False))

    def test_41_no_comma_recognized_when_on(self):
        with _Switch(ENABLE_MULTI_STEP_BA_CHAIN_LOOSE=True):
            for q in self._NO_COMMA:
                self.assertTrue(PulseInnerWorld._m29_has_multi_step_signal(q), q)

    def test_42_no_comma_rejected_when_off(self):
        """灰度关闭 → 复现修复前行为（必须有逗号）。"""
        with _Switch(ENABLE_MULTI_STEP_BA_CHAIN_LOOSE=False):
            for q in self._NO_COMMA:
                self.assertFalse(PulseInnerWorld._m29_has_multi_step_signal(q), q)

    def test_43_negatives_not_misjudged(self):
        for _on in (True, False):
            with _Switch(ENABLE_MULTI_STEP_BA_CHAIN_LOOSE=_on):
                for q in self._NEG:
                    self.assertFalse(PulseInnerWorld._m29_has_multi_step_signal(q), q)


class TestDocstrings(unittest.TestCase):
    def test_50_admit_layer_task_docstring(self):
        from nucleus.field.InfoField import InfoField
        d = InfoField._admit_layer_task.__doc__ or ""
        for seg in ("参数:", "返回:", "示例:"):
            self.assertIn(seg, d)

    def test_51_probe_docstring(self):
        d = PulseInnerWorld._m35_entry_probe.__doc__ or ""
        for seg in ("参数:", "返回:", "示例:"):
            self.assertIn(seg, d)

    def test_52_loose_switch_docstring(self):
        d = PulseInnerWorld._m35_ba_chain_loose_on.__doc__ or ""
        self.assertIn("返回:", d)


class TestNoqaPolicyGuard(unittest.TestCase):
    """T2 政策护栏：承重 noqa 不得被删；死规则 noqa 不得残留在非热文件。"""

    _LOAD_BEARING_MIN = {"E402": 110, "F841": 40, "F401": 25}
    _DEAD = ("BLE001", "RUF012", "B010", "PERF102", "PLW1510", "DTZ007",
             "S102", "PLC0206", "ASYNC230", "I001", "DTZ005", "DTZ006", "PLW0602")
    _HOT = {"main.py", "config.py", "PulseInnerWorld.py", "PulseCortex.py", "PulseLung.py"}

    @classmethod
    def setUpClass(cls):
        cnt = collections.Counter()
        dead_non_hot = []
        for dp, dn, fn in os.walk(_ROOT):
            # ★主线第47批 T4（P2-310）：改用**统一排除列表**（唯一权威来源
            #   ``nucleus/data/exclude_dirs.py``），避免每个测试各自维护导致遗漏。
            #   背景：第46批新建的 ``.tmp_backup/`` 未在此处排除，备份副本里的
            #   历史 noqa 被当成源码死 noqa —— **实测误报 19 处**。
            from nucleus.data.exclude_dirs import prune as _prune_dirs
            dn[:] = _prune_dirs(dn)
            for f in fn:
                if not f.endswith(".py"):
                    continue
                try:
                    t = io.open(os.path.join(dp, f), encoding="utf-8").read()
                except Exception:
                    continue
                for line in t.split("\n"):
                    if "# noqa" not in line:
                        continue
                    body = line.split("# noqa", 1)[1].split("#")[0]
                    for c in re.findall(r"[A-Z]+[0-9]+", body):
                        cnt[c] += 1
                        if c in cls._DEAD and os.path.basename(f) not in cls._HOT:
                            dead_non_hot.append((f, c))
        cls._cnt = cnt
        cls._dead_non_hot = dead_non_hot

    def test_60_load_bearing_noqa_intact(self):
        for rule, floor in self._LOAD_BEARING_MIN.items():
            self.assertGreaterEqual(
                self._cnt[rule], floor,
                f"{rule} 的门禁承重 noqa 被误删（现值 {self._cnt[rule]} < {floor}）")

    def test_61_dead_noqa_absent_outside_hot_files(self):
        self.assertEqual(
            len(self._dead_non_hot), 0,
            f"非热文件仍有死规则 noqa：{self._dead_non_hot[:8]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
