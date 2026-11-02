# -*- coding: utf-8 -*-
"""415 刀4 单测：经验命中拼 prompt 全链路 + 截断 + 开关双向。

被测对象：`organs/body/PulseLung.py::_m415_build_experience_fragment`
口径（任务书原文）：top3 / 每条 ≤100 字 / 合计 ≤500 字 / 开关默认 False（影子）。

★423 刀2 起默认值已由任务书改为 **True（实弹）**，两条相关断言随之调整：
  - `test_switch_exists_and_default_on_since_423`：钉新规格（回滚开关时必须同步改这里）；
  - `test_shadow_returns_empty_but_reports_numbers`：改为**显式关闸**再验影子分支，
    不再依赖全局默认值 —— 否则默认值一动，这条行为用例就会假红。

★本批探针实测：真实经验池对「怎么优化代码质量」等三问**全部 0 命中**
  （`score_one` 是中文 2~4 字词片**整段切**，池内 summary 全是内部动机叙述）。
  所以这里的"全链路"用**注入 top_hits 返回值**来验，别依赖生产池内容 ——
  否则测试会因为池子内容变化而随机红（铁律 3：断言钉语义不钉历史快照）。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
import organs.body.PulseLung as PL

SWITCH = "ENABLE_M415_EXPERIENCE_IN_PROMPT"
HEADER = "【相关经验参考】"


def _hits(*texts, weight=1.0):
    return [{"id": f"exp_{i}", "text": t, "weight": weight, "score": 1, "is_template": False}
            for i, t in enumerate(texts)]


# ---------------------------------------------------------------- 开关
def test_switch_exists_and_default_on_since_423():
    """423 刀2：开关由影子（False）转实弹（True）。

    ★这是**规格断言**：要回滚成影子，必须同时改 config.py 与这条用例，
      避免"默认值悄悄变了没人发现"。
    """
    assert hasattr(config, SWITCH), "config 里必须有这个开关"
    assert isinstance(getattr(config, SWITCH), bool), "开关必须是 bool，别用 0/1 糊"
    assert getattr(config, SWITCH) is True, "423 刀2 起默认 True；回滚需同步改本用例"


def test_switch_reader_false_when_missing(monkeypatch):
    monkeypatch.delattr(config, SWITCH, raising=False)
    assert PL._m415_experience_in_prompt_on() is False


# ---------------------------------------------------------------- 影子 vs 实弹
def test_shadow_returns_empty_but_reports_numbers(monkeypatch):
    """影子模式：fragment 为空 ⇒ `prompt += ""` 逐字不变；但 obs 如实报字数。

    ★显式关闸：本用例验的是"关闸时行为"，不该依赖 config 的全局默认值。
    """
    monkeypatch.setattr(config, SWITCH, False, raising=False)
    frag, obs = PL._m415_build_experience_fragment("今天感觉怎么样", hits=_hits("经验甲", "经验乙"))
    assert frag == ""
    assert obs["on"] is False
    assert obs["n_hits"] == 2 and obs["n_used"] == 2
    assert obs["chars"] > 0


def test_live_mode_builds_fragment(monkeypatch):
    monkeypatch.setattr(config, SWITCH, True, raising=False)
    frag, obs = PL._m415_build_experience_fragment("今天感觉怎么样", hits=_hits("经验甲", "经验乙"))
    assert obs["on"] is True
    assert frag.startswith("\n" + HEADER)
    assert "1. 经验甲" in frag and "2. 经验乙" in frag
    assert frag.endswith("\n")


# ---------------------------------------------------------------- 截断
def test_item_truncated_to_100_chars(monkeypatch):
    monkeypatch.setattr(config, SWITCH, True, raising=False)
    long_text = "甲" * 260
    frag, obs = PL._m415_build_experience_fragment("q", hits=_hits(long_text))
    body = frag.strip().split("\n")[1]
    assert body.startswith("1. ")
    assert len(body[3:]) == 100, "每条最多 100 字"
    assert obs["chars"] == len(body)


def test_three_hits_total_309_chars(monkeypatch):
    """3 条 × (100 字 + "N. ") = 309 字 ⇒ 500 预算不会被顶到（如实记账）。"""
    monkeypatch.setattr(config, SWITCH, True, raising=False)
    _frag, obs = PL._m415_build_experience_fragment("q", hits=_hits("甲" * 200, "乙" * 200, "丙" * 200))
    assert obs["n_used"] == 3
    assert obs["chars"] == 309
    assert obs["dropped"] == 0
    assert obs["chars"] <= PL._M415_EXP_TOTAL_CHARS


def test_total_budget_500_stops(monkeypatch):
    """注入 10 条（绕过 top3）验 500 字硬预算：第 5 条顶破 ⇒ 停，记 dropped。"""
    monkeypatch.setattr(config, SWITCH, True, raising=False)
    frag, obs = PL._m415_build_experience_fragment("q", hits=_hits(*["甲" * 200] * 10))
    assert obs["chars"] <= PL._M415_EXP_TOTAL_CHARS
    assert obs["n_used"] == 4
    assert obs["dropped"] == 6
    assert frag.count("\n1. ") + frag.count("\n2. ") + frag.count("\n3. ") == 3


def test_top_k_constant_is_three():
    assert PL._M415_EXP_TOP_K == 3
    assert PL._M415_EXP_ITEM_CHARS == 100
    assert PL._M415_EXP_TOTAL_CHARS == 500


# ---------------------------------------------------------------- 边界输入
def test_empty_question_no_retrieval(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("空问题不该去查池")
    monkeypatch.setattr("nucleus.observability.experience_hits.top_hits", _boom, raising=False)
    frag, obs = PL._m415_build_experience_fragment("   ")
    assert frag == "" and obs["n_hits"] == 0


def test_no_hits(monkeypatch):
    monkeypatch.setattr(config, SWITCH, True, raising=False)
    frag, obs = PL._m415_build_experience_fragment("q", hits=[])
    assert frag == "" and obs["n_hits"] == 0 and obs["n_used"] == 0


def test_bad_hits_are_skipped(monkeypatch):
    monkeypatch.setattr(config, SWITCH, True, raising=False)
    frag, obs = PL._m415_build_experience_fragment("q", hits=[None, "裸字符串", {"text": ""}, {"text": "好的"}])
    assert obs["n_hits"] == 4
    assert obs["n_used"] == 1 and obs["dropped"] == 3
    assert "好的" in frag


def test_weight_fallback_on_bad_value(monkeypatch):
    monkeypatch.setattr(config, SWITCH, True, raising=False)
    frag, obs = PL._m415_build_experience_fragment("q", hits=[{"text": "甲", "weight": "不是数"}])
    assert obs["weights"] == [1.0]
    assert frag


# ---------------------------------------------------------------- 全链路（query → 检索 → 拼 prompt）
def test_full_chain_passes_clean_query_and_top3(monkeypatch):
    """★拼 prompt 时必须用**用户问题原文**当 query，不能把拼好的 prompt 塞进去。"""
    seen = {}

    def _fake_top_hits(q, top_k=3, **kw):
        seen["q"], seen["top_k"] = q, top_k
        return _hits("先看日志再改代码", weight=0.3)

    monkeypatch.setattr("nucleus.observability.experience_hits.top_hits", _fake_top_hits, raising=False)
    monkeypatch.setattr(config, SWITCH, True, raising=False)
    frag, obs = PL._m415_build_experience_fragment("怎么优化代码质量")
    assert seen["q"] == "怎么优化代码质量"
    assert seen["top_k"] == 3
    assert obs["n_hits"] == 1 and obs["weights"] == [0.3]
    assert "先看日志再改代码" in frag


def test_top_hits_failure_degrades_silently(monkeypatch):
    def _boom(*a, **k):
        raise TypeError("签名变了")
    monkeypatch.setattr("nucleus.observability.experience_hits.top_hits", _boom, raising=False)
    monkeypatch.setattr(config, SWITCH, True, raising=False)
    frag, obs = PL._m415_build_experience_fragment("q")
    assert frag == "" and obs["n_hits"] == 0


def test_shadow_never_leaks_header(monkeypatch):
    """开关关 ⇒ 任何情况下都不出现段落标题（逐字不变的硬保证）。"""
    monkeypatch.setattr(config, SWITCH, False, raising=False)
    for hs in (_hits("甲"), _hits("甲" * 300), []):
        frag, _ = PL._m415_build_experience_fragment("q", hits=hs)
        assert HEADER not in frag and frag == ""


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
