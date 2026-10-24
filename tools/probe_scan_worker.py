# -*- coding: utf-8 -*-
"""363 刀 D2：规则通道（self_inspector 12 检测器）**子进程工作器**（隔离试点）。

为什么要子进程化
----------------
`exploration_audit._rule_channel()` 调 `self_inspector.detect_code_issues()`：
全仓 AST + 12 种检测器，**进程内**跑 ⇒ 受 GIL 约束且与主循环抢同一颗 CPU。
实测一轮 687 问题 / 约 10.6min，主进程在这段时间里是被拖着的。
（LLM 通道在 2026-09-03 已按同样理由改成子进程，本刀是规则通道的同款改造。）

约束（任务书 + K2 安全面）
-------------------------
* **不建进程池**：只做一次 `subprocess.run`，带超时，跑完即销毁；
* 输入 = 文件/器官清单 + 判据版本，输出 = 问题 JSON（原子写）；
* **结构零破坏**：产物只是「原 issues 列表 + 元数据」，路径归一等落盘加工
  仍由主侧既有 `_m312_build_items` 负责，工作器不复刻；
* 主侧失败/超时 ⇒ **回退进程内**计算，零回归（开关默认关闭，先试点）。

用法
----
    python tools/probe_scan_worker.py --root <项目根> --out <json路径> \\
        [--organs '["PulseHeart"]'] [--skip-detectors '["dead_code"]'] \\
        [--min-severity high] [--rules-version 363.d2.v1]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time


def _atomic_write_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(description="规则通道子进程工作器（363刀D2）")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--out", required=True)
    ap.add_argument("--organs", default="", help='JSON 数组，例: ["PulseHeart"]；空=全量')
    ap.add_argument("--skip-detectors", default="", help="JSON 数组")
    ap.add_argument("--min-severity", default="", help="low/medium/high")
    ap.add_argument("--rules-version", default="363.d2.v1")
    args = ap.parse_args()

    _t0 = time.time()
    _out: dict = {"status": "error", "reason": "", "rules_version": args.rules_version,
                  "count": 0, "issues": [], "pid": os.getpid(),
                  "duration": 0.0, "root": args.root}
    try:
        if args.root not in sys.path:
            sys.path.insert(0, args.root)
        from nucleus.self_inspector import SelfInspector

        _organs = json.loads(args.organ) if False else (json.loads(args.organs) if args.organs else None)
        _skip = json.loads(args.skip_detectors) if args.skip_detectors else None
        _ms = str(args.min_severity or "").strip() or None

        _insp = SelfInspector()
        _issues = _insp.detect_code_issues(target_organs=_organs,
                                           skip_detectors=_skip,
                                           min_severity=_ms)
        _out["status"] = "ok"
        _out["issues"] = list(_issues or [])
        _out["count"] = len(_out["issues"])
    except Exception as e:
        _out["reason"] = "%s: %s" % (type(e).__name__, e)
        try:
            _out["duration"] = round(time.time() - _t0, 3)
            _atomic_write_json(args.out, _out)
        except Exception as _we:
            # ★不得纯 pass：工作器写不出产物时，主侧只能看到「回退」，
            #   若这里也静默，就永远查不出是执行失败还是写盘失败。
            print("[probe_scan_worker] 产物写失败: %s: %s" % (type(_we).__name__, _we),
                  file=sys.stderr)
        return 1
    _out["duration"] = round(time.time() - _t0, 3)
    _atomic_write_json(args.out, _out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
