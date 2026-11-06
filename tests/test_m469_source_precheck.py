# -*- coding: utf-8 -*-
"""★469 批：补丁应用前源码核对 + 构造侧粒度对齐 + 中风险门槛旋钮。

病因（2026-11-06 T0 实测，全部来自真实 data/patches/pending_patches.json）：

  P0-A 补丁瞄准的方法名在文件里不存在 —— 12 条 LLM 补丁里 **5 条**：
       `periodic_task_no_reentry`（把 issue_type 当方法名）、`_process_l2_nodes`
       （文件里叫 `_compress_l1_to_l2`）、`collect_value_data`（方法还没被创建）等。
       这类补丁无论缩进对不对都应用不了。

  P0-B 粒度不对照 —— 12 条里 **11 条** 的 `original_code` 是 8 空格缩进的
       **方法体片段**（`get_method_body()["body"]`），`modified_code` 是 0 缩进的
       **整个方法（带 def）**。原位替换等于在别的方法体里插入嵌套 def，
       缩进关必然以 `基础缩进不一致（未替换）: original=8 modified=0` 拒绝。
       实证：patch_llm_1793945467_7587（11-06 14:11 唯一被放行的那条）就是这样死的。

  P0-C 应用前没有「源码是否还对得上」的核对 —— 只有事后 `apply_error` 文案匹配
       （`_OBSOLETE_MARKERS`），既分不清「补丁烂」与「源码漂移」，又会白写备份、
       白跑一次副本验证。

  P1-D 中风险补丁两道门都过不去：trust 恒 40 < 门槛 60，即便提上去还会撞
       `auto_apply_max_risk=2` ⇒ 结构性永远不通过（5/5 实测）。

本批不放松任何安全门槛：
  · 刀3 只把「低 / 其余」两档拆成「低 / 中 / 高」三档，`mid_risk_min_trust`
    缺省刻意等于 `high_risk_min_trust` ⇒ **零行为变化**，只是给出独立旋钮。
  · 被安全门槛拒绝的补丁改为留 `safety_blocked` 结构化标记 + 汇总计数，不静默。

测试纪律（铁律 58）：每用例独立 basetemp，不与其他文件合跑。
"""
import ast
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus.evolution.EvolutionEffectVerifier import EvolutionEffectVerifier
from nucleus.reasoning.PatchManager import (
    _m469_file_sha256,
    _m469_source_precheck,
)
from nucleus.reasoning.SafeEvolutionExecutor import (
    _m469_align_method_patch,
    _m469_find_method_span,
    _m469_method_exists,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PM_PATH = os.path.join(_ROOT, "nucleus", "reasoning", "PatchManager.py")
_EX_PATH = os.path.join(_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
_CFG_PATH = os.path.join(_ROOT, "config.py")


def _src(path):
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _method_src(path, name):
    """按名取方法源码段（含 def / 含注释）。"""
    _s = _src(path)
    _tree = ast.parse(_s)
    for _n in ast.walk(_tree):
        if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _n.name == name:
            return ast.get_source_segment(_s, _n) or ""
    raise AssertionError(f"{path} 中找不到方法 {name}")


def _write(path, text):
    Path(path).write_text(text, encoding="utf-8", newline="")


class TestA_SourcePrecheck(unittest.TestCase):
    """A 组：`_m469_source_precheck` 六类判据（含行尾归一与指纹漂移）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m469a_")
        self.f = os.path.join(self.tmp, "sample.py")
        _write(self.f, "class K:\n    def alpha(self):\n        return 1\n\n"
                       "    def beta(self):\n        return 2\n")

    def test_A1_ok_when_found_once(self):
        _r = _m469_source_precheck(
            {"file": self.f, "original_code": "    def alpha(self):\n        return 1"}, _ROOT)
        self.assertEqual(_r["kind"], "ok")
        self.assertTrue(_r["ok"])
        self.assertEqual(_r["occurrences"], 1)
        self.assertEqual(_r["detail"], "")

    def test_A2_stale_when_absent(self):
        _r = _m469_source_precheck(
            {"file": self.f, "original_code": "    def gamma(self):\n        return 3"}, _ROOT)
        self.assertEqual(_r["kind"], "stale")
        self.assertFalse(_r["ok"])
        self.assertEqual(_r["occurrences"], 0)
        self.assertIn("源码已漂移", _r["detail"])

    def test_A3_ambiguous_when_multiple(self):
        _r = _m469_source_precheck(
            {"file": self.f, "original_code": "return"}, _ROOT)
        self.assertEqual(_r["kind"], "ambiguous")
        self.assertFalse(_r["ok"])
        self.assertEqual(_r["occurrences"], 2)

    def test_A4_no_code_when_field_missing_or_blank(self):
        for _p in ({"file": self.f},
                   {"file": self.f, "original_code": ""},
                   {"file": self.f, "original_code": "   \n  "}):
            _r = _m469_source_precheck(_p, _ROOT)
            self.assertEqual(_r["kind"], "no_code", _p)
            self.assertFalse(_r["ok"])

    def test_A5_file_missing(self):
        _r = _m469_source_precheck(
            {"file": os.path.join(self.tmp, "nope.py"), "original_code": "x"}, _ROOT)
        self.assertEqual(_r["kind"], "file_missing")
        self.assertFalse(_r["ok"])
        _r2 = _m469_source_precheck({"original_code": "x"}, _ROOT)
        self.assertEqual(_r2["kind"], "file_missing", "缺 file 字段也必须是 file_missing")

    def test_A6_guard_off_returns_ok(self):
        _old = getattr(config, "ENABLE_M469_SOURCE_PRECHECK", None)
        config.ENABLE_M469_SOURCE_PRECHECK = False
        try:
            _r = _m469_source_precheck(
                {"file": self.f, "original_code": "绝对不存在的片段"}, _ROOT)
            self.assertEqual(_r["kind"], "guard_off")
            self.assertTrue(_r["ok"], "关闸时调用方要能按老逻辑继续 ⇒ ok 必须为 True")
        finally:
            config.ENABLE_M469_SOURCE_PRECHECK = True if _old is None else _old

    def test_A7_crlf_lf_normalized(self):
        """磁盘是 CRLF、补丁里存 LF ⇒ 仍应判 ok（否则每次都会假报 stale）。"""
        _crlf = os.path.join(self.tmp, "crlf.py")
        Path(_crlf).write_text(
            "class K:\r\n    def alpha(self):\r\n        return 1\r\n",
            encoding="utf-8", newline="")
        _r = _m469_source_precheck(
            {"file": _crlf, "original_code": "    def alpha(self):\n        return 1"}, _ROOT)
        self.assertEqual(_r["kind"], "ok", "行尾归一后必须命中")

    def test_A8_snapshot_drift_does_not_reject(self):
        """指纹不同但片段仍在 ⇒ 仍 ok，只置 snapshot_drift（别人改的是别的方法）。"""
        _r = _m469_source_precheck(
            {"file": self.f,
             "original_code": "    def alpha(self):\n        return 1",
             "source_file_sha256": "0" * 64}, _ROOT)
        self.assertEqual(_r["kind"], "ok")
        self.assertTrue(_r["snapshot_drift"])
        self.assertEqual(_r["source_file_sha256_now"], _m469_file_sha256(self.f))

    def test_A9_sha256_stable_and_empty_on_missing(self):
        _a = _m469_file_sha256(self.f)
        self.assertEqual(len(_a), 64)
        self.assertEqual(_a, _m469_file_sha256(self.f), "同内容必须同指纹")
        self.assertEqual(_m469_file_sha256(os.path.join(self.tmp, "nope.py")), "",
                         "读不到给空串，不编造")


class TestB_Wiring(unittest.TestCase):
    """B 组：接线门禁（AST 定位真实调用点与顺序）。"""

    def _fn(self, path, name):
        _tree = ast.parse(_src(path))
        for _n in ast.walk(_tree):
            if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _n.name == name:
                return _n
        raise AssertionError(f"{os.path.basename(path)} 中找不到 {name}")

    def test_B1_precheck_runs_before_verify_and_backup(self):
        _n = self._fn(_PM_PATH, "apply_all_pending")
        _calls = {}
        for _node in ast.walk(_n):
            if isinstance(_node, ast.Call):
                _f = _node.func
                _nm = getattr(_f, "id", None) or getattr(_f, "attr", None) or ""
                if _nm in ("_m469_source_precheck", "_verify_in_copy",
                           "create_backup", "full_content"):
                    _calls.setdefault(_nm, _node.lineno)
        self.assertIn("_m469_source_precheck", _calls, "apply_all_pending 里没有源码核对")
        self.assertIn("_verify_in_copy", _calls)
        self.assertLess(_calls["_m469_source_precheck"], _calls["_verify_in_copy"],
                        "核对必须早于副本验证（否则白跑一次 copy+import）")

    def test_B2_results_has_stale_and_safety_blocked(self):
        _s = _method_src(_PM_PATH, "apply_all_pending")
        self.assertIn('"stale": 0', _s, "results 初始化缺 stale 计数器")
        self.assertIn('"safety_blocked": 0', _s, "results 初始化缺 safety_blocked 计数器")
        self.assertIn('results["stale"] += 1', _s)
        self.assertIn('results["safety_blocked"] += 1', _s)

    def test_B3_verify_in_copy_has_precheck(self):
        _s = _method_src(_PM_PATH, "_verify_in_copy")
        self.assertIn("_m469_source_precheck", _s)
        self.assertIn("source_precheck_failed", _s)

    def test_B4_obsolete_honors_structured_field(self):
        _s = _method_src(_PM_PATH, "_is_obsolete_patch")
        self.assertIn('patch.get("stale_source") is True', _s,
                      "过期判定不能只靠错误文案（文案改字就失效）")

    def test_B5_granularity_align_wired_at_construction(self):
        _s = _src(_EX_PATH)
        self.assertIn("_m469_align_method_patch(_llm_patch)", _s,
                      "构造点未接线粒度对齐")
        self.assertIn("m469_granularity_align_failed", _s)
        self.assertIn("[469刀2] 粒度对齐", _s)

    def test_B6_summary_log_counts_four_ways(self):
        _s = _method_src(_PM_PATH, "apply_all_pending")
        self.assertIn("[469汇总]", _s, "缺一行汇总，用户看不出卡在哪一类")


class TestC_GranularityAlign(unittest.TestCase):
    """C 组：粒度对齐（刀2 的核心判据）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m469c_")
        self.f = os.path.join(self.tmp, "mod.py")
        _write(self.f, textwrap.dedent('''\
            class Host:
                def __init__(self):
                    self.q = []

                def target(self, x):
                    if x > 0:
                        return x
                    return 0

                def tail(self):
                    return 1
            '''))

    def _patch(self, **kw):
        _p = {"file": self.f, "method": "target",
              "original_code": "        if x > 0:\n            return x\n"
                               "        return 0",
              "modified_code": "def target(self, x):\n    if x > 1:\n        return x\n"
                               "    return 0"}
        _p.update(kw)
        return _p

    def test_C1_aligns_to_whole_method(self):
        _p = self._patch()
        _r = _m469_align_method_patch(_p)
        self.assertTrue(_r["ok"], _r)
        self.assertEqual(_r["how"], "whole_method_aligned")
        self.assertEqual(_r["indent"], 4)
        self.assertTrue(_p["original_code"].startswith("    def target(self, x):"))
        self.assertIn("return 0", _p["original_code"])
        self.assertNotIn("def tail", _p["original_code"], "不能把下一个方法吞进来")
        self.assertTrue(_p["m469_granularity_aligned"])

    def test_C2_base_indent_equal_after_align(self):
        _p = self._patch()
        _m469_align_method_patch(_p)

        def _bi(s):
            for _ln in s.split("\n"):
                if _ln.strip():
                    return len(_ln) - len(_ln.lstrip())
            return -1

        self.assertEqual(_bi(_p["original_code"]), _bi(_p["modified_code"]),
                         "对齐后 base indent 必须相等（这正是缩进关的判据）")

    def test_C3_original_unique_and_replace_parseable(self):
        _p = self._patch()
        _m469_align_method_patch(_p)
        _disk = Path(self.f).read_text(encoding="utf-8")
        self.assertEqual(_disk.count(_p["original_code"]), 1,
                         "整段方法必须在文件中唯一出现")
        ast.parse(_disk.replace(_p["original_code"], _p["modified_code"], 1))

    def test_C4_non_whole_method_untouched(self):
        _p = {"file": self.f, "method": "target",
              "original_code": "        return 0",
              "modified_code": "        return 9"}
        _r = _m469_align_method_patch(_p)
        self.assertTrue(_r["ok"])
        self.assertEqual(_r["how"], "as_is")
        self.assertEqual(_p["original_code"], "        return 0", "as_is 不得改字段")
        self.assertEqual(_p["modified_code"], "        return 9")

    def test_C5_duplicate_method_name_refused(self):
        _f2 = os.path.join(self.tmp, "dup.py")
        _write(_f2, "class A:\n    def dup(self):\n        return 1\n\n"
                    "class B:\n    def dup(self):\n        return 2\n")
        _p = {"file": _f2, "method": "dup", "original_code": "x",
              "modified_code": "def dup(self):\n    return 9"}
        _r = _m469_align_method_patch(_p)
        self.assertFalse(_r["ok"])
        self.assertEqual(_r["how"], "span_not_found")
        self.assertIn("唯一", _r["detail"])

    def test_C6_absent_method_refused_on_as_is_path(self):
        """方法名根本不存在（as_is 分支也必须拦住）。"""
        _p = {"file": self.f, "method": "ghost_method",
              "original_code": "        return 0",
              "modified_code": "        return 9"}
        _r = _m469_align_method_patch(_p)
        self.assertFalse(_r["ok"])
        self.assertEqual(_r["how"], "method_not_found")
        self.assertIn("ghost_method", _r["detail"])

    def test_C7_missing_target_fields(self):
        _r = _m469_align_method_patch({"file": self.f, "original_code": "x",
                                       "modified_code": "def t(self):\n    return 1"})
        self.assertFalse(_r["ok"])
        self.assertEqual(_r["how"], "no_target")
        _r2 = _m469_align_method_patch("not-a-dict")
        self.assertFalse(_r2["ok"])
        self.assertEqual(_r2["how"], "bad_patch")

    def test_C8_source_hash_recorded(self):
        _p = self._patch()
        _p["generated_at"] = 123.0
        _m469_align_method_patch(_p)
        self.assertEqual(_p.get("source_file_sha256"), _m469_file_sha256(self.f))
        self.assertEqual(_p.get("source_snapshot_at"), 123.0)

    def test_C9_guard_off_leaves_patch_alone(self):
        _old = getattr(config, "ENABLE_M469_GRANULARITY_ALIGN", None)
        config.ENABLE_M469_GRANULARITY_ALIGN = False
        try:
            _p = self._patch()
            _before = (_p["original_code"], _p["modified_code"])
            _r = _m469_align_method_patch(_p)
            self.assertEqual(_r["how"], "guard_off")
            self.assertEqual((_p["original_code"], _p["modified_code"]), _before)
        finally:
            config.ENABLE_M469_GRANULARITY_ALIGN = True if _old is None else _old

    def test_C10_method_exists_three_states(self):
        self.assertEqual(_m469_method_exists({"file": self.f, "method": "target"}),
                         "present")
        self.assertEqual(_m469_method_exists({"file": self.f, "method": "ghost"}),
                         "absent")
        self.assertEqual(_m469_method_exists({"file": self.f}), "unknown")
        self.assertEqual(_m469_method_exists({"file": os.path.join(self.tmp, "no.py"),
                                              "method": "x"}), "unknown")

    def test_C11_find_method_span_precise(self):
        _lines = _src(self.f).split("\n")
        _span = _m469_find_method_span(_lines, "target")
        self.assertIsNotNone(_span)
        _s, _e, _ind = _span
        self.assertEqual(_ind, 4)
        self.assertTrue(_lines[_s].strip().startswith("def target("))
        self.assertEqual(_lines[_s - 1].strip(), "", "区间起点必须是 def 行")
        self.assertNotIn("def tail", "\n".join(_lines[_s:_e]))


class TestD_TrustThresholdTiers(unittest.TestCase):
    """D 组：信任门槛三级化 —— 默认零行为变化，旋钮单点可调。"""

    def _p(self, risk, orig="    a = 1", mod="    a = 2"):
        return {"risk_level": risk, "original_code": orig, "modified_code": mod}

    def test_D1_high_risk_uses_high(self):
        self.assertEqual(EvolutionEffectVerifier.trust_threshold_for(self._p("高")), 60.0)

    def test_D2_mid_risk_default_equals_high(self):
        """★零行为变化的核心断言：中风险门槛默认必须等于高风险门槛。"""
        self.assertEqual(
            EvolutionEffectVerifier.trust_threshold_for(self._p("中")),
            EvolutionEffectVerifier.trust_threshold_for(self._p("高")))

    def test_D3_low_risk_uses_low(self):
        _p = self._p("低", "    # 注释\n    a = 1", "    # 注释改\n    a = 1")
        self.assertEqual(EvolutionEffectVerifier.trust_threshold_for(_p), 40.0)

    def test_D4_mid_knob_is_independent(self):
        """只改 mid 旋钮 ⇒ 中风险变 40，高风险仍是 60（互不连累）。"""
        _cfg = config.EVOLUTION_EFFECT_VERIFY_CONFIG
        _old = _cfg.get("mid_risk_min_trust", None)
        _cfg["mid_risk_min_trust"] = 40
        try:
            self.assertEqual(
                EvolutionEffectVerifier.trust_threshold_for(self._p("中")), 40.0)
            self.assertEqual(
                EvolutionEffectVerifier.trust_threshold_for(self._p("高")), 60.0,
                "调中风险不得连累高风险")
        finally:
            if _old is None:
                _cfg.pop("mid_risk_min_trust", None)
            else:
                _cfg["mid_risk_min_trust"] = _old

    def test_D5_config_declares_mid_risk(self):
        self.assertIn("mid_risk_min_trust", config.EVOLUTION_EFFECT_VERIFY_CONFIG)
        self.assertEqual(config.EVOLUTION_EFFECT_VERIFY_CONFIG["mid_risk_min_trust"], 60)


class TestE_ConfigRegistration(unittest.TestCase):
    """E 组：config 登记（开关真在文件里、真能读出来）。"""

    def test_E1_two_switches_registered(self):
        _s = _src(_CFG_PATH)
        self.assertIn("ENABLE_M469_SOURCE_PRECHECK = True", _s)
        self.assertIn("ENABLE_M469_GRANULARITY_ALIGN = True", _s)
        self.assertTrue(config.ENABLE_M469_SOURCE_PRECHECK)
        self.assertTrue(config.ENABLE_M469_GRANULARITY_ALIGN)

    def test_E2_marker_in_latter_half(self):
        """★不钉死精确字符数（铁律 9）：只要求本批块在文件后半段。"""
        _s = _src(_CFG_PATH)
        _i = _s.find("[M469-CFG]")
        self.assertGreater(_i, 0, "config 里找不到 [M469-CFG] 块")
        self.assertGreater(_i, len(_s) // 2, "本批配置块应在文件后半段（只追加）")

    def test_E3_existing_switches_not_rewritten(self):
        _s = _src(_CFG_PATH)
        for _k in ("ENABLE_M447_APPROVAL_BRIDGE", "M447_APPROVE_DAILY_CAP",
                   "ENABLE_M468_REVERIFY_APPLIED_GUARD", "ENABLE_M468_APPROVAL_REFUND",
                   "ENABLE_M468_ORGAN_FILE_FALLBACK"):
            self.assertIn(_k, _s, f"既有关键开关不得被本批改写: {_k}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
