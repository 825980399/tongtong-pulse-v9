# -*- coding: utf-8 -*-
"""★主线第95批门控单测：待裁决项落地 + 零调用点补埋点。

任务书（第95批，P1/P2）五件：
  T-95a 死配置清理 —— ``no_fix_cooldown_seconds["已有待审批"]`` 删除；
  T-95b 口径统一   —— ``evolution_local_rule_rate`` 与 ``real_fix_rate`` 同分母；
  T-95c 别名移除   —— ``total_requests`` 兼容别名删除 + 4 处消费方同步；
  T-95d 补埋点     —— ``SCENE_CODE_LEARN`` 场景透传 + ``KIND_GUARD`` 守卫埋点；
  T-95e 命名收敛   —— ``_m40_last_usage`` / ``_m44_last_usage`` → ``_last_llm_usage``。

★★本批 T0 的**前提核实结论（4 项中 2 项被证伪 / 1 项需改向）**——本文件按实测结论落地：
  * T-95a **成立**：``_cooldown_classify`` 只会返回「高危·安全拦截」/「本地无规则·转LLM」，
    故该键**永不被前缀匹配** ⇒ 真死配置（A 组固化「可达 reason 的 TTL 零变化」）。
  * T-95b **方向被改**：任务书要求「统一为**总数**口径」，但那会推翻第85批 T-85c 的
    刻意决策（分母=可判定数）并打红 5 个守护测试 ⇒ 经裁决**反向统一**（B 组）。
  * T-95c **成立但消费面更大**：除 ``health_ui`` 外还有 4 处内部消费方（C 组）。
  * T-95d **部分证伪**：``KIND_RULE`` 第84批**已接线**（``ResonanceEngine`` 用别名
    ``_rec_local84(_KIND_RULE84)``，字面量 grep 命中不到 ⇒ 假零调用点）；真零调用点
    只剩 ``SCENE_CODE_LEARN`` 与 ``KIND_GUARD``（D 组只补这两个，且**不重复计数**）。
  * T-95e **成立**：6 处源码 + 3 个测试文件，两个属性分属不同类、重命名无冲突（E 组）。
"""
import ast
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
from nucleus import LLMDependencyMetrics as M  # noqa: E402
from nucleus.evolution.patch_verification_split import (  # noqa: E402
    F_SPLIT_VERSION, real_fix_rate,
)
from nucleus.llm import call_recorder as cr  # noqa: E402

