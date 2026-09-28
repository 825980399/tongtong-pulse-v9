# -*- coding: utf-8 -*-
"""
test_e402_guard_m14.py —— 主线第14批 任务2 门控单测（E402 只减不增）

护栏：
  · nucleus/ 目录 E402 总数必须 ≤ 50（本批验收红线；修复前 77，本批后 0）
  · 本批修复的 19 个文件必须全部为 0
  · 本批改动过的模块必须仍可正常 import（import 搬移不得破坏加载）

说明：ruff 不在时（无 CLI）整组用例 skip，不阻塞 pytest。
"""
import importlib
import os
import re
import subprocess
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

def _resolve_ruff():
    """定位 ruff 可执行文件（ruff.exe / ruff / PATH）。找不到返回 None。"""
    import shutil
    for cand in (r"D:\Program Files\Python312\Scripts\ruff.exe",
                 r"D:\Program Files\Python312\Scripts\ruff",
                 "ruff.exe", "ruff"):
        if os.path.isabs(cand):
            if os.path.exists(cand):
                return cand
        else:
            found = shutil.which(cand)
            if found:
                return found
    return None


_RUFF = _resolve_ruff()
_E402_CEILING = 50          # 任务书验收红线
_FIXED_FILES = [
    "nucleus/semantic/VectorStore.py",
    "nucleus/reasoning/SelfCalibrator.py",
    "nucleus/qica/QICA.py",
    "nucleus/mnemosyne/experience_pool.py",
    "nucleus/pulse/FrequencyCodec.py",
    "nucleus/HybridParallelScheduler.py",
    "nucleus/evolution/EvolutionLoop.py",
    "nucleus/reasoning/SafeEvolutionExecutor.py",
    "nucleus/mnemosyne/PulseNode.py",
    "nucleus/mnemosyne/KnowledgeTree.py",
    "nucleus/synapsys/ResonanceEngine.py",
    "nucleus/exploration_audit.py",
    "nucleus/runtime_tempo.py",
    "nucleus/mnemosyne/PulseNodePool.py",
    "nucleus/self_inspector.py",
    "nucleus/reasoning/EvolutionSandbox.py",
    "nucleus/reasoning/AutonomousDeriver.py",
    "nucleus/diagnostics.py",
    "nucleus/CompanionBridge.py",
]


def _ruff_e402(path):
    """返回 (条数, 明细行)。ruff 不可用时返回 None。"""
    if not _RUFF:
        return None
    out = subprocess.run([_RUFF, "check", "--select", "E402",
                          "--output-format", "concise", path],
                         capture_output=True, text=True, cwd=_PROJECT_ROOT,
                         encoding="utf-8", errors="replace").stdout
    rows = [l for l in out.splitlines() if re.search(r": E402", l)]
    return len(rows), rows


class TestE402Ceiling(unittest.TestCase):
    def setUp(self):
        r = _ruff_e402("nucleus/")
        if r is None:
            self.skipTest("ruff CLI 不可用")
        self.count, self.rows = r

    def test_nucleus_e402_within_ceiling(self):
        self.assertLessEqual(self.count, _E402_CEILING,
                             f"nucleus E402={self.count} 超出红线 {_E402_CEILING}\n"
                             + "\n".join(self.rows[:20]))

    def test_fixed_files_have_zero_e402(self):
        still = []
        for rel in _FIXED_FILES:
            r = _ruff_e402(rel)
            if r and r[0] > 0:
                still.append((rel, r[1]))
        self.assertEqual(still, [], f"以下本批修复文件仍有 E402: {still}")


class TestFixedModulesImportable(unittest.TestCase):
    """import 搬移后必须仍可正常加载（防搬移引入循环导入 / NameError）。"""

    MODULES = [
        "nucleus.semantic.VectorStore",
        "nucleus.reasoning.SelfCalibrator",
        "nucleus.qica.QICA",
        "nucleus.mnemosyne.experience_pool",
        "nucleus.pulse.FrequencyCodec",
        "nucleus.HybridParallelScheduler",
        "nucleus.evolution.EvolutionLoop",
        "nucleus.reasoning.SafeEvolutionExecutor",
        "nucleus.mnemosyne.PulseNode",
        "nucleus.mnemosyne.KnowledgeTree",
        "nucleus.synapsys.ResonanceEngine",
        "nucleus.exploration_audit",
        "nucleus.runtime_tempo",
        "nucleus.mnemosyne.PulseNodePool",
        "nucleus.self_inspector",
        "nucleus.reasoning.EvolutionSandbox",
        "nucleus.reasoning.AutonomousDeriver",
        "nucleus.diagnostics",
        "nucleus.CompanionBridge",
    ]

    def test_all_importable(self):
        failed = []
        for m in self.MODULES:
            try:
                importlib.import_module(m)
            except Exception as e:  # 测试聚合报告
                failed.append((m, f"{type(e).__name__}: {e}"))
        self.assertEqual(failed, [], f"import 失败: {failed}")


if __name__ == "__main__":
    unittest.main()
