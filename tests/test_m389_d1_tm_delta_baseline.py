# -*- coding: utf-8 -*-
"""389刀1：tracemalloc 差分基线**按进程代分键** + 槽位口径回正。

为什么单独一刀（T0 实测证据）
============================
389 票面要求「用 `tracemalloc_delta_rows`（相邻两拍差分）做归因」。T0 核实发现
**这套差分当时拿不出可用读数**，两个原因都在观测面：

1. 基线文件固定叫 `_tm_baseline.snap`，**不分进程代**。
   13:37 的基线由 pid21168 写下（该进程 13:37:53 因 RSS 超限重启）；14:08 起的
   pid22680 首次采样会拿**死进程**的快照做 `compare_to` ⇒ 全是负增量 ⇒ 被
   `size_diff <= 0` 全过滤 ⇒ 报 `no_growth`。**假「没涨」与真「没涨」同形**。
2. 388刀2 把类默认槽位改成"每 2 小时 12 槽"，但 `config.py::M339_MEMORY_PROBE_HOURS
   =(5,13)` 才是生产生效值 ⇒ 12 槽从未生效（第 4 次同型坑）；而真生效会更糟：
   tracemalloc 单次实测 837s，12 槽 = 每 2 小时卡进程 14 分钟。

本测试钉住：分代分键、跨代不继承、清理有界、槽位与 config 单源、代价上限 3 槽。
"""

import os
import sys
import tracemalloc

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from organs.core import PulseMetricsCollector as M  # noqa: E402

SRC = os.path.join(ROOT, "organs", "core", "PulseMetricsCollector.py")


def _snap():
    """取一份真实快照（`_m377_tm_delta` 要求真 Snapshot 对象）。"""
    if not tracemalloc.is_tracing():
        tracemalloc.start(1)
        tracemalloc.stop()
    tracemalloc.start(1)
    try:
        return tracemalloc.take_snapshot()
    finally:
        tracemalloc.stop()


def test_m389_d1_baseline_path_is_per_proc_gen(monkeypatch, tmp_path):
    """★核心：不同进程代拿到**不同**基线路径，且路径里不含非法字符。"""
    monkeypatch.setattr("nucleus.proc_gen.proc_gen_str",
                        lambda: "pid111@20261029-075059", raising=False)
    _pa = M._m389_tm_baseline_path(str(tmp_path))
    monkeypatch.setattr("nucleus.proc_gen.proc_gen_str",
                        lambda: "pid222@20261029-140818", raising=False)
    _pb = M._m389_tm_baseline_path(str(tmp_path))

    assert _pa != _pb, "跨代必须换基线文件"
    assert os.path.basename(_pa).startswith("_tm_baseline_")
    assert os.path.dirname(_pa) == str(tmp_path)
    for _p in (_pa, _pb):
        _base = os.path.basename(_p)
        assert " " not in _base and ":" not in _base and "@" not in _base, _base


def test_m389_d1_cross_gen_does_not_inherit_baseline(monkeypatch, tmp_path):
    """★核心：新代第一次采样必须是 `no_baseline`，**不许**静默报 `no_growth`。

    改前跨代会拿死进程快照比 ⇒ 全是负增量 ⇒ `no_growth`（假读数）。
    """
    monkeypatch.setattr("nucleus.proc_gen.proc_gen_str",
                        lambda: "pid111@20261029-075059", raising=False)
    _pa = M._m389_tm_baseline_path(str(tmp_path))
    _s1 = _snap()
    _rows_a, _state_a = M._m377_tm_delta(_s1, _pa, 5)
    assert _state_a == "no_baseline", _state_a
    assert os.path.isfile(_pa), "差分后必须写下本代基线"

    # 换代：同一目录下必须找不到基线（旧代基线还在，但不能被继承）
    monkeypatch.setattr("nucleus.proc_gen.proc_gen_str",
                        lambda: "pid222@20261029-140818", raising=False)
    _pb = M._m389_tm_baseline_path(str(tmp_path))
    _rows_b, _state_b = M._m377_tm_delta(_snap(), _pb, 5)
    assert _state_b == "no_baseline", "新代不许继承旧代基线：%s" % _state_b
    assert os.path.isfile(_pa), "旧代基线不该被新代覆盖"