_BAK = os.path.join(ROOT, ".bak_batch95")
_CFG = os.path.join(ROOT, "config.py")
_SE = os.path.join(ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
_METRICS = os.path.join(ROOT, "nucleus", "LLMDependencyMetrics.py")
_HEALTH = os.path.join(ROOT, "functions", "health_ui.py")
_LUNG = os.path.join(ROOT, "organs", "body", "PulseLung.py")
_PCL = os.path.join(ROOT, "organs", "brain", "PulseCodeLearner.py")
_RES = os.path.join(ROOT, "nucleus", "synapsys", "ResonanceEngine.py")
_CR = os.path.join(ROOT, "nucleus", "llm", "call_recorder.py")

_EXPECT_COOLDOWN_KEYS = {"高危·安全拦截", "本地无规则·转LLM", "_default"}
# `_cooldown_classify` 实际可达的 reason（前缀匹配用真实形态）
_REACHABLE_REASONS = ("高危·安全拦截", "高危·安全拦截(unsafe_eval)",
                      "高危·安全拦截(sql_injection)", "本地无规则·转LLM")


def _read(path):
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        return f.read().replace("\r\n", "\n")


def _bak(rel):
    """`.bak_batch95/<扁平名>.bak` 路径（rel 用 / 分隔）。"""
    return os.path.join(_BAK, rel.replace("/", "__") + ".bak")


def _tree(path):
    return ast.parse(_read(path))


def _func_node(path, cls, name):
    for _n in ast.walk(_tree(path)):
        if isinstance(_n, ast.ClassDef) and _n.name == cls:
            for _m in _n.body:
                if isinstance(_m, ast.FunctionDef) and _m.name == name:
                    return _m
    return None


def _nested_func(path, outer, inner):
    _o = _func_node(path, "SafeEvolutionExecutor", outer)
    if _o is None:
        return None
    for _x in ast.walk(_o):
        if isinstance(_x, ast.FunctionDef) and _x.name == inner:
            return _x
    return None


def _kwdefaults(node):
    return {a.arg: d for a, d in zip(node.args.kwonlyargs, node.args.kw_defaults)}


def _all_args(node):
    _a = list(node.args.args) + list(node.args.kwonlyargs)
    return [x.arg for x in _a]


def _default_of(node, name):
    """取形参 `name` 的默认值节点（pos-or-kw 与 kwonly 均可）。"""
    _a = list(node.args.args)
    _dd = list(node.args.defaults)
    _off = len(_a) - len(_dd)
    for _i, _arg in enumerate(_a):
        if _arg.arg == name:
            return _dd[_i - _off] if _i >= _off else None
    for _arg, _d in zip(node.args.kwonlyargs, node.args.kw_defaults):
        if _arg.arg == name:
            return _d
    return None


def _dict_keys(node):
    return {k.value for k in node.keys if isinstance(k, ast.Constant)}


def _call_attr_names(node, attr):
    out = []
    for _x in ast.walk(node):
        if (isinstance(_x, ast.Call) and isinstance(_x.func, ast.Attribute)
                and _x.func.attr == attr):
            out.append(_x)
    return out


def _production_py():
    """全项目生产 .py（跳过 .git/tmp/data/__pycache__/.bak*/.pytest_tmp）。"""
    _skip = {".git", "tmp", "data", "__pycache__", ".workbuddy", ".pytest_tmp",
             "node_modules", ".venv", "venv"}
    for _dp, _dn, _fn in os.walk(ROOT):
        _dn[:] = [d for d in _dn
                  if d not in _skip and not d.startswith(".bak")]
        for _n in _fn:
            if _n.endswith(".py"):
                yield os.path.join(_dp, _n)


# ==================================================================== A：T-95a
class TestM95DeadConfig(unittest.TestCase):
    """A 组：死配置清理（T-95a）—— 静态 + 行为 + 先红后绿。"""

    def test_A1_config_cooldown_keys_exact(self):
        _keys = None
        for _n in ast.walk(_tree(_CFG)):
            if isinstance(_n, ast.Dict) and "高危·安全拦截" in _dict_keys(_n):
                _keys = _dict_keys(_n)
        self.assertIsNotNone(_keys, "★未定位到 no_fix_cooldown_seconds 字典")
        self.assertEqual(_EXPECT_COOLDOWN_KEYS, _keys,
                         "★死配置「已有待审批」必须已从 config 删除")

    def test_A2_se_fallback_table_keys_exact(self):
        _found = []
        for _n in ast.walk(_tree(_SE)):
            if not isinstance(_n, ast.Assign):
                continue
            _t = _n.targets[0]
            if not (isinstance(_t, ast.Attribute)
                    and _t.attr == "_no_fix_cooldown_secs"
                    and isinstance(_n.value, ast.Dict)):
                continue
            _k = _dict_keys(_n.value)
            if not _k:
                continue  # L272/L284 的空表初始化，不构成「回填表」
            _found.append(_k)
        self.assertTrue(_found, "★未定位到 SE 的 _no_fix_cooldown_secs 回填表")
        for _k in _found:
            self.assertEqual(_EXPECT_COOLDOWN_KEYS, _k,
                             "★硬编码回填表同样不得含死配置")

    def test_A3_cooldown_classify_return_set(self):
        """★死配置的**根因**：分类器只能返回两值 + None。"""
        _fn = _nested_func(_SE, "repair_with_distillation", "_cooldown_classify")
        self.assertIsNotNone(_fn, "★未定位到 _cooldown_classify")
        _vals = set()
        for _x in ast.walk(_fn):
            if isinstance(_x, ast.Return):
                if _x.value is None or (isinstance(_x.value, ast.Constant)
                                        and _x.value.value is None):
                    _vals.add(None)
                elif isinstance(_x.value, ast.Constant):
                    _vals.add(_x.value.value)
        self.assertEqual({"高危·安全拦截", "本地无规则·转LLM", None}, _vals,
                         "★分类器若新增返回值，死配置结论需重评")

    def test_A4_reachable_ttls_unchanged_vs_before(self):
        """★零行为变化证据：**可达 reason** 的 TTL 改前改后逐项相同。"""
        _bak_cfg = _bak("config.py")
        if not os.path.isfile(_bak_cfg):
            self.skipTest("改前备份缺失")
        _old = None
        for _n in ast.walk(ast.parse(_read(_bak_cfg))):
            if isinstance(_n, ast.Dict) and "高危·安全拦截" in _dict_keys(_n):
                _old = {k.value: v.value for k, v in zip(_n.keys, _n.values)
                        if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)}
        self.assertIsNotNone(_old, "★改前字典未定位")
        self.assertIn("已有待审批", _old, "★先红：改前必须存在该键")

        _obj = _se_new()
        _obj._no_fix_cooldown_secs = dict(config.EVOLUTION_CONFIG[
            "no_fix_cooldown_seconds"])
        for _r in _REACHABLE_REASONS:
            self.assertEqual(_old[_r.split("(")[0]], _obj._cooldown_ttl_for(_r),
                             "★可达 reason %s 的 TTL 必须与改前一致" % _r)
        self.assertEqual(float(_old["_default"]),
                         _obj._cooldown_ttl_for("完全未知的原因xyz"))

    def test_A5_active_table_has_no_dead_key(self):
        _obj = _se_new()
        _obj._no_fix_cooldown_secs = dict(config.EVOLUTION_CONFIG[
            "no_fix_cooldown_seconds"])
        self.assertEqual(_EXPECT_COOLDOWN_KEYS, set(_obj._no_fix_cooldown_secs))
        self.assertNotIn("已有待审批", _obj._no_fix_cooldown_secs)

    def test_A6_red_then_green_source_marker(self):
        _src = _read(_CFG)
        _bak_cfg = _bak("config.py")
        if not os.path.isfile(_bak_cfg):
            self.skipTest("改前备份缺失")
        self.assertIn('"已有待审批": 7200.0', _read(_bak_cfg), "★先红：改前含死配置")
        self.assertNotIn('"已有待审批": 7200.0', _src, "★后绿：改后已删除")
        self.assertIn("T-95a", _src, "★须留痕说明删除依据")


