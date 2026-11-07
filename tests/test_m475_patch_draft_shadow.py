# -*- coding: utf-8 -*-
"""475 刀1：曈曈主笔草稿影子旁路的门控单测。

判据纪律：
  · 基准来自**独立入口** `_code_metrics()`（ast.parse + 文本），不拿算子
    verify 结论当基准（铁律 30）。
  · 反证用例：换成空 kernel 后 covered/revision_covered 必须变 False，
    否则判据在旧实现上也成立（铁律 35）。
  · 影子契约：绝不修改传入 patch 的任何字段。
"""
from __future__ import annotations

import ast
import copy
import json
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config
from nucleus import autonomy  # noqa: F401
from nucleus.autonomy import patch_draft as PD
from nucleus.autonomy.kernel import CapabilityKernel

_PM_SRC = os.path.join(_ROOT, "nucleus", "reasoning", "PatchManager.py")
_MOD_DIR = os.path.join(_ROOT, "nucleus", "autonomy")

# 与真实补丁同构：4 格 def、8 格方法体（类方法片段）
_GOOD = ("    def _save(self, x):\n"
         '        """保存。"""\n'
         "        self.data = x\n"
         "        return True\n")
_OVER = ("    def _save(self, x):\n"
         "            self.data = x\n"
         "            return True\n")
_BARE = ("    def _save(self, x):\n"
         "        try:\n"
         "            self.data = x\n"
         "        except OSError:\n"
         "            pass\n"
         "        return True\n")


def _patch(orig=_GOOD, llm=None, **kw):
    _p = {"id": "patch_test_001", "file": "organs/core/X.py",
          "method": "_save", "issue_type": "non_atomic_write",
          "original_code": orig, "modified_code": llm if llm is not None else orig}
    _p.update(kw)
    return _p


class _Tmp:
    def __init__(self):
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="m475_")
        self.path = os.path.join(self.dir, "shadow.jsonl")

    def rows(self):
        if not os.path.isfile(self.path):
            return []
        with open(self.path, encoding="utf-8") as _f:
            return [json.loads(_x) for _x in _f if _x.strip()]

    def close(self):
        from nucleus._silent_except import silent_exc
        for _n in os.listdir(self.dir):
            try:
                os.remove(os.path.join(self.dir, _n))
            except OSError as _e:
                silent_exc(_e, where="test_m475::_Tmp.close", level="debug")
        try:
            os.rmdir(self.dir)
        except OSError as _e:
            silent_exc(_e, where="test_m475::_Tmp.close rmdir", level="debug")


class TestIndependentMetrics(unittest.TestCase):
    def test_01_metrics_on_good_code(self):
        _m = PD._code_metrics(_GOOD)
        self.assertTrue(_m["parse_ok"])
        self.assertEqual(4, _m["def_indent"])
        self.assertEqual(8, _m["body_indent"])
        self.assertTrue(_m["has_docstring"])
        self.assertEqual(0, _m["bare_except_pass"])

    def test_02_metrics_detect_over_indent_and_bare_except(self):
        self.assertEqual(12, PD._code_metrics(_OVER)["body_indent"])
        self.assertEqual(1, PD._code_metrics(_BARE)["bare_except_pass"])

    def test_03_bad_syntax_is_reported_not_raised(self):
        _m = PD._code_metrics("    def f(:\n        pass")
        self.assertFalse(_m["parse_ok"])
        self.assertIn("SyntaxError", _m["parse_error"])


class TestSelfDraft(unittest.TestCase):
    def test_04_main_draft_covers_original_defect(self):
        """原文自带「方法体多缩」⇒ 曈曈主笔能自己修（covered=True）。"""
        _r = PD.draft_from_patch(_patch(orig=_OVER, llm=_OVER))
        self.assertEqual("ok", _r["status"])
        self.assertTrue(_r["covered"])
        self.assertIn("code_repair.indent_level_align", _r["operators"])
        self.assertEqual(8, _r["quality"]["self_body_indent"])
        self.assertTrue(_r["quality"]["self_parse_ok"])

    def test_05_counterproof_empty_kernel_disables_coverage(self):
        """★反证：空算子内核下同一输入必须 covered=False。"""
        _empty = CapabilityKernel(enforce=True, ledger_enabled=False)
        _r = PD.draft_from_patch(_patch(orig=_OVER, llm=_OVER), kernel=_empty)
        self.assertFalse(_r["covered"])
        self.assertEqual([], _r["operators"])

    def test_06_business_logic_patch_is_honestly_uncovered(self):
        """真实样本形态：原文没问题、LLM 改了业务 ⇒ 主笔诚实登记需外部。"""
        _llm = _GOOD.replace("self.data = x", "with _atomic():\n            self.data = x")
        _r = PD.draft_from_patch(_patch(orig=_GOOD, llm=_llm))
        self.assertFalse(_r["covered"])
        self.assertTrue(_r["external_needed"])
        self.assertEqual("uncovered_needs_external", _r["verdict"])

    def test_07_shadow_never_mutates_patch(self):
        _p = _patch(orig=_OVER, llm=_BARE)
        _before = copy.deepcopy(_p)
        PD.draft_from_patch(_p)
        self.assertEqual(_before, _p)


