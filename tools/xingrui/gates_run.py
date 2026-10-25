# -*- coding: utf-8 -*-
"""gates_run.py · 星轨自用：CI 三门禁顺序复跑＋汇总（只读，不刷新基线）。

用途：替代每批分散手写的 gates 复跑探针（复盘 3.3 校验链分散重复 · P0 工具 2）。
边界：只调用 tools/ci 既有门禁脚本，**不传 --update-baseline**；不修改任何门禁判据。

三关口径：
  M1  tools/ci/ledger_consistency_check.py        状态分布/行结构/状态合法/重复ID
  M2  tools/ci/check_pending_register.py --batch N  总数/ID集合/到期未裁/排期逾期
  M3  tools/ci/check_register_columns.py --root .    九列完整/P级合法/到期批次合法

用法：
    python tools/xingrui/gates_run.py --batch 365
    python tools/xingrui/gates_run.py --batch 365 --json

退出码：0 = 三关全绿；1 = 有关卡 FAIL；2 = 内部错误（解释器/脚本缺失等）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
CI_DIR = os.path.join(REPO_ROOT, "tools", "ci")

def _log(level: str, msg: str) -> None:
    sys.stderr.write(f"[gates_run][{level}] {msg}\n")

GATES = [
    ("M1", [sys.executable, os.path.join(CI_DIR, "ledger_consistency_check.py")]),
    ("M2", None),  # 命令行按 --batch 拼装
    ("M3", [sys.executable, os.path.join(CI_DIR, "check_register_columns.py"), "--root", "."]),
]


def run_gate(name: str, cmd: list[str]) -> dict:
    try:
        r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        return {"name": name, "rc": r.returncode, "cmd": " ".join(os.path.relpath(c, REPO_ROOT) for c in cmd),
                "ok": r.returncode == 0, "output": out.strip()}
    except OSError as e:
        _log("error", f"门禁 {name} 启动失败: {e!r}")
        return {"name": name, "rc": 2, "cmd": " ".join(cmd), "ok": False, "output": "启动失败: %r" % e}


def main(argv=None):
    p = argparse.ArgumentParser(description="CI 三门禁只读复跑＋汇总（星轨自用）")
    p.add_argument("--batch", type=int, required=True, help="当前批次号，透传 M2")
    p.add_argument("--root", default=".", help="仓库根，透传 M3")
    p.add_argument("--json", action="store_true", help="输出 JSON 到 stdout")
    args = p.parse_args(argv)

    if not os.path.isdir(CI_DIR):
        sys.stderr.write("CI 目录缺失: %s\n" % CI_DIR)
        return 2

    results = []
    results.append(run_gate("M1", [sys.executable, os.path.join(CI_DIR, "ledger_consistency_check.py")]))
    results.append(run_gate("M2", [sys.executable, os.path.join(CI_DIR, "check_pending_register.py"),
                                  "--batch", str(args.batch)]))
    results.append(run_gate("M3", [sys.executable, os.path.join(CI_DIR, "check_register_columns.py"),
                                  "--root", args.root]))

    all_ok = all(g["ok"] for g in results)

    if args.json:
        print(json.dumps({"batch": args.batch, "all_ok": all_ok, "gates": results},
                         ensure_ascii=False, indent=2))
    else:
        print("=== 三门禁只读复跑（batch=%d）===" % args.batch)
        for g in results:
            tag = "PASS" if g["ok"] else "FAIL"
            print("[%s] %s rc=%d" % (g["name"], tag, g["rc"]))
            for line in g["output"].splitlines():
                if line.strip():
                    print("    " + line.strip())
        print("=== 汇总: %s ===" % ("三关全绿" if all_ok else "存在 FAIL，禁止提交"))

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
