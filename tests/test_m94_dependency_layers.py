# -*- coding: utf-8 -*-
"""★主线第94批 相关任务：依赖度**口径修正（方案 C + 方案 A）** 门控单测。

任务书原文（第94批 相关任务）：
    方案C：``total_requests`` → ``answer_requests``（保留旧字段一个版本兼容）；
    方案A：新增 ``overall_llm_share = llm_total/(llm+local+search+digestion)``
           （实测 **12.72%**）
           + ``evolution_local_rule_rate = 本地规则修复数/总修复数``（实测 **95.4%**）。

★★本批 T0 的**前提核实**（成立）：实测 ``data/metrics/llm_dependency.json``
    llm 5382 / local 125 / search 774 / digestion 36032 ⇒ 旧口径 97.73%、
    新口径 **12.72%**，与任务书一致。E 组为可复算证据。

★★第95批对本文件的**同步改写**（相关任务 / 相关任务，口径与别名均已变更）：
  ① ``total_requests`` 兼容别名**已彻底移除** ⇒ A3/B 组由「双键同值」改为
     「旧方法与旧键均不存在」；面板回落分支一并删除（A5）。
  ② ``evolution_local_rule_rate`` 分母由「local_rule 总数」改为
     「local_rule **可判定数**」⇒ 与 ``real_fix_rate`` **口径等价**
     （62/62 = 1.0，不再是 62/65 = 0.9538）。方向由裁决确定：任务书 §相关任务
     要求「统一为总数口径」，但那会**推翻第85批 相关任务 的刻意决策**并打红
     5 个守护测试 ⇒ 改为**反向统一**（记 D95-2）。

  ★注意：E 组读的是**运行中框架写入的生产文件**，可能是本批之前的 schema ⇒
    对旧键只做「若存在则同值」的宽容校验，schema 断言一律落在 A3/B2 的
    **内存态新建快照**上（铁律：不对运行态产物做 schema 断言）。

覆盖五组：
  A. 静态接线 —— 类内新成员 + 派生键 + 面板字段 + 补丁库读取器；
  B. 别名移除（方案 C 收尾）—— 旧方法与旧键**均已不存在**；
  C. 分层指标（方案 A）—— 公式、边界、与旧口径的**可复算差异**；
  D. ``evolution_local_rule_rate`` —— 可判定数口径、不编造、缓存、
     与 ``real_fix_rate`` **等价**；
  E. 生产快照只读取证 + 自洽不变量。
"""
import ast
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus import LLMDependencyMetrics as M  # noqa: E402
from nucleus.evolution.patch_verification_split import (  # noqa: E402
    F_SPLIT_VERSION, real_fix_rate,
)

_METRICS_SRC = os.path.join(ROOT, "nucleus", "LLMDependencyMetrics.py")
_BAK = os.path.join(ROOT, ".bak_batch94", "LLMDependencyMetrics.py.bak")
_HEALTH = os.path.join(ROOT, "functions", "health_ui.py")
_PROD_SNAP = os.path.join(ROOT, "data", "metrics", "llm_dependency.json")

_NEW_MEMBERS = ("search_total", "digestion_total", "answer_requests",
                "overall_llm_share", "evolution_local_rule_rate")


def _read(path):
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        return f.read().replace("\r\n", "\n")


