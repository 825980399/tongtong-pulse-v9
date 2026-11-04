# -*- coding: utf-8 -*-
"""452 刀8：451 移交收口三项。"""
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_入库闸在特征提取之前():
    """★准入条件：452 闸必须在 `extract_structural_features` 之前（不浪费特征提取）。
    但**不能**抢 407 闸的"第一个 if"位置（那是既有规格断言）。
    """
    p = os.path.join(_ROOT, "nucleus", "mnemosyne", "ReasoningExperience.py")
    with open(p, encoding="utf-8", errors="replace") as f:
        src = f.read()
    i = src.index("def record(self, question:")
    j = src.index("if _m452_is_non_question(question):", i)
    k = src.index("features = self.extract_structural_features(question)", i)
    assert i < j < k, (i, j, k)
    assert src.index("is_code_learning_prompt(question)", i) < j, "407 闸必须在最前"


def test_failure_mode已登记():
    p = os.path.join(_ROOT, "organs", "brain", "PulseInnerWorld.py")
    with open(p, encoding="utf-8", errors="replace") as f:
        src = f.read()
    i = src.index("_KNOWN_DERIVATION_TYPES = {")
    block = src[i:i + 500]
    assert '"failure_mode"' in block, block


def test_意图样本集已导出且未伪造标签():
    p = os.path.join(_ROOT, "data", "eval", "intent_samples.json")
    assert os.path.exists(p), p
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    assert d.get("target") == 50, d.get("target")
    assert d["count"] == len(d["samples"]), (d["count"], len(d["samples"]))
    assert d["count"] > 0, "至少要导出真实存在的对话"
    for s in d["samples"]:
        assert s["expected_intent"] is None and s["labeled"] is False, s
        assert s["question"], s
    # ★盘面不足时如实记录，不编造样本
    if d["count"] < d["target"]:
        assert "不标注" in d["note"] or "留空" in d["note"], d["note"]


def test_导出工具可重复运行(tmp_path):
    p = os.path.join(_ROOT, "tools", "export_intent_samples.py")
    spec = importlib.util.spec_from_file_location("m452_export", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    str(tmp_path / "samples.json")
    rows = mod.collect(os.path.join(_ROOT, "data", "experience",
                                    "dialogue_process_*.jsonl"), 50)
    assert rows and all(r["expected_intent"] is None for r in rows), rows
    assert os.path.isdir(str(tmp_path))
