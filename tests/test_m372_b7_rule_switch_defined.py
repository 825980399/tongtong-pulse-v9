# -*- coding: utf-8 -*-
"""372 刀7 定向单测：规则通道开关补定义（零行为变化）。

票 `T-371-规则通道开关补定义-1`｜裁决：只补开关、不搬、不翻闸。

改前 `config.py` 对该开关**零命中**，唯一读取点是
`nucleus/exploration_audit.py::_m363_rule_subprocess_on` 的
`getattr(_cfg, "ENABLE_RULE_CHANNEL_SUBPROCESS", False)` 兜底
⇒ 隐性默认：能跑，但配置里看不见、改不了。
"""
from __future__ import annotations

import io


def test_01_switch_defined_and_default_false():
    """①配置定义存在且默认 False。"""
    import config

    assert hasattr(config, "ENABLE_RULE_CHANNEL_SUBPROCESS"), (
        "★开关必须成为明面配置项（本刀的全部意义）")
    assert config.ENABLE_RULE_CHANNEL_SUBPROCESS is False, "默认必须保持 False（不翻闸）"
    # 是显式 bool，不是 0/None（防"看着关了其实是空"）
    assert isinstance(config.ENABLE_RULE_CHANNEL_SUBPROCESS, bool)


def test_02_read_path_unchanged_zero_behaviour():
    """②读取口径与补定义前一致（零行为变化）。"""
    from nucleus import exploration_audit as _ea

    assert _ea._m363_rule_subprocess_on() is False
    # 读取侧代码一字未改 —— 仍是 getattr 兜底 False（补定义不该改读取逻辑）
    _src = io.open("nucleus/exploration_audit.py", encoding="utf-8",
                   errors="replace").read()
    assert 'getattr(_cfg, "ENABLE_RULE_CHANNEL_SUBPROCESS", False)' in _src
    # 只有一处读取点（防悄悄新增第二处口径）
    assert _src.count("ENABLE_RULE_CHANNEL_SUBPROCESS") == 2, (
        "预期 1 处 docstring 提及 + 1 处读取；实得 %d"
        % _src.count("ENABLE_RULE_CHANNEL_SUBPROCESS"))


def test_03_style_matches_neighbours():
    """③注释体例与相邻开关一致（承接票号 + [M<批>-CFG] 标记 + 读取点）。"""
    _cfg = io.open("config.py", encoding="utf-8", errors="replace").read()
    # 有独立注释块头（与 339 批 ENABLE_PENDING_QUEUE_AGING_O3 同体例）
    assert "# ★第372批刀7（`T-371-规则通道开关补定义-1`）" in _cfg
    # 带批号标记，便于后续 grep 归批
    assert "# [M372-CFG]" in _cfg
    # 写明读取点与"翻闸须另裁决"
    assert "nucleus/exploration_audit.py::_m363_rule_subprocess_on" in _cfg
    assert "真翻闸须先有小林裁决" in _cfg
    # 全仓只定义一次（防重复定义导致后者覆盖）
    assert _cfg.count("ENABLE_RULE_CHANNEL_SUBPROCESS = False") == 1
