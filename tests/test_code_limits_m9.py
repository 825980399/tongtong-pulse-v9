# -*- coding: utf-8 -*-
"""主线第9批 任务3 P2-66：代码行数红线机制 —— 回归测试。

覆盖：
1. config.CODE_QUALITY_CONFIG 配置块存在且键值正确
2. check_code_limits 能真实扫描并识别超过红线的文件（合成大文件）
3. 小文件 / 正常文件不被误报（无假阳性）
4. --json 输出结构正确（供 CI/CD 集成）
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
from tools.check_code_limits import collect, build_report, DEFAULT_CONFIG  # noqa: E402


def test_code_quality_config_block_exists():
    assert hasattr(config, "CODE_QUALITY_CONFIG"), "config 缺少 CODE_QUALITY_CONFIG"
    cfg = config.CODE_QUALITY_CONFIG
    assert cfg["max_lines_per_file"] == 5000
    assert cfg["max_lines_per_function"] == 300
    assert cfg["max_functions_per_file"] == 50
    assert cfg["enable_line_count_check"] is True
    # 警告档存在
    assert "warn_lines_per_file" in cfg


def test_check_code_limits_detects_over_limit_file():
    """合成一个远超红线的文件，验证扫描能识别（文件行数 + 超长函数双重红线）。"""
    with tempfile.TemporaryDirectory() as d:
        big = os.path.join(d, "giant_module.py")
        # 超长函数（>300 行） + 大量文件级赋值，使文件总行数 > 5000
        func_body = "".join("    y = %d\n" % i for i in range(400))
        file_body = "".join("X_%d = %d\n" % (i, i) for i in range(5000))
        with open(big, "w", encoding="utf-8") as fh:
            fh.write("def huge_function():\n" + func_body + "\n" + file_body)

        results = collect(d, DEFAULT_CONFIG)
        assert len(results) == 1, f"应只扫到 1 个文件，实际 {len(results)}"
        rep = results[0]
        assert rep.total_lines > DEFAULT_CONFIG["max_lines_per_file"], "应判定文件行数超限"
        assert rep.func_over_300, "应检测到超长函数"
        assert rep.severity == "error", f"严重程度应为 error，实际 {rep.severity}"


def test_check_code_limits_no_false_positive_on_small_file():
    """正常小文件不应被判超限。"""
    with tempfile.TemporaryDirectory() as d:
        small = os.path.join(d, "tiny.py")
        with open(small, "w", encoding="utf-8") as fh:
            fh.write("def f(a, b):\n    return a + b\n\n\ndef g():\n    pass\n")
        results = collect(d, DEFAULT_CONFIG)
        assert len(results) == 1
        assert results[0].severity == "ok", "小文件不应超限"
        assert results[0].func_over_300 == []


def test_check_code_limits_json_structure():
    """build_report 输出结构正确，含 scanned/over_limit/items。"""
    with tempfile.TemporaryDirectory() as d:
        big = os.path.join(d, "big.py")
        with open(big, "w", encoding="utf-8") as fh:
            fh.write("def h():\n" + "".join("    z = %d\n" % i for i in range(350)))
        results = collect(d, DEFAULT_CONFIG)
        report = build_report(results, DEFAULT_CONFIG, top=None)
        assert "scanned_files" in report
        assert "over_limit_files" in report
        assert report["over_limit_count"] >= 1
        item = report["over_limit_files"][0]
        assert item["severity"] in ("error", "warn")
        assert "total_lines" in item
        # 确保可 JSON 序列化
        json.dumps(report, ensure_ascii=False)
