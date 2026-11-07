# -*- coding: utf-8 -*-
"""475 刀26（静默异常）+ 刀27（补消费端）门控单测。"""
from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config
from nucleus.autonomy import patch_draft as PD
from nucleus.autonomy import pulse
from nucleus.evolution import self_verdict as SV

_SV_SRC = os.path.join(_ROOT, "nucleus", "evolution", "self_verdict.py")
_FOUR_DEF = "    def _save(self):\n        return 1\n"
_ZERO_DEF = "def _save():\n    return 1\n"


class _Sandbox:
    def __init__(self, n_patches=3):
        self.dir = tempfile.mkdtemp(prefix="m475_k27_")
        self.ledger = os.path.join(self.dir, "shadow.jsonl")
        self.root = os.path.join(self.dir, "repo")
        os.makedirs(os.path.join(self.root, "data", "patches"))
        _pending = [{"id": "p1", "file": "organs/X.py", "method": "f",
                     "issue_type": "silent_exception",
                     "original_code": _FOUR_DEF.replace("return 1", "return 2"),
                     "modified_code": _FOUR_DEF}]
        _hist = []
        for _i in range(n_patches):
            _hist.append({
                "id": f"h{_i}", "file": "organs/Y.py", "method": "g",
                "issue_type": "periodic_task_no_reentry" if _i % 2 else "silent_exception",
                "original_code": _FOUR_DEF,
                "modified_code": _FOUR_DEF.replace("return 1", "return 3"),
                "applied": True,
            })
        with open(os.path.join(self.root, "data", "patches", "pending_patches.json"),
                  "w", encoding="utf-8") as _f:
            json.dump(_pending, _f, ensure_ascii=False)
        with open(os.path.join(self.root, "data", "patches", "patch_history.json"),
                  "w", encoding="utf-8") as _f:
            json.dump(_hist, _f, ensure_ascii=False)

    def rows(self):
        if not os.path.isfile(self.ledger):
            return []
        with open(self.ledger, encoding="utf-8") as _f:
            return [json.loads(_x) for _x in _f if _x.strip()]

    def close(self):
        from nucleus._silent_except import silent_exc
        for _d, _, _files in os.walk(self.dir, topdown=False):
            for _n in _files:
                try:
                    os.remove(os.path.join(_d, _n))
                except OSError as _e:
                    silent_exc(_e, where="test_m475_k26k27::_Sandbox", level="debug")
            try:
                os.rmdir(_d)
            except OSError as _e:
                silent_exc(_e, where="test_m475_k26k27::_Sandbox rmdir", level="debug")


class TestKnife26SilentException(unittest.TestCase):
    def test_01_four_space_def_returns_unchanged(self):
        _c, _w = SV.normalize_snippet(_FOUR_DEF)
        self.assertEqual(_FOUR_DEF, _c)
        self.assertFalse(_w)

    def test_02_counterproof_parse_would_have_failed(self):
        """★反证：同一片段直接 ast.parse 必然抛 IndentationError，
        证明「提前返回」确实避开了一次注定失败的解析（判据有判别力）。"""
        with self.assertRaises(IndentationError):
            ast.parse(_FOUR_DEF)
        with mock.patch.object(SV, "silent_exc") as _se:
            SV.normalize_snippet(_FOUR_DEF)
            _se.assert_not_called()

    def test_03_zero_indent_def_also_unchanged(self):
        with mock.patch.object(SV, "silent_exc") as _se:
            _c, _w = SV.normalize_snippet(_ZERO_DEF)
        self.assertEqual(_ZERO_DEF, _c)
        self.assertFalse(_w)
        _se.assert_not_called()

    def test_04_valid_fragment_without_def_stays_unwrapped(self):
        """契约不变：无 def 但可解析的片段仍原样返回。"""
        self.assertEqual(("x = 1", False), SV.normalize_snippet("x = 1"))

    def test_05_invalid_fragment_without_def_is_wrapped(self):
        """契约不变：无 def 且解析失败的片段仍包装成探针函数。"""
        _c, _w = SV.normalize_snippet("self.x =")
        self.assertTrue(_w)
        self.assertTrue(_c.startswith("def _m461_probe():"))

    def test_06_def_check_precedes_ast_parse_in_source(self):
        with open(_SV_SRC, encoding="utf-8") as _f:
            _tree = ast.parse(_f.read())
        _fn = next(_n for _n in ast.walk(_tree)
                   if isinstance(_n, ast.FunctionDef) and _n.name == "normalize_snippet")
        _s = ast.unparse(_fn)
        self.assertLess(_s.find("re.search"), _s.find("ast.parse"))

    def test_07_collect_evidence_still_runs(self):
        """下游不回归：真实形态补丁能跑完证据收集。"""
        _p = {"file": "organs/X.py", "method": "f",
              "original_code": _FOUR_DEF, "modified_code": _FOUR_DEF}
        _r = SV.collect_evidence(_p)
        self.assertIsInstance(_r, dict)