def _se_new():
    """免构造副作用地取一个 SafeEvolutionExecutor 实例（只需方法可用）。"""
    from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor as _SE
    return object.__new__(_SE)


# ==================================================================== B：T-95b
class TestM95RateUnification(unittest.TestCase):
    """B 组：修复率口径统一（T-95b，**反向**统一到「可判定数」）。"""

    def setUp(self):
        self.m = M.LLMDependencyMetrics(base_dir=os.path.join(
            ROOT, ".pytest_tmp", "m95_rate"), auto_hourly_log=False)
        with self.m._lock:
            self.m._counters = M._blank_counters()
            self.m._history = []
        self.m._m94_lr_cache = None
        self.m._m94_lr_cache_ts = 0.0

    def tearDown(self):
        shutil.rmtree(os.path.join(ROOT, ".pytest_tmp", "m95_rate"),
                      ignore_errors=True)

    @staticmethod
    def _p(pf, source="local_rule", **extra):
        _d = {"source": source, "problem_fixed": pf,
              F_SPLIT_VERSION: 1, "file": "organs/x.py", "method": "m"}
        _d.update(extra)
        return _d

    def test_B1_equivalent_on_mixed_set(self):
        """★62 条 True + 3 条 None ⇒ 两者**等价**（1.0）。"""
        _ps = [self._p(True) for _ in range(62)] + [self._p(None) for _ in range(3)]
        self.assertEqual(1.0, self.m.evolution_local_rule_rate(_ps))
        self.assertEqual(round(real_fix_rate(_ps), 4),
                         self.m.evolution_local_rule_rate(_ps),
                         "★口径统一后必须等价（D95-2）")

    def test_B2_equivalent_on_true_false_mix(self):
        _ps = [self._p(True), self._p(False), self._p(True)]
        self.assertEqual(0.6667, self.m.evolution_local_rule_rate(_ps))
        self.assertEqual(round(real_fix_rate(_ps), 4),
                         self.m.evolution_local_rule_rate(_ps))

    def test_B3_real_fix_rate_denominator_unchanged(self):
        """★real_fix_rate 的分母**未被本批改动**（版本无关复算，铁律 100）。"""
        self.assertEqual(0.5, round(real_fix_rate(
            [self._p(True), self._p(False), self._p(None)]), 4),
            "★可判定 2 条中 1 条修好 ⇒ 0.5（None 不进分母）")

    def test_B4_local_rule_excludes_unjudgeable(self):
        _ps = [self._p(True), self._p(None), self._p(None), self._p(False)]
        self.assertEqual(0.5, self.m.evolution_local_rule_rate(_ps),
                         "★分母=可判定数=2（1 True / 1 False）")

    def test_B5_all_unjudgeable_returns_none(self):
        _ps = [self._p(None), self._p(None)]
        self.assertIsNone(self.m.evolution_local_rule_rate(_ps),
                          "★无可判定样本 ⇒ None（不虚报）")

    def test_B6_legacy_95_4_no_longer_applies(self):
        """★旧口径 62/65 = 0.9538 在本批口径下**不再成立**。"""
        _ps = [self._p(True) for _ in range(62)] + [self._p(None) for _ in range(3)]
        _r = self.m.evolution_local_rule_rate(_ps)
        self.assertNotEqual(0.9538, _r, "★95.4% 属旧（含不可判定）口径")
        self.assertEqual(1.0, _r)

    def test_B7_marker_renamed(self):
        _src = _read(_METRICS)
        self.assertIn("_m95_lr_denominator_verifiable", _src)
        self.assertNotIn("_m94_lr_denominator_total", _src,
                         "★旧口径标记必须移除，防误读")

    def test_B8_zero_regression_on_other_sources(self):
        _ps = [self._p(True), self._p(False, source="llm"),
               self._p(False, source="llm")]
        self.assertEqual(1.0, self.m.evolution_local_rule_rate(_ps),
                         "★非 local_rule 来源仍被排除")

    def test_B9_returns_none_when_no_local_rule(self):
        self.assertIsNone(self.m.evolution_local_rule_rate(
            [self._p(True, source="llm")]))


