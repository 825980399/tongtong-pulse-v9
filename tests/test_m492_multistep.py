# -*- coding: utf-8 -*-
"""492-1 / 492-2 / 492-3 多步推理接线单测（每刀 on/off）。"""
import pytest

import config
from organs.brain.PulseInnerWorld import (
    PulseInnerWorld as IW,
    _m492_calc_extra_hit,
    _m492_quantify_op_on,
)

_SWITCHES = (
    "ENABLE_M492_FAIL_REASON_LOG",
    "ENABLE_M492_EXPAND_CALC_KEYWORDS",
    "ENABLE_M492_QUANTIFY_OP",
)


@pytest.fixture
def cfg():
    _saved = {k: getattr(config, k, None) for k in _SWITCHES}
    yield config
    for k, v in _saved.items():
        if v is None:
            if hasattr(config, k):
                delattr(config, k)
        else:
            setattr(config, k, v)


def _iw():
    _i = IW.__new__(IW)
    _i._log = lambda *a, **k: None
    return _i


# ---------------------------------------------------------------- 492-2
def test_4922_range_plus_fuzzy_hits(cfg):
    cfg.ENABLE_M492_EXPAND_CALC_KEYWORDS = True
    assert _m492_calc_extra_hit("1 到 100 里有几个质数") is True


def test_4922_plain_arithmetic_unaffected(cfg):
    """常规算术题不含范围 ⇒ 新增词不生效，但原 has_calc 词表仍无条件命中。"""
    cfg.ENABLE_M492_EXPAND_CALC_KEYWORDS = True
    assert _m492_calc_extra_hit("计算 3 加 5 等于多少") is False
    _plan = IW._m454_annotate_plan(
        [{"name": "数据提取"}, {"name": "计算验证"}, {"name": "结果输出"}],
        "计算 3 加 5 等于多少")
    assert [s["op"] for s in _plan] == ["retrieve", "quantify", "retrieve"]


def test_4922_daily_question_not_misclassified(cfg):
    """'最近有几次心跳' 不含数字范围 ⇒ 不得被切成计算模板。"""
    cfg.ENABLE_M492_EXPAND_CALC_KEYWORDS = True
    assert _m492_calc_extra_hit("最近有几次心跳") is False
    _plan = IW._m454_annotate_plan(
        [{"name": "基础了解"}, {"name": "深入分析"}, {"name": "总结输出"}],
        "最近有几次心跳")
    assert [s["op"] for s in _plan] == ["retrieve"] * 3


def test_4922_both_keyword_tables_synced(cfg):
    """两处词表同步：含范围 + 模糊词 ⇒ 生成「计算验证」步并标 quantify。"""
    cfg.ENABLE_M492_EXPAND_CALC_KEYWORDS = True
    _i = _iw()
    _plan = _i._generate_task_plan("1 到 100 里有几个质数")
    assert any(s.get("name") == "计算验证" for s in _plan)
    _annotated = IW._m454_annotate_plan(_plan, "1 到 100 里有几个质数")
    assert any(s.get("op") == "quantify" for s in _annotated)


def test_4922_off_verbatim(cfg):
    cfg.ENABLE_M492_EXPAND_CALC_KEYWORDS = False
    assert _m492_calc_extra_hit("1 到 100 里有几个质数") is False
    _plan = IW._m454_annotate_plan(
        [{"name": "数据提取"}, {"name": "计算验证"}, {"name": "结果输出"}],
        "1 到 100 里有几个质数")
    assert [s["op"] for s in _plan] == ["retrieve"] * 3


# ---------------------------------------------------------------- 492-3
def _prev(text):
    return [{"success": True, "result": text, "confidence": 0.8}]


def test_4923_on_quantify_ok(cfg):
    cfg.ENABLE_M492_QUANTIFY_OP = True
    assert _m492_quantify_op_on() is True
    _r = _iw()._m456_run_local_op(
        "quantify", "1 到 100 里有几个质数",
        {"name": "计算验证", "step_id": "S2", "op": "quantify"},
        _prev("甲数值 18 个，乙数值 24 个；范围 1 到 100"))
    assert _r["status"] == "ok"
    assert _r["step_result"]["success"] is True
    assert _r["step_result"]["source"] == "local_op:quantify"
    assert _r["step_result"]["result"].startswith("计算")


def test_4923_no_numbers_not_hard_answered(cfg):
    """抽不到数字 ⇒ insufficient_numbers，绝不硬答 0。"""
    cfg.ENABLE_M492_QUANTIFY_OP = True
    _r = _iw()._m456_run_local_op(
        "quantify", "有几个", {"name": "计算验证", "step_id": "S2"},
        _prev("这里没有任何可用数值与计数信息"))
    assert _r["status"] == "insufficient_numbers"
    assert _r["step_result"]["success"] is False
    assert _r["step_result"]["fail_reason"] == "insufficient_numbers"


def test_4923_off_returns_to_retrieve(cfg):
    """关开关 ⇒ 白名单不放行，quantify 步骤逐字落到原 retrieve 路径。"""
    cfg.ENABLE_M492_QUANTIFY_OP = False
    assert _m492_quantify_op_on() is False
    import inspect
    _src = inspect.getsource(IW._multi_step_execute_v2)
    assert '"quantify" and _m492_quantify_op_on()' in _src


# ---------------------------------------------------------------- 492-1
def test_4921_fail_reason_in_log(cfg):
    """492-1 接线就位：开关、拼接、三种元素都可读的 fail_reason 键。"""
    import inspect
    _src = inspect.getsource(IW._multi_step_execute_v2)
    assert "ENABLE_M492_FAIL_REASON_LOG" in _src
    assert "_m492_detail" in _src
    assert "_s.get('fail_reason')" in _src
    # 关开关时拼接串为空（源码里默认拼空串后再按开关填值）
    assert 'f"回落上层单次大模型兜底（P2-196）{_m492_detail}"' in _src
