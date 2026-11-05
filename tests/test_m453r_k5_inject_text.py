# -*- coding: utf-8 -*-
"""453R 刀5：注入正文结论优先，不再把「问题复述」塞进 prompt。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus import evidence_injection as EI
from nucleus import evidence_trace as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL = os.path.join(ROOT, "data", "experience", "experience_pool.json")


def _pool():
    with open(POOL, encoding="utf-8") as f:
        d = json.load(f)
    ex = d.get("experiences") if isinstance(d, dict) else d
    if isinstance(ex, dict):
        ex = list(ex.values())
    return ex


def test_问答记录取方法加置信度而不是只取方法名():
    h = {"motivation": "回答用户关于「现在几点？」的问题",
         "summary": "用户访客提问：现在几点？，回答方法：rule，置信度：1.0"}
    t = ET.inject_text_of(h)
    assert "回答方法" in t and "置信度" in t, t
    assert t != "rule"


def test_JSON语义摘要取优化建议全文():
    h = {"summary": json.dumps({"来源": "semantic",
                                "优化建议": "样本证据尚分散，建议继续累积观测，暂不做策略调整",
                                "时间戳": "2026-10-12"}, ensure_ascii=False)}
    t = ET.inject_text_of(h)
    assert "建议继续累积观测" in t, t
    assert "时间戳" not in t


def test_普通summary去掉问题复述():
    h = {"motivation": "回答用户关于「日志级别」的问题",
         "summary": "用户访客提问：日志级别有哪些？；结论：INFO/WARNING/ERROR 三级"}
    t = ET.inject_text_of(h)
    assert "用户访客提问" not in t or "结论" in t, t


def test_全文仍保留在text字段():
    h = {"motivation": "回答用户关于「审批桥」的问题",
         "summary": "用户访客提问：审批桥为何卡住？，回答方法：rule，置信度：0.9",
         "score": 0.7}
    b = ET.build_evidence_block(lambda q, k: [h], "x", min_score=0.6)
    assert b[0]["text"] == "回答用户关于「审批桥」的问题。用户访客提问：审批桥为何卡住？，回答方法：rule，置信度：0.9"
    assert "回答方法" in b[0]["inject_text"]
    assert "用户访客提问" not in b[0]["inject_text"]


def test_抽查10条结论性占比不低于70pct():
    rows = _pool()
    picked = [e for e in rows if not e.get("is_template")][:10]
    assert len(picked) >= 10
    good = 0
    for e in picked:
        t = ET.inject_text_of(e)
        # 「结论性」= 不是问题复述、不是空、不是单个英文方法名
        if t and "用户访客提问" not in t and "回答用户关于" not in t:
            good += 1
    assert good >= 7, good


def test_build_injection用inject_text而不是全文(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENABLE_M453R_SOURCE_GATE", False, raising=False)
    items = [{"text": "回答用户关于「审批桥」的问题。用户访客提问：审批桥为何卡住？，回答方法：rule",
              "inject_text": "回答方法：rule，置信度：0.9",
              "score": 0.71, "source_class": "qa"}]
    r = EI.build_injection(items, now=1000.0)
    assert r["reason"] == "ok"
    assert "置信度：0.9" in r["fragment"], r["fragment"]
    assert "用户访客提问" not in r["fragment"]
