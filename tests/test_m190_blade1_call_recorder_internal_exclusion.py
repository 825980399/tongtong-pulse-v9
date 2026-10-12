# -*- coding: utf-8 -*-
"""190刀1 隔离单测：188刀4剔除判据缺口修复（call_recorder 拒绝物化系统内部/内在模型产出）。

验收：ORIGIN_SYSTEM_INTERNAL 调用不物化入训练集；内在模型产出不物化；
正常 user_query 仍物化。零 LLM、零生产 IO（临时目录）。
"""
import json
import os
import tempfile

from nucleus.llm.call_recorder import (
    ORIGIN_SYSTEM_INTERNAL,
    ORIGIN_USER_QUERY,
    STATUS_SUCCESS,
    build_training_pair,
    convert_calls_to_training_pairs,
)


def _rec(origin, prompt="你好", response="我是曈曈", **extra):
    r = {
        "status": STATUS_SUCCESS,
        "prompt": prompt,
        "response": response,
        "origin": origin,
        "trace_id": "t1",
        "ts": 1,
        "model": "m",
        "channel": "c",
    }
    r.update(extra)
    return r


def test_system_internal_rejected():
    """系统内部调用（自检/监控/后台学习）不物化入训练集。"""
    pair = build_training_pair(_rec(ORIGIN_SYSTEM_INTERNAL))
    assert pair is None, "ORIGIN_SYSTEM_INTERNAL 不应物化为训练对"


def test_internal_model_output_rejected_by_criterion():
    """内在模型产出（origin=internal_model）经 is_internal_model_output 判据剔除。"""
    pair = build_training_pair(_rec("internal_model"))
    assert pair is None, "内在模型产出(origin=internal_model)不应物化"


def test_internal_model_output_flag_rejected():
    """含 is_model_output 真值标记的记录亦剔除。"""
    pair = build_training_pair(_rec(ORIGIN_USER_QUERY, is_model_output=True))
    assert pair is None, "is_model_output=True 不应物化"


def test_normal_user_query_accepted():
    """正常用户提问仍正常物化。"""
    pair = build_training_pair(_rec(ORIGIN_USER_QUERY))
    assert isinstance(pair, dict)
    assert pair["input"] == "你好"
    assert pair["output"] == "我是曈曈"
    assert pair["origin"] == ORIGIN_USER_QUERY


def test_convert_filters_internal_in_batch():
    """批量转换：混合来源中系统内部/内在模型产出被过滤，仅正常样本落盘。"""
    lines = [
        _rec(ORIGIN_USER_QUERY, prompt="q1", response="a1"),
        _rec(ORIGIN_SYSTEM_INTERNAL, prompt="q2", response="a2"),
        _rec("internal_model", prompt="q3", response="a3"),
        _rec(ORIGIN_USER_QUERY, prompt="q4", response="a4"),
    ]
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "src.jsonl")
        out = os.path.join(d, "out.jsonl")
        with open(src, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in lines)
        n = convert_calls_to_training_pairs(src, out)
        assert n == 2, f"应仅物化 2 条正常样本，实际 {n}"
        with open(out, "r", encoding="utf-8") as f:
            kept = [json.loads(x) for x in f if x.strip()]
        assert len(kept) == 2
        assert all(k["origin"] == ORIGIN_USER_QUERY for k in kept)
