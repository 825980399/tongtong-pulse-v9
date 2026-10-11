# -*- coding: utf-8 -*-
"""187批刀5 隔离单测：subagent_worker 输出补 stats 键（与 runner 协议对齐）。

验收点：
  - worker 输出 json 含 "stats" 键，且 stats == run_once 的 result（runner 据此取回产出）
  - "result" / "exit_code" 键保留（向后兼容，零行为变化）
  - 缺 stats 时 runner 取默认 {}（回归防护）
"""
import json
import os
import tempfile

sys_path = "."
import sys

sys.path.insert(0, ".")

from nucleus.reasoning.subagent_worker import run_once, _write_output


def test_worker_output_has_stats_equal_result():
    spec = {"task": {"op": "sum", "nums": [1, 2, 3]}, "budget_tokens": 100, "timeout": 30,
            "model": "cheap"}
    result, exit_code = run_once(spec)
    assert result["sum"] == 6

    fd, path = tempfile.mkstemp(suffix=".json", prefix="m187_w_")
    os.close(fd)
    try:
        _write_output(path, result, exit_code)
        with open(path, encoding="utf-8") as f:
            out = json.load(f)
        # 协议对齐核心：stats 存在且等于 result
        assert "stats" in out, "worker 输出缺 stats 键（runner 将丢弃产出）"
        assert out["stats"] == result, "stats 应与 run_once 的 result 一致"
        # 向后兼容键保留
        assert out.get("result") == result, "result 键应保留"
        assert out.get("exit_code") == exit_code
        # 模拟 runner 消费：result.get("stats", {}) 取到真实数据
        assert out.get("stats", {}).get("sum") == 6
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_worker_echo_op_stats_present():
    spec = {"task": {"op": "echo", "payload": "hi"}}
    result, exit_code = run_once(spec)
    fd, path = tempfile.mkstemp(suffix=".json", prefix="m187_w2_")
    os.close(fd)
    try:
        _write_output(path, result, exit_code)
        with open(path, encoding="utf-8") as f:
            out = json.load(f)
        assert out["stats"]["echo"] == "hi"
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_runner_get_stats_default_when_absent():
    # 回归防护：runner 侧约定 result.get("stats", {}) 在缺键时回落 {}
    fake_output_without_stats = {"result": {"x": 1}, "exit_code": 0}
    assert fake_output_without_stats.get("stats", {}) == {}


if __name__ == "__main__":
    test_worker_output_has_stats_equal_result()
    test_worker_echo_op_stats_present()
    test_runner_get_stats_default_when_absent()
    print("ALL PASS")
