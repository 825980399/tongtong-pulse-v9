# -*- coding: utf-8 -*-
"""453R 刀1：预算只在有证据时生效（34/24/8），无证据走原渠道超时。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus import evidence_injection as EI

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LUNG = os.path.join(ROOT, "organs", "body", "PulseLung.py")


def _src():
    with open(LUNG, encoding="utf-8") as f:
        return f.read()


def test_默认预算是34_24_8():
    b = EI.Budget(now=1000.0)
    assert (b.total, b.first, b.retry, b.fallback) == (34.0, 24.0, 8.0, 0.2)
    assert b.channel_timeout(30.0, now=1000.0, stage=1) == 24.0
    assert b.channel_timeout(30.0, now=1024.0, stage=2) == 8.0


def test_config读数是34_24_8_80():
    assert config.M453_TOTAL_BUDGET_SEC == 34.0
    assert config.M453_FIRST_TRY_SEC == 24.0
    assert config.M453_RETRY_SEC == 8.0
    c = EI.budget_config()
    assert c == {"total": 34.0, "first": 24.0, "retry": 8.0, "fallback": 0.2}


def test_预算创建在fragment非空之后():
    src = _src()
    i_gate = src.index('if _inj453["fragment"]:')
    i_budget = src.index("_m453_budget = _ei453.Budget(**_ei453.budget_config())")
    i_no = src.index('_ei453.note("no_evidence")')
    assert i_gate < i_budget, "预算又跑到 fragment 判空之前了（P0-1 回潮）"
    assert i_no > i_budget, "no_evidence 分支应位于 if 之外"


def test_无证据分支不设预算且不改skip_l2():
    src = _src()
    # else 分支里只有 note("no_evidence")，不能有 Budget / skip_l2 赋值
    i_no = src.index('_ei453.note("no_evidence")')
    i_next = src.index('_ei453.note("injection_on")', i_no)
    seg = src[i_no:i_next]
    assert "Budget(" not in seg
    assert '["skip_l2"]' not in seg


def test_初始状态budget为None():
    st = EI.fresh_state(origin="user_query")
    assert st["budget"] is None
    assert st["injected"] is False
    EI.end_request_state()


def test_无证据时不注入且fragment为空(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M453R_SOURCE_GATE", False, raising=False)
    r = EI.build_injection([], now=1000.0)
    assert r["fragment"] == "" and r["reason"] == "no_usable_evidence"
    assert EI.attach("原始问题", r["fragment"]) == "原始问题"


def test_有证据时fragment非空才该建预算(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M453R_SOURCE_GATE", False, raising=False)
    items = [{"text": "审批桥三闸需上游供数", "source_class": "qa",
              "score": 0.71, "quality_weight": 0.9},
             {"text": "补丁链漏斗作废十七", "source_class": "qa",
              "score": 0.66, "quality_weight": 0.5}]
    r = EI.build_injection(items, now=1000.0)
    assert r["fragment"] and r["reason"] == "ok"
    b = EI.Budget(**EI.budget_config())
    assert b.total == 34.0 and b.first == 24.0


def test_渠道clamp只在budget存在时生效():
    src = _src()
    i = src.index("_m453_b = None", src.index("def _call_channel("))
    assert "_st453cl.get(\"budget\")" in src[i:i + 500]
    assert "callable(_m453_clamp)" in src
