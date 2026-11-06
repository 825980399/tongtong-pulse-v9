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
    """★466 改口径：渠道池 6→11 条（百炼 2→7），原「六条顺序原样」已不成立。

    现钉「466 裁决后的完整体序」：priority 必须 1..11 连续、与声明一致。
    466 的诉求仍是「modelscope 降级不影响他人」，只是他人从 6 条变 11 条。
    回滚点：466 渠道扩容回退时，本断言需退回 6 条旧表。
    """
    _order = [(c["name"], c["priority"]) for c in config.get_active_channels()]
    assert _order == [
        ("zhipu-47-flash", 1),
        ("bailian-qwen38-flash", 2),
        ("bailian-deepseek-v41-flash", 3),
        ("bailian-glm53", 4),
        ("siliconflow-qwen2-7b", 5),
        ("bailian-qwen37-plus", 6),
        ("bailian-kimi-k3", 7),
        ("zhipu", 8),
        ("bailian-deepseek-v4-pro", 9),
        ("bailian-qwen38-max", 10),
        ("deepseek", 11),
    ], _order


def test_40_load_balance_never_returns_it():
    """418 负载均衡重排（真实消费点）拿到的是 active 列表，不会把 modelscope 捞回来。"""
    from nucleus.llm.channel_balance import order_by_load

    _ordered, _info = order_by_load(list(config.get_active_channels()))
    _names = [c["name"] for c in _ordered]
    assert "modelscope" not in _names
    # ★466：6 条 ⇒ 11 条（百炼 2→7）。集合仍逐名钉死，不用被测函数自身输出当基准。
    assert len(_names) == 11
    assert set(_names) == {n for n, _ in [
        ("zhipu-47-flash", 1), ("bailian-qwen38-flash", 2),
        ("bailian-deepseek-v41-flash", 3), ("bailian-glm53", 4),
        ("siliconflow-qwen2-7b", 5), ("bailian-qwen37-plus", 6),
        ("bailian-kimi-k3", 7), ("zhipu", 8),
        ("bailian-deepseek-v4-pro", 9), ("bailian-qwen38-max", 10),
        ("deepseek", 11)]}
