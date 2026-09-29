# -*- coding: utf-8 -*-
"""第六批验收测量脚本：5 个标准测试问题

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026-09-10

职责：
    对星轨 2026-09-10 拍板的 5 个标准测试问题执行端到端测量，输出
    每个问题的「意图标签 / 融合路径 / 执行方法 / 置信度 / 是否调用大模型」，
    并统计分类准确率与本地回答率（验收硬指标）。

用法：
    python tools/measure_batch17_questions.py
    python tools/measure_batch17_questions.py --baseline   # 关闭所有新开关，测改造前基线

★说明：
    - 使用轻量实例（绕过重量级 __init__），不写任何生产数据
    - "是否调用大模型" 依据置信度与阈值(0.7)的关系判定，
      并单列 high_confidence_sample（每 7 次强制抽样，属设计内行为）
"""

from __future__ import annotations

import argparse
import os
import sys
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.qica import IntentChannels as IC  # noqa: E402
from nucleus.qica.QICA import INTENT_RULES, QICA  # noqa: E402
from organs.brain.PulseSemanticComprehension import PulseSemanticComprehension  # noqa: E402

# ★星轨 2026-09-10 拍板的期望标签表
CASES = [
    ("什么是五维共振？", "知识查询", "knowledge_retrieve"),
    ("你有哪些器官？", "身份确认", "rule_reason"),
    ("什么是共振引擎？", "知识查询", "knowledge_retrieve"),
    ("你和小林是什么关系？", "关系查询", "rule_reason"),
    ("简单介绍一下你自己", "身份确认", "rule_reason"),
]

THRESHOLD = 0.7


def _make_qica():
    inst = QICA.__new__(QICA)
    inst._log = lambda level, msg: None  # type: ignore[method-assign]
    inst._intent_rules = [dict(r) for r in INTENT_RULES]
    inst._intent_to_method = {
        k: {"method": v, "paths": IC.INTENT_DEFAULT_PATHS.get(k, ["/知识"])}
        for k, v in IC.INTENT_TO_METHOD.items()
    }
    inst._intent_rules_lock = threading.Lock()
    inst._rule_hits = {}
    inst._resolve_knowledge_paths = (  # type: ignore[method-assign]
        lambda intent, default_paths, raw_input="": list(default_paths))
    return inst


def _make_psc():
    inst = PulseSemanticComprehension.__new__(PulseSemanticComprehension)
    inst._log = lambda level, msg: None  # type: ignore[method-assign]
    inst._lessons = {}
    inst._check_topic_mismatch = (  # type: ignore[method-assign]
        lambda i, c, l: {"mismatch": False, "reason": ""})
    return inst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true",
                    help="关闭全部新开关，测量改造前基线")
    args = ap.parse_args()

    if args.baseline:
        config.ENABLE_QICA_MULTI_CHANNEL = False
        config.ENABLE_QICA_FUSION_ROUTING = False
        config.ENABLE_CONFIDENCE_CALIBRATION = False
        print("=== 基线模式（全部新开关关闭）===\n")

    qica = _make_qica()
    psc = _make_psc()

    ok_intent = 0
    local_answers = 0
    print("%-22s %-10s %-18s %-18s %-8s %s" % (
        "问题", "意图", "融合路径", "执行方法", "置信度", "本地回答"))
    print("-" * 100)

    for q, expect_intent, expect_method in CASES:
        anchor = {"raw_input": q, "has_negation": False, "sentence_type": "",
                  "entities": [], "need_reasoner": False, "semantic_confidence": 0.5}
        try:
            qica._i_classify(anchor)
        except Exception as e:
            print("%-22s 分类异常: %s: %s" % (q[:20], type(e).__name__, e))
            continue

        intent = anchor.get("task_type", "")
        method = anchor.get("suggested_method", "")
        fusion = anchor.get("fusion") or {}
        fpath = fusion.get("top_path", "-")

        local_result = {
            "matched_rule_id": anchor.get("matched_rule_id", ""),
            "intent_type": intent,
            "rule_conflict": anchor.get("rule_conflict", False),
            "fusion": anchor.get("fusion"),
        }
        try:
            conf = psc._calculate_confidence(local_result, q)
        except Exception as e:
            print("%-22s 置信度异常: %s: %s" % (q[:20], type(e).__name__, e))
            conf = 0.0

        is_local = conf >= THRESHOLD
        hit = (intent == expect_intent)
        if hit:
            ok_intent += 1
        if is_local:
            local_answers += 1

        print("%-22s %-10s %-18s %-18s %-8s %s%s" % (
            q[:20], intent, fpath, method, "%.2f" % conf,
            "是" if is_local else "否",
            "" if hit else "  ← 期望%s" % expect_intent))

    n = len(CASES)
    print("-" * 100)
    print("分类准确率:   %d/%d = %.0f%%  (验收 ≥80%%)" % (
        ok_intent, n, 100.0 * ok_intent / n))
    print("本地回答率:   %d/%d = %.0f%%  (验收 ≥50%%)" % (
        local_answers, n, 100.0 * local_answers / n))
    print("注: 另有 high_confidence_sample 每 7 次强制调用大模型（设计内，"
          "为本地回答率理论上限 ~86%）")

    ok = (ok_intent / n >= 0.8) and (local_answers / n >= 0.5)
    print("\n总判定: %s" % ("通过" if ok else "未达标"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
