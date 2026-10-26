# -*- coding: utf-8 -*-
"""372 刀14 定向单测（2 例）：评估结论与证据件一致 + 生产面零 diff。

本刀只做评估（任务书：若补建"本刀不实现"），故测试锚的是**结论本身**与**边界**，
而不是并发行为（并发孪生按证据件 §四 另立测试票落地）。
"""
from __future__ import annotations

import glob
import io
import os
import subprocess

_EVID = os.path.join("docs", "路灯与星轨对话", "交付报告", "证据件", "372",
                     "刀14_tests孪生评估_20261026.md")
_SELF = os.path.basename(__file__)


def _read(fp):
    return io.open(fp, encoding="utf-8", errors="replace").read()


def test_01_conclusion_and_anchors_present():
    """结论=建议补建；四个方案锚点齐备；且"测试面零覆盖"这一判断仍然成立。"""
    assert os.path.isfile(_EVID), "证据件缺失"
    _t = _read(_EVID)
    assert "建议补建" in _t
    # 四个关键锚点：建议文件 / 被测方法 / 枚举口径 / 隔离要求
    for _k in ("tests/test_m372_b14_organ_concurrency_twin.py",
               "_handle_pulse_safe",
               "OrganStatus",
               "本刀不实现"):
        assert _k in _t, "证据件缺锚点: %s" % _k
    # ★结论成立的前提：被测方法在 tests 面确实零覆盖（排除本文件自身的提及）
    _hits = []
    for _fp in glob.glob(os.path.join("tests", "*.py")):
        if os.path.basename(_fp) == _SELF:
            continue
        if "_handle_pulse_safe" in _read(_fp):
            _hits.append(_fp)
    assert _hits == [], (
        "★若已有测试覆盖，本条结论（缺口真实）就失效了，须重评；实得: %s" % _hits)
    # 被测方法仍在产品代码里（不是"对象都删了所以不用补"）
    assert "def _handle_pulse_safe" in _read("base/BasePulseOrgan.py")


def test_02_production_untouched():
    """本刀不施工、不补测试：产品面与 tests 面都不得有本刀新增。"""
    _r = subprocess.run(
        ["git", "diff", "--name-only", "5dff8abe", "--",
         "base/BasePulseOrgan.py"], capture_output=True, text=True)
    assert _r.returncode == 0
    assert _r.stdout.strip() == "", "★本刀不许改 base/BasePulseOrgan.py；实得 %r" % _r.stdout
    # 也确认没有偷偷建孪生文件（补建须另立票）
    assert not os.path.exists("tests/test_m372_b14_organ_concurrency_twin.py"), (
        "★任务书明确'本刀不实现'；要补建请另立测试票")
