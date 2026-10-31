# -*- coding: utf-8 -*-
"""406 刀4 判据：低风险补丁「三条件前置闸」+ diff 窄闸 + 留痕。

六组断言：
  A diff 分类：只改注释 / 只改 docstring / 改逻辑 / 文档 / 测试 / 无法判定
  B 三条件：单测全绿 / 不跨器官 / 不碰审批池 —— 逐项正反例
  C 分级：low / medium / high 的边界，`eligible` 只在 low 为真
  D 打标签 + 留痕：落盘带 `m406_risk`、幂等、只写变化、留痕要素齐全
  E 通道挂接：`_m85` / `_m101` 两通道在窄闸下**只收窄不放宽**（开关关 ⇒ 完全回退）
  F 结构：config 三个键的取值、方法在位、窄闸无硬编码

★为什么 E 组必须「开闸/关闸」各跑一遍：
  本刀的验收是「medium/high 行为不变」——只证"开闸后拦得住"不够，
  还要证"关闸后与 406 前逐字相同"，否则分不清是修复还是新引入的行为漂移。
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.evolution import patch_risk_gate as G

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 非核心文件（`_m80_is_core_file` 实测 False）—— E 组用它，才分得清
#: 「被窄闸拦」与「被既有核心红线拦」。
NON_CORE = "organs/senses/PulseTouch.py"

ORIG_C = "def f(self):\n    # 旧注释\n    return 1\n"
NEW_C = "def f(self):\n    # 新注释\n    return 1\n"      # comment_only
NEW_L = "def f(self):\n    # 新注释\n    return 2\n"      # logic
ORIG_D = 'def f(self):\n    """旧 docstring"""\n    return 1\n'
NEW_D = 'def f(self):\n    """新 docstring"""\n    return 1\n'


def rec(**kw):
    """一条「本该放行」的干净记录；各用例只改它关心的那一项。"""
    p = {
        "id": "p1", "file": NON_CORE, "method": "_on_touch",
        "original_code": ORIG_C, "modified_code": NEW_C,
        "verification": {"passed": True}, "no_regression": True,
        "post_apply_errors": None, "verified": True,
        "status": "runtime_verified", "risk_level": "低",
        "source": "local_rule", "confidence": "high",
        # B 通道（_m101）要的真验证字段
        "runtime_verify_result": {"verified": True}, "baseline_errors": 2,
    }
    p.update(kw)
    return p


class TestA_DiffKind(unittest.TestCase):
    """任务书对 low 的定义：「只改注释 / 文档 / 测试 / 配置默认值 False」。"""

    def test_01_comment_only(self):
        self.assertEqual("comment_only", G.diff_kind(rec()))

    def test_02_docstring_only(self):
        self.assertEqual("comment_only",
                         G.diff_kind(rec(original_code=ORIG_D, modified_code=NEW_D)),
                         "改 docstring 也是只动注释面 —— 逐行剥 # 的做法会漏掉这一类")

    def test_03_logic(self):
        self.assertEqual("logic", G.diff_kind(rec(modified_code=NEW_L)))

    def test_04_test_file(self):
        self.assertEqual("test_only", G.diff_kind(rec(file="tests/test_x.py")))

    def test_05_doc_file(self):
        self.assertEqual("doc_only", G.diff_kind(rec(file="docs/路灯与星轨对话/a.md")))

    def test_06_missing_code(self):
        self.assertEqual("unknown", G.diff_kind(rec(original_code="", modified_code="")))

    def test_07_identical_is_logic_not_comment(self):
        self.assertEqual("logic", G.diff_kind(rec(modified_code=ORIG_C)),
                         "前后一模一样是「没改」，不能算「只改注释」而放行")

    def test_08_backslash_path(self):
        self.assertEqual("test_only", G.diff_kind(rec(file="tests\\test_x.py")))

    def test_09_non_dict(self):
        self.assertEqual("unknown", G.diff_kind(None))
        self.assertEqual("unknown", G.diff_kind("x"))


class TestB_ThreeConditions(unittest.TestCase):
    """三条件逐条正反例 —— 每条都要能单独把结论翻过来。"""

    def test_01_unit_green(self):
        self.assertTrue(G.check_unit_tests_green(rec())["ok"])

    def test_02_no_verification(self):
        self.assertFalse(G.check_unit_tests_green(rec(verification={}))["ok"])

    def test_03_no_regression_none(self):
        self.assertFalse(G.check_unit_tests_green(rec(no_regression=None))["ok"])

    def test_04_post_apply_errors(self):
        self.assertFalse(G.check_unit_tests_green(rec(post_apply_errors=3))["ok"])

    def test_05_unverified(self):
        self.assertFalse(G.check_unit_tests_green(rec(verified=False, status="pending"))["ok"])

    def test_06_status_can_stand_in(self):
        self.assertTrue(G.check_unit_tests_green(rec(verified=False, status="verified"))["ok"])

    def test_07_single_organ_ok(self):
        self.assertTrue(G.check_single_organ(rec())["ok"])

    def test_08_cross_organ_import(self):
        _r = rec(modified_code="from organs.body.PulseLung import PulseLung\n" + NEW_C)
        self.assertFalse(G.check_single_organ(_r)["ok"])
        self.assertEqual(["body"], G.check_single_organ(_r)["crossed"])

    def test_09_same_organ_import_ok(self):
        # 夹具在 organs/senses/ ⇒ 同器官 import 必须引 organs.senses
        _r = rec(modified_code="from organs.senses.X import y\n" + NEW_C)
        self.assertTrue(G.check_single_organ(_r)["ok"])

    def test_10_non_organ_file_ok(self):
        self.assertTrue(G.check_single_organ(rec(file="nucleus/evolution/x.py"))["ok"])

    def test_11_approval_pool(self):
        for _f in ("nucleus/reasoning/PatchManager.py", "config.py", "main.py",
                   "nucleus/evolution/patch_risk_gate.py"):
            self.assertFalse(G.check_not_approval_pool(rec(file=_f))["ok"], _f)

    def test_12_organ_file_not_pool(self):
        self.assertTrue(G.check_not_approval_pool(rec())["ok"])


class TestC_Level(unittest.TestCase):
    """分级边界：`eligible` 只在 low 为真，medium/high 一律 False。"""

    def test_01_low(self):
        _j = G.judge(rec())
        self.assertEqual("low", _j["level"])
        self.assertTrue(_j["eligible"])

    def test_02_logic_medium(self):
        _j = G.judge(rec(modified_code=NEW_L))
        self.assertEqual("medium", _j["level"])
        self.assertFalse(_j["eligible"])

    def test_03_approval_pool_high(self):
        self.assertEqual("high", G.judge(rec(file="nucleus/reasoning/PatchManager.py"))["level"],
                         "审批池文件即便只改注释也是 high —— 这条比 diff 更优先")

    def test_04_cross_organ_high(self):
        _r = rec(modified_code="from organs.body.PulseLung import X\n" + NEW_C)
        self.assertEqual("high", G.judge(_r)["level"])

    def test_05_narrow_but_tests_red_medium(self):
        self.assertEqual("medium", G.judge(rec(no_regression=None))["level"])

    def test_06_unknown_not_eligible(self):
        self.assertFalse(G.judge(rec(original_code="", modified_code=""))["eligible"])

    def test_07_why_is_readable(self):
        for _r in (rec(), rec(modified_code=NEW_L), rec(file="main.py")):
            self.assertTrue(G.judge(_r)["why"], "结论必须带一句可读理由（留痕要用）")

    def test_08_medium_high_never_eligible(self):
        for _r in (rec(modified_code=NEW_L), rec(file="main.py"),
                   rec(modified_code="from organs.body.X import Y\n" + NEW_C)):
            self.assertFalse(G.judge(_r)["eligible"])


class TestD_AnnotateAndAudit(unittest.TestCase):
    """落 pending 打标签 + 留痕：字段齐、幂等、只写变化、不污染隔离目录之外。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m406k4_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rows(self):
        return [rec(id="a"), rec(id="b", modified_code=NEW_L), rec(id="c", file="main.py")]

    def test_01_annotate_all(self):
        _rows = self._rows()
        self.assertEqual(3, G.annotate_rows(_rows)["changed"])
        self.assertTrue(all(isinstance(_r.get("m406_risk"), dict) for _r in _rows))

    def test_02_fields_complete(self):
        _rows = self._rows()
        G.annotate_rows(_rows)
        for _r in _rows:
            self.assertGreaterEqual(
                set(_r["m406_risk"]),
                {"schema", "level", "diff_kind", "eligible", "why", "checks", "at"})

    def test_03_levels(self):
        _rows = self._rows()
        G.annotate_rows(_rows)
        self.assertEqual(["low", "medium", "high"],
                         [_r["m406_risk"]["level"] for _r in _rows])

    def test_04_idempotent(self):
        _rows = self._rows()
        G.annotate_rows(_rows)
        self.assertEqual(0, G.annotate_rows(_rows)["changed"])

    def test_05_idempotent_keeps_at(self):
        _rows = self._rows()
        G.annotate_rows(_rows)
        _at = [_r["m406_risk"]["at"] for _r in _rows]
        G.annotate_rows(_rows)
        self.assertEqual(_at, [_r["m406_risk"]["at"] for _r in _rows],
                         "结论没变就不该刷新 at —— 否则 mtime 永远在动")

    def test_06_non_list(self):
        self.assertEqual(0, G.annotate_rows(None)["total"])

    def test_07_skip_non_dict(self):
        self.assertEqual(1, G.annotate_rows([None, rec(id="z")])["changed"])

    def test_08_audit_first_write(self):
        _rows = self._rows()
        G.annotate_rows(_rows)
        _a = G.record_audit(_rows, project_root=self.tmp, writer="t")
        self.assertEqual(3, _a["written"])
        self.assertTrue(os.path.isfile(_a["path"]))

    def test_09_audit_fields(self):
        _rows = self._rows()
        G.annotate_rows(_rows)
        _a = G.record_audit(_rows, project_root=self.tmp, writer="t")
        _ls = [json.loads(x) for x in open(_a["path"], encoding="utf-8") if x.strip()]
        for _x in _ls:
            self.assertGreaterEqual(
                set(_x), {"ts", "iso", "patch_id", "file", "level", "diff_kind",
                          "eligible", "why", "checks", "writer"})

    def test_10_audit_only_on_change(self):
        _rows = self._rows()
        G.annotate_rows(_rows)
        G.record_audit(_rows, project_root=self.tmp, writer="t")
        _b = G.record_audit(_rows, project_root=self.tmp, writer="t")
        self.assertEqual(0, _b["written"], "结论没变不该重复抄一遍留痕")

    def test_11_audit_appends_on_change(self):
        _rows = self._rows()
        G.annotate_rows(_rows)
        _a = G.record_audit(_rows, project_root=self.tmp, writer="t")
        _rows[0]["m406_risk"]["level"] = "medium"
        _c = G.record_audit(_rows, project_root=self.tmp, writer="t")
        self.assertEqual(1, _c["written"])
        self.assertEqual(4, len([x for x in open(_a["path"], encoding="utf-8") if x.strip()]),
                         "追加而非覆盖")

    def test_12_audit_respects_isolation_root(self):
        _rows = self._rows()
        G.annotate_rows(_rows)
        _a = G.record_audit(_rows, project_root=self.tmp, writer="t")
        self.assertTrue(_a["path"].startswith(self.tmp),
                        "留痕必须落在传入的隔离根内，不得写进仓库 data/")

    def test_13_audit_bad_input(self):
        self.assertEqual(0, G.record_audit(None, project_root=self.tmp)["written"])


