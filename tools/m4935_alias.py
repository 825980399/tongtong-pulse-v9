#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""493.5-2 别名验证：20 条口语指称正确率≥18/20，0 次瞎猜。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nucleus.self_index.exporters import link_organ

TESTS = [
    ("管消化的", "胃"), ("管说话的", "嘴巴"), ("看东西的", "眼睛"), ("过滤血液的", "肾"),
    ("管记忆的", "叙事自我"), ("管心跳的", "心脏"), ("管呼吸的", "肺"), ("管排泄的", "肾"),
    ("管代谢的", "肝"), ("管思考的", "大脑皮层"), ("管学习的", "全局学习器"),
    ("管情绪的", "激素"), ("管危险的", "风险感知"), ("管紧急的", "紧急处理"),
    ("管健康的", "健康监控"), ("管推理的", "推理引擎"), ("管语言的", "肺"),
    ("管免疫的", "白细胞"), ("管直觉的", "潜意识"), ("管视觉的", "眼睛"),
]


def main():
    ok = wild = 0
    miss = []
    for q, exp in TESTS:
        r = link_organ(q)
        if r is None:
            miss.append((q, exp, "NONE"))
            continue
        if "organ" in r:
            if r["organ"] == exp:
                ok += 1
            else:
                wild += 1
                miss.append((q, exp, "WRONG->" + r["organ"]))
        else:
            cands = [c["organ"] for c in r["candidates"]]
            if exp in cands:
                ok += 1
            else:
                wild += 1
                miss.append((q, exp, "CAND" + str(cands)))
    print(f"correct(set-include)={ok}/20 wild_guess={wild}")
    for m in miss:
        print("  MISS", m)
    if ok >= 18 and wild == 0:
        print("PASS alias: >=18/20, 0 wild guess")
        sys.exit(0)
    print("FAIL alias")
    sys.exit(1)


if __name__ == "__main__":
    main()
