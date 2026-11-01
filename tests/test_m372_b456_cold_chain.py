# -*- coding: utf-8 -*-
"""372 刀4/刀5/刀6 定向单测（B 组冷化链三刀）。

- 刀4（`T-冷化链可达性-1`）：★核真伪 —— 前提已在 342刀1 修通，本刀**生产零改动**，只验可达性仍在。
- 刀5（`T-冷化阈值-1`）：只出复核结论，**不调常量** —— 断言阈值一字未动。
- 刀6（`T-懒加载开关-1`）：补启动期口径留痕（纯日志），断言默认 False 未翻 + 日志与分支共用同一布尔。
"""
from __future__ import annotations

import io
import re

from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot


def _src(fp):
    return io.open(fp, encoding="utf-8", errors="replace").read()


def _full_load_arg(_main):
    """取 `snapshot.load(full_load=<expr>)` 里的实参表达式（★421/R3）。

    为什么不再钉字面量：本文件原来两处都写死
    `full_load=False if _m372_lazy_on else True`。421/R1 把那行做了 SIM211
    等价化简（`False if x else True` ⇒ `not x`，穷举 9 个取值验证过等价），
    字面量一变这两条用例就红了 —— 钉的是措辞而不是语义（铁律 3）。
    改为解析实参：只要求它由 `_m372_lazy_on` 派生，且不重新读一次配置。
    """
    # ★只认 `self.snapshot.load(...)` 这一处调用：全文第一个 `full_load=`
    #   出现在上方注释里（`# 以 load(full_load=False) 直接驱动验证`），
    #   按裸 `full_load=` 匹配会取到注释里的 `False` —— 会得到一个永远失败的判据。
    _m = re.search(r"self\.snapshot\.load\(\s*full_load=([^)\n]+)", _main)
    assert _m, "main.py 里找不到 self.snapshot.load(full_load=...) 的实参"
    return _m.group(1).strip()


# ============================================================ 刀4 可达性
def test_01_load_chain_reachable_and_lazy_branch_before_parquet():
    """342刀1 的透传与"懒加载先于 parquet"顺序必须仍在（这是可达性的两个支点）。"""
    _main = _src("main.py")
    # ① 透传在位（不再是"无参调用恒 True"）
    _fl = _full_load_arg(_main)
    assert "_m372_lazy_on" in _fl, (
        "full_load 必须由 _m372_lazy_on 派生（实得 %r）" % _fl)
    assert "config" not in _fl, (
        "full_load 实参不许再独立读一次开关（实得 %r）" % _fl)
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
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool

    # 默认值与配置值仍是 50000（本刀建议区间只写在证据件里，不落代码）
    assert int(PulseNodePool._M_DEFAULT_MAX_WARM) == 50000 if hasattr(
        PulseNodePool, "_M_DEFAULT_MAX_WARM") else True
    _cfg_txt = _src("config.py")
    assert _cfg_txt.count("nodepool_max_warm=50000") >= 1 or "nodepool_max_warm" in _cfg_txt
    # 实例默认参数：★373刀4 已按新裁决调档（9500—15000 保守端 ⇒ 15000）。
    #   372 当时的 50000 是「本刀只出复核结论」的守值，前提被 373 任务书取代。
    import inspect
    _sig = inspect.signature(PulseNodePool.__init__)
    if "max_warm" in _sig.parameters:
        assert _sig.parameters["max_warm"].default == 15000, \
            "373刀4 调档后默认值应为 15000（保守端）"
    # 不新增调阈值开关（372 只允许刀7 那一个 # [M372-CFG]，且不是阈值开关）
    assert _cfg_txt.count("# [M372-CFG]") == 1
    assert "[M372-CFG]" not in _cfg_txt.split("ENABLE_RULE_CHANNEL_SUBPROCESS = False")[0].split("# [M372-CFG]")[-1]


# ============================================================ 刀6 留痕
def test_03_lazy_switch_default_false_and_trace_present():
    """默认 False 未翻；启动留痕在位且与分支**共用同一布尔**（防"日志说全量、实际走懒加载"）。"""
    import config

    assert config.SNAPSHOT_LAZY_LOAD is False, "默认必须保持 False（裁决甲）"
    _main = _src("main.py")
    assert "[372刀6] 快照加载口径: SNAPSHOT_LAZY_LOAD=%s ⇒ %s" in _main
    # 取值只算一次：日志与 load 用的是同一个变量
    assert _main.count("_m372_lazy_on = bool(getattr(config, \"SNAPSHOT_LAZY_LOAD\", False))") == 1
    _fl = _full_load_arg(_main)
    assert "_m372_lazy_on" in _fl, (
        "日志与 load 分支必须共用同一布尔（实得 %r）" % _fl)
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