class TestKnife27Consume(unittest.TestCase):
    def setUp(self):
        self.sb = _Sandbox()

    def tearDown(self):
        self.sb.close()

    def test_08_backfill_consumes_existing_patches(self):
        _r = PD.consume_existing_patches(ledger_path=self.sb.ledger, root=self.sb.root)
        self.assertEqual("ok", _r["status"])
        self.assertGreater(_r["new"], 0)
        self.assertGreater(_r["stats"]["total"], 0)
        for _x in self.sb.rows():
            self.assertEqual("offline_backfill", _x["source"])
            self.assertIn("quality", _x)

    def test_09_second_run_is_deduplicated(self):
        PD.consume_existing_patches(ledger_path=self.sb.ledger, root=self.sb.root)
        _n = len(self.sb.rows())
        _r = PD.consume_existing_patches(ledger_path=self.sb.ledger, root=self.sb.root)
        self.assertEqual(0, _r["new"])
        self.assertEqual(_n, len(self.sb.rows()))

    def test_10_stats_bucket_by_issue_type(self):
        PD.consume_existing_patches(ledger_path=self.sb.ledger, root=self.sb.root)
        _s = PD.shadow_stats(self.sb.ledger)
        self.assertIn("silent_exception", _s["by_issue_type"])
        self.assertIn("coverage_rate", _s)
        self.assertIn("revision_rate", _s)

    def test_11_max_patches_is_bounded(self):
        _r = PD.consume_existing_patches(max_patches=2, ledger_path=self.sb.ledger,
                                         root=self.sb.root)
        self.assertLessEqual(_r["scanned"], 2)
        self.assertEqual(2, len(self.sb.rows()))

    def test_12_bad_json_does_not_raise(self):
        with open(os.path.join(self.sb.root, "data", "patches", "patch_history.json"),
                  "w", encoding="utf-8") as _f:
            _f.write("{broken")
        _r = PD.consume_existing_patches(ledger_path=self.sb.ledger, root=self.sb.root)
        self.assertEqual("ok", _r["status"])

    def test_13_default_path_skipped_under_pytest(self):
        _prod = os.path.join(_ROOT, "data", "autonomy", "patch_draft_shadow.jsonl")
        _before = os.path.getsize(_prod) if os.path.exists(_prod) else -1
        self.assertEqual("skipped_test", PD.consume_existing_patches()["status"])
        _after = os.path.getsize(_prod) if os.path.exists(_prod) else -1
        self.assertEqual(_before, _after)


class TestKnife27PulseWiring(unittest.TestCase):
    def setUp(self):
        self._state = dict(pulse._STATE)
        self._obs = pulse._OBSERVER
        pulse._STATE.clear()
        pulse._STATE.update({"last_ts": 0.0, "runs": 0})
        pulse._OBSERVER = None

    def tearDown(self):
        pulse._STATE.clear()
        pulse._STATE.update(self._state)
        pulse._OBSERVER = self._obs

    def test_14_switch_off_disables_backfill(self):
        with mock.patch.object(config, "ENABLE_M475_PATCH_BACKFILL", False, create=True), \
             mock.patch.object(PD, "consume_existing_patches") as _fn:
            _r = pulse.autonomy_observe_tick(now=1000.0, hub=None, force=True)
        _fn.assert_not_called()
        self.assertEqual("disabled", _r["backfill"])

    def test_15_zero_is_explicit_off(self):
        with mock.patch.object(config, "ENABLE_M475_PATCH_BACKFILL", 0, create=True):
            _r = pulse.autonomy_observe_tick(now=1000.0, hub=None, force=True)
        self.assertEqual("disabled", _r["backfill"])

    def test_16_backfill_error_does_not_break_observation(self):
        with mock.patch.object(PD, "consume_existing_patches",
                               side_effect=RuntimeError("模拟消费端故障")):
            _r = pulse.autonomy_observe_tick(now=1000.0, hub=None, force=True)
        self.assertEqual("ok", _r["status"])
        self.assertEqual("error", _r["backfill"])

    def test_17_config_documents_rollback(self):
        with open(os.path.join(_ROOT, "config.py"), encoding="utf-8") as _f:
            _s = _f.read()
        self.assertIn("ENABLE_M475_PATCH_BACKFILL = True", _s)
        self.assertIn("False ⇒ 逐字回滚", _s)


if __name__ == "__main__":
    unittest.main()
