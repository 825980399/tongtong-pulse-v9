# -*- coding: utf-8 -*-
"""第134批 T-134c：self_inspector 三检测器单元测。

覆盖：
  B1 silent_growth：首扫建基线=0告警；周增超阈告警
  B2 l3_inversion：首扫建基线=0告警；心跳陈旧超阈告警；l1<400 不执法
  B3 god_file：首扫建基线=0告警；相对基线 >+5% 告警；豁免表跳过
"""
import os
import time

from nucleus.self_inspector import SelfInspector


def _make_inspector(root, boot_ts=0.0):
    ins = SelfInspector()
    ins._project_root = root
    ins._boot_ts = boot_ts  # 默认绕过 boot 静默
    return ins


def test_b1_first_scan_zero_and_baseline(tmp_path, monkeypatch):
    ins = _make_inspector(str(tmp_path))
    monkeypatch.setattr(ins, "_si_collect_metrics", lambda: (100, 5, {}))
    assert ins._detect_silent_growth() == []
    hist = os.path.join(str(tmp_path), "data", "self_inspector_history.jsonl")
    assert os.path.exists(hist), "首扫应建趋势账基线"


def test_b1_alert_on_weekly_growth(tmp_path, monkeypatch):
    ins = _make_inspector(str(tmp_path))
    hist = os.path.join(str(tmp_path), "data", "self_inspector_history.jsonl")
    os.makedirs(os.path.dirname(hist), exist_ok=True)
    _old = time.time() - 8 * 86400
    with open(hist, "w", encoding="utf-8") as f:
        f.write(f'{{"ts": {_old}, "loc": 100, "silent": 5}}\n')
        # 一条 6h 前的采样，确保节流放行
        f.write(f'{{"ts": {time.time() - 7*86400}, "loc": 100, "silent": 5}}\n')
    monkeypatch.setattr(ins, "_si_collect_metrics", lambda: (2000, 6, {}))
    issues = ins._detect_silent_growth()
    assert any(i["type"] == "code_loc_weekly_growth" for i in issues), "周增应告警"


def test_b2_first_scan_zero(tmp_path, monkeypatch):
    ins = _make_inspector(str(tmp_path))
    monkeypatch.setattr(ins, "_si_count_loc", lambda: 9999)
    # 运行态时间戳为当前时间 → 不陈旧 → 无告警
    rs = os.path.join(str(tmp_path), "data", "runtime_state.json")
    os.makedirs(os.path.dirname(rs), exist_ok=True)
    with open(rs, "w", encoding="utf-8") as f:
        f.write('{{"timestamp": {}}}'.format(time.time()))
    assert ins._detect_l3_inversion() == []
    # B2 不再自建旧的 L3 心跳文件
    hb = os.path.join(str(tmp_path), "data", "mnemosyne", "l3_heartbeat.timestamp")
    assert not os.path.exists(hb), "B2 不应再自建 L3 心跳文件"


def test_b2_stale_alert(tmp_path, monkeypatch):
    ins = _make_inspector(str(tmp_path))
    rs = os.path.join(str(tmp_path), "data", "runtime_state.json")
    os.makedirs(os.path.dirname(rs), exist_ok=True)
    with open(rs, "w", encoding="utf-8") as f:
        f.write('{{"timestamp": {}}}'.format(time.time() - 1000))  # 陈旧 1000s
    monkeypatch.setattr(ins, "_si_count_loc", lambda: 9999)
    issues = ins._detect_l3_inversion()
    assert any(i["type"] == "runtime_state_stale" for i in issues), "运行态时间戳陈旧应告警"


def test_b2_l1_floor_skip(tmp_path):
    ins = _make_inspector(str(tmp_path))  # 小项目 loc<400 → 不执法
    assert ins._detect_l3_inversion() == []


def test_b3_first_scan_zero_and_baseline(tmp_path):
    ins = _make_inspector(str(tmp_path))
    p = tmp_path / "module_x.py"
    p.write_text("def f():\n    return 1\n", encoding="utf-8")
    assert ins._detect_god_file() == []
    base = os.path.join(str(tmp_path), "data", "god_file_baseline.json")
    assert os.path.exists(base), "首扫应建 god_file 基线"


def test_b3_alert_on_ceiling(tmp_path):
    ins = _make_inspector(str(tmp_path))
    p = tmp_path / "module_x.py"
    p.write_text("def f():\n    return 1\n", encoding="utf-8")
    ins._detect_god_file()  # 首扫建基线
    # 膨胀：行数从 2 → 200（相对基线 >> +5%）
    p.write_text("def f():\n" + "\n".join(f"    x{i} = {i}" for i in range(198)) + "\n", encoding="utf-8")
    issues = ins._detect_god_file()
    assert any(i["type"] == "loc_ceiling_exceeded" for i in issues), "超天花板应告警"


def test_b3_exempt_skipped(tmp_path):
    ins = _make_inspector(str(tmp_path))
    p = tmp_path / "main.py"  # 豁免表成员
    p.write_text("def f():\n    return 1\n", encoding="utf-8")
    ins._detect_god_file()  # 首扫：main.py 被跳过，不进基线
    p.write_text("def f():\n" + "\n".join(f"    y{i} = {i}" for i in range(198)) + "\n", encoding="utf-8")
    issues = ins._detect_god_file()
    assert not any("main.py" in i.get("file", "") for i in issues), "豁免文件不应告警"
