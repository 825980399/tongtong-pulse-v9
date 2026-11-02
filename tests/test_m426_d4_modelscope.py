# -*- coding: utf-8 -*-
"""426 刀4：modelscope 渠道降级（不删配置、只降级、可一键恢复）。

basetemp：`--basetemp=tmp/m426_bt_d4`（本批每文件独立 basetemp，铁律 58）。
"""

import config


def _ch(name, include_disabled=False):
    for _c in config.get_active_channels(include_disabled=include_disabled):
        if _c.get("name") == name:
            return _c
    return None


def test_10_modelscope_not_selectable():
    """渠道选择真的不会选它：enabled=False ⇒ 不在 active 列表里。"""
    _names = [c["name"] for c in config.get_active_channels()]
    assert "modelscope" not in _names
    assert config.get_primary_usable_channel().get("name") != "modelscope"


def test_20_config_kept_verbatim_so_it_can_be_re_enabled():
    """任务书：不要删配置，只是降级。api_url/model/api_key 必须逐字还在。"""
    _c = _ch("modelscope", include_disabled=True)
    assert _c is not None, "整个渠道条目被删掉了 —— 违反「只降级不删配置」"
    assert _c["enabled"] is False
    assert _c["priority"] == 99
    assert _c["model"] == "Qwen/Qwen2.5-7B-Instruct"
    assert _c["api_url"] == "https://api-inference.modelscope.cn/v1/chat/completions"
    assert "api_key" in _c and "api_url" in _c and "quota_type" in _c
    assert _c["channel_status"] == "invalid"
    assert _c["invalid_since"] == "2026-11-02"
    assert "400" in _c["invalid_reason"] or "下架" in _c["invalid_reason"]


def test_30_other_six_channels_order_untouched():
    """其他渠道优先级不受影响：1/2/3/4/5/8 六条顺序原样。"""
    _order = [(c["name"], c["priority"]) for c in config.get_active_channels()]
    assert _order == [
        ("zhipu-47-flash", 1),
        ("bailian-qwen-plus", 2),
        ("siliconflow-qwen2-7b", 3),
        ("zhipu", 4),
        ("bailian-qwen-max", 5),
        ("deepseek", 8),
    ], _order


def test_40_load_balance_never_returns_it():
    """418 负载均衡重排（真实消费点）拿到的是 active 列表，不会把 modelscope 捞回来。"""
    from nucleus.llm.channel_balance import order_by_load

    _ordered, _info = order_by_load(list(config.get_active_channels()))
    _names = [c["name"] for c in _ordered]
    assert "modelscope" not in _names
    assert len(_names) == 6
    assert set(_names) == {n for n, _ in [
        ("zhipu-47-flash", 1), ("bailian-qwen-plus", 2), ("siliconflow-qwen2-7b", 3),
        ("zhipu", 4), ("bailian-qwen-max", 5), ("deepseek", 8)]}
