# -*- coding: utf-8 -*-
"""171批刀1 · 进化费用单价表 门控单测（170批C9遗留L-1收口）。

验收：
① config.EVOLUTION_LLM_PRICE_TABLE 非空且含项目实际接入模型条目；
② 单价表内正数条目经 _compute_cost_estimate 可算出非 null（= price @1000 tokens）；
③ 未出现在单价表的模型 → 不猜价（null）；免费/本地模型(=0) → null。
"""
import config
from nucleus.llm.call_recorder import _compute_cost_estimate


def test_price_table_non_empty_and_models_present():
    """判据①：单价表非空且为合法非负数值字典。"""
    _tbl = config.EVOLUTION_LLM_PRICE_TABLE
    assert isinstance(_tbl, dict) and _tbl, "单价表须非空（170 C9 L-1 收口）"
    assert "deepseek-v4-flash" in _tbl, "须含实际接入的 deepseek 模型条目"
    for _k, _v in _tbl.items():
        assert isinstance(_v, (int, float)) and not isinstance(_v, bool) and _v >= 0, \
            f"单价须为非负数值：{_k}={_v!r}"


def test_priced_entries_compute_non_null():
    """判据②：正数单价条目 → 1000 tokens 费用=单价（非 null）。"""
    _tbl = config.EVOLUTION_LLM_PRICE_TABLE
    for _k, _v in _tbl.items():
        if _v > 0:
            _cost = _compute_cost_estimate(_k, 1000)
            assert _cost is not None, f"{_k} 正数单价应算出非 null"
            assert abs(_cost - _v) < 1e-9, f"{_k} @1000tokens 费用应=单价 {_v}（实际={_cost}）"


def test_unknown_model_not_priced():
    """判据③：未配置模型不猜价（null）；免费/本地(=0)亦为 null。"""
    assert _compute_cost_estimate("not-in-table-model-xyz", 1000) is None
    # 免费/本地模型单价=0 → 视为不估算（与「_price<=0 即 null」口径一致）
    _free = next((k for k, v in config.EVOLUTION_LLM_PRICE_TABLE.items() if v == 0), None)
    if _free is not None:
        assert _compute_cost_estimate(_free, 1000) is None
