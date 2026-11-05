# -*- coding: utf-8 -*-
"""454 刀1：计划 v3 加法扩展（只生成不执行算子）。"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from organs.brain.PulseInnerWorld import PulseInnerWorld as IW

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IW_SRC = os.path.join(ROOT, "organs", "brain", "PulseInnerWorld.py")


def _base(names):
    return [{"name": n, "prompt": "p-" + n, "fallback": "f-" + n} for n in names]


def test_对比类计划至少一步非retrieve():
    plan = IW._m454_annotate_plan(
        _base(["了解A", "了解B", "对比分析", "结论建议"]), "对比 A 和 B 的区别")
    ops = [s["op"] for s in plan]
    assert any(o != "retrieve" for o in ops), ops
    assert ops == ["retrieve", "retrieve", "compare", "exclude"], ops


def test_计算类计划有quantify():
    plan = IW._m454_annotate_plan(
        _base(["数据提取", "计算验证", "结果输出"]), "计算 3 加 5 等于多少")
    assert [s["op"] for s in plan] == ["retrieve", "quantify", "retrieve"]


def test_排除类计划有exclude():
    plan = IW._m454_annotate_plan(
        _base(["基础了解", "汇总整理", "总结输出"]), "哪个不是审批桥的闸门，请排除")
    assert "exclude" in [s["op"] for s in plan]


def test_通用型保持全retrieve_不为验收硬标():
    # ★烛微 455 前置 §C1：给「总结输出」硬贴 compare 是假标签，本项目不这么做
    plan = IW._m454_annotate_plan(
        _base(["基础了解", "深入分析", "总结输出"]), "随便聊聊曈曈的近况")
    assert [s["op"] for s in plan] == ["retrieve"] * 3


def test_旧字段逐字不变():
    base = _base(["了解A", "对比分析", "结论建议"])
    plan = IW._m454_annotate_plan(base, "对比 A 和 B")
    for a, b in zip(base, plan):
        for k in ("name", "prompt", "fallback"):
            assert a[k] == b[k], (k, a, b)
    assert all({"name", "prompt", "fallback"} <= set(s) for s in plan)


def test_五个新字段齐全且依赖成链():
    plan = IW._m454_annotate_plan(_base(["了解A", "对比分析"]), "对比 A 和 B")
    assert [s["step_id"] for s in plan] == ["S1", "S2"]
    assert plan[0]["depends_on"] == [] and plan[1]["depends_on"] == ["S1"]
    for s in plan:
        for k in ("op", "goal", "expected_evidence"):
            assert s.get(k), k
    assert plan[1]["expected_evidence"] == "对照维度"
    assert "用户原文" not in str(plan)       # goal 不含问题全文


def test_op枚举不含hypothesize():
    plans = []
    for q, names in (("对比 A B", ["对比分析"]), ("计算多少", ["计算验证"]),
                      ("排除哪个不是", ["汇总整理"]), ("查资料", ["汇总整理"])):
        plans.append(IW._m454_annotate_plan(_base(names), q))
    ops = {s["op"] for p in plans for s in p}
    assert ops <= {"retrieve", "compare", "exclude", "quantify"}, ops
    assert "hypothesize" not in ops


def test_签名不变且开关可回退(monkeypatch):
    sig = inspect.signature(IW._generate_task_plan)
    assert list(sig.parameters) == ["self", "question"]
    inst = object.__new__(IW)
    inst._generate_task_plan_base = lambda q: _base(["甲", "乙"])
    monkeypatch.setattr(config, "ENABLE_M454_PLAN_V3", True, raising=False)
    assert "step_id" in inst._generate_task_plan("x")[0]
    monkeypatch.setattr(config, "ENABLE_M454_PLAN_V3", False, raising=False)
    assert inst._generate_task_plan("x") == _base(["甲", "乙"])


def test_算子本批不执行_只记trace():
    with open(IW_SRC, encoding="utf-8") as f:
        src = f.read()
    assert '"op_executed": "retrieve"' in src       # 如实标明本批未执行算子
    assert '"op_planned":' in src
    # 执行器循环里没有调用本地算子
    assert "local_ops.compare(" not in src


def test_多步执行器旁路写set_plan():
    with open(IW_SRC, encoding="utf-8") as f:
        src = f.read()
    assert "_m454_tr.set_plan(" in src
    assert "input_digest" in src and "output_digest" in src