class TestE_ChannelWiring(unittest.TestCase):
    """两条免签通道的窄闸：只收窄、不放宽；开关关 ⇒ 完全回退 406 前。"""

    CFG = {"auto_apply_enabled": True, "local_auto_apply_enabled": True,
           "allow_core_auto_apply": False, "auto_apply_min_trust": 40,
           "auto_apply_max_risk": 2}

    @classmethod
    def setUpClass(cls):
        from nucleus.reasoning.PatchManager import PatchManager
        cls.PM = PatchManager

    def test_01_non_core_choice_is_valid(self):
        """E 组全部依赖这句话：NON_CORE 不是核心文件。变了就要换测试夹具。"""
        self.assertFalse(self.PM._m80_is_core_file(NON_CORE))

    def test_02_gate_off_b_channel_allows_logic(self):
        with patch("config.PATCH_M406_NARROW_GATE", False):
            self.assertTrue(self.PM._m101_low_risk_release_path(rec(modified_code=NEW_L), self.CFG),
                            "关闸后必须与 406 前逐字相同（zero-regression 基线）")

    def test_03_gate_off_a_channel_allows_logic(self):
        with patch("config.PATCH_M406_NARROW_GATE", False):
            self.assertTrue(self.PM._m85_local_low_risk_auto_apply(rec(modified_code=NEW_L), self.CFG))

    def test_04_b_channel_allows_comment(self):
        self.assertTrue(self.PM._m101_low_risk_release_path(rec(), self.CFG))

    def test_05_b_channel_blocks_logic(self):
        self.assertFalse(self.PM._m101_low_risk_release_path(rec(modified_code=NEW_L), self.CFG))

    def test_06_b_channel_allows_doc(self):
        self.assertTrue(self.PM._m101_low_risk_release_path(rec(file="docs/x.md"), self.CFG))

    def test_07_b_channel_allows_test(self):
        self.assertTrue(self.PM._m101_low_risk_release_path(rec(file="tests/test_x.py"), self.CFG))

    def test_08_a_channel_allows_comment(self):
        self.assertTrue(self.PM._m85_local_low_risk_auto_apply(rec(), self.CFG))

    def test_09_a_channel_blocks_logic(self):
        self.assertFalse(self.PM._m85_local_low_risk_auto_apply(rec(modified_code=NEW_L), self.CFG))

    def test_10_pool_still_blocked(self):
        """碰审批池：既有红线仍拦（窄闸不放宽任何东西）。"""
        self.assertFalse(self.PM._m101_low_risk_release_path(
            rec(file="nucleus/reasoning/PatchManager.py"), self.CFG))

    def test_11_llm_still_blocked(self):
        """372刀2 的口径未动：llm 来源一律不走 B 通道。"""
        self.assertFalse(self.PM._m101_low_risk_release_path(rec(source="llm"), self.CFG))

    def test_12_master_switch_still_blocked(self):
        self.assertFalse(self.PM._m101_low_risk_release_path(
            rec(), dict(self.CFG, auto_apply_enabled=False)),
            "关总闸时窄闸放行也不算数 —— 窄闸只收窄，不替代总闸")

    def test_13_gate_error_is_conservative(self):
        """闸自己炸了 ⇒ 一律不许放行（与 _m101 的 except 保守口径一致）。"""

        class _Boom(dict):
            def get(self, *a, **k):
                raise RuntimeError("boom")

        _ok, _why = self.PM._m406_narrow_gate(_Boom())
        self.assertFalse(_ok, "判据异常时必须保守拦下")
        self.assertEqual("judge_failed", _why)


