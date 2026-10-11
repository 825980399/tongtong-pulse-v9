# -*- coding: utf-8 -*-
"""186批刀2 隔离单测：SUBAGENT worker 最小实装（零停窗）。

验收：
  - worker 模块就位；run_once 读 input → 单次确定性调用 → 写 output → exit code 语义；
  - 不调 LLM（纯确定性 ack / 纯函数）；
  - CLI 契约与 subagent_runner 一致：python -m nucleus.reasoning.subagent_worker
    <input.json> <output.json> [stderr.log]；
  - 开关默认关零行为变化（本测试直接驱动 worker，不依赖任何 config 开关）。
"""
from __future__ import annotations

import json
import subprocess
import sys

import nucleus.reasoning.subagent_worker as m


def test_run_once_echo():
    res, code = m.run_once({"task": {"op": "echo", "payload": "hi"}})
    assert code == 0
    assert res["echo"] == "hi"
    assert res["deterministic"] is True


def test_run_once_sum():
    res, code = m.run_once({"task": {"op": "sum", "nums": [1, 2, 3]}})
    assert code == 0
    assert res["sum"] == 6


def test_run_once_hash():
    import hashlib
    res, code = m.run_once({"task": {"op": "hash", "data": "abc"}})
    assert code == 0
    assert res["sha256"] == hashlib.sha256(b"abc").hexdigest()


def test_run_once_unknown_op_is_ack():
    res, code = m.run_once({"task": {"op": "frobnicate", "x": 1},
                            "model": "cheap", "budget_tokens": 10})
    assert code == 0
    assert res["handled"] is True


def test_run_once_string_task():
    res, code = m.run_once({"task": "hello"})
    assert code == 0
    assert res["echo"] == "hello"


def test_main_writes_output_and_returns_zero(tmp_path):
    inp = tmp_path / "in.json"
    out = tmp_path / "out.json"
    err = tmp_path / "err.log"
    inp.write_text(json.dumps({"task": {"op": "sum", "nums": [2, 3]},
                               "model": "m", "budget_tokens": 5}), encoding="utf-8")
    rc = m.main([str(inp), str(out), str(err)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["exit_code"] == 0
    assert data["result"]["sum"] == 5


def test_main_invalid_input_returns_two(tmp_path):
    inp = tmp_path / "bad.json"
    out = tmp_path / "out.json"
    inp.write_text("{not valid json", encoding="utf-8")
    rc = m.main([str(inp), str(out)])
    assert rc == 2
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["exit_code"] == 2


def test_cli_subprocess_contract(tmp_path):
    """真起方式（与 subagent_runner 完全一致）验证 CLI 契约。"""
    inp = tmp_path / "in.json"
    out = tmp_path / "out.json"
    err = tmp_path / "err.log"
    inp.write_text(json.dumps({"task": {"op": "noop"},
                               "model": "cheap", "budget_tokens": 1,
                               "timeout": 30}), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "nucleus.reasoning.subagent_worker",
         str(inp), str(out), str(err)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["result"]["handled"] is True
    assert data["exit_code"] == 0
