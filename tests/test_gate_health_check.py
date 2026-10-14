# -*- coding: utf-8 -*-
"""216 批·刀2：gate_health_check 隔离单测（铁律 151 · 合成 fixture · 不读生产文件）。

测试目标：tools/gate_health_check.py 的八个纯函数 checker + main() 整体编排。
全部使用合成数据，绝不读取当日生产 pulse.log / pulse_pairs / experiments 等。
"""

import json
import os
import sys

# 将 tools/ 纳入导入路径（与运行时 PYTHONPATH 同构）
_TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import gate_health_check as g  # noqa: E402


# ---------------------------------------------------------------------------
# M1 PAIR 覆盖率
# ---------------------------------------------------------------------------
def test_m1_consecutive_pass():
    rows = [{"seq": i, "target_organ": "x", "amplitude": 1.0} for i in range(100)]
    r = g.check_pair_coverage(rows)
    assert r["status"] == g.PASS
    assert r["value"].endswith("%")


def test_m1_gap_fail():
    # 故意制造大段跳变（0..49 连续，然后跳到 200..249）
    rows = [{"seq": i} for i in range(50)] + [{"seq": i} for i in range(200, 250)]
    r = g.check_pair_coverage(rows)
    assert r["status"] == g.FAIL


def test_m1_legacy_only_warn():
    # 全老格式（无 seq）应跳过 → WARN（无法评估）
    rows = [{"pulse_id": "p1", "type": "emit"} for _ in range(10)]
    r = g.check_pair_coverage(rows)
    assert r["status"] == g.WARN
    assert r["value"] == "n/a"


def test_m1_mixed_format():
    # 混格式：老行 + 新行（连续）→ PASS
    rows = [{"pulse_id": "p1"}] + [{"seq": i} for i in range(50)]
    r = g.check_pair_coverage(rows)
    assert r["status"] == g.PASS


# ---------------------------------------------------------------------------
# M2 Pairs 尺寸/cap
# ---------------------------------------------------------------------------
def test_m2_under_cap_pass():
    r = g.check_pair_size(1000, 1024)
    assert r["status"] == g.PASS


def test_m2_at_cap_warn_first_window():
    r = g.check_pair_size(g.PAIR_FILE_CAP, 15 * 1024 * 1024)
    assert r["status"] == g.WARN  # 首窗一次性裁回 20000 属预期


def test_m2_over_cap_fail():
    r = g.check_pair_size(g.PAIR_FILE_CAP + 1, 15 * 1024 * 1024)
    assert r["status"] == g.FAIL


def test_m2_oversize_fail():
    r = g.check_pair_size(100, g.PAIR_FILE_MAX_BYTES + 1)
    assert r["status"] == g.FAIL


# ---------------------------------------------------------------------------
# M3 填充率差分
# ---------------------------------------------------------------------------
def test_m3_two_lines_pass():
    lines = [(500, 500, 100.0), (1000, 1000, 100.0)]
    r = g.check_fillrate_diff(lines)
    assert r["status"] == g.PASS
    assert "待复核" in r["detail"]


def test_m3_insufficient_warn():
    r = g.check_fillrate_diff([(500, 500, 100.0)])
    assert r["status"] == g.WARN


def test_m3_negative_diff_warn():
    lines = [(500, 500, 100.0), (1000, 800, 80.0)]
    r = g.check_fillrate_diff(lines)
    assert r["status"] == g.WARN
    assert r["value"] == "-20.0pp"


# ---------------------------------------------------------------------------
# M4 分布行密度
# ---------------------------------------------------------------------------
def test_m4_sparse_pass():
    from datetime import datetime, timedelta
    base = datetime(2026, 10, 14, 16, 0, 0)
    dist = [(base + timedelta(seconds=60 * i), 1, 1) for i in range(5)]  # 5 行分散
    rr = g.check_distribution_density(dist)
    assert rr["status"] == g.PASS


def test_m4_dense_warn():
    from datetime import datetime, timedelta
    base = datetime(2026, 10, 14, 16, 0, 0)
    # 同一 10min 桶内 50 行 → 超 40 阈值
    dist = [(base + timedelta(seconds=i), 1, 1) for i in range(50)]
    r = g.check_distribution_density(dist)
    assert r["status"] == g.WARN


def test_m4_none_warn():
    r = g.check_distribution_density([])
    assert r["status"] == g.WARN


# ---------------------------------------------------------------------------
# M5 IO/silent_exc 留痕
# ---------------------------------------------------------------------------
def test_m5_resonance_fail():
    lines = ["[pulse.silent_except] DEBUG: [静默异常可见化] nucleus/synapsys/ResonanceEngine.py:10 ValueError: x"]
    r = g.check_io_silent_exc(lines)
    assert r["status"] == g.FAIL


def test_m5_pair_warn():
    lines = ["[pulse.silent_except] DEBUG: [静默异常可见化] utils/pulse_tracer.py:10 TimeoutError: x"]
    r = g.check_io_silent_exc(lines)
    assert r["status"] == g.WARN


def test_m5_lfi_warn():
    lines = ["[pulse.silent_except] DEBUG: [静默异常可见化] nucleus/lif_shadow.py:10 OSError: x"]
    r = g.check_io_silent_exc(lines)
    assert r["status"] == g.WARN


def test_m5_clean_pass():
    r = g.check_io_silent_exc([])
    assert r["status"] == g.PASS