# ==================================================================== C：T-95c
class TestM95AliasRemoved(unittest.TestCase):
    """C 组：`total_requests` 别名移除 + 4 处内部消费方同步（T-95c）。"""

    def setUp(self):
        self.m = M.LLMDependencyMetrics(base_dir=os.path.join(
            ROOT, ".pytest_tmp", "m95_alias"), auto_hourly_log=False)
        with self.m._lock:
            self.m._counters = M._blank_counters()
            self.m._history = []

    def tearDown(self):
        shutil.rmtree(os.path.join(ROOT, ".pytest_tmp", "m95_alias"),
                      ignore_errors=True)

    def test_C1_class_has_no_alias(self):
        _names = []
        for _n in ast.walk(_tree(_METRICS)):
            if isinstance(_n, ast.ClassDef) and _n.name == "LLMDependencyMetrics":
                _names = [x.name for x in _n.body if isinstance(x, ast.FunctionDef)]
        self.assertIn("answer_requests", _names)
        self.assertNotIn("total_requests", _names, "★别名必须已移除")

    def test_C2_no_internal_reference(self):
        _src = _read(_METRICS)
        self.assertNotIn("self.total_requests()", _src,
                         "★4 处消费方须全部改读 answer_requests")

    def test_C3_self_sufficiency_value_unchanged(self):
        """★消费方同步后取值不变：5382/(5382+125) = 0.0227。"""
        with self.m._lock:
            self.m._counters["llm_call_count"][M.SCENE_LUNG] = 5382
            self.m._counters["local_inference_count"][M.KIND_RULE] = 125
        self.assertEqual(0.0227, self.m.self_sufficiency_score(),
                         "★自持力口径未变（只换名字）")
        self.assertEqual(5382 + 125, self.m.answer_requests())

    def test_C4_snapshot_has_no_legacy_key(self):
        with self.m._lock:
            self.m._counters["llm_call_count"][M.SCENE_LUNG] = 4
            self.m._counters["local_inference_count"][M.KIND_SIMPLE] = 1
        _d = self.m.get_snapshot()["derived"]
        self.assertNotIn("total_requests", _d)
        self.assertEqual(5, _d["answer_requests"])

    def test_C5_health_ui_dropped_fallback(self):
        _h = _read(_HEALTH)
        self.assertIn("dv.answer_requests", _h)
        self.assertNotIn("dv.total_requests", _h,
                         "★面板回落分支必须同步删除")

    def test_C6_log_text_uses_new_name(self):
        self.assertIn("回答请求=", _read(_METRICS))
        self.assertNotIn('f"总请求=', _read(_METRICS))

    def test_C7_anomaly_check_uses_new_name(self):
        self.assertIn('_d["answer_requests"] >= 20', _read(_METRICS))

    def test_C8_red_then_green(self):
        _bakm = _bak("nucleus/LLMDependencyMetrics.py")
        if not os.path.isfile(_bakm):
            self.skipTest("改前备份缺失")
        _old = _read(_bakm)
        self.assertIn("def total_requests", _old, "★先红：改前有别名")
        self.assertIn('"total_requests": self.total_requests()', _old)
        self.assertNotIn("def total_requests", _read(_METRICS), "★后绿：已删除")


