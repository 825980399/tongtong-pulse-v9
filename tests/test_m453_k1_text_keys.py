# -*- coding: utf-8 -*-
"""453 刀1：dry-run 正文提取（经验池 motivation/summary）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus import evidence_trace as ET
from nucleus.mnemosyne.experience_retriever import experience_text


def _exp_hit():
    return {"motivation": "维持系统平衡", "summary": "动机循环内部评估",
            "_score": 0.8, "is_template": True,
            "result_reward": {"type": "cognitive", "intensity": 0.36},
            "pollution_risk": "medium", "activation_count": 28676}


def test_文本键补齐四个经验池字段():
    for k in ("motivation", "summary", "explanation", "hypothesis"):
        assert k in ET._TEXT_KEYS, k
    # 452 已加的 example_question 不能丢（任务书写「只有 5 个键」与实测不符）
    assert "example_question" in ET._TEXT_KEYS


def test_经验池正文不再是空串():
    # 烛微 §1.1 实测：452 版 build_evidence_block 返回 text=''
    b = ET.build_evidence_block(lambda q, k: [_exp_hit()], "测试", min_score=0.6)
    assert len(b) == 1
    assert b[0]["text"].strip(), b


def test_正文按motivation加summary拼装():
    assert ET._text_of(_exp_hit()) == "维持系统平衡。动机循环内部评估"


def test_与检索器experience_text逐字同口径():
    # 用真实生产函数当基准，不用被测函数的输出自证（铁律 30）
    assert ET._text_of(_exp_hit()) == experience_text(_exp_hit())


def test_通用单键形态不被破坏():
    assert ET._text_of({"text": "A 正文", "summary": "不该被拼上"}) == "A 正文"
    assert ET._text_of({"content": "C 正文"}) == "C 正文"


def test_explanation与hypothesis也认():
    t = ET._text_of({"explanation": "因为 A", "hypothesis": "假设 B"})
    assert "因为 A" in t and "假设 B" in t


def test_result_reward是dict时取intensity():
    assert ET._reward_of(_exp_hit()) == pytest.approx(0.36)
    assert ET._reward_of({"result_reward": 0.7}) == pytest.approx(0.7)
    assert ET._reward_of({"result_reward": None}) == 0.0


def test_质量字段随命中一起返回():
    b = ET.build_evidence_block(lambda q, k: [_exp_hit()], "测试", min_score=0.6)
    r = b[0]
    for k in ("quality_weight", "is_template", "result_reward", "pollution_risk"):
        assert k in r, k
    assert r["is_template"] is True
    assert r["result_reward"] == pytest.approx(0.36)


def test_模板权重被压到非模板之下():
    good = {"text": "审批桥三闸需上游供数", "score": 0.7,
            "result_reward": {"intensity": 0.9}}
    b = ET.build_evidence_block(lambda q, k: [_exp_hit(), good], "x",
                                min_score=0.6, top_k=2)
    assert len(b) == 2
    assert b[0]["text"] == "审批桥三闸需上游供数", b
    assert b[0]["quality_weight"] > b[1]["quality_weight"]


def test_低分仍被过滤且开关关闭归零(monkeypatch):
    low = {"text": "低分", "score": 0.2}
    assert ET.build_evidence_block(lambda q, k: [low], "x", min_score=0.65) == []
    monkeypatch.setattr("config.ENABLE_M452_EVIDENCE_TRACE", False, raising=False)
    assert ET.build_evidence_block(lambda q, k: [_exp_hit()], "x") == []
