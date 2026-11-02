# -*- coding: utf-8 -*-
"""427 刀3：生成侧失败 ⇒ (file, method) 冷却 ⇒ 生成入口拦截。

隔离：把 422 的账本路径函数整体指向 tmp 文件，生产
`data/patches/method_cooldown.json` 一字不动。
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.evolution import patch_execution_dispatch as PD
from nucleus.evolution import patch_generator as PG
from nucleus.reasoning import PatchManager as PM

_BROKEN_MOD = "        try:\n            x = 2\n"      # ← 截断在 try 中间
_ORIG = ("    def _compress_group(self, prefix, nodes):\n"
         "        try:\n            x = 1\n"
         "        except Exception:\n            pass\n"
         "        return False\n")


def _patch(pid: str, file: str = "organs/body/PulseLiver.py") -> dict:
    return {"id": pid, "file": file, "method": "_compress_group",
            "issue_type": "code_optimization", "source": "llm_multi_file",
            "risk_level": "中等", "original_code": _ORIG,
            "modified_code": _BROKEN_MOD}


class TestMethodCooldown(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        self.ledger = os.path.join(self.tmp, "method_cooldown.json")
        self._orig_path = PD._m422_cooldown_path
        PD._m422_cooldown_path = lambda path=None: path or self.ledger
        self.o = PM.PatchManager.__new__(PM.PatchManager)
        self.o._project_root = self.tmp

    def tearDown(self):
        PD._m422_cooldown_path = self._orig_path
        self._td.cleanup()

    def _fail_once(self, pid: str) -> str:
        _p = _patch(pid)
        _r = self.o._check_llm_patch_completeness(dict(_p))
        self.assertFalse(_r["complete"], "用例前提：这条片段必须被拒")
        PM._m427_note_method_fail(_p, _r["reason"])
        return str(_r["reason"])

    def test_01_three_failures_enter_cooldown_and_block_generation(self):
        """连续 3 次拒绝进 24h 冷却；第 4 次生成入口直接跳过（draft=None）。"""
        for i in range(1, 4):
            self._fail_once(f"patch_llm_427_{i:03d}")
            _st = PD._m422_method_state(_patch("probe"))
            self.assertEqual(_st["consec_fail"], i, "连续失败计数必须逐次累加")
        _st = PD._m422_method_state(_patch("probe"))
        self.assertTrue(_st["cooling"], "3 次后必须进冷却")
        self.assertAlmostEqual(_st["remain_sec"] / 3600.0, 24.0, places=1)

        _entry = {"file": "organs/body/PulseLiver.py", "method": "_compress_group",
                  "issue_type": "code_optimization", "desc": "d", "severity": "中",
                  "klass": "code"}
        _d = PG.generate_patch_draft(_entry, project_root=self.tmp)
        self.assertEqual(_d["state"], PG.STATE_METHOD_COOLDOWN,
                         f"冷却期内生成入口必须跳过，实得 state={_d['state']!r}")
        self.assertIsNone(_d["draft"], "跳过时不得产出草稿")
        self.assertIn("method_cooldown", str(_d["error"]))

    def test_02_cooling_is_idempotent_no_extension(self):
        """冷却期内再记账 ⇒ 不计数、不延期（否则每次被拦都续命，永远解不开）。"""
        for i in range(1, 4):
            self._fail_once(f"patch_llm_427b_{i:03d}")
        _before = PD._m422_method_state(_patch("probe"))
        _until_before = _before["cooldown_until"]
        _consec_before = _before["consec_fail"]

        _r = PD._m422_note_method_fail(_patch("patch_llm_427b_999"),
                                       reason="冷却期内再失败", category="fragment_incomplete")
        self.assertTrue(_r.get("ignored"), "冷却期内的记账必须被忽略")
        self.assertEqual(_r.get("why"), "already_cooling")
        _after = PD._m422_method_state(_patch("probe"))
        self.assertEqual(_after["cooldown_until"], _until_before, "冷却截止时间不许被延长")
        self.assertEqual(_after["consec_fail"], _consec_before, "连续计数不许在冷却期增长")

    def test_03_absolute_and_relative_path_share_one_key(self):
        """绝对路径与相对路径必须归一到同一个方法键（否则冷却永远命不中）。"""
        _b = chr(92)
        _root = PD._m326_repo_root()
        _variants = [
            _root + _b + "organs" + _b + "body" + _b + "PulseLiver.py",
            _root.replace(_b, "/") + "/organs/body/PulseLiver.py",
            "organs/body/PulseLiver.py",
            "./organs/body/PulseLiver.py",
        ]
        _keys = {PD._m422_pair_key(PD._m422_pair_of({"file": f, "method": "_compress_group"}))
                 for f in _variants}
        self.assertEqual(_keys, {"organs/body/PulseLiver.py::_compress_group"},
                         f"四种写法必须同键，实得: {_keys}")

    def test_04_production_ledger_untouched(self):
        """隔离跑完整条链路后，生产账本的方法集合不得变化。"""
        _prod = os.path.join("data", "patches", "method_cooldown.json")
        with open(_prod, encoding="utf-8") as fh:
            _n_before = len(json.load(fh).get("methods") or {})
        for i in range(1, 4):
            self._fail_once(f"patch_llm_427c_{i:03d}")
        with open(_prod, encoding="utf-8") as fh:
            _n_after = len(json.load(fh).get("methods") or {})
        self.assertEqual(_n_before, _n_after, "单测污染了生产冷却账本")


class TestStateEnum(unittest.TestCase):
    def test_10_cooldown_state_is_distinct(self):
        """冷却态必须是**独立** state，不能复用 generation_failed（否则会被当失败重试）。"""
        self.assertEqual(PG.STATE_METHOD_COOLDOWN, "method_cooldown")
        self.assertNotEqual(PG.STATE_METHOD_COOLDOWN, PG.STATE_GENERATION_FAILED)
        self.assertNotEqual(PG.STATE_METHOD_COOLDOWN, PG.STATE_GATE_REJECTED)


if __name__ == "__main__":
    unittest.main()
