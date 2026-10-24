# -*- coding: utf-8 -*-
"""363 批 D2 验收：规则通道子进程化（隔离试点，默认关 + 失败回退）。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from nucleus import exploration_audit as ea  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _FakeInspector:
    def __init__(self, issues=None):
        self._issues = issues if issues is not None else [{"type": "demo", "file": "a.py"}]

    def detect_code_issues(self, *a, **k):
        return list(self._issues)


class TestD2RuleChannelSubprocess(unittest.TestCase):
    def setUp(self):
        self._orig = getattr(config, "ENABLE_RULE_CHANNEL_SUBPROCESS", None)

    def tearDown(self):
        if self._orig is None:
            if hasattr(config, "ENABLE_RULE_CHANNEL_SUBPROCESS"):
                delattr(config, "ENABLE_RULE_CHANNEL_SUBPROCESS")
        else:
            config.ENABLE_RULE_CHANNEL_SUBPROCESS = self._orig

    def test_01_switch_default_off(self):
        """隔离试点：默认必须关（未设属性 ⇒ False），生产主链路不改道。"""
        if hasattr(config, "ENABLE_RULE_CHANNEL_SUBPROCESS"):
            delattr(config, "ENABLE_RULE_CHANNEL_SUBPROCESS")
        self.assertFalse(ea._m363_rule_subprocess_on())

    def test_02_off_uses_inprocess_path(self):
        if hasattr(config, "ENABLE_RULE_CHANNEL_SUBPROCESS"):
            delattr(config, "ENABLE_RULE_CHANNEL_SUBPROCESS")
        self.assertIsNone(ea._m363_rule_channel_subprocess(_FakeInspector()))
        self.assertEqual(ea._rule_channel(_FakeInspector([{"type": "x"}])),
                         [{"type": "x"}])

    def test_03_on_runs_subprocess_and_returns_list(self):
        config.ENABLE_RULE_CHANNEL_SUBPROCESS = True
        out = ea._m363_rule_channel_subprocess(
            _FakeInspector(), organs=["PulseHeart"], min_severity="high", timeout=120)
        self.assertIsNotNone(out, "开关开且工作器在位 ⇒ 应走子进程")
        self.assertIsInstance(out, list)
        st = ea.m363_rule_scan_last()
        self.assertEqual(st.get("state"), "ok", st)
        self.assertEqual(st.get("rules_version"), "363.d2.v1")

    def test_04_fallback_when_worker_missing(self):
        """工作器不在 ⇒ 回退进程内（零回归），且状态记为 no_worker。"""
        config.ENABLE_RULE_CHANNEL_SUBPROCESS = True
        orig = ea.__dict__.get("_M363_RULE_SCAN_LAST")
        try:
            # 用不存在的 root ⇒ 工作器路径算不出来
            _real = os.path.isfile

            def _fake_isfile(p):
                if str(p).endswith("probe_scan_worker.py"):
                    return False
                return _real(p)

            import nucleus.exploration_audit as _mod
            _saved = _mod.os.path.isfile
            _mod.os.path.isfile = _fake_isfile
            try:
                self.assertIsNone(ea._m363_rule_channel_subprocess(_FakeInspector()))
                self.assertEqual(ea.m363_rule_scan_last().get("state"), "no_worker")
                self.assertEqual(ea._rule_channel(_FakeInspector([{"t": 1}])), [{"t": 1}])
            finally:
                _mod.os.path.isfile = _saved
        finally:
            if orig is not None:
                ea._M363_RULE_SCAN_LAST.clear()
                ea._M363_RULE_SCAN_LAST.update(orig)

    def test_05_timeout_falls_back(self):
        """超时必须回退，不得把审查卡死。"""
        config.ENABLE_RULE_CHANNEL_SUBPROCESS = True
        self.assertIsNone(ea._m363_rule_channel_subprocess(
            _FakeInspector(), organs=["PulseHeart"], timeout=0.001))
        self.assertIn(ea.m363_rule_scan_last().get("state"), ("exception", "rc_nonzero"))

    def test_06_worker_cli_contract(self):
        """工作器 CLI 契约：--out 落地 JSON，含 status/count/issues/pid。"""
        import json
        import subprocess
        d = tempfile.mkdtemp()
        out = os.path.join(d, "probe.json")
        r = subprocess.run(
            [sys.executable, os.path.join(_ROOT, "tools", "probe_scan_worker.py"),
             "--root", _ROOT, "--out", out, "--organs", '["PulseHeart"]',
             "--min-severity", "high"],
            capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        doc = json.load(open(out, encoding="utf-8"))
        self.assertEqual(doc["status"], "ok")
        self.assertIsInstance(doc["issues"], list)
        self.assertIn("pid", doc)
        self.assertIn("rules_version", doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
