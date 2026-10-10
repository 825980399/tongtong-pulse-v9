# -*- coding: utf-8 -*-
"""第159批上A 刀2（T-规则生命周期-1）：规则签名基线回归。

覆盖统一 sha16 口径的核心性质：
  (a) 改检测逻辑函数体 -> 指纹变红
  (b) 仅改缩进/换行/挪行 -> 归一串不变 -> 指纹不红
  (d) rule_signatures() 幂等且覆盖 RULE_REGISTRY 全量
日志守卫：本件为纯函数指纹，不触碰生产日志口径，无需 Temp\\m53_* 隔离标记。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.self_awareness.rule_engine import (  # noqa: E402
    RULE_REGISTRY,
    rule_sha16,
    rule_signatures,
)


def test_rule_sha16_deterministic_and_format():
    _s = rule_sha16("T", "error", "py", "def f():\n    return 1\n")
    assert _s == rule_sha16("T", "error", "py", "def f():\n    return 1\n")
    assert len(_s) == 16
    assert all(c in "0123456789abcdef" for c in _s)


def test_rule_sha16_body_change_is_red():
    # 改检测逻辑函数体 -> 归一串变 -> 指纹变
    _a = "def chk():\n    return 1\n"
    _b = "def chk():\n    return 2\n"
    assert rule_sha16("T", "error", "py", _a) != rule_sha16("T", "error", "py", _b)


def test_rule_sha16_indent_or_line_noop():
    # 仅改缩进/换行/挪行 -> 归一串不变 -> 指纹不红（与 ci_common.normalize_body 同源）
    _ind = "def chk():\n    return 1\n"
    _ded = "def chk():\n  return 1\n"
    _moved = "def chk():\n\n    return 1\n"   # 中间多空行
    assert rule_sha16("T", "error", "py", _ind) == rule_sha16("T", "error", "py", _ded)
    assert rule_sha16("T", "error", "py", _ind) == rule_sha16("T", "error", "py", _moved)


def test_rule_signatures_covers_registry_and_sha16():
    _sigs = rule_signatures()
    for _rid in RULE_REGISTRY:
        assert _rid in _sigs
        assert isinstance(_sigs[_rid], str) and len(_sigs[_rid]) == 16


def test_rule_signatures_idempotent():
    # 幂等：两次调用结果一致（第2.4 回归要求）
    assert rule_signatures() == rule_signatures()
