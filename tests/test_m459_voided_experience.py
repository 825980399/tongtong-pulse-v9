# -*- coding: utf-8 -*-
"""459 刀2：converge_voided（审批/收敛作废）接入补丁失败经验库。

★本文件守四件事（任务书验收逐条对应）
  1. 一条 voided 记录真的进经验库（经 `note_converge_voided` 唯一写口）；
  2. 只提**一句话特征**，不塞完整补丁代码；
  3. 提醒段总长 ≤300 字，且 voided 特征占比 ≤一半；
  4. 452 刀6 的既有契约不破（pytest 护栏、幂等、开关、既有接线）。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus import patch_failure_experience as PFE
from nucleus.evolution import criteria_window as CW


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    exp = str(tmp_path / "failure_experience.jsonl")
    ledger = str(tmp_path / "converge_voided.jsonl")
    monkeypatch.setattr(config, "M452_FAILURE_EXPERIENCE_PATH", exp, raising=False)
    monkeypatch.setattr(config, "ENABLE_PATCH_FAILURE_EXPERIENCE", True, raising=False)
    return exp, ledger


def _rows(path):
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


# ---------------------------------------------------------------- 特征提取（纯函数）
@pytest.mark.parametrize("row,expect_type", [
    ({"no_def": True}, "voided_no_def"),
    ({"mismatch": True}, "voided_original_mismatch"),
    ({"o3_dead_code": True}, "voided_dead_code"),
    ({"issue_type": "code_optimization"}, "voided_code_optimization"),
    ({}, "voided_structural_defect"),
])
def test_voided_feature_maps_to_one_sentence(row, expect_type):
    _kind, _sentence = PFE.voided_feature(row)
    assert _kind == expect_type, (_kind, row)
    assert _kind.startswith(PFE.VOIDED_PREFIX)
    assert isinstance(_sentence, str) and 0 < len(_sentence) <= 80
    assert "patch" not in _sentence.lower()


def test_voided_feature_never_copies_reason_or_code():
    _row = {"reason": "x" * 500, "patch_snippet": "def x(): pass" * 100,
            "no_def": True, "file": "a.py"}
    _kind, _sentence = PFE.voided_feature(_row)
    assert len(_sentence) < 80
    assert "def x()" not in _sentence


# ---------------------------------------------------------------- 接线
def test_note_converge_voided_writes_both_ledger_and_experience(isolated):
    exp, ledger = isolated
    p = CW.note_converge_voided(
        "patch_459_test", file="organs/body/PulseLiver.py",
        method="_compress_group", reason="284刀5 结构缺陷补丁作废",
        path=ledger, no_def=True, mismatch=False, issue_type="code_optimization")
    assert p == ledger
    _led = _rows(ledger)
    assert len(_led) == 1 and _led[0]["patch_id"] == "patch_459_test"
    _exp = _rows(exp)
    assert len(_exp) == 1, "voided 必须真的进经验库（任务书刀2 唯一硬指标）"
    assert _exp[0]["error_type"] == "voided_no_def"
    assert _exp[0]["patch_snippet"] == "", "不许把补丁正文塞进经验库"
    assert "PulseLiver.py" in _exp[0]["file_path"]
    assert len(str(_exp[0]["error_msg"])) <= 80


def test_record_voided_respects_switch_off(isolated, monkeypatch):
    exp, _ = isolated
    monkeypatch.setattr(config, "ENABLE_PATCH_FAILURE_EXPERIENCE", False, raising=False)
    assert PFE.record_voided({"no_def": True, "file": "a.py"}) is False
    assert _rows(exp) == []


def test_pytest_guard_still_blocks_default_path(monkeypatch):
    """452 刀6 的护栏不能因为新接线被拆掉。"""
    monkeypatch.setattr(config, "M452_FAILURE_EXPERIENCE_PATH",
                        PFE.DEFAULT_REL, raising=False)
    assert PFE.record_voided({"no_def": True}) is False


# ---------------------------------------------------------------- 长度与占比
def test_reminder_head_mentions_approval_rejection():
    assert "含审批被打回的" in PFE.REMINDER_HEAD
    assert "请检查你的代码是否包含以下特征" in PFE.REMINDER_HEAD


def test_reminder_total_and_voided_share(isolated):
    for _ in range(6):
        PFE.record_voided({"no_def": True, "file": "a.py"})
    for _ in range(3):
        PFE.record_failure("no_def 结构缺陷")
    PFE.record_failure("unindent does not match")
    rem = PFE.build_reminder(top_n=10, max_chars=300)
    assert len(rem) <= 300, len(rem)
    _voided_chars = sum(len(l) for l in rem.splitlines()
                        if PFE.VOIDED_PREFIX in l)
    assert _voided_chars <= 300 // 2, (_voided_chars, rem)
    assert "no_def_missing" in rem, "既有代码验证失败类型不能被 voided 全挤掉"


def test_append_reminder_is_idempotent_and_keeps_prompt(isolated):
    PFE.record_voided({"mismatch": True, "file": "a.py"})
    p = "原始 prompt 一个字都不能少。"
    out = PFE.append_reminder(p)
    assert out.startswith(p)
    assert PFE.append_reminder(out) == out


def test_no_records_means_prompt_unchanged(isolated):
    assert PFE.append_reminder("p") == "p"


# ---------------------------------------------------------------- 回滚点
def test_hook_is_in_the_single_ledger_write_point():
    _p = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "nucleus", "evolution", "criteria_window.py")
    with open(_p, encoding="utf-8") as _f:
        src = _f.read()
    assert "record_voided as _m459_rec" in src
    assert src.count("_m459_rec(_rec)") == 1
