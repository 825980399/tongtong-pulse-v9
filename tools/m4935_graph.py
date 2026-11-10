#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""493.5-1 模块图验收：节点≥57、四类边≥100、Cortex→Lung event 边、IW→InfoField inject/call 边。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collections import Counter
from nucleus.self_index.module_graph import build_module_graph


def main():
    d = build_module_graph()
    nodes, edges = d["nodes"], d["edges"]
    kinds = Counter(e["kind"] for e in edges)
    errs = []
    if len(nodes) < 57:
        errs.append(f"nodes {len(nodes)} < 57")
    if sum(kinds.values()) < 100:
        errs.append(f"total edges {sum(kinds.values())} < 100")
    ce = [e for e in edges if e["kind"] == "event"
          and e["from"] == "organs.brain.PulseCortex"
          and e["to"] == "organs.body.PulseLung"]
    if not ce:
        errs.append("no Cortex->Lung event edge")
    ii = [e for e in edges
          if e["from"] == "organs.brain.PulseInnerWorld"
          and e["to"] == "nucleus.field.InfoField"]
    if not ii:
        errs.append("no IW->InfoField inject/call edge")
    if errs:
        print("FAIL:", "; ".join(errs))
        sys.exit(1)
    print(f"PASS module_graph: nodes={len(nodes)} edges={sum(kinds.values())} "
          f"kinds={dict(kinds)} cortex_lung_event={len(ce)} iw_infofield={len(ii)}")
    sys.exit(0)


if __name__ == "__main__":
    main()