def test_m389_d1_same_gen_second_tick_compares(monkeypatch, tmp_path):
    """同代第二拍必须能与上一拍比较（差分真正可用）。"""
    monkeypatch.setattr("nucleus.proc_gen.proc_gen_str",
                        lambda: "pid333@20261029-160000", raising=False)
    _p = M._m389_tm_baseline_path(str(tmp_path))
    M._m377_tm_delta(_snap(), _p, 5)
    _rows, _state = M._m377_tm_delta(_snap(), _p, 5)
    assert _state in ("ok", "no_growth"), _state
    assert isinstance(_rows, list)


def test_m389_d1_prune_keeps_newest_n(tmp_path):
    """★清理有界：只留最近 N 份，且留下的是**最新**的。"""
    _names = []
    for _i in range(10):
        _n = "_tm_baseline_pid%d@2026102%d-000000.snap" % (_i, _i)
        _f = tmp_path / _n
        _f.write_bytes(b"x")
        os.utime(str(_f), (1000000 + _i * 10, 1000000 + _i * 10))
        _names.append(_n)
    _kept_old = tmp_path / "_tm_baseline.snap"      # 旧版无后缀名也要能被收走
    _kept_old.write_bytes(b"x")
    os.utime(str(_kept_old), (1, 1))

    _dropped = M._m389_tm_prune_baselines(str(tmp_path), keep=3)
    _left = sorted(n for n in os.listdir(str(tmp_path)) if n.endswith(".snap"))
    assert len(_left) == 3, _left
    assert _left == sorted(_names[-3:]), "留下的必须是最新三份：%s" % _left
    assert len(_dropped) == 8, _dropped


def test_m389_d1_prune_is_noop_when_under_limit(tmp_path):
    (tmp_path / "_tm_baseline_a.snap").write_bytes(b"x")
    assert M._m389_tm_prune_baselines(str(tmp_path), keep=8) == []
    assert M._m389_tm_prune_baselines(str(tmp_path / "missing"), keep=8) == []


def test_m389_d1_probe_hours_match_config():
    """★槽位口径**单源**：类默认必须等于 config 生效值，否则默认值会骗人。"""
    import config as _cfg
    _want = tuple(int(_h) for _h in _cfg.M339_MEMORY_PROBE_HOURS)
    assert tuple(M.PulseMetricsCollector._M339_PROBE_HOURS) == _want, (
        "类默认 %r != config %r —— 388刀2 就是这样改了个永不生效的值"
        % (M.PulseMetricsCollector._M339_PROBE_HOURS, _want))


def test_m389_d1_probe_slots_bounded_by_cost():
    """★代价护栏：tracemalloc 单次实测 837s ⇒ 槽位数必须 ≤3（3 槽/日 ≈ 42 分钟）。"""
    assert len(M.PulseMetricsCollector._M339_PROBE_HOURS) <= 3, (
        "tracemalloc 单次实测 837s（3300 万对象做 statistics）；"
        "槽位加密到每 2 小时会让进程每 2 小时卡 14 分钟")


def test_m389_d1_legacy_fixed_baseline_not_used_at_callsite():
    """★调用点不许再拼死路径（否则分代分键形同虚设）。"""
    _s = open(SRC, encoding="utf-8").read()
    assert "os.path.join(_dir, _M377_TM_BASELINE)" not in _s
    assert "_m389_tm_baseline_path(_dir)" in _s


def test_m389_d1_reason_is_documented():
    """理由留证：为什么分代 + 为什么回滚 12 槽，都写在代码里。"""
    _s = open(SRC, encoding="utf-8").read()
    assert "★389刀1" in _s
    _i = _s.index("★389刀1：差分基线**按进程代分键**")
    assert "死进程" in _s[_i:_i + 900]
    _j = _s.index("★389刀1：**回滚 388刀2 的每 2 小时 12 槽**")
    _blk = _s[_j:_j + 500]
    assert "837" in _blk or "14 分钟" in _blk, "回滚必须写明实测代价"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