# ---------------------------------------------------------------------------
# M6 心跳节奏偏移
# ---------------------------------------------------------------------------
def test_m6_within_pass():
    r = g.check_heartbeat_offset([13.0, 13.8, 14.2])
    assert r["status"] == g.PASS


def test_m6_offset_warn():
    r = g.check_heartbeat_offset([20.0, 21.0, 19.0])
    assert r["status"] == g.WARN


def test_m6_none_warn():
    r = g.check_heartbeat_offset([])
    assert r["status"] == g.WARN


# ---------------------------------------------------------------------------
# M7 dump 行字段完备
# ---------------------------------------------------------------------------
def test_m7_complete_pass():
    rows = [{"seq": i, "target_organ": "x", "amplitude": 1.0} for i in range(10)]
    r = g.check_pair_field_completeness(rows)
    assert r["status"] == g.PASS


def test_m7_missing_fail():
    rows = [{"seq": i, "target_organ": "x"} for i in range(10)]  # 缺 amplitude
    r = g.check_pair_field_completeness(rows)
    assert r["status"] == g.FAIL


def test_m7_no_new_warn():
    rows = [{"pulse_id": "p1"} for _ in range(5)]
    r = g.check_pair_field_completeness(rows)
    assert r["status"] == g.WARN


# ---------------------------------------------------------------------------
# M8 装配/启动对账
# ---------------------------------------------------------------------------
def test_m8_balanced_pass():
    r = g.check_assembly_startup_reconcile(assembly_count=3, stop_count=2, start_count=2)
    assert r["status"] == g.PASS


def test_m8_stop_without_start_fail():
    r = g.check_assembly_startup_reconcile(assembly_count=0, stop_count=2, start_count=0)
    assert r["status"] == g.FAIL


def test_m8_assembly_without_start_fail():
    r = g.check_assembly_startup_reconcile(assembly_count=5, stop_count=0, start_count=0)
    assert r["status"] == g.FAIL


# ---------------------------------------------------------------------------
# 集成：main() 以临时合成文件驱动（不读生产）
# ---------------------------------------------------------------------------
def _write_tmp_pairs(tmp_path, rows):
    p = tmp_path / "pulse_pairs_20260101.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return str(p)


def _write_tmp_log(tmp_path, lines):
    p = tmp_path / "pulse.log"
    with open(p, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")
    return str(p)


def test_main_runs_and_exits(tmp_path):
    pairs = [{"seq": i, "target_organ": "自我认知", "amplitude": 1.0} for i in range(100)]
    pairs_file = _write_tmp_pairs(tmp_path, pairs)
    log_lines = [
        "2026-01-01 10:00:00 [pulse.tracer] INFO: pulse_tracer.target_organ 填充率: 窗口=500 非空=500 占比=100.0%",
        "2026-01-01 10:05:00 [pulse.tracer] INFO: pulse_tracer.target_organ 填充率: 窗口=1000 非空=1000 占比=100.0%",
        "2026-01-01 10:00:00 [pulse.resonance] INFO: PulseCore.resonance 映射分布: 累计=500 突触=32 real=18 simplified=14",
        "2026-01-01 10:01:00 [pulse.resonance] INFO: PulseCore.resonance 映射分布: 累计=1000 突触=43 real=26 simplified=17",
        "2026-01-01 10:00:10 [心脏] DEBUG: 心跳节律: [pid=1] 第30拍, 间隔=13.00s, 心率≈4.6次/分",
        "2026-01-01 10:00:20 [心脏] DEBUG: 心跳节律: [pid=1] 第31拍, 间隔=13.50s, 心率≈4.4次/分",
        "2026-01-01 10:00:00 [框架] INFO: 曈曈 停止指令已发出，正在清理后台任务... (总耗时24.0s)",
        "2026-01-01 10:05:00 [框架] INFO: 器官并行启动完成: 57/57 个",
    ]
    log_file = _write_tmp_log(tmp_path, log_lines)

    code = g.main([
        "--pairs-file", pairs_file,
        "--log-file", log_file,
        "--monitor-dir", str(tmp_path),
        "--date", "20260101",
    ])
    # 全部 PASS → exit 0
    assert code == 0


def test_main_json_output(tmp_path, capsys):
    pairs = [{"seq": i, "target_organ": "x", "amplitude": 1.0} for i in range(10)]
    pairs_file = _write_tmp_pairs(tmp_path, pairs)
    log_file = _write_tmp_log(tmp_path, [
        "2026-01-01 10:00:00 [pulse.tracer] INFO: 填充率: 窗口=500 非空=500 占比=100.0%",
    ])
    code = g.main([
        "--pairs-file", pairs_file, "--log-file", log_file,
        "--monitor-dir", str(tmp_path), "--date", "20260101", "--json",
    ])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["exit_code"] == code
    assert len(payload["checks"]) == 8
    assert payload["summary"]["total"] == 8


def test_main_metric_format_lines(tmp_path, capsys):
    pairs = [{"seq": i, "target_organ": "x", "amplitude": 1.0} for i in range(10)]
    pairs_file = _write_tmp_pairs(tmp_path, pairs)
    log_file = _write_tmp_log(tmp_path, [])
    g.main([
        "--pairs-file", pairs_file, "--log-file", log_file,
        "--monitor-dir", str(tmp_path), "--date", "20260101",
    ])
    out = capsys.readouterr().out
    for line in out.splitlines():
        if " = " in line and any(s in line for s in (g.PASS, g.WARN, g.FAIL)):
            assert line.startswith("M") and ("PASS" in line or "WARN" in line or "FAIL" in line)
    assert "SUMMARY" in out
