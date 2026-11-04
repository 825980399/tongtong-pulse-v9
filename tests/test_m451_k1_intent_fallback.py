# -*- coding: utf-8 -*-
"""451 刀1：高频意图规则兜底（规则逐条 + 知识闸 + PROTECTED + QICA 接线）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus.qica import intent_fallback as IF


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M451_INTENT_FALLBACK", True, raising=False)


# ---------------------------------------------------------------- 四类高频意图
@pytest.mark.parametrize("text,intent,rule", [
    ("现在几点了？", "时间日程", "hf_time"),
    ("你是什么时候开始运行的", "时间日程", "hf_time"),
    ("那你在读取你的运行日志信息的时候你会不会关注哪些不同级别的日志？", "元认知报告", "hf_log"),
    ("刚才的错误日志里有什么", "元认知报告", "hf_log"),
    ("那你在修改你自身的代码的时候是怎么做的？", "技术推理", "hf_code"),
    ("这个函数报错了怎么办", "技术推理", "hf_code"),
    ("你是谁呀", "身份确认", "hf_identity"),
    ("你叫什么名字", "身份确认", "hf_identity"),
])
def test_四类高频意图能分类(text, intent, rule):
    assert IF.classify(text) == (intent, rule), IF.classify(text)


def test_判不出时返回空():
    assert IF.classify("你有没有发现自己哪里有不流畅的地方？") == ("", "")
    assert IF.classify("") == ("", "")


# ---------------------------------------------------------------- 知识查询闸
@pytest.mark.parametrize("text", [
    "什么是日志", "日志是什么", "怎么理解日志级别", "介绍一下日志系统",
    "代码和脚本的区别", "两种方案对比",
])
def test_知识查询闸让位(text):
    """★"什么是日志"必须留在知识查询，不能被改成元认知报告。"""
    assert IF.classify(text) == ("", ""), IF.classify(text)


# ---------------------------------------------------------------- PROTECTED
@pytest.mark.parametrize("cur", ["任务执行", "子代理任务", "否定质疑", "健康检查"])
def test_强指令类不可覆盖(cur):
    assert IF.may_override(cur) is False


@pytest.mark.parametrize("cur", ["情感问候", "一般对话", "系统命令", "身份确认", "状态查询", ""])
def test_其余意图可被纠正(cur):
    assert IF.may_override(cur) is True


def test_开关关掉不分类(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M451_INTENT_FALLBACK", False, raising=False)
    assert IF.classify("现在几点了？") == ("", "")


def test_目标意图都在既有词表内():
    """★不新造意图名：否则 INTENT_TO_PATH / INTENT_TO_METHOD 两张表都要跟着漂。"""
    from nucleus.qica.IntentChannels import ALL_INTENTS
    for _r in IF.rules_snapshot():
        assert _r["intent"] in ALL_INTENTS, _r


# ---------------------------------------------------------------- QICA 接线
def _qica():
    from nucleus.qica.QICA import QICA
    return QICA()


def test_接线_情感问候被纠成元认知报告():
    q = _qica()
    anchor = {"task_type": "情感问候", "raw_input": "那你在读取你的运行日志信息的时候…"}
    out = q._apply_intent_refine("那你在读取你的运行日志信息的时候你会不会关注哪些不同级别的日志？",
                                 anchor)
    assert out["task_type"] == "元认知报告", out.get("task_type")
    assert out["refined_by"] == "high-freq:hf_log", out.get("refined_by")
    assert out["refined"] is True


def test_接线_强指令类不被推翻():
    q = _qica()
    anchor = {"task_type": "任务执行", "raw_input": "去读日志"}
    out = q._apply_intent_refine("去把运行日志读一遍", anchor)
    assert out["task_type"] == "任务执行", out.get("task_type")


def test_接线_知识查询不被改写():
    q = _qica()
    anchor = {"task_type": "情感问候", "raw_input": "什么是日志"}
    out = q._apply_intent_refine("什么是日志", anchor)
    assert out["task_type"] == "情感问候", out.get("task_type")


def test_接线后路由跟着变():
    """意图改了，channel/method/knowledge_paths 必须一起重算（不能只改标签）。"""
    q = _qica()
    anchor = {"task_type": "一般对话", "raw_input": "x"}
    out = q._apply_intent_refine("那你在修改你自身的代码的时候是怎么做的？", anchor)
    assert out["task_type"] == "技术推理", out.get("task_type")
    assert out["suggested_method"] == "cognitive_compute", out.get("suggested_method")
    assert out["channel"] == "deep", out.get("channel")