# ==================================================================== D：T-95d
class TestM95Instrumentation(unittest.TestCase):
    """D 组：`SCENE_CODE_LEARN` 场景透传 + `KIND_GUARD` 守卫埋点（T-95d）。"""

    def setUp(self):
        self.m = M.LLMDependencyMetrics(base_dir=os.path.join(
            ROOT, ".pytest_tmp", "m95_instr"), auto_hourly_log=False)
        with self.m._lock:
            self.m._counters = M._blank_counters()
            self.m._history = []

    def tearDown(self):
        shutil.rmtree(os.path.join(ROOT, ".pytest_tmp", "m95_instr"),
                      ignore_errors=True)

    # ---- SCENE_CODE_LEARN ----
    def test_D1_followup_accepts_scene(self):
        _fn = _func_node(_SE, "SafeEvolutionExecutor", "_call_llm_with_followup")
        self.assertIsNotNone(_fn)
        self.assertIn("scene", _all_args(_fn), "★scene 形参必须存在")
        _d = _default_of(_fn, "scene")
        self.assertTrue(_d is None or (isinstance(_d, ast.Constant)
                                       and _d.value is None),
                        "★默认必须为 None（零回归）")

    def test_D2_record_uses_scene_with_evolution_default(self):
        _src = _read(_SE)
        self.assertIn("record_llm_call(scene or SCENE_EVOLUTION)", _src,
                      "★默认 None ⇒ 行为与改前逐字一致")

    def test_D3_for_repair_accepts_and_forwards_scene(self):
        _fn = _func_node(_SE, "SafeEvolutionExecutor", "_call_llm_for_repair")
        self.assertIsNotNone(_fn)
        self.assertIn("scene", _all_args(_fn))
        _calls = _call_attr_names(_fn, "_call_llm_with_followup")
        self.assertEqual(2, len(_calls), "★追问路径共 2 个出口")
        for _c in _calls:
            _names = [ast.unparse(a) for a in _c.args] + \
                     [k.arg for k in _c.keywords]
            self.assertIn("scene", _names,
                          "★每个出口都必须透传 scene（否则仍归 SCENE_EVOLUTION）")

    def test_D4_distillation_accepts_and_forwards_scene(self):
        _fn = _func_node(_SE, "SafeEvolutionExecutor", "repair_with_distillation")
        self.assertIsNotNone(_fn)
        self.assertIn("scene", _all_args(_fn))
        _calls = _call_attr_names(_fn, "_call_llm_for_repair")
        self.assertEqual(2, len(_calls), "★蒸馏内共 2 个取建议出口")
        for _c in _calls:
            _names = [ast.unparse(a) for a in _c.args] + \
                     [k.arg for k in _c.keywords]
            self.assertIn("scene", _names)

    def test_D5_code_learner_declares_code_learn_scene(self):
        _src = _read(_PCL)
        self.assertIn("SCENE_CODE_LEARN", _src)
        self.assertIn("scene=_m95_code_learn", _src,
                      "★代码学习必须显式声明场景")

    def test_D6_metric_accepts_code_learn_bucket(self):
        """★功能侧：`SCENE_CODE_LEARN` 是合法场景 ⇒ 不再坍缩到「其他」。"""
        self.assertIn(M.SCENE_CODE_LEARN, M.LLM_SCENES)
        with self.m._lock:
            self.m.record_llm_call(M.SCENE_CODE_LEARN)
            _b = dict(self.m._counters["llm_call_count"])
        self.assertEqual(1, _b.get(M.SCENE_CODE_LEARN, 0))
        self.assertEqual(0, _b.get(M.SCENE_OTHER, 0), "★不得坍缩为「其他」")

    def test_D7_single_record_call_no_double_counting(self):
        """★★不重复计数：同一出口只能记 1 次 `record_llm_call`。"""
        _fn = _func_node(_SE, "SafeEvolutionExecutor", "_call_llm_with_followup")
        _n = 0
        for _x in ast.walk(_fn):
            if (isinstance(_x, ast.Call) and isinstance(_x.func, ast.Name)
                    and _x.func.id == "record_llm_call"):
                _n += 1
        self.assertEqual(1, _n,
                         "★多记一次就会虚增 llm_total（口径守恒）")

    # ---- KIND_GUARD ----
    def test_D8_guard_instrumented_in_low_branch(self):
        """★埋点必须落在 `_calc_confidence` 的「低置信度」分支内（每查询 ≤1 次）。"""
        _fn = _func_node(_RES, "ResonanceEngine", "_calc_confidence")
        self.assertIsNotNone(_fn, "★未定位到 _calc_confidence")
        _hit = False
        for _x in _fn.body:
            if not isinstance(_x, ast.If):
                continue
            _txt = ast.unparse(_x.test)
            if "level" in _txt and "low" in _txt:
                _body = ast.unparse(_x)
                if "record_local_inference" in _body and "KIND_GUARD" in _body:
                    _hit = True
                    self.assertIn("try", _body,
                                  "★埋点须异常隔离（不影响检索）")
        self.assertTrue(_hit, "★「低置信度」分支内未见 KIND_GUARD 埋点")

    def test_D9_guard_metric_bump(self):
        with self.m._lock:
            self.m.record_local_inference(M.KIND_GUARD)
            _b = dict(self.m._counters["local_inference_count"])
        self.assertEqual(1, _b.get(M.KIND_GUARD, 0), "★「置信度守卫」不再恒 0")
        self.assertEqual(0, _b.get(M.KIND_SIMPLE, 0), "★不得坍缩为简单回答")

    def test_D10_guard_marker_traced(self):
        _src = _read(_RES)
        self.assertIn("T-95d", _src)
        self.assertIn("_m95_kind_guard", _src)

    def test_D11_kind_rule_not_reinstrumented(self):
        """★★T-95d 前提证伪留档：KIND_RULE 第84批**已接线**，本批**不得**重复加。"""
        _res = _read(_RES)
        self.assertIn("_rec_local84(_KIND_RULE84)", _res,
                      "★第84批的既有接线必须仍在（不得被本批覆盖）")
        self.assertEqual(1, _res.count("_rec_local84(_KIND_RULE84)"),
                         "★若本批再加一处即为重复计数")


