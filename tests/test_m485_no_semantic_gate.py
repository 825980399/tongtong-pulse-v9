# -*- coding: utf-8 -*-
"""485刀4（P1）：零语义变更闸 —— 注释/标点/类型注解层面的补丁不得自动应用。

★守任务书验收（§485-4）：
  1. 删一个类型注解的补丁 → 被零语义闸拒（406 的 _skeleton 只去 docstring，
     注解仍在 AST 里 ⇒ 会被判成 logic 绕过窄闸语义，本闸补上）；
  2. 只改注释标点的补丁 → 被拒（reason=comment_only_non_doc_issue，坑二）；
  3. 真修 bug 的补丁 → 406 口径**不变**（本闸只收窄不放宽）；
  4. 开关关 → 行为与改前逐字一致；
  5. doc 类 issue_type 白名单（M485_DOC_COMMENT_ISSUE_TYPES）可放行注释补丁。
★拒绝 = level 降为 medium + eligible=False + 拒因写进判定（转人工，不删补丁）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus.evolution import patch_risk_gate

_ORIG = "def calc(x):\n    return x * 2 + 1\n"


def _rec(orig, new, issue_type="code_optimization", fname="organs/brain/PulseCortex.py"):
    return {"id": "m485t", "file": fname, "method": "calc",
            "issue_type": issue_type, "original_code": orig,
            "modified_code": new,
            "verification": {"passed": True},
            "no_regression": True, "post_apply_errors": None,
            "verified": True, "status": "runtime_verified"}


def _judged(rec):
    return patch_risk_gate.judge(rec, project_root=os.getcwd())


# ---------------------------------------------- 1) 删类型注解 ⇒ 拒
def test_annotation_only_patch_rejected(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_NO_SEMANTIC_GATE", True, raising=False)
    monkeypatch.setattr(config, "M485_DOC_COMMENT_ISSUE_TYPES", (), raising=False)
    rec = _rec("def calc(x: int) -> int:\n    return x * 2 + 1\n",
               "def calc(x):\n    return x * 2 + 1\n")
    j = _judged(rec)
    assert j.get("m485_reject_reason") == "no_semantic_change", j
    assert j["level"] == "medium" and j["eligible"] is False, j


# ---------------------------------------------- 2) 只改注释标点 ⇒ 拒（坑二）
def test_comment_punct_patch_rejected(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_NO_SEMANTIC_GATE", True, raising=False)
    monkeypatch.setattr(config, "M485_DOC_COMMENT_ISSUE_TYPES", (), raising=False)
    rec = _rec(_ORIG + "# 计算翻倍, 加一\n",
               _ORIG + "# 计算翻倍，加一。\n")
    j = _judged(rec)
    assert j["eligible"] is False and j["level"] == "medium", j
    assert j.get("m485_reject_reason") in ("comment_only_non_doc_issue",
                                           "no_semantic_change"), j


# ---------------------------------------------- 3) 真修 bug ⇒ 406 口径不变
def test_real_fix_still_judged_normally(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_NO_SEMANTIC_GATE", True, raising=False)
    rec = _rec(_ORIG, "def calc(x):\n    return x * 2 + 2\n")
    j = _judged(rec)
    assert not j.get("m485_reject_reason"), j
    assert j["diff_kind"] == "logic" and j["level"] == "medium", j
    assert j["eligible"] is False, "logic 本来就不 eligible（406 口径不变）"


# ---------------------------------------------- 4) 开关关 ⇒ 逐字旧口径
def test_switch_off_old_semantics(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_NO_SEMANTIC_GATE", False, raising=False)
    rec = _rec(_ORIG + "# a, b\n", _ORIG + "# a，b。\n")
    j = _judged(rec)
    assert not j.get("m485_reject_reason"), "开关关 ⇒ 无 485 拒因"
    assert j["diff_kind"] == "comment_only" and j["eligible"] is True, j
    # 注解用例：开关关时 _skeleton 会判 logic（注解在 AST 里）——这正是要补的洞
    rec2 = _rec("def calc(x: int) -> int:\n    return x * 2 + 1\n",
                "def calc(x):\n    return x * 2 + 1\n")
    j2 = _judged(rec2)
    assert j2["diff_kind"] == "logic" and j2["eligible"] is False, j2


# ---------------------------------------------- 5) doc 白名单放行注释补丁
def test_doc_whitelist_allows_comment_patch(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_NO_SEMANTIC_GATE", True, raising=False)
    monkeypatch.setattr(config, "M485_DOC_COMMENT_ISSUE_TYPES",
                        ("doc_tuning",), raising=False)
    rec = _rec(_ORIG + "# a, b\n", _ORIG + "# a，b。\n", issue_type="doc_tuning")
    j = _judged(rec)
    assert not j.get("m485_reject_reason"), j
    assert j["eligible"] is True, "doc 类 issue_type 的 comment_only 放行"


# ---------------------------------------------- 6) doc_only 目标不受影响
def test_doc_only_target_unchanged(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_NO_SEMANTIC_GATE", True, raising=False)
    rec = _rec("旧说明\n", "新说明\n", fname="docs/说明.md")
    j = _judged(rec)
    assert j["diff_kind"] == "doc_only", j
    assert not j.get("m485_reject_reason"), ".md 目标不走代码语义闸"


# ---------------------------------------------- 7) gate_allows 联动拦住
def test_gate_allows_blocks_zero_semantic(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_NO_SEMANTIC_GATE", True, raising=False)
    monkeypatch.setattr(config, "PATCH_M406_NARROW_GATE", True, raising=False)
    monkeypatch.setattr(config, "ENABLE_M485_ISSUE_MATCH_GATE", False, raising=False)
    rec = _rec(_ORIG + "# a, b\n", _ORIG + "# a，b。\n")
    allowed, why = patch_risk_gate.gate_allows(rec, project_root=os.getcwd())
    assert allowed is False and "485" in why, (allowed, why)
