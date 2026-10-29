# -*- coding: utf-8 -*-
"""398 刀3：落空率换源 —— 口径拆分与归因证伪。

本批做的事（与任务书的差别写在这）：
  1. 把 `unmatched_receive` 拆成「无 pulse_id（结构上不可 join）」+「真落空」；
  2. 扫描改两遍（先全量建 emit 索引）⇒ 不再依赖文件行序；
  3. **证伪**票面给的归因（"65.8% 是 emit 回填时序假阳性"）：
     单遍与两遍在同一份真实数据上结果完全相同 ⇒ 时序不是成因，无 pid 才是。
     这条固化成 test_06，免得下一批再按旧归因去改一遍扫描顺序。
"""
from __future__ import annotations

import io
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

sys.path.insert(0, os.path.join(_ROOT, "tools"))
import count_match_frequency as _cmf  # noqa: E402

from nucleus._silent_except import silent_exc  # noqa: E402


def _write(tmp_path, rows) -> str:
    _p = tmp_path / "pairs.jsonl"
    with io.open(_p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return str(_p)


# ---------------------------------------------------------------- 拆字段
def test_01_no_pid_is_split_out(tmp_path):
    """无 pulse_id 的接收行不能进 unmatched（这是旧口径虚高的唯一主因）。"""
    _p = _write(tmp_path, [
        {"type": "emit", "pulse_id": "p1", "organ": "A", "event_type": "e1"},
        {"type": "receive", "pulse_id": "p1", "organ": "B", "event_type": "e1"},
        {"type": "receive", "pulse_id": "p2", "organ": "B", "event_type": "e1"},
        {"type": "receive", "organ": "B", "event_type": "e1"},
    ])
    _r = _cmf.compute_frequency(_p)
    assert _r["emit_rows"] == 1
    assert _r["receive_rows"] == 3
    assert _r["matched"] == 1
    assert _r["unmatched_receive"] == 1, "p2 有 pid 无 emit ⇒ 真落空"
    assert _r["recv_no_pid"] == 1, "无 pid 那条应单独计"


def test_02_empty_pid_counts_as_no_pid(tmp_path):
    """空串 pulse_id 与缺失同等对待（都是没有主键）。"""
    _p = _write(tmp_path, [
        {"type": "emit", "pulse_id": "p1", "organ": "A", "event_type": "e1"},
        {"type": "receive", "pulse_id": "", "organ": "B", "event_type": "e1"},
        {"type": "receive", "pulse_id": None, "organ": "B", "event_type": "e1"},
    ])
    _r = _cmf.compute_frequency(_p)
    assert _r["recv_no_pid"] == 2
    assert _r["unmatched_receive"] == 0


def test_03_three_counts_add_up(tmp_path):
    """各分支之和 == 总接收行（铁律 A5：不许漏一类）。"""
    _p = _write(tmp_path, [
        {"type": "emit", "pulse_id": "p1", "organ": "A", "event_type": "e1"},
        {"type": "emit", "pulse_id": "p2", "organ": "A", "event_type": "e2"},
        {"type": "receive", "pulse_id": "p1", "organ": "B", "event_type": "e1"},
        {"type": "receive", "pulse_id": "p2", "organ": "C", "event_type": "e2"},
        {"type": "receive", "pulse_id": "p9", "organ": "B", "event_type": "e1"},
        {"type": "receive", "organ": "B", "event_type": "e1"},
        {"type": "garbage"},
    ])
    _r = _cmf.compute_frequency(_p)
    assert (_r["matched"] + _r["unmatched_receive"] + _r["recv_no_pid"]
            == _r["receive_rows"])


# ---------------------------------------------------------------- 率
def test_04_pct_none_on_zero_denominator():
    """分母 0 ⇒ None（不是 0）。没样本与"真是零"必须能分开（铁律 155/276）。"""
    assert _cmf._pct(0, 0) is None
    assert _cmf._pct(1, 0) is None
    assert _cmf._pct(1, 4) == 25.0


def test_05_drop_rates_three_views(tmp_path):
    """三口径各自的分母要对得上。"""
    _p = _write(tmp_path, [
        {"type": "emit", "pulse_id": "p1", "organ": "A", "event_type": "e1"},
        {"type": "receive", "pulse_id": "p1", "organ": "B", "event_type": "e1"},  # matched
        {"type": "receive", "pulse_id": "p2", "organ": "B", "event_type": "e1"},  # unmatched
        {"type": "receive", "organ": "B", "event_type": "e1"},                    # no_pid
        {"type": "receive", "organ": "B", "event_type": "e1"},                    # no_pid
    ])
    _r = _cmf.compute_frequency(_p)
    _d = _cmf.drop_rates(_r)
    assert _d["matched"] == 1 and _d["unmatched"] == 1 and _d["recv_no_pid"] == 2
    assert _d["joinable"] == 2                     # 1 matched + 1 unmatched
    assert _d["unmatched_legacy"] == 3             # 旧口径把 no_pid 也算进来
    assert _d["rate_legacy"] == round(100.0 * 3 / 4, 2)      # 75.0
    assert _d["rate_true"] == round(100.0 * 1 / 4, 2)        # 25.0
    assert _d["rate_true_joinable"] == round(100.0 * 1 / 2, 2)  # 50.0
    assert _d["no_pid_share"] == round(100.0 * 2 / 4, 2)     # 50.0


def test_06_two_pass_is_order_independent(tmp_path):
    """★两遍扫描的价值：receive 排在 emit 之前也能正确 join。

    旧实现（单遍顺序扫）会把它误判成落空。这条锁住行为，防止有人"优化"回单遍。
    """
    _p = _write(tmp_path, [
        {"type": "receive", "pulse_id": "p1", "organ": "B", "event_type": "e1"},
        {"type": "emit", "pulse_id": "p1", "organ": "A", "event_type": "e1"},
    ])
    _r = _cmf.compute_frequency(_p)
    assert _r["matched"] == 1, "receive 在前也必须能配对（两遍扫描）"
    assert _r["unmatched_receive"] == 0


def test_07_single_pass_would_misjudge(tmp_path):
    """反证：单遍顺序扫在同一份输入上确实会误判 —— 说明 test_06 钉的不是空话。"""
    _p = _write(tmp_path, [
        {"type": "receive", "pulse_id": "p1", "organ": "B", "event_type": "e1"},
        {"type": "emit", "pulse_id": "p1", "organ": "A", "event_type": "e1"},
    ])
    # 复刻旧实现（单遍）
    _idx = {}
    _matched = _unmatched = 0
    with io.open(_p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("type") == "emit":
                _idx[row.get("pulse_id")] = 1
            elif row.get("type") == "receive":
                if row.get("pulse_id") in _idx:
                    _matched += 1
                else:
                    _unmatched += 1
    assert _matched == 0 and _unmatched == 1, \
        "单遍实现在此输入上的行为应为误判（这是本批改两遍的理由）"


# ---------------------------------------------------------------- 归因证伪
def test_08_timing_is_not_the_cause_on_real_data():
    """★把"时序不是成因"钉在**真实数据**上（票面归因的证伪）。

    同一份 pulse_pairs 上，单遍与两遍的 unmatched 必须相同：
      * 相同 ⇒ 行序恰好满足假设，未匹配全是"无 pid + 真无 emit"；
      * 不同 ⇒ 行序确实是成因，那时才该按票面的说法处理。

    样本不足（文件缺失/为空）时**跳过而不是判过** —— 判不了不等于通过。
    """
    _cands = [
        os.path.join(_ROOT, "data", "monitor", "pulse_pairs_20261015.jsonl"),
    ]
    _p = next((x for x in _cands if os.path.isfile(x)), None)
    if not _p:
        pytest.skip("没有可用于复核的 pulse_pairs 样本")
    if os.path.getsize(_p) > 120 * 1024 * 1024:
        pytest.skip("样本过大（>120MB），不在单测里跑")

    _two = _cmf.compute_frequency(_p)

    _idx = {}
    _one_unmatched = 0
    _one_matched = 0
    with io.open(_p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as _e:
                silent_exc(_e, where="m398_k3::test_08 single-pass replica",
                           level="debug")
                continue
            _t = row.get("type")
            if _t == "emit":
                _idx[row.get("pulse_id")] = 1
            elif _t == "receive":
                if row.get("pulse_id") in _idx:
                    _one_matched += 1
                else:
                    _one_unmatched += 1

    # 单遍的 unmatched = 无 pid + 真落空（旧口径就是这么混起来的）
    _legacy = _one_unmatched
    _now = _two["unmatched_receive"] + _two["recv_no_pid"]
    assert _legacy == _now, (
        f"单遍未匹配 {_legacy} ≠ 两遍(真落空+无pid) {_now} "
        f"⇒ 行序确实参与判定，票面「纯时序假阳性」的说法需要重审")
    # 并且：真落空必须是旧口径的一个真子集，占比要能算出来
    assert 0 < _two["unmatched_receive"] < _legacy
