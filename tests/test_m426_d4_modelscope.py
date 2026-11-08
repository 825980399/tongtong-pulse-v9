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


def test_10_dead_channel_not_selectable():
    """★478：402 余额耗尽的 siliconflow 已 enabled=False ⇒ 不在 active 列表里。

    （魔搭 478 起已恢复启用，不再承担"不可选"的反例角色。）
    """
    _names = [c["name"] for c in config.get_active_channels()]
    assert "siliconflow-qwen2-7b" not in _names
    assert config.get_primary_usable_channel().get("name") != "siliconflow-qwen2-7b"


def test_20_config_kept_verbatim_so_it_can_be_re_enabled():
    """★478：426 的「只降级不删配置」已被验证生效 —— 探针实测新模型可用后，
    魔搭以 Qwen/Qwen3.8-Flash-Next 恢复启用（p5）。

    本用例改守「结构字段仍在 + 已恢复」，即当年保留配置的**目的**已达成。
    """
    _c = _ch("modelscope", include_disabled=True)
    assert _c is not None, "整个渠道条目被删掉了 —— 违反「只降级不删配置」"
    assert _c["enabled"] is True
    assert _c["priority"] == 5
    assert _c["model"] == "Qwen/Qwen3.8-Flash-Next"
    assert _c["api_url"] == "https://api-inference.modelscope.cn/v1/chat/completions"
    assert "api_key" in _c and "api_url" in _c and "quota_type" in _c


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
        ("modelscope", 5),
        ("bailian-qwen37-plus", 6),
        ("bailian-kimi-k3", 7),
        ("zhipu", 8),
        ("bailian-deepseek-v4-pro", 9),
        ("bailian-qwen38-max", 10),
        ("qianfan-ernie51", 11),
        ("deepseek", 12),
    ], _order


def test_40_load_balance_never_returns_it():
    """418 负载均衡重排（真实消费点）拿到的是 active 列表，不会把 modelscope 捞回来。"""
    from nucleus.llm.channel_balance import order_by_load

    _ordered, _info = order_by_load(list(config.get_active_channels()))
    _names = [c["name"] for c in _ordered]
    # ★478：402 余额耗尽的 siliconflow 不得入池（魔搭已恢复启用）。
    assert "siliconflow-qwen2-7b" not in _names
    # ★466：6 条 ⇒ 11 条；★478：魔搭恢复 + 千帆新增 ⇒ 12 条。
    assert len(_names) == 12
    assert set(_names) == {n for n, _ in [
        ("zhipu-47-flash", 1), ("bailian-qwen38-flash", 2),
        ("bailian-deepseek-v41-flash", 3), ("bailian-glm53", 4),
        ("modelscope", 5), ("bailian-qwen37-plus", 6),
        ("bailian-kimi-k3", 7), ("zhipu", 8),
        ("bailian-deepseek-v4-pro", 9), ("bailian-qwen38-max", 10),
        ("qianfan-ernie51", 11), ("deepseek", 12)]}
