# -*- coding: utf-8 -*-
"""第162批刀13 GATE_PATH_SPLIT 门禁自测（3 用例，计入 collect）。

通过子进程在真实仓库中调用已安装的 .git/hooks/pre-commit，
构造三类暂存场景验证路径分级：
  1) 纯文档提交 → 跳过代码/collect 门禁（打印「仅文档路径」），退出 0；
  2) 含 1 个 .py 提交 → 跑完整门禁（打印「5 静态门并发执行」），退出 0；
  3) 文档 + .py 混合提交 → 跑全套，退出 0。

注意：测试会临时 git add / git reset 临时探针文件，并在 finally 中清理，
不影响仓库既有暂存内容（仅复位本测试创建的探针）。
"""
import os
import subprocess

import pytest

ROOT = subprocess.check_output(
    ["git", "rev-parse", "--show-toplevel"], text=True
).strip()
HOOK = os.path.join(ROOT, ".git", "hooks", "pre-commit")

DOCS_PROBE = os.path.join(ROOT, "docs", "_probe_docs_m162.md")
PY_PROBE = os.path.join(ROOT, "tests", "_probe_py_m162.py")


def _write(path, content):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _run_hook():
    """运行已安装 pre-commit hook，返回 (returncode, combined_output)。"""
    proc = subprocess.run(
        ["sh", HOOK],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc.returncode, proc.stdout


@pytest.fixture
def probes():
    created = []
    try:
        yield created
    finally:
        # 复位并删除本测试创建的所有探针
        for p in created:
            subprocess.run(
                ["git", "reset", "-q", "--", os.path.relpath(p, ROOT)],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if os.path.exists(p):
                os.remove(p)


def test_docs_only_skips_code_gates(probes):
    """纯文档提交：跳过代码/collect 门禁，退出 0，且打印分级提示。"""
    _write(DOCS_PROBE, "# 刀13 探针（文档专用）\n")
    probes.append(DOCS_PROBE)
    subprocess.run(["git", "add", "--", os.path.relpath(DOCS_PROBE, ROOT)],
                   cwd=ROOT, check=True)
    rc, out = _run_hook()
    assert rc == 0, f"纯文档提交应退出 0，实际 {rc}\n{out}"
    assert "GATE_PATH_SPLIT：仅文档路径" in out, f"应打印文档路径跳过提示\n{out}"


def test_py_commit_runs_full_gates(probes):
    """含 1 个 .py 提交：仍跑完整门禁（含 5 静态门并发），退出 0。"""
    _write(PY_PROBE,
           "def test_probe_speed_placeholder():\n"
           "    \"\"\"刀13 提速探针（提交后即删）。\"\"\"\n"
           "    assert True\n")
    probes.append(PY_PROBE)
    subprocess.run(["git", "add", "--", os.path.relpath(PY_PROBE, ROOT)],
                   cwd=ROOT, check=True)
    rc, out = _run_hook()
    assert rc == 0, f"含 .py 提交应退出 0，实际 {rc}\n{out}"
    assert "GATE_PARALLEL：5 静态门并发执行" in out, f"应打印静态门并发提示\n{out}"


def test_mixed_runs_full_gates(probes):
    """文档 + .py 混合提交：按就严跑全套，退出 0。"""
    _write(DOCS_PROBE, "# 刀13 混合探针（文档）\n")
    _write(PY_PROBE,
           "def test_probe_speed_placeholder_mixed():\n"
           "    assert True\n")
    probes.append(DOCS_PROBE)
    probes.append(PY_PROBE)
    subprocess.run(["git", "add", "--",
                    os.path.relpath(DOCS_PROBE, ROOT),
                    os.path.relpath(PY_PROBE, ROOT)],
                   cwd=ROOT, check=True)
    rc, out = _run_hook()
    assert rc == 0, f"混合提交应退出 0，实际 {rc}\n{out}"
    assert "GATE_PARALLEL：5 静态门并发执行" in out, f"混合提交应跑全套\n{out}"
