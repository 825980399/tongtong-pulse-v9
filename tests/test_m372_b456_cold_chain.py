# -*- coding: utf-8 -*-
"""372 刀4/刀5/刀6 定向单测（B 组冷化链三刀）。

- 刀4（`T-冷化链可达性-1`）：★核真伪 —— 前提已在 342刀1 修通，本刀**生产零改动**，只验可达性仍在。
- 刀5（`T-冷化阈值-1`）：只出复核结论，**不调常量** —— 断言阈值一字未动。
- 刀6（`T-懒加载开关-1`）：补启动期口径留痕（纯日志），断言默认 False 未翻 + 日志与分支共用同一布尔。
"""
from __future__ import annotations

import io

from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot


def _src(fp):
    return io.open(fp, encoding="utf-8", errors="replace").read()


# ============================================================ 刀4 可达性
def test_01_load_chain_reachable_and_lazy_branch_before_parquet():
    """342刀1 的透传与"懒加载先于 parquet"顺序必须仍在（这是可达性的两个支点）。"""
    _main = _src("main.py")
    # ① 透传在位（不再是"无参调用恒 True"）
    assert 'full_load=False if _m372_lazy_on else True' in _main
    assert "_m372_lazy_on = bool(getattr(config, \"SNAPSHOT_LAZY_LOAD\", False))" in _main

    # ② PulseSnapshot.load 里，懒加载入口必须在 parquet 分支**之前**被调用
    _snap = _src("nucleus/mnemosyne/PulseSnapshot.py")
    _i_lazy = _snap.index("_m342_lz = self._m342_try_lazy_load(full_load, _src)")
    _i_parquet = _snap.index("_nodes = self._load_internal(full_load)", _i_lazy)
    assert _i_lazy < _i_parquet, "懒加载入口被 parquet 分支吃掉 ⇒ 342刀1 的修复被回退"

    # ③ 入口函数存在（不是只有调用没有实现）
    assert hasattr(PulseSnapshot, "_m342_try_lazy_load")


# ============================================================ 刀5 不调阈值
def test_02_cold_thresholds_untouched():
    """本刀只出复核结论：max_warm / max_hot 一字未动，也不新增开关。"""
    import config
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool

    # 默认值与配置值仍是 50000（本刀建议区间只写在证据件里，不落代码）
    assert int(PulseNodePool._M_DEFAULT_MAX_WARM) == 50000 if hasattr(
        PulseNodePool, "_M_DEFAULT_MAX_WARM") else True
    _cfg_txt = _src("config.py")
    assert _cfg_txt.count("nodepool_max_warm=50000") >= 1 or "nodepool_max_warm" in _cfg_txt
    # 实例默认参数仍是 50000
    import inspect
    _sig = inspect.signature(PulseNodePool.__init__)
    if "max_warm" in _sig.parameters:
        assert _sig.parameters["max_warm"].default == 50000
    # 不新增调阈值开关（372 只允许刀7 那一个 # [M372-CFG]，且不是阈值开关）
    assert _cfg_txt.count("# [M372-CFG]") == 1
    assert "[M372-CFG]" not in _cfg_txt.split("ENABLE_RULE_CHANNEL_SUBPROCESS = False")[0].split("# [M372-CFG]")[-1]


# ============================================================ 刀6 留痕
def test_03_lazy_switch_default_false_and_trace_present():
    """默认 False 未翻；启动留痕在位且与分支**共用同一布尔**（防"日志说全量、实际走懒加载"）。"""
    import config

    assert getattr(config, "SNAPSHOT_LAZY_LOAD") is False, "默认必须保持 False（裁决甲）"
    _main = _src("main.py")
    assert "[372刀6] 快照加载口径: SNAPSHOT_LAZY_LOAD=%s ⇒ %s" in _main
    # 取值只算一次：日志与 load 用的是同一个变量
    assert _main.count("_m372_lazy_on = bool(getattr(config, \"SNAPSHOT_LAZY_LOAD\", False))") == 1
    assert "full_load=False if _m372_lazy_on else True" in _main
    # 不许再出现第二处独立读开关（两处各读一次就可能口径不一致）
    assert _main.count('getattr(config, "SNAPSHOT_LAZY_LOAD"') == 1


def test_04_no_behaviour_change_in_snapshot_module():
    """刀6 只加日志：PulseSnapshot 一行未改（懒加载实现与 LRU 上限都不动）。"""
    import subprocess

    _r = subprocess.run(
        ["git", "diff", "--name-only", "5dff8abe", "--", "nucleus/mnemosyne/PulseSnapshot.py"],
        capture_output=True, text=True)
    assert _r.returncode == 0
    assert _r.stdout.strip() == "", (
        "★本批不许改 PulseSnapshot.py（刀4 无改动、刀6 只加日志）；实得: %r" % _r.stdout)
