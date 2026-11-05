# -*- coding: utf-8 -*-
"""453R 刀4：真 tokenizer 实测 token 增量（诚实口径，不估算）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus import evidence_injection as EI

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_参数是80字与30字以内system规则():
    assert config.M453_EVIDENCE_MAX_CHARS == 80
    assert config.M453_EVIDENCE_TOP_K == 2
    assert len(EI.SYSTEM_EVIDENCE_RULE) <= 30, len(EI.SYSTEM_EVIDENCE_RULE)


def test_user_prompt只剩数据块不含说明段(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M453R_SOURCE_GATE", False, raising=False)
    r = EI.build_injection([{"text": "审批桥三闸需上游供数", "score": 0.71}],
                           now=1000.0)
    assert "<evidence" in r["fragment"]
    assert "以下是历史经验记忆" not in r["fragment"]
    assert "仅供参考" not in r["fragment"]
    assert r["system_rule"] == EI.SYSTEM_EVIDENCE_RULE


def test_token实测返回完整字段():
    m = EI.measure_token_delta("请分析审批桥八道闸。", "\n\n<evidence id=\"E1\" source=\"x\" score=\"0.71\">结论</evidence>")
    assert m["available"] is True, m
    for k in ("encoding", "base_tokens", "full_tokens", "added_tokens", "pct", "passed"):
        assert k in m, k
    assert m["encoding"] == "cl100k_base"


def test_真实经验短结论增量达标(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M453R_SOURCE_GATE", False, raising=False)
    base = "请分析审批桥八道闸为什么连续建议查审批，而真实瓶颈在生成侧质量。" * 12
    frag = EI.build_injection([
        {"text": "回答方法：multi_step_execute，置信度：0.8", "score": 0.72},
        {"text": "回答方法：rule，置信度：1.0", "score": 0.71},
    ], now=1000.0)["fragment"]
    m = EI.measure_token_delta(base, frag)
    if not m["available"]:
        pytest.skip("tiktoken 离线缓存不可用")
    assert m["added_tokens"] > 0
    assert m["pct"] <= 15.0, m          # 实测口径：相对 base_tokens


def test_最坏2x80中文会超门_如实钉住():
    """★诚实口径：2×80 全中文最坏情况 **必然** 超 +15%。

    这条断言不是「期望失败」，而是把「任务书验收做不到」这件事钉在测试里，
    防止将来有人悄悄改判据掩盖。交付报告 §4 给出定量调整建议。
    """
    base = "请分析审批桥八道闸为什么连续建议查审批，而真实瓶颈在生成侧质量。" * 12
    frag = "\n\n" + "\n".join(
        f'<evidence id="E{i}" source="organs/a.py" score="0.71">'
        f'{"知识" * 40}</evidence>' for i in (1, 2))
    m = EI.measure_token_delta(base, frag)
    if not m["available"]:
        pytest.skip("tiktoken 离线缓存不可用")
    assert m["passed"] is False, m
    assert m["pct"] > 15.0, m


def test_token不可用时不编造(monkeypatch):
    import builtins
    _real = builtins.__import__

    def _no_tiktoken(name, *a, **k):
        if name == "tiktoken":
            raise ImportError("mocked unavailable")
        return _real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_tiktoken)
    m = EI.measure_token_delta("abc", "def")
    assert m["available"] is False
    assert "added_tokens" not in m
