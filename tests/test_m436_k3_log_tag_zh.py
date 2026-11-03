# -*- coding: utf-8 -*-
"""第436批刀3 单测：日志前缀中文化（统一在 logger 做）。

守四件事：
  ① 高频英文前缀映射成中文（verify_feedback / self_inspector / main …）；
  ② `pulse.organ.PulseHeart` 截短后的 `Heart` 也能映射（查 Pulse+tag）；
  ③ 未收录的名字**原样返回**（第三方库不硬凑译名）；
  ④ `ENABLE_M436_LOG_TAG_ZH=False` ⇒ 逐字回到英文原名。
"""
import logging
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config
import nucleus.logger as LG


def _tag_of(logger_name: str) -> str:
    _fmt = LG.PulseFormatter(fmt="[%(organ_tag)s] %(message)s")
    _rec = logging.LogRecord(logger_name, logging.INFO, "", 0, "x", (), None)
    _out = _fmt.format(_rec)
    return _out.split("]")[0].lstrip("[")


def test_high_freq_names_mapped():
    for _en, _zh in (("verify_feedback", "验证反馈"),
                     ("self_inspector", "自检视"),
                     ("audit_problem_pool", "问题池"),
                     ("main", "主循环"),
                     ("InfoField", "信息场")):
        assert _tag_of(_en) == _zh, f"{_en} 未映射为 {_zh}"


def test_pulse_organ_short_name_mapped():
    assert _tag_of("pulse.organ.PulseHeart") == "心脏", "PulseHeart 截短后应命中 Pulse+tag"
    assert _tag_of("pulse.organ.肝脏") == "肝脏", "中文器官名不应被改动"


def test_unknown_name_unchanged():
    assert _tag_of("third_party_lib.xyz") == "third_party_lib.xyz", "未收录应原样返回"


def test_switch_off_restores_english(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M436_LOG_TAG_ZH", False)
    assert _tag_of("verify_feedback") == "verify_feedback", "关开关应回英文原名"
    assert _tag_of("pulse.framework") == "框架", "pulse.framework 是既有中文，不属刀3范围"