# ==================================================================== E：T-95e
class TestM95UsageAttrRename(unittest.TestCase):
    """E 组：usage 暂存属性统一命名（T-95e）。"""

    _LEGACY = ("_m44_last_usage", "_m40_last_usage")
    _NEW = "_last_llm_usage"

    def test_E1_no_legacy_write_targets_anywhere(self):
        """★生产源码不得再**把 usage 写入**旧名；仅允许 `= None` 式兼容清理。"""
        _bad = []
        for _p in _production_py():
            if os.path.relpath(_p, ROOT).split(os.sep)[0] == "tests":
                continue
            try:
                _tr = _tree(_p)
            except SyntaxError:
                continue
            for _n in ast.walk(_tr):
                if not isinstance(_n, ast.Assign):
                    continue
                _hit = [t.attr for t in _n.targets
                        if isinstance(t, ast.Attribute)
                        and isinstance(t.value, ast.Name)
                        and t.value.id == "self" and t.attr in self._LEGACY]
                if not _hit:
                    continue
                _is_none = (isinstance(_n.value, ast.Constant)
                            and _n.value.value is None)
                if not _is_none:
                    _bad.append((os.path.relpath(_p, ROOT), _hit[0]))
        self.assertEqual([], _bad,
                         "★生产源码不得再把 usage 写入旧名（`= None` 兼容清理除外）")

    def test_E2_new_name_present_in_all_sites(self):
        _src = _read(_SE)
        self.assertIn("self." + self._NEW + " = _data.get", _src)
        for _f in ("nucleus/evolution/LLMEvolutionEngine.py",
                   "nucleus/evolution/SelfReflectionEngine.py"):
            self.assertIn("self." + self._NEW + " =", _read(os.path.join(
                ROOT, _f.replace("/", os.sep))), "★%s 未同步" % _f)
        self.assertIn("self." + self._NEW + " = _m94_usage", _read(_LUNG))

    def test_E3_recorder_reads_new_with_legacy_fallback(self):
        _src = _read(_CR)
        self.assertIn('getattr(_self, "_last_llm_usage", None)', _src)
        self.assertIn('getattr(_self, "_m44_last_usage", None)', _src,
                      "★旧名回落读取必须保留")
        self.assertIn("_self._last_llm_usage = None", _src)

    def test_E4_lung_reads_new_with_legacy_fallback(self):
        _src = _read(_LUNG)
        self.assertIn('getattr(self, "_last_llm_usage", None)', _src)
        self.assertIn('getattr(self, "_m40_last_usage", None)', _src)
        self.assertIn("self._last_llm_usage = None", _src)

    def test_E5_tests_use_new_name_only(self):
        for _rel in ("tests/test_m94_tokens_usage.py",
                     "tests/test_lung_trace_m40.py"):
            _t = _read(os.path.join(ROOT, _rel.replace("/", os.sep)))
            for _w in self._LEGACY:
                self.assertNotIn(_w, _t, "★%s 残留旧名 %s" % (_rel, _w))

    def test_E6_adapter_doc_updated(self):
        _p = os.path.join(ROOT, "nucleus", "llm", "openai_compatible_adapter.py")
        self.assertIn("_last_llm_usage", _read(_p))

    def test_E7_red_then_green(self):
        _baks = _bak("nucleus/llm/call_recorder.py")
        if not os.path.isfile(_baks):
            self.skipTest("改前备份缺失")
        self.assertIn('getattr(_self, "_m44_last_usage", None)',
                      _read(_baks), "★先红：改前用旧名")

    def test_E8_decorator_loop_carries_usage(self):
        """★功能闭环：装饰器经新属性名把 usage 带出，并清空（防脏读）。"""
        _dir = tempfile.mkdtemp(prefix="m95_usage_")
        _o = (getattr(config, "ENABLE_LLM_CALL_RECORDER", True),
              getattr(config, "ENABLE_EVOLUTION_CALL_TRACE", True),
              cr._recorder)
        config.ENABLE_LLM_CALL_RECORDER = True
        config.ENABLE_EVOLUTION_CALL_TRACE = True
        cr._recorder = cr.LLMCallRecorder(base_dir=_dir)
        try:
            class _Eng:
                def __init__(self):
                    self._m44_last_model = "m95-model"
                    self._m44_last_error = ""
                    self._last_llm_usage = None

                @cr.trace_evolution_call(prompt_pos=2, version="test.m95.v1")
                def _call_llm(self, system, prompt):
                    self._last_llm_usage = {"total_tokens": 21}
                    return "ok"

            _e = _Eng()
            _e._call_llm("s", "p")
            _fp = cr._recorder._path_for(time.time(), prefix="calls")
            _recs = [json.loads(x) for x in io.open(_fp, encoding="utf-8")
                     if x.strip()]
            self.assertEqual(21, _recs[-1]["tokens"])
            self.assertEqual(21, _recs[-1]["usage"]["total_tokens"])
            self.assertIsNone(_e._last_llm_usage, "★finally 必须清空")
        finally:
            config.ENABLE_LLM_CALL_RECORDER, \
                config.ENABLE_EVOLUTION_CALL_TRACE, cr._recorder = _o
            shutil.rmtree(_dir, ignore_errors=True)

    def test_E9_legacy_writer_still_honoured(self):
        """★向后兼容：外部脚本仍写旧属性时，装饰器仍能取到 usage。"""
        _dir = tempfile.mkdtemp(prefix="m95_legacy_")
        _o = (getattr(config, "ENABLE_LLM_CALL_RECORDER", True),
              getattr(config, "ENABLE_EVOLUTION_CALL_TRACE", True),
              cr._recorder)
        config.ENABLE_LLM_CALL_RECORDER = True
        config.ENABLE_EVOLUTION_CALL_TRACE = True
        cr._recorder = cr.LLMCallRecorder(base_dir=_dir)
        try:
            class _Eng:
                def __init__(self):
                    self._m44_last_model = "legacy"
                    self._m44_last_error = ""

                @cr.trace_evolution_call(prompt_pos=2, version="test.m95.legacy.v1")
                def _call_llm(self, system, prompt):
                    self._m44_last_usage = {"total_tokens": 33}
                    return "ok"

            _e = _Eng()
            _e._call_llm("s", "p")
            _fp = cr._recorder._path_for(time.time(), prefix="calls")
            _recs = [json.loads(x) for x in io.open(_fp, encoding="utf-8")
                     if x.strip()]
            self.assertEqual(33, _recs[-1]["tokens"], "★旧名回落读取必须生效")
        finally:
            config.ENABLE_LLM_CALL_RECORDER, \
                config.ENABLE_EVOLUTION_CALL_TRACE, cr._recorder = _o
            shutil.rmtree(_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
