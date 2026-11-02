# -*- coding: utf-8 -*-
"""423 批刀2 门控单测：`ENABLE_M415_EXPERIENCE_IN_PROMPT` 由影子转实弹。

判据来源：
  * 开关值读 `config` 属性本身（不是读测试里写的常量副本）；
  * 拼接结果比对的是**自己注入的 hits**（构造件），不是检索出来的东西 ⇒ 不受池内容影响。
"""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from organs.body import PulseLung as lung


def _hits(n=3, text_len=40):
    return [{"id": f"h{i}", "text": f"经验正文{i}" + "甲" * text_len,
             "weight": 1.0, "score": 3, "dup": 0} for i in range(n)]


def test_01_config_switch_is_on():
    """本刀的唯一硬要求：开关必须是 True（影子 → 实弹）。"""
    assert getattr(config, "ENABLE_M415_EXPERIENCE_IN_PROMPT", None) is True, (
        "423 刀2 要求开关为 True；若回滚成 False，本条会明确报出来")


def test_02_switch_on_builds_fragment_within_budget(monkeypatch):
    """开闸后必须真的产出段落，且每条 ≤100 字、合计 ≤500 字（任务书预算）。"""
    monkeypatch.setattr(config, "ENABLE_M415_EXPERIENCE_IN_PROMPT", True)
    frag, obs = lung._m415_build_experience_fragment("测试问题", hits=_hits(3))

    assert obs["on"] is True
    assert obs["n_hits"] == 3
    assert frag, "开闸后必须拼出内容"
    assert "【相关经验参考】" in frag
    assert obs["chars"] <= lung._M415_EXP_TOTAL_CHARS, obs["chars"]
    assert obs["n_used"] >= 1, "至少要拼进一条"

    for _ln in frag.splitlines():
        if _ln[:2].strip().rstrip(".").isdigit():
            assert len(_ln) <= lung._M415_EXP_ITEM_CHARS + 8, _ln


def test_03_switch_off_keeps_prompt_bytewise_unchanged(monkeypatch):
    """关回影子后：fragment 必须为空（prompt 逐字不变），但读数照算。"""
    monkeypatch.setattr(config, "ENABLE_M415_EXPERIENCE_IN_PROMPT", False)
    frag, obs = lung._m415_build_experience_fragment("测试问题", hits=_hits(2))

    assert obs["on"] is False
    assert frag == "", "影子模式下 fragment 必须是空串（否则 prompt 就不是逐字不变了）"
    assert obs["n_hits"] == 2, "影子模式仍要如实报命中数（那是判断要不要开闸的依据）"
    assert obs["n_used"] == 2, "影子模式仍要报'如果开拼会占几条'"


def test_04_budget_truncates_instead_of_overflow(monkeypatch):
    """超长内容必须被预算截断（不是拼爆）。"""
    monkeypatch.setattr(config, "ENABLE_M415_EXPERIENCE_IN_PROMPT", True)
    _big = [{"id": f"b{i}", "text": "乙" * 300, "weight": 1.0} for i in range(5)]
    _frag, obs = lung._m415_build_experience_fragment("测试问题", hits=_big)
    assert obs["chars"] <= lung._M415_EXP_TOTAL_CHARS
    assert obs["dropped"] >= 1, "预算吃不下时要如实记丢弃数"


def test_05_wiring_sits_before_speaking_rules():
    """接线位置必须仍在【说话要求】之前、且在 `_build_chat_prompt` 内。"""
    with open(os.path.join(ROOT, "organs", "body", "PulseLung.py"),
              encoding="utf-8") as _f:
        _src = _f.read()
    _i_def = _src.find("def _build_chat_prompt(")
    _i_call = _src.find("_m415_build_experience_fragment(user_message)")
    _i_rule = _src.find("【说话要求】", _i_def)
    assert _i_def > 0 and _i_call > _i_def, "拼装调用必须在 _build_chat_prompt 内"
    assert 0 < _i_call < _i_rule, "经验段必须在【说话要求】之前"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
