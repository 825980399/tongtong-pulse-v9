#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T155-2 红基线机检前置门禁：pytest 全量「新增失败 = 0」。

口径（与任务书 §二 一致）：
  - 读取基线 tools/ci/baselines/red_baseline_155.json 的 failed_node_ids（已知失败全集）。
  - 运行（或读取已捕获的）全量 pytest 输出，提取所有 FAILED 节点。
  - 任何 FAILED 节点不在基线 => 判为「新增失败」，门禁 FAIL（exit 1）。
  - xfail / xpass / skipped / passed 不计入失败（xfail 不计 fail）；其中 iw=38 已由代码显式 xfail。
  - 基线节点本轮未失败（被修复 / 转 xfail / 被 skip）属正向，仅报告不判失败。

用法：
  python tools/ci/check_red_baseline_gate.py            # 自动运行全量 pytest（根目录 tests/）
  python tools/ci/check_red_baseline_gate.py out.txt    # 解析已捕获的 `pytest -rF` 文本输出
  python tools/ci/check_red_baseline_gate.py --selftest # 内置解析单测，不跑 pytest
"""
import os
import re
import sys
import json
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))  # tools/ci -> tools -> root
BASELINE_PATH = os.path.join(SCRIPT_DIR, "baselines", "red_baseline_155.json")

FAILED_RE = re.compile(r"^\s*FAILED\s+(\S+)", re.M)
XFAIL_RE = re.compile(r"^\s*XFAIL\s+(\S+)", re.M)
XPASS_RE = re.compile(r"^\s*XPASS\s+(\S+)", re.M)


def load_baseline(path):
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    nodes = d.get("failed_node_ids", [])
    assert isinstance(nodes, list), "baseline failed_node_ids 必须为 list"
    return set(nodes), d


def parse_failed(text):
    failed = set(FAILED_RE.findall(text))
    xfailed = set(XFAIL_RE.findall(text))
    xpassed = set(XPASS_RE.findall(text))
    return failed, xfailed, xpassed


def run_full_pytest():
    cmd = [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=line", "-rF",
           "-p", "no:cacheprovider"]
    print(f"[gate] 运行全量 pytest: {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc.stdout + "\n" + proc.stderr


def selftest():
    sample = (
        "FAILED tests/test_a.py::TestA::test_one - AssertionError\n"
        "FAILED tests/test_a.py::test_two - assert 0\n"
        "XFAIL tests/test_b.py::test_x - reason\n"
        "XPASS tests/test_c.py::test_y\n"
        "passed 10\n"
    )
    failed, xfailed, xpassed = parse_failed(sample)
    assert failed == {"tests/test_a.py::TestA::test_one", "tests/test_a.py::test_two"}, failed
    assert xfailed == {"tests/test_b.py::test_x"}, xfailed
    assert xpassed == {"tests/test_c.py::test_y"}, xpassed
    print("[selftest] 解析单测通过")


def main(argv):
    if "--selftest" in argv:
        selftest()
        return 0

    baseline, meta = load_baseline(BASELINE_PATH)
    print(f"[gate] 基线 HEAD={meta.get('generated_at_head')} "
          f"已知失败={meta.get('total')} 分类={meta.get('categories')}", file=sys.stderr)

    arg_path = None
    for a in argv[1:]:
        if not a.startswith("-"):
            arg_path = a
            break

    if arg_path and os.path.isfile(arg_path):
        with open(arg_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        print(f"[gate] 解析已捕获输出: {arg_path}", file=sys.stderr)
    else:
        text = run_full_pytest()

    failed, xfailed, xpassed = parse_failed(text)

    new_failures = failed - baseline
    baseline_not_failed = baseline - failed

    print(f"[gate] 本轮 FAILED={len(failed)}  XFAIL={len(xfailed)}  XPASS={len(xpassed)}")
    print(f"[gate] 基线已知失败={len(baseline)}  不在基线(新增)={len(new_failures)}  "
          f"基线未失败(已修复/转态)={len(baseline_not_failed)}")

    if new_failures:
        print("\n[gate] ❌ 检出新增失败（不在红基线）：")
        for n in sorted(new_failures):
            print(f"   + {n}")
        print("\n[gate] 结论：FAIL（存在新增失败，违反「新增失败=0」）")
        return 1

    # 正向提示：基线中有节点本轮未失败
    if baseline_not_failed:
        print("\n[gate] ℹ️ 以下基线失败节点本轮未以 FAILED 出现（可能已修复/转 xfail/被 skip）：")
        for n in sorted(baseline_not_failed):
            print(f"   - {n}")

    print("\n[gate] 结论：PASS（新增失败=0，xfail 不计 fail）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