class TestRevision(unittest.TestCase):
    def test_08_llm_over_indent_is_repaired(self):
        """475 刀1 真实样本：3/4 的 LLM 产物方法体多缩 4 格。"""
        _r = PD.draft_from_patch(_patch(orig=_GOOD, llm=_OVER))
        self.assertTrue(_r["revision_covered"])
        self.assertIn("code_repair.indent_level_align", _r["llm_defects"])
        self.assertEqual(12, _r["quality"]["llm_body_indent"])
        self.assertEqual(8, _r["quality"]["revision_body_indent"])
        self.assertTrue(_r["quality"]["revision_parse_ok"])

    def test_09_llm_bare_except_is_backfilled(self):
        """475 刀1 真实样本：715f 引入裸 except: pass。"""
        _r = PD.draft_from_patch(_patch(orig=_GOOD, llm=_BARE))
        self.assertTrue(_r["revision_covered"])
        self.assertIn("code_repair.silent_exc_backfill", _r["llm_defects"])
        self.assertEqual(1, _r["quality"]["llm_bare_except_pass"])
        self.assertEqual(0, _r["quality"]["revision_bare_except_pass"])

    def test_10_counterproof_clean_llm_needs_no_revision(self):
        """★反证：LLM 产物合格 ⇒ revision_covered 必须 False。"""
        _r = PD.draft_from_patch(_patch(orig=_GOOD, llm=_GOOD))
        self.assertFalse(_r["revision_covered"])
        self.assertEqual([], _r["llm_defects"])


class TestShadowLedger(unittest.TestCase):
    def setUp(self):
        self.t = _Tmp()

    def tearDown(self):
        self.t.close()

    def test_11_writes_structured_ab_record(self):
        _r = PD.note_patch_draft_shadow(_patch(orig=_GOOD, llm=_OVER),
                                        ledger_path=self.t.path)
        self.assertEqual("ok", _r["status"])
        _rows = self.t.rows()
        self.assertEqual(1, len(_rows))
        _x = _rows[0]
        for _k in ("schema", "patch_id", "issue_type", "covered",
                   "revision_covered", "llm_defects", "self", "llm",
                   "revision", "quality", "at"):
            self.assertIn(_k, _x)

    def test_12_switch_off_disables_everything(self):
        with mock.patch.object(config, "ENABLE_M475_PATCH_DRAFT_SHADOW", False, create=True):
            _r = PD.note_patch_draft_shadow(_patch(), ledger_path=self.t.path)
        self.assertEqual("disabled", _r["status"])
        self.assertEqual([], self.t.rows())

    def test_13_zero_is_explicit_off(self):
        with mock.patch.object(config, "ENABLE_M475_PATCH_DRAFT_SHADOW", 0, create=True):
            self.assertEqual("disabled",
                             PD.note_patch_draft_shadow(_patch(), ledger_path=self.t.path)["status"])

    def test_14_default_path_not_written_under_pytest(self):
        _prod = os.path.join(_ROOT, "data", "autonomy", "patch_draft_shadow.jsonl")
        _before = os.path.getsize(_prod) if os.path.exists(_prod) else -1
        PD.note_patch_draft_shadow(_patch())
        _after = os.path.getsize(_prod) if os.path.exists(_prod) else -1
        self.assertEqual(_before, _after)

    def test_15_bad_patch_does_not_raise(self):
        self.assertEqual("no_original", PD.draft_from_patch({})["status"])
        self.assertEqual("no_original", PD.note_patch_draft_shadow(
            None, ledger_path=self.t.path)["status"])
        self.assertEqual([], self.t.rows())

    def test_16_kernel_failure_is_contained(self):
        class _Boom:
            def run(self, *a, **kw):
                raise RuntimeError("模拟算子故障")
        _r = PD.draft_from_patch(_patch(), kernel=_Boom())
        self.assertEqual("kernel_error", _r["status"])
        self.assertFalse(_r["covered"])

    def test_17_shadow_stats_buckets_by_issue_type(self):
        PD.note_patch_draft_shadow(_patch(orig=_GOOD, llm=_OVER),
                                   ledger_path=self.t.path)
        PD.note_patch_draft_shadow(_patch(orig=_GOOD, llm=_BARE,
                                          issue_type="atomic_write"),
                                   ledger_path=self.t.path)
        _s = PD.shadow_stats(self.t.path)
        self.assertEqual(2, _s["total"])
        self.assertEqual(0, _s["covered"])
        self.assertEqual(2, _s["revision_covered"])
        self.assertAlmostEqual(1.0, _s["revision_rate"], places=4)
        self.assertIn("non_atomic_write", _s["by_issue_type"])
        self.assertIn("atomic_write", _s["by_issue_type"])
        self.assertTrue(_s["llm_defect_hits"])


