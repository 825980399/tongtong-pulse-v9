# -*- coding: utf-8 -*-
"""452 刀6：补丁失败经验库 + prompt 提醒段。"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus import patch_failure_experience as PFE


@pytest.fixture()
def exp_path(tmp_path, monkeypatch):
    p = str(tmp_path / "failure_experience.jsonl")
    monkeypatch.setattr(config, "M452_FAILURE_EXPERIENCE_PATH", p, raising=False)
    monkeypatch.setattr(config, "ENABLE_PATCH_FAILURE_EXPERIENCE", True, raising=False)
    return p


def test_错误归类():
    assert PFE.classify_error("284刀5 结构缺陷补丁作废 no_def") == "no_def_missing"
    assert PFE.classify_error("unindent does not match") == "indent_error"
    assert PFE.classify_error("补丁生成失败或无变化") == "generation_failed"
    assert PFE.classify_error("随便什么") == "other"


def test_失败记录字段齐全(exp_path):
    assert PFE.record_failure("no_def 结构缺陷",
                              file_path="organs/body/PulseLiver.py::_compress_group",
                              patch_snippet="def x(): pass") is True
    with open(exp_path, encoding="utf-8") as f:
        r = json.loads(f.readline())
    for k in ("timestamp", "error_type", "error_msg", "file_path", "patch_snippet"):
        assert k in r, (k, r)
    assert r["error_type"] == "no_def_missing", r


def test_常见类型排前面且提醒段不超300字(exp_path):
    for _ in range(3):
        PFE.record_failure("no_def 结构缺陷")
    PFE.record_failure("语法错误")
    PFE.record_failure("unindent")
    rem = PFE.build_reminder(top_n=10, max_chars=300)
    assert rem.startswith(PFE.REMINDER_HEAD), rem
    assert len(rem) <= 300, len(rem)
    assert rem.index("no_def_missing") < rem.index("syntax_error"), rem


def test_prompt只追加不改原文且幂等(exp_path):
    PFE.record_failure("no_def 结构缺陷")
    p = "原始 prompt 内容，一个字都不能少。"
    out = PFE.append_reminder(p)
    assert out.startswith(p), out
    assert PFE.append_reminder(out) == out, "重复追加必须幂等"


def test_无失败记录时prompt逐字不变(exp_path):
    p = "原始 prompt"
    assert PFE.append_reminder(p) == p


def test_开关关掉不写不拼(exp_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_PATCH_FAILURE_EXPERIENCE", False, raising=False)
    assert PFE.record_failure("x") is False
    assert PFE.append_reminder("p") == "p"


def test_测试进程不许写默认生产路径(monkeypatch):
    monkeypatch.setattr(config, "M452_FAILURE_EXPERIENCE_PATH",
                        PFE.DEFAULT_REL, raising=False)
    assert PFE.record_failure("x") is False


def test_两处接线都在():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "nucleus", "reasoning", "SafeEvolutionExecutor.py")
    with open(p, encoding="utf-8", errors="replace") as f:
        src = f.read()
    assert "record_failure as _m452_rec" in src
    assert "append_reminder as _m452_remind" in src
