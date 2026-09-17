# -*- coding: utf-8 -*-
"""QICA 意图 3 层层次结构构建器 —— 生成 data/qica/intent_hierarchy.json

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026-09-10

职责：
    建立 L1 大类 → L2 中类 → L3 具体意图 的 3 层层次，
    并生成反向索引（intent → L1 / L2）供分类结果直接携带 l1_category / l2_category。

★校验：生成时比对 IntentChannels.ALL_INTENTS，确保 24 个意图全覆盖、无遗漏、无多余。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

OUT_DIR = os.path.join(_ROOT, "data", "qica")
OUT_PATH = os.path.join(OUT_DIR, "intent_hierarchy.json")

# L1 → L2 → [L3 意图]
HIERARCHY: dict[str, dict[str, list[str]]] = {
    "自我认知类": {
        "身份与关系": ["身份确认", "关系查询"],
        "情感与表达": ["情感表达", "情感问候"],
        "元认知与健康": ["状态查询", "健康检查", "元认知报告", "学习成长"],
    },
    "知识获取类": {
        "知识检索": ["知识查询", "概念解释", "记忆回想", "搜索获取"],
        "一般对话": ["一般对话"],
    },
    "规则与命令类": {
        "规则查阅": ["规则查阅"],
        "系统命令": ["系统命令"],
        "时间日程": ["时间日程"],
    },
    "深度思考类": {
        "技术推理": ["技术推理"],
        "创造性思考": ["创造性思考"],
        "深度分析": ["深度分析"],
        "对比分析": ["对比分析"],
    },
    "交互与执行类": {
        "任务执行": ["任务执行", "请求帮助"],
        "对话管理": ["追问澄清", "否定质疑"],
    },
}


def main() -> int:
    from nucleus.qica.IntentChannels import ALL_INTENTS

    os.makedirs(OUT_DIR, exist_ok=True)

    intent_to_l1: dict[str, str] = {}
    intent_to_l2: dict[str, str] = {}
    dup: list[str] = []

    for l1, subs in HIERARCHY.items():
        for l2, intents in subs.items():
            for it in intents:
                if it in intent_to_l1:
                    dup.append(it)
                intent_to_l1[it] = l1
                intent_to_l2[it] = l2

    covered = set(intent_to_l1.keys())
    declared = set(ALL_INTENTS)
    missing = sorted(declared - covered)     # 在 ALL_INTENTS 但层次里没有
    extra = sorted(covered - declared)       # 在层次里但 ALL_INTENTS 没有

    if missing or extra or dup:
        print("校验失败：")
        if missing:
            print("  层次缺失: %s" % missing)
        if extra:
            print("  层次多余: %s" % extra)
        if dup:
            print("  重复归属: %s" % dup)
        return 1

    payload = {
        "version": "v1.0",
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "levels": ["L1", "L2", "L3"],
        "l1_count": len(HIERARCHY),
        "l2_count": sum(len(v) for v in HIERARCHY.values()),
        "intent_count": len(covered),
        "hierarchy": HIERARCHY,
        "intent_to_l1": intent_to_l1,
        "intent_to_l2": intent_to_l2,
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("OK -> %s" % OUT_PATH)
    print("  L1=%d  L2=%d  L3(意图)=%d  全覆盖校验通过" % (
        payload["l1_count"], payload["l2_count"], payload["intent_count"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
