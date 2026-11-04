# -*- coding: utf-8 -*-
"""453 刀5：B0 eval 基线 + eval_runner 双形态输入 / 3 次中位数。"""
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(ROOT, "tools", "eval_runner.py")
spec = importlib.util.spec_from_file_location("m453_eval_runner", RUNNER)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

B0 = os.path.join(ROOT, "data", "eval", "baseline_b0.json")
AFTER = os.path.join(ROOT, "data", "eval", "baseline_b2.json")
REPORT = os.path.join(ROOT, "data", "eval", "b2_comparison.md")


def _b0():
    with open(B0, encoding="utf-8") as f:
        return json.load(f)


def test_b0基线文件存在():
    assert os.path.isfile(B0), B0
    assert os.path.getsize(B0) > 1000


def test_b0覆盖52题与8维度():
    d = _b0()
    assert len(d["rows"]) == 52, len(d["rows"])
    assert d["max"] == 52.0
    assert len(d["by_dimension"]) >= 8, d["by_dimension"]


def test_b0三次取中位数且如实标注完全相同():
    d = _b0()
    assert d["repeat"] == 3
    assert d["identical"] is True          # 离线答案固定，绝不伪造差异
    assert len(d["runs"]) == 3
    assert all(r["score_runs"] == [r["score"]] * 3 for r in d["rows"])


def test_b0是demo连通性基线而非能力基线():
    # --demo 全空答案 ⇒ 总分 0；报告必须写明这不是「模型水平」
    assert _b0()["total"] == 0.0
    assert _b0()["pct"] == 0.0


def test_load_score_source识别跑分结果JSON():
    kind, obj = runner.load_score_source(B0)
    assert kind == "result" and "rows" in obj and "total" in obj


def test_load_score_source识别答案文件(tmp_path):
    p = tmp_path / "answers.json"
    p.write_text(json.dumps({"q1": "甲", "q2": "乙"}), encoding="utf-8")
    kind, obj = runner.load_score_source(str(p))
    assert kind == "answers" and obj == {"q1": "甲", "q2": "乙"}


def test_结果JSON对比不会把dict当答案():
    # 452 版会把结果 JSON 整个当答案映射 ⇒ 52 题恒 0 分的假对比
    k1, v1 = runner.load_score_source(B0)
    k2, v2 = runner.load_score_source(AFTER)
    c = runner.compare(v1, v2) if k1 == k2 == "result" else {}
    assert c["tie"] == 52 and c["win"] == 0 and c["loss"] == 0, c


def test_run_repeated两次结果一致(monkeypatch):
    _meta, qs = runner.load_set(os.path.join(ROOT, "data", "eval", "eval_set_v1.json"))
    r = runner.run_repeated(qs, {}, adapter=None, repeat=2)
    assert r["repeat"] == 2 and r["identical"] is True
    assert len(r["rows"]) == 52


def test_gate质量闸双向():
    assert runner.gate_verdict({"win": 0, "tie": 45, "loss": 7})["passed"] is True
    assert runner.gate_verdict({"win": 0, "tie": 44, "loss": 8})["recommendation"] == "disable"
    assert runner.gate_verdict({"win": 0, "tie": 44, "loss": 8})["auto_applied"] is False


def test_对比报告含六项且缺的遥测写NA():
    assert os.path.isfile(REPORT)
    with open(REPORT, encoding="utf-8") as _f:
        txt = _f.read()
    for k in ("总分与胜平负", "各维度变化", "证据被引用次数", "token 增量",
              "延迟增量", "质量闸", "N/A", "无判别力"):
        assert k in txt, k
