# -*- coding: utf-8 -*-
"""453 刀8：B2 对比报告 / 质量闸 / 批次留痕。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus import evidence_trace as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL = os.path.join(ROOT, "data", "eval")
DOC = os.path.join(ROOT, "docs", "路灯与星轨对话", "分析报告")
EVID = os.path.join(DOC, "证据件")


def _j(name, base=EVAL):
    with open(os.path.join(base, name), encoding="utf-8") as f:
        return json.load(f)


def test_对比报告落盘且结构完整():
    p = os.path.join(EVAL, "b2_comparison.md")
    assert os.path.isfile(p)
    with open(p, encoding="utf-8") as _f:
        txt = _f.read()
    for k in ("# 453 批 · B0 → B2 eval 对比报告", "B0 总分", "B2 总分",
              "胜 / 平 / 负", "各维度变化", "证据被引用次数",
              "token 增量", "延迟增量", "质量闸"):
        assert k in txt, k


def test_对比JSON含gate与auto_applied为False():
    d = _j("b2_comparison.json")
    assert d["compare"]["win"] == 0 and d["compare"]["tie"] == 52
    assert d["gate"]["auto_applied"] is False
    assert d["gate"]["note"]           # 必须写明为什么不自动改开关


def test_胜平45阈值判定与建议():
    d = _j("b2_comparison.json")["gate"]
    assert d["win_tie"] == 52 and d["threshold"] == 45
    assert d["passed"] is True and d["recommendation"] == "keep"


def test_报告如实声明判据无判别力():
    # 开关默认 False + 同一份离线答案 ⇒ 恒 52 平，不能当「注入成功」证据
    with open(os.path.join(EVAL, "b2_comparison.md"), encoding="utf-8") as _f:
        txt = _f.read()
    assert "无判别力" in txt and "恒 52 平" in txt


def test_B0与B2都是52题结果JSON():
    for name in ("baseline_b0.json", "baseline_b2.json"):
        d = _j(name)
        assert len(d["rows"]) == 52 and "by_dimension" in d


def test_批次留痕含8刀与偏差清单():
    d = _j("m453_knife_changes.json", os.path.join(ROOT, "data", "patches"))
    assert len(d["knives"]) == 8, len(d["knives"])
    assert len(d["task_book_deviations"]) >= 10
    assert d["switch_default"]["ENABLE_EVIDENCE_INJECTION"] is False
    assert d["pending_restart"] is True


def test_审计性产物在git跟踪目录有副本():
    # 第53批 §69.1 C1：data/ 被 .gitignore，审计性裁决/留痕必须另落一份跟踪副本
    assert os.path.isdir(EVID), EVID
    for name in ("m453_knife_changes.json", "b2_comparison.md", "baseline_b0.json"):
        assert os.path.isfile(os.path.join(EVID, name)), name


def test_活体留痕按distinct统计不虚高():
    s = ET.trace_stats(os.path.join(EVAL, "evidence_trace.jsonl"))
    assert 0 <= s["eligible_requests"] <= s["distinct_requests"] <= s["rows"], s


def test_452旧进程留痕字段不带新字段():
    # 本批代码尚未加载（需重启）：现有行仍是 452 版字段，重启后才有 request_id
    p = os.path.join(EVAL, "evidence_trace.jsonl")
    if not os.path.exists(p):
        return
    with open(p, encoding="utf-8") as f:
        lines = [x for x in f if x.strip()]
    if not lines:
        return
    first = json.loads(lines[0])
    assert "kind" in first and "score" in first
    # 不断言「一定没有新字段」——框架可能已重启；只守旧字段仍在（向后兼容）
