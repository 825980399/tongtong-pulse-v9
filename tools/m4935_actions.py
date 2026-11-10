#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""493.5-3 动作库验收：动作≥30，读/控类必须带 file:line 锚点（写类 blocked 允许无锚点）。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nucleus.self_index.exporters import export_actions


def main():
    d = export_actions()
    acts = d["actions"]
    errs = []
    if len(acts) < 30:
        errs.append(f"action_count {len(acts)} < 30")
    for a in acts:
        if a.get("kind") == "write" and a.get("status") == "blocked":
            continue  # 写类只登记不执行，允许无 file:line
        impl = a.get("impl") or {}
        if not impl.get("file"):
            errs.append(f"{a['action_id']} missing file anchor")
        if "line" not in impl:
            errs.append(f"{a['action_id']} missing line anchor")
    if errs:
        print("FAIL:", "; ".join(errs[:5]))
        sys.exit(1)
    print(f"PASS actions: count={len(acts)} (write/blocked excluded from anchor check)")
    sys.exit(0)


if __name__ == "__main__":
    main()
