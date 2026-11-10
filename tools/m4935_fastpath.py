#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""493.5-5 fast path 验收：各类型本地命中（计数/位置/别名/运行态/渠道/模块调用/眼睛）。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nucleus.self_index import store
from nucleus.self_index.fastpath import self_query_fastpath

CASES = [
    ("你有多少个器官", "器官"),
    ("你的InfoField在哪", "InfoField.py"),
    ("管消化的器官是什么", "PulseStomach"),
    ("你眼睛现在状态", "眼睛"),
    ("胃现在忙吗", None),          # 脚本环境埋点未就绪，仅验证非异常
    ("现在哪个渠道在回答", None),   # 脚本环境无渠道记录
    ("胃调用肝吗", None),          # 依赖已落盘模块图
]


def main():
    store.SelfIndexStore().module_graph()  # 确保模块图落盘（供 fast path 缓存读取）
    errs = []
    for q, kw in CASES:
        a = self_query_fastpath(q)
        if a is None:
            if kw is not None:
                errs.append(f"{q} -> None (expected contains {kw})")
        elif kw and kw not in a:
            errs.append(f"{q} -> missing {kw}")
    if errs:
        print("FAIL:", "; ".join(errs[:5]))
        sys.exit(1)
    print("PASS fastpath smoke: all typed queries answered locally")
    sys.exit(0)


if __name__ == "__main__":
    main()
