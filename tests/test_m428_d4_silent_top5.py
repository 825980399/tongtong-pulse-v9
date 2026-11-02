# -*- coding: utf-8 -*-
"""428 批刀4 单测：Top5 文件静默 except 清理 + 真 bug 修复。

钉语义：
  · 五个目标文件里**非豁免**静默 handler 必须为 0（这是本批的可复核结论，
    也是防回归的硬线：谁再塞回一个裸 `except: pass` 就红）；
  · 「知识分布查询失败」不得再被记成「学习失败」（不累加连续失败计数）。
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import time
from typing import ClassVar

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

TARGETS = ["organs/brain/PulseCortex.py", "organs/brain/PulseInnerWorld.py",
           "nucleus/reasoning/PatchManager.py",
           "nucleus/reasoning/SafeEvolutionExecutor.py",
           "nucleus/qica/IntentChannels.py"]


def _exempt_ids():
    _p = os.path.join(_ROOT, "tools", "ci", "silent_except_fingerprints.json")
    with open(_p, encoding="utf-8") as f:
        return {(i.get("path"), i.get("qualname"), i.get("tname"),
                 i.get("kind"), i.get("sha16")) for i in json.load(f)}


def test_01_top5_have_no_non_exempt_silent_handler():
    from tools.ci.ci_common import extract_handlers, handler_identity
    _ex = _exempt_ids()
    _left = {}
    for _rel in TARGETS:
        with open(os.path.join(_ROOT, _rel), encoding="utf-8", errors="replace") as f:
            _src = f.read()
        _n = sum(1 for h in extract_handlers(_src, _rel)
                 if handler_identity(h) not in _ex)
        if _n:
            _left[_rel] = _n
    assert _left == {}, f"仍有非豁免静默 handler：{_left}"


def test_02_nodes_query_failure_is_not_learning_failure():
    """查询失败 ⇒ 不计连续失败（与同文件「无样本 ≠ 零成功率」同口径）。"""
    piw = importlib.import_module("organs.brain.PulseInnerWorld")

    class _Pool:
        def get_path_distribution(self):
            raise RuntimeError("路径分布查询失败（模拟）")

    class _Stub:
        _learning_history: ClassVar[list] = [{
            "target_area": "代码理解",
            "started_at": time.time() - 3600,
            "nodes_before": 5,
            "evaluated": False,
        }]
        node_pool = _Pool()
        _inference_trace: ClassVar[list] = []
        _consecutive_fail_counts: ClassVar[dict] = {}
        _learning_goal_switch_count = 0

        def _log(self, *a, **k):
            pass

    _stub = _Stub()
    piw.PulseInnerWorld._evaluate_learning_effectiveness(_stub)
    assert _stub._consecutive_fail_counts.get("代码理解", 0) == 0, (
        f"查询失败被记成了学习失败：{_stub._consecutive_fail_counts!r}")
    assert _stub._learning_history[0]["evaluated"] is True