class TestIronLawAndWiring(unittest.TestCase):
    def test_18_no_external_imports_or_calls(self):
        with open(os.path.join(_MOD_DIR, "patch_draft.py"), encoding="utf-8") as _f:
            _tree = ast.parse(_f.read())
        _bad_imports = {"requests", "urllib", "httpx", "aiohttp", "openai",
                        "zhipuai", "dashscope", "socket", "http"}
        _bad_calls = {"urlopen", "chatcompletions", "generate_content", "invoke_model"}
        for _n in ast.walk(_tree):
            if isinstance(_n, ast.Import):
                for _a in _n.names:
                    self.assertNotIn(_a.name.split(".")[0], _bad_imports)
            elif isinstance(_n, ast.ImportFrom):
                self.assertNotIn(str(_n.module or "").split(".")[0], _bad_imports)
            elif isinstance(_n, ast.Call):
                _fn = _n.func
                _name = _fn.id if isinstance(_fn, ast.Name) else getattr(_fn, "attr", "")
                self.assertNotIn(str(_name).lower(), _bad_calls)

    def test_19_hook_is_guarded_in_patch_manager(self):
        with open(_PM_SRC, encoding="utf-8") as _f:
            _tree = ast.parse(_f.read())
        _hits = []
        for _try in (n for n in ast.walk(_tree) if isinstance(n, ast.Try)):
            for _stmt in _try.body:
                for _n in ast.walk(_stmt):
                    if (isinstance(_n, ast.Call)
                            and getattr(_n.func, "id", getattr(_n.func, "attr", ""))
                            == "note_patch_draft_shadow"):
                        _silent = any(
                            isinstance(_x, ast.Call)
                            and getattr(_x.func, "id", getattr(_x.func, "attr", ""))
                            == "silent_exc"
                            for _h in _try.handlers for _x in ast.walk(_h))
                        _hits.append((bool(_try.handlers), _silent))
        self.assertTrue(_hits, "PatchManager 未找到影子挂点")
        self.assertTrue(all(_h and _s for _h, _s in _hits))

    def test_20_hook_sits_before_any_patch_mutation(self):
        with open(_PM_SRC, encoding="utf-8") as _f:
            _src = _f.read()
        _i_hook = _src.find("note_patch_draft_shadow(patch)")
        _i_mutate = _src.find('patch.setdefault("entry_source"', _i_hook)
        self.assertGreater(_i_hook, 0)
        self.assertGreater(_i_mutate, _i_hook, "旁路必须在任何 patch 修改之前")

    def test_21_config_documents_rollback(self):
        with open(os.path.join(_ROOT, "config.py"), encoding="utf-8") as _f:
            _src = _f.read()
        self.assertIn("ENABLE_M475_PATCH_DRAFT_SHADOW = True", _src)
        self.assertIn("False ⇒ 逐字回滚", _src)

    def test_22_real_pool_samples_show_llm_defects(self):
        """只读实证：用真实池/账本补丁核对「LLM 产物有结构性缺陷」。"""
        _p = os.path.join(_ROOT, "data", "patches", "pending_patches.json")
        if not os.path.isfile(_p):
            self.skipTest("补丁池不存在")
        with open(_p, encoding="utf-8") as _f:
            _rows = json.load(_f)
        if isinstance(_rows, dict):
            _rows = _rows.get("patches") or []
        _samples = [_r for _r in _rows if isinstance(_r, dict)
                    and _r.get("original_code") and _r.get("modified_code")][:2]
        if not _samples:
            self.skipTest("池内无可用样本")
        for _r in _samples:
            _d = PD.draft_from_patch(_r)
            self.assertEqual("ok", _d["status"])
            self.assertIn("quality", _d)
            self.assertTrue(_d["quality"]["self_parse_ok"])


if __name__ == "__main__":
    unittest.main()
