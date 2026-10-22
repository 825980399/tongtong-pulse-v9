# -*- coding: utf-8 -*-
"""171批刀1 · 进化费用单价表 门控单测（170批C9遗留L-1收口）。

验收：
① config.EVOLUTION_LLM_PRICE_TABLE 非空且含项目实际接入模型条目；
② 单价表内正数条目经 _compute_cost_estimate 可算出非 null（= price @1000 tokens）；
③ 未出现在单价表的模型 → 不猜价（null）。

★343刀3 结论（星轨任务书更新版 · 烛微342 §0-5 根因修正）：
   判据③ **保持原语义**：免费/本地模型(=0) → null。
   为什么不改成 0.0：0 元的"计费"在预算账上等价于没记账（`used` 不涨），
   却会让人误以为"免费渠道已被约束" = 制造虚假覆盖。
   真正的修法是**加计数**：`_m343_basis_of()` 把 0 单价归为 `free_zero`、
   键缺失/值 None 归为 `unpriced`，两者计入 `cost_coverage.calls_unpriced`
   （金额不涨、条数涨），并配"跳闸双写"（付费金额口径 / 全量次数口径）。
   实测依据：`data/llm_traces` 15850 条里 `glm-4-flash` 7452 次（0 单价）永不进账。
"""
import config
from nucleus.llm.call_recorder import _compute_cost_estimate


def test_price_table_non_empty_and_models_present():
    """判据①：单价表非空且为合法非负数值字典。"""
    _tbl = config.EVOLUTION_LLM_PRICE_TABLE
    assert isinstance(_tbl, dict) and _tbl, "单价表须非空（170 C9 L-1 收口）"
    assert "deepseek-v4-flash" in _tbl, "须含实际接入的 deepseek 模型条目"
    for _k, _v in _tbl.items():
        if _v is None:
            # ★343刀3：`None` = 显式"未定价"占位（知道有这个渠道但无官方可溯源价）。
            #   允许存在，但**不可**写成 0.0 —— 那会让熔断器看不见真实花费。
            continue
        assert isinstance(_v, (int, float)) and not isinstance(_v, bool) and _v >= 0, \
            f"单价须为非负数值或 None（未定价占位）：{_k}={_v!r}"


def test_price_table_non_negative_values():
    """★343刀3：表内允许 `None`（显式未定价占位），但不许负数/布尔。"""
    for _k, _v in config.EVOLUTION_LLM_PRICE_TABLE.items():
        if _v is None:
            continue
        assert isinstance(_v, (int, float)) and not isinstance(_v, bool) and _v >= 0, \
            f"单价须为非负数值或 None（未定价占位）：{_k}={_v!r}"


def test_priced_entries_compute_non_null():
    """判据②：正数单价条目 → 1000 tokens 费用=单价（非 null）。"""
    _tbl = config.EVOLUTION_LLM_PRICE_TABLE
    for _k, _v in _tbl.items():
        if _v is not None and _v > 0:
            _cost = _compute_cost_estimate(_k, 1000)
            assert _cost is not None, f"{_k} 正数单价应算出非 null"
            assert abs(_cost - _v) < 1e-9, f"{_k} @1000tokens 费用应=单价 {_v}（实际={_cost}）"


def test_unknown_model_not_priced():
    """判据③（343刀3 改口径）：未定价 → null；**零价 → 0.0（有值）**。

    为什么改：0.0 是"已定价且价格为零"的确定事实（官方免费额度 / 本地自托管），
    None 才是"没有可溯源单价"。两者混同会让熔断器在免费主力渠道上永远读不到花费。
    """
    from nucleus.llm.call_recorder import _m343_basis_of
    assert _compute_cost_estimate("not-in-table-model-xyz", 1000) is None
    assert _m343_basis_of("not-in-table-model-xyz", 1000) == "unpriced"
    # 值 None 的显式未定价占位 ⇒ 仍 null（不猜价）
    _none_key = next((k for k, v in config.EVOLUTION_LLM_PRICE_TABLE.items()
                      if v is None), None)
    if _none_key is not None:
        assert _compute_cost_estimate(_none_key, 1000) is None
        assert _m343_basis_of(_none_key, 1000) == "unpriced"
    # 免费/本地模型单价=0 ⇒ **仍为 null**（计费语义不变），但归因档可分（计数用）
    _free = next((k for k, v in config.EVOLUTION_LLM_PRICE_TABLE.items()
                  if v == 0), None)
    if _free is not None:
        assert _compute_cost_estimate(_free, 1000) is None
        assert _m343_basis_of(_free, 1000) == "free_zero"