def _class_methods(path, cls):
    _t = ast.parse(_read(path))
    for _n in _t.body:
        if isinstance(_n, ast.ClassDef) and _n.name == cls:
            return {x.name for x in _n.body
                    if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return set()


class _Fresh(unittest.TestCase):
    """每个用例一个**内存态**度量器（不落盘、不启小时线程）。"""

    def setUp(self):
        self.m = M.LLMDependencyMetrics(base_dir=os.path.join(
            ROOT, ".pytest_tmp", "m94_dep_never_used"), auto_hourly_log=False)
        # 清掉 _load() 可能带来的残留，保证每个用例从全零开始
        with self.m._lock:
            self.m._counters = M._blank_counters()
            self.m._history = []
        self.m._m94_lr_cache = None
        self.m._m94_lr_cache_ts = 0.0

    def _seed(self, llm=0, local=0, search=0, digestion=0):
        with self.m._lock:
            self.m._counters["llm_call_count"][M.SCENE_LUNG] = llm
            self.m._counters["local_inference_count"][M.KIND_RULE] = local
            self.m._counters["search_count"][M.SEARCH_WIKI] = search
            self.m._counters["digestion_count"][M.DIGEST_KNOWLEDGE] = digestion

    def tearDown(self):
        import shutil
        shutil.rmtree(os.path.join(ROOT, ".pytest_tmp", "m94_dep_never_used"),
                      ignore_errors=True)


class TestM94DepStaticWiring(unittest.TestCase):
    """A 组：静态接线。"""

    def test_A1_new_members_inside_class(self):
        _names = _class_methods(_METRICS_SRC, "LLMDependencyMetrics")
        for _w in _NEW_MEMBERS:
            self.assertIn(_w, _names, "★%s 必须挂在 LLMDependencyMetrics 类内" % _w)

    def test_A2_before_has_none_of_this(self):
        """先红后绿：改前源码不含本批任何新名字。"""
        if not os.path.isfile(_BAK):
            self.skipTest("改前备份缺失")
        _old = _read(_BAK)
        for _w in ("answer_requests", "overall_llm_share",
                   "evolution_local_rule_rate", "_M94_PATCH_FILES"):
            self.assertNotIn(_w, _old, "★改前不应有 %s" % _w)
            self.assertIn(_w, _read(_METRICS_SRC))

    def test_A3_snapshot_has_derived_keys(self):
        _snap = M.LLMDependencyMetrics(base_dir=os.path.join(
            ROOT, ".pytest_tmp", "m94_dep_sw"), auto_hourly_log=False).get_snapshot()
        _d = _snap["derived"]
        for _k in ("llm_call_total", "local_inference_total", "search_total",
                   "digestion_total", "answer_requests",
                   "llm_dependency_ratio", "self_sufficiency_score",
                   "overall_llm_share", "evolution_local_rule_rate"):
            self.assertIn(_k, _d, "★derived 必须含 %s" % _k)
        self.assertNotIn("total_requests", _d,
                         "★第95批 T-95c：旧键已彻底移除")
        import shutil
        shutil.rmtree(os.path.join(ROOT, ".pytest_tmp", "m94_dep_sw"),
                      ignore_errors=True)

    def test_A4_patch_loader_is_module_level_readonly(self):
        _src = _read(_METRICS_SRC)
        self.assertIn('_M94_PATCH_FILES = ("data/patches/patch_history.json",', _src)
        self.assertIn('"data/patches/pending_patches.json")', _src)
        _t = ast.parse(_src)
        _mod_funcs = {n.name for n in _t.body if isinstance(n, ast.FunctionDef)}
        self.assertIn("_m94_load_patch_records", _mod_funcs,
                      "★补丁库读取器应为模块级纯读函数（可单测隔离）")
        # 只读：不得出现任何写盘调用
        _f = next(n for n in _t.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_m94_load_patch_records")
        _body = ast.unparse(_f)
        for _w in ("open(", "safe_write_json", "os.replace", "shutil", "write("):
            self.assertNotIn(_w, _body, "★读取器必须只读（出现 %s）" % _w)

    def test_A5_health_panel_exposes_overall_share(self):
        _h = _read(_HEALTH)
        self.assertIn('id="llmdepOverall"', _h, "★面板必须新增全栈占比字段")
        self.assertIn("全栈大模型占比", _h)
        self.assertIn("dv.answer_requests", _h, "★面板只读 answer_requests")
        self.assertNotIn("dv.total_requests", _h,
                         "★第95批 T-95c：回落分支已删除")


class TestM94RenameCompat(_Fresh):
    """B 组（★第95批 相关任务 改写）：兼容别名已按裁决移除 ⇒ 断言「不存在」。"""

    def test_B1_alias_method_removed(self):
        self.assertFalse(hasattr(self.m, "total_requests"),
                         "★第95批 T-95c：total_requests 别名已移除")
        self._seed(llm=5382, local=125)
        self.assertEqual(5382 + 125, self.m.answer_requests())

    def test_B2_snapshot_has_no_legacy_key(self):
        """★内存态新建快照：旧键必须**不存在**（确定性 schema 断言）。"""
        self._seed(llm=10, local=5)
        _d = self.m.get_snapshot()["derived"]
        self.assertNotIn("total_requests", _d, "★旧键已彻底移除")
        self.assertEqual(15, _d["answer_requests"])

    def test_B3_no_total_requests_anywhere(self):
        """★别名移除必须**彻底**：类内无定义，且内部消费方已不再引用。"""
        _t = ast.parse(_read(_METRICS_SRC))
        for _n in ast.walk(_t):
            if isinstance(_n, ast.ClassDef) and _n.name == "LLMDependencyMetrics":
                _names = [x.name for x in _n.body
                          if isinstance(x, ast.FunctionDef)]
                self.assertNotIn("total_requests", _names,
                                 "★别名必须已从类内移除")
        _src = _read(_METRICS_SRC)
        self.assertNotIn("self.total_requests()", _src,
                         "★内部消费方须同步改读 answer_requests")

    def test_B4_search_digestion_not_in_answer_total(self):
        """★方案C 的语义边界：回答类总数**不含**搜索/消化。"""
        self._seed(llm=3, local=2, search=100, digestion=1000)
        self.assertEqual(5, self.m.answer_requests())
        self.assertEqual(100, self.m.search_total())
        self.assertEqual(1000, self.m.digestion_total())


class TestM94LayeredMetrics(_Fresh):
    """C 组：方案 A 分层指标。"""

    def test_C1_formula(self):
        self._seed(llm=30, local=10, search=40, digestion=20)
        self.assertAlmostEqual(30 / 100.0, self.m.overall_llm_share(), places=6)

    def test_C2_empty_and_zero_denominator(self):
        self.assertEqual(0.0, self.m.overall_llm_share())
        self._seed(llm=0, local=0, search=7, digestion=9)
        self.assertEqual(0.0, self.m.overall_llm_share())
        self._seed(llm=0)
        self.assertEqual(0.0, self.m.overall_llm_share())

    def test_C3_rounded_to_4dp(self):
        self._seed(llm=1, local=1, search=1, digestion=1)
        self.assertEqual(0.25, self.m.overall_llm_share())
        self._seed(llm=1, local=0, search=0, digestion=6)
        self.assertEqual(round(1 / 7.0, 4), self.m.overall_llm_share())

    def test_C4_totals_match_counters(self):
        self._seed(llm=11, local=22, search=33, digestion=44)
        self.assertEqual(11, self.m.llm_total())
        self.assertEqual(22, self.m.local_total())
        self.assertEqual(33, self.m.search_total())
        self.assertEqual(44, self.m.digestion_total())

    def test_C5_recompute_task_book_measurements(self):
        """★任务书实测值复算：5382/125/774/36032 → 0.9773 与 **0.1272**。"""
        self._seed(llm=5382, local=125, search=774, digestion=36032)
        self.assertEqual(0.1272, self.m.overall_llm_share(),
                         "★与任务书 12.72% 必须逐位一致")
        self.assertEqual(0.9773, self.m.llm_dependency_ratio(),
                         "★旧口径 97.73%")
        self.assertEqual(0.0227, self.m.self_sufficiency_score())
        # 两口径分母不同 ⇒ 差异可复算
        self.assertEqual(5382 + 125, self.m.answer_requests())
        _den = 5382 + 125 + 774 + 36032
        self.assertAlmostEqual(1 - 5382 / _den, 1 - self.m.overall_llm_share(),
                               places=4)

    def test_C6_existing_metrics_unchanged_by_new_one(self):
        """★零行为变化：新增指标不得改动既有两指标的取值。"""
        for _seed in ((0, 0, 0, 0), (100, 0, 0, 0), (1, 1, 1, 1),
                      (5382, 125, 774, 36032)):
            self._seed(llm=_seed[0], local=_seed[1], search=_seed[2],
                       digestion=_seed[3])
            _r1 = self.m.llm_dependency_ratio()
            _s1 = self.m.self_sufficiency_score()
            _t1 = self.m.answer_requests()
            self.m.overall_llm_share()          # 调新指标
            self.m.get_snapshot()               # 调快照
            self.assertEqual(_r1, self.m.llm_dependency_ratio())
            self.assertEqual(_s1, self.m.self_sufficiency_score())
            self.assertEqual(_t1, self.m.answer_requests())

    def test_C7_share_le_ratio_when_search_digestion_positive(self):
        self._seed(llm=50, local=50, search=1, digestion=1)
        self.assertLessEqual(self.m.overall_llm_share(),
                             self.m.llm_dependency_ratio())


class TestM94LocalRuleRate(_Fresh):
    """D 组：`evolution_local_rule_rate` 口径。"""

    def _p(self, fixed, source="local_rule", **kw):
        _d = {"source": source, "_id": "x"}
        if fixed is not None:
            _d["problem_fixed"] = fixed
        _d.update(kw)
        return _d

    def test_D1_reproduces_task_book_95_4pct(self):
        """★第95批 相关任务：任务书的 95.4% 源于**旧（含不可判定）**口径。

        62 条 True + 3 条不可判定（缺 `problem_fixed`）：
          * 旧口径（分母 = local_rule 总数 65）⇒ 62/65 = 0.9538 = 任务书 95.4%；
          * 新口径（分母 = 可判定数 62，与 `real_fix_rate` 同）⇒ 62/62 = 1.0。
        """
        _ps = [self._p(True) for _ in range(62)] + [self._p(None) for _ in range(3)]
        self.assertEqual(65, len(_ps))
        self.assertNotEqual(0.9538, self.m.evolution_local_rule_rate(_ps),
                            "★旧口径的 95.4% 在本批口径下不再成立")
        self.assertEqual(1.0, self.m.evolution_local_rule_rate(_ps),
                         "★可判定数口径：62 条 True / 62 条可判定")

    def test_D2_denominator_is_verifiable_count(self):
        # ★第95批 相关任务：None（不可判定）**移出分母**、不计入分子 ⇒ 1/2
        _ps = [self._p(True), self._p(None), self._p(None), self._p(False)]
        self.assertEqual(0.5, self.m.evolution_local_rule_rate(_ps))

    def test_D3_other_sources_excluded(self):
        _ps = [self._p(True), self._p(False, source="llm"),
               self._p(False, source="llm")]
        self.assertEqual(1.0, self.m.evolution_local_rule_rate(_ps))

    def test_D4_no_local_rule_returns_none(self):
        self.assertIsNone(self.m.evolution_local_rule_rate(
            [self._p(True, source="llm")]))

    def test_D5_empty_never_fabricates_zero(self):
        self.assertIsNone(self.m.evolution_local_rule_rate([]),
                          "★无样本必须 None，不得编造 0.0")

    def test_D6_unjudgeable_excluded_from_denominator(self):
        """★第95批 相关任务：不可判定**移出分母**（与 real_fix_rate 同口径）。"""
        _ps = [self._p(True), self._p(None, **{F_SPLIT_VERSION: 1})]
        self.assertEqual(1.0, self.m.evolution_local_rule_rate(_ps),
                         "★1 条可判定且为 True ⇒ 1/1")

    def test_D6b_all_unjudgeable_returns_none(self):
        """★无可判定样本 ⇒ 返回 None（不虚报 0.0，也不虚报 1.0）。"""
        _ps = [self._p(None), self._p(None, **{F_SPLIT_VERSION: 1})]
        self.assertIsNone(self.m.evolution_local_rule_rate(_ps))

    def test_D7_equivalent_to_real_fix_rate(self):
        """★第95批 相关任务：两者分母口径已统一 ⇒ 同数据下**等价**（D95-2）。"""
        _ps = [self._p(True) for _ in range(62)] + [self._p(None) for _ in range(3)]
        self.assertEqual(1.0, self.m.evolution_local_rule_rate(_ps))
        self.assertEqual(1.0, round(real_fix_rate(_ps), 4),
                         "★real_fix_rate 分母是可判定数（62/62）")
        self.assertEqual(round(real_fix_rate(_ps), 4),
                         self.m.evolution_local_rule_rate(_ps),
                         "★口径统一后必须等价")

    def test_D8_cache_respected_and_resettable(self):
        _calls = {"n": 0}
        _orig = M._m94_load_patch_records

        def _fake():
            _calls["n"] += 1
            return [self._p(True)] * 10

        M._m94_load_patch_records = _fake
        try:
            self.m._m94_lr_cache = None
            self.m._m94_lr_cache_ts = 0.0
            self.assertEqual(1.0, self.m.evolution_local_rule_rate())
            self.assertEqual(1.0, self.m.evolution_local_rule_rate())
            self.assertEqual(1, _calls["n"], "★60s TTL 内只读一次补丁库（面板 10s 轮询）")
        finally:
            M._m94_load_patch_records = _orig
            self.m._m94_lr_cache = None
            self.m._m94_lr_cache_ts = 0.0

    def test_D9_explicit_patches_bypass_cache_and_io(self):
        _calls = {"n": 0}
        _orig = M._m94_load_patch_records

        def _fake():
            _calls["n"] += 1
            return []

        M._m94_load_patch_records = _fake
        try:
            self.assertEqual(0.5, self.m.evolution_local_rule_rate(
                [self._p(True), self._p(False)]))
            self.assertEqual(0, _calls["n"], "★显式传入必须零 IO")
        finally:
            M._m94_load_patch_records = _orig

    def test_D10_never_raises_on_garbage(self):
        self.assertIsNone(self.m.evolution_local_rule_rate("x"))
        self.assertIsNone(self.m.evolution_local_rule_rate([None, 1, "a"]))

    def test_D11_rate_is_in_unit_interval(self):
        for _n_ok, _n_tot in ((0, 7), (3, 7), (7, 7)):
            _ps = ([self._p(True)] * _n_ok + [self._p(False)] * (_n_tot - _n_ok))
            _r = self.m.evolution_local_rule_rate(_ps)
            self.assertGreaterEqual(_r, 0.0)
            self.assertLessEqual(_r, 1.0)


class TestM94ProductionSnapshot(unittest.TestCase):
    """E 组：生产快照只读取证 + 自洽不变量。"""

    def _snap(self):
        if not os.path.isfile(_PROD_SNAP):
            self.skipTest("生产指标文件缺失")
        with io.open(_PROD_SNAP, encoding="utf-8") as f:
            return json.load(f)

    def test_E1_read_only(self):
        import hashlib
        if not os.path.isfile(_PROD_SNAP):
            self.skipTest("生产指标文件缺失")
        _b = open(_PROD_SNAP, "rb").read()
        _h = hashlib.md5(_b).hexdigest()
        M.get_dependency_snapshot()
        self.assertEqual(_h, hashlib.md5(open(_PROD_SNAP, "rb").read()).hexdigest())

    def test_E2_share_recomputable_from_counters(self):
        _s = self._snap()
        _c = _s.get("counters") or {}
        _d = _s.get("derived") or {}
        _llm = sum(int(x) for x in (_c.get("llm_call_count") or {}).values())
        _loc = sum(int(x) for x in (_c.get("local_inference_count") or {}).values())
        _sea = sum(int(x) for x in (_c.get("search_count") or {}).values())
        _dig = sum(int(x) for x in (_c.get("digestion_count") or {}).values())
        if "overall_llm_share" in _d and (_llm + _loc + _sea + _dig) > 0:
            self.assertEqual(round(_llm / float(_llm + _loc + _sea + _dig), 4),
                             _d["overall_llm_share"],
                             "★落盘的全栈占比必须可由 counters 精确复算")
        if "answer_requests" in _d:
            _aa = _d["answer_requests"]
            if _llm + _loc > 0:
                self.assertEqual(_llm + _loc, _aa,
                                 "★answer_requests 必须可由 counters 复算")
            # 历史落盘文件可能仍是本批之前的 schema（含旧键）⇒ 只做同值校验；
            # 「旧键已移除」由 B2/A3 的内存态新建快照确定性固化。
            if "total_requests" in _d:
                self.assertEqual(_aa, _d["total_requests"],
                                 "★历史文件若残留旧键，值必须与新键一致")

    def test_E3_stable_keys_survive(self):
        """★第95批 相关任务：稳定键继续落盘（消费方零回归）。

        ★此处**不**断言旧键 `total_requests` 不存在、也**不**强制
        `answer_requests` 存在 —— 生产文件由运行中的框架写入，可能是本批
        之前的 schema（铁律：不对运行态产物做 schema 断言）；「旧键已移除」
        由 A3/B2 用内存态新建快照确定性固化。
        """
        _s = self._snap()
        _d = _s.get("derived") or {}
        for _k in ("llm_call_total", "local_inference_total",
                   "llm_dependency_ratio", "self_sufficiency_score"):
            self.assertIn(_k, _d, "★稳定键必须继续落盘（消费方零回归）")
        self.assertTrue("answer_requests" in _d or "total_requests" in _d,
                        "★回答总数键必须以新旧之一形态存在（改名灰度期）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
