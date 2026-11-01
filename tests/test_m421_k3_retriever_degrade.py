"""421 刀3：经验检索异常带 degraded 标记。

被守护的五条：
  1) 池抛错 ⇒ `_all_experiences()` 仍返回 `[]`（零行为变化），但 `last_degrade()`
     明确报 `degraded=True` + reason ⇒ 上层能区分「空」与「故障」；
  2) 池正常 ⇒ `degraded=False`（不误报）；
  3) `retrieve()` 异常同样记降级；
  4) `retrieve_with_adjacency()` / `stats()` 两个口都带 `degraded`；
  5) ★`retrieve()` 的返回**仍是 list** —— 本刀只加状态，不改形态
     （6 条既有单测断言 `== []`，改了会当场炸）。

★不碰生产：全部用注入的假池，不构造 `get_experience_pool()` 单例。
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.mnemosyne import experience_retriever as er_mod
from nucleus.mnemosyne.experience_retriever import ExperienceRetriever


class _BoomPool:
    """任何属性访问都抛 —— 模拟「池对象自己坏了」。"""

    def __getattr__(self, name):
        raise RuntimeError(f"池炸了:{name}")


class _GoodPool:
    def __init__(self, exps):
        self._experiences = exps


class _BarePool:
    """既无 _experiences 也无 query_experiences。"""



class TestK3Degrade(unittest.TestCase):
    def test_01_pool_boom_marks_degraded(self):
        r = ExperienceRetriever(pool=_BoomPool())
        self.assertEqual(r._all_experiences(), [], '返回形态必须仍是空 list（零行为变化）')
        d = r.last_degrade()
        self.assertTrue(d["degraded"], '池炸了却报"没降级" ⇒ 故障被伪装成"没有"')
        self.assertIn("RuntimeError", d["reason"])

    def test_02_good_pool_not_degraded(self):
        r = ExperienceRetriever(pool=_GoodPool([{"id": "a"}]))
        self.assertEqual(r._all_experiences(), [{"id": "a"}])
        self.assertFalse(r.last_degrade()["degraded"])

    def test_03_degrade_recovers(self):
        """先炸后好 ⇒ 状态要能回到 False（不能一次故障永久标红）。"""
        r = ExperienceRetriever(pool=_BoomPool())
        r._all_experiences()
        self.assertTrue(r.last_degrade()["degraded"])
        r._pool = _GoodPool([{"id": "b"}])
        r._all_experiences()
        self.assertFalse(r.last_degrade()["degraded"])

    def test_04_bare_pool_marks_degraded(self):
        r = ExperienceRetriever(pool=_BarePool())
        self.assertEqual(r._all_experiences(), [])
        d = r.last_degrade()
        self.assertTrue(d["degraded"])
        self.assertIn("query_experiences", d["reason"])

    def test_05_retrieve_exception_marks_degraded(self):
        r = ExperienceRetriever(pool=_GoodPool([{"id": "a", "content": "x"}]))
        # 编码器可用，但候选集阶段炸 ⇒ 走 retrieve 的 except
        r.available = lambda: True
        r.candidates = lambda **_k: (_ for _ in ()).throw(RuntimeError("候选集炸了"))
        self.assertEqual(r.retrieve("量子"), [], '失败仍返回空 list')
        d = r.last_degrade()
        self.assertTrue(d["degraded"])
        self.assertIn("retrieve", d["reason"])

    def test_06_retrieve_still_returns_list(self):
        """★形态守门：本刀不得把 retrieve 的返回改成 dict。"""
        r = ExperienceRetriever(pool=_GoodPool([]))
        self.assertIsInstance(r.retrieve("任意"), list)
        self.assertIsInstance(r.retrieve(""), list)
        self.assertIsInstance(r.retrieve(None), list)

    def test_07_stats_exposes_degrade(self):
        r = ExperienceRetriever(pool=_BoomPool())
        r._all_experiences()
        st = r.stats()
        self.assertTrue(st["degraded"])
        self.assertTrue(st["degrade_reason"])
        # 既有键不能被本刀碰掉
        for _k in ("enabled", "encoder_available", "top_k", "filter_polluted",
                   "cached_vectors", "observations", "last_observe"):
            self.assertIn(_k, st)

    def test_08_retrieve_with_adjacency_carries_degrade(self):
        r = ExperienceRetriever(pool=_BoomPool())
        r._all_experiences()
        r.retrieve = lambda *a, **k: []
        out = r.retrieve_with_adjacency("量子")
        self.assertIn("degraded", out)
        self.assertIn("degrade_reason", out)
        self.assertTrue(out["degraded"])
        # 既有键保持
        for _k in ("direct", "adjacent", "adjacent_meta", "enabled"):
            self.assertIn(_k, out)

    def test_09_healthy_adjacency_not_degraded(self):
        r = ExperienceRetriever(pool=_GoodPool([]))
        r.retrieve = lambda *a, **k: []
        out = r.retrieve_with_adjacency("量子")
        self.assertFalse(out["degraded"])


# ======================================================================
# 附带：ruff 等价改写守门（UP031 %-格式化 → f-string）
#   ★基准用**硬编码期望字面量**（固定输入 → 人手算出的固定输出），
#     不复制被测函数的表达式，也不拿被测函数的输出当自己的期望值 ——
#     后者是循环论证：格式写错了照样"通过"（412 的 score_weighted 栽过）。
# ======================================================================


class _CapLog:
    def __init__(self):
        self.msgs = []

    def info(self, m, *a, **k):
        self.msgs.append(m)


_CMP = {"new_count": 5, "old_count": 9, "overlap": 2,
        "old_polluted": 3, "old_polluted_ratio": 0.3333}


class TestK3RuffEquivalence(unittest.TestCase):

    def test_10_observe_line_identical(self):
        r = ExperienceRetriever(pool=_GoodPool([]))
        r.compare = lambda _q, top_k=None: dict(_CMP)
        with mock.patch.object(er_mod, 'retriever_enabled', lambda: True):
            _log = _CapLog()
            r.observe("q", logger=_log)
        self.assertEqual(
            _log.msgs[0],
            "[经验检索] 新检索器返回5条，现有返回9条，重叠2条"
            "（现有污染3条/33.3%）",
            'observe 的播报行改写后内容变了')

    def test_11_observe_daily_line_identical(self):
        r = ExperienceRetriever(pool=_GoodPool([]))
        r.available = lambda: True
        r.candidates = lambda **_k: [{"content": "a"}, {"content": "b"}, {"content": "c"}]
        r.compare = lambda _q, top_k=None: dict(_CMP)
        with mock.patch.object(er_mod, 'retriever_enabled', lambda: True):
            _log = _CapLog()
            r.observe_daily(sample=3, logger=_log)
        self.assertEqual(
            _log.msgs[-1],
            "[经验检索] 每日观测: 3 个样本 / 新均5.0条 / 旧均9.0条 / "
            "重叠均2.0条（旧结果污染率均 33.3%）",
            'observe_daily 的汇总行改写后内容变了')

    def test_12_pollution_findings_identical(self):
        _pool = _GoodPool([
            {"id": "a", "content": "x", "polluted": True, "pollution_reason": "a"},
            {"id": "b", "content": "x", "polluted": True, "pollution_reason": "a"},
            {"id": "c", "content": "x", "polluted": True, "pollution_reason": "b"},
            {"id": "d", "content": "x"},
        ])
        _rep = er_mod.analyze_pollution(_pool)
        self.assertEqual(_rep["polluted"], 3)
        self.assertEqual(_rep["total"], 4)
        self.assertEqual(
            _rep["findings"][0],
            "污染率 75.0%（3/4）—— 现有检索器按时间倒序取前 N 条时，"
            "结果极易被同质模板文本占满。",
            '污染率行改写后内容变了')
        self.assertEqual(_rep["findings"][1], "污染原因分布：a×2、b×1")


if __name__ == '__main__':
    unittest.main()