class TestF_Structure(unittest.TestCase):
    """结构判据：开关取值、方法在位、没有把判定写死。"""

    def test_01_config_keys(self):
        import config as C
        self.assertIs(True, C.EVOLUTION_CONFIG["auto_apply_enabled"],
                      "406刀4 裁决：总闸打开")
        self.assertIs(True, C.PATCH_M406_NARROW_GATE, "窄闸默认启用")
        self.assertIs(False, C.EVOLUTION_CONFIG["local_auto_apply_enabled"],
                      "A 通道未放开（本批不碰）")
        self.assertIs(False, C.EVOLUTION_CONFIG["allow_core_auto_apply"],
                      "核心文件红线未动")

    def test_02_gate_method_exists(self):
        from nucleus.reasoning.PatchManager import PatchManager
        self.assertTrue(callable(getattr(PatchManager, "_m406_narrow_gate", None)))

    def test_03_gate_called_in_both_channels(self):
        import ast
        import io
        _src = io.open(os.path.join(_ROOT, "nucleus", "reasoning", "PatchManager.py"),
                       encoding="utf-8", errors="replace").read()
        _tree = ast.parse(_src)
        for _name in ("_m101_low_risk_release_path", "_m85_local_low_risk_auto_apply"):
            _fn = None
            for _n in ast.walk(_tree):
                if isinstance(_n, ast.FunctionDef) and _n.name == _name:
                    _fn = _n
                    break
            self.assertIsNotNone(_fn, _name)
            _calls = [_x for _x in ast.walk(_fn)
                      if isinstance(_x, ast.Call) and isinstance(_x.func, ast.Attribute)
                      and _x.func.attr == "_m406_narrow_gate"]
            self.assertTrue(_calls, "%s 未挂窄闸" % _name)

    def test_04_narrow_kinds_frozen(self):
        self.assertEqual(("comment_only", "doc_only", "test_only"), G.DIFF_NARROW_KINDS)

    def test_05_pending_write_wired(self):
        import io
        _src = io.open(os.path.join(_ROOT, "nucleus", "evolution", "pending_write.py"),
                       encoding="utf-8", errors="replace").read()
        self.assertIn("_m406_annotate", _src, "唯一写口未挂风险标签")
        self.assertIn("annotate_rows", _src)
        self.assertIn("record_audit", _src)

    def test_06_no_hardcoded_levels_in_judge(self):
        """判定必须走常量集合，不能散落字面量（改一处漏三处）。"""
        import ast
        import io
        _src = io.open(os.path.join(_ROOT, "nucleus", "evolution", "patch_risk_gate.py"),
                       encoding="utf-8", errors="replace").read()
        _fn = None
        for _n in ast.walk(ast.parse(_src)):
            if isinstance(_n, ast.FunctionDef) and _n.name == "judge":
                _fn = _n
                break
        self.assertIsNotNone(_fn)
        _seg = ast.get_source_segment(_src, _fn) or ""
        self.assertIn("DIFF_NARROW_KINDS", _seg, "judge 必须引用常量集合")


if __name__ == "__main__":
    unittest.main()
