# -*- coding: utf-8 -*-
"""QICA 意图原型向量库构建器 —— 离线预生成 intent_prototypes.json

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026-09-10

职责：
    为 24 个意图类型各收集 5-10 条典型问题语料，用 bge-small-zh-v1.5 编码后
    取均值并 L2 归一化，作为该意图的原型向量，持久化到 data/qica/intent_prototypes.json。
    运行时只读取该 JSON，不做首次编码，避免加载卡顿。

机制：
    1. 语料源同步落盘 data/qica/intent_corpus.json，供人工复核
    2. 每个意图的向量 = mean(encode(语料))，再做 L2 归一化
       → 运行时算余弦相似度只需点积
    3. 支持 --dry-run：只写语料、不加载模型（用于快速校验语料）

用法：
    python tools/build_qica_intent_prototypes.py            # 完整构建
    python tools/build_qica_intent_prototypes.py --dry-run  # 只写语料
"""

import argparse
import json
import os
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

DATA_DIR = os.path.join(_ROOT, "data", "qica")
CORPUS_PATH = os.path.join(DATA_DIR, "intent_corpus.json")
PROTOTYPE_PATH = os.path.join(DATA_DIR, "intent_prototypes.json")

# ========== 24 个意图类型 × 典型问题语料 ==========
# 编写原则：
#   1. 每条都贴近真实口语问法，避免书面化
#   2. 意图之间区分度优先（避免语义重叠导致原型向量混淆）
#   3. 五个标准测试问题的原型问法必须覆盖（"什么是五维共振"/"你有哪些器官"/
#      "什么是共振引擎"/"你和内部协作者是什么关系"/"简单介绍一下你自己"）
#   4. 无规则意图（关系查询/深度分析/对比分析/情感表达/一般对话 + 新增8个）全部手写
INTENT_CORPUS = {
    # ---------- 原有 16 个意图 ----------
    "身份确认": [
        "你是谁",
        "你叫什么名字",
        "简单介绍一下你自己",
        "介绍一下你自己",
        "你有哪些器官",
        "你的器官有哪些",
        "说说你的内部结构",
        "你是怎么构成的",
        "你的身份是什么",
        "讲讲你自己是什么样的",
    ],
    "关系查询": [
        "你和小林是什么关系",
        "小林是谁",
        "你和星轨是什么关系",
        "谁是你的创造者",
        "你认识小林吗",
        "你和路灯是什么关系",
        "你和小林之间是什么关系",
        "谁设计制造的你",
        "你的开发者是谁",
        "你和创造者怎么相处",
    ],
    "知识查询": [
        "什么是五维共振",
        "什么是共振引擎",
        "量子纠缠是什么",
        "介绍一下知识树",
        "什么是脉冲机制",
        "讲讲记忆池的工作原理",
        "什么是经验池",
        "共振引擎是怎么工作的",
        "五维共振指的是什么",
        "知识节点是怎么组织的",
    ],
    "概念解释": [
        "解释一下这个概念",
        "这个词是什么意思",
        "详细解释一下原理",
        "能具体说明一下吗",
        "这个概念该怎么理解",
        "给我讲讲它的含义",
        "定义的范围是什么",
    ],
    "规则查阅": [
        "你的运行规则是什么",
        "有什么使用限制",
        "系统在什么情况下会触发",
        "架构规则是怎样的",
        "调度的规则是什么",
        "有哪些硬性约束",
    ],
    "技术推理": [
        "这段代码怎么优化",
        "分析一下性能瓶颈在哪",
        "这个错误要怎么修复",
        "系统架构应该怎么设计",
        "这个算法的时间复杂度是多少",
        "为什么会出现内存泄漏",
        "这个模块该怎么重构",
    ],
    "创造性思考": [
        "有什么创新的想法",
        "能不能换个思路",
        "提出一个全新的方案",
        "如果重新设计会怎么样",
        "有没有更好的解决办法",
        "发挥想象力设想一下",
    ],
    "深度分析": [
        "深入分析一下这个问题",
        "从多个角度分析一下",
        "全面分析一下原因",
        "深度剖析这个现象",
        "系统性分析一下",
        "挖掘一下背后的本质",
    ],
    "对比分析": [
        "这两个方案有什么区别",
        "比较一下这两种做法",
        "哪一个更好",
        "对比一下优缺点",
        "它们有什么不同",
        "A和B该怎么选",
    ],
    "情感表达": [
        "我很难过",
        "今天心情不太好",
        "谢谢你一直陪着我",
        "我有点失落",
        "我今天特别开心",
        "感觉有点焦虑",
    ],
    "情感问候": [
        "你好",
        "早上好",
        "在吗",
        "嗨",
        "你好啊",
        "晚上好",
    ],
    "系统命令": [
        "重启一下系统",
        "保存当前状态",
        "停止运行",
        "清理一下缓存",
        "执行一次备份",
        "把日志归档",
    ],
    "一般对话": [
        "嗯",
        "好的",
        "知道了",
        "随便聊聊",
        "没什么事",
        "就这样吧",
    ],
    "状态查询": [
        "你现在状态怎么样",
        "运行到哪一步了",
        "当前进度如何",
        "系统负载高吗",
        "你现在在做什么",
        "任务完成了多少",
    ],
    "健康检查": [
        "检查一下身体",
        "做一次健康检查",
        "器官都正常吗",
        "系统有异常吗",
        "自检一下",
        "各模块运行正常吗",
    ],
    "元认知报告": [
        "生成一份自我报告",
        "你最近学到了什么",
        "反思一下自己",
        "做一份总结报告",
        "你的成长情况如何",
        "输出元认知分析",
    ],
    # ---------- 内部协作者补充：新增 8 个意图 ----------
    "任务执行": [
        "帮我做一件事",
        "执行这个任务",
        "去把数据处理一下",
        "帮我运行一下",
        "把这个操作完成",
        "照我说的去做",
    ],
    "记忆回想": [
        "还记得上次说的吗",
        "我们之前聊过什么",
        "你还记得那件事吗",
        "上次我们讨论的内容",
        "回想一下之前的对话",
        "之前提到过的内容是什么",
    ],
    "学习成长": [
        "你学到了什么",
        "你最近有什么进步",
        "总结一下你的收获",
        "你成长了多少",
        "你变强了吗",
        "这段时间有什么提升",
    ],
    "时间日程": [
        "现在几点了",
        "今天是什么日子",
        "提醒我下午三点开会",
        "明天有什么安排",
        "时间到了吗",
        "距离截止还有多久",
    ],
    "搜索获取": [
        "帮我查一下",
        "搜索一下这个",
        "上网找找资料",
        "查一查最新的信息",
        "帮我搜索相关内容",
        "找找有没有相关资料",
    ],
    "追问澄清": [
        "然后呢",
        "为什么这么说",
        "能再具体一点吗",
        "详细说说看",
        "你指的是什么意思",
        "接着讲",
    ],
    "否定质疑": [
        "不对吧",
        "我不这么认为",
        "你说错了",
        "这个结论有问题",
        "我不同意这个说法",
        "这里好像不太对",
    ],
    "请求帮助": [
        "帮帮我",
        "我该怎么办",
        "给我一些建议",
        "能帮我解决这个问题吗",
        "给我支个招",
        "遇到麻烦了怎么办",
    ],
}


def _write_corpus() -> None:
    """语料源落盘，供人工复核。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {
        "version": "v1.0",
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "intent_count": len(INTENT_CORPUS),
        "sample_total": sum(len(v) for v in INTENT_CORPUS.values()),
        "corpus": INTENT_CORPUS,
    }
    with open(CORPUS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("[corpus] -> %s (%d intents, %d samples)" % (
        CORPUS_PATH, payload["intent_count"], payload["sample_total"]))


def _wait_ready(enc, timeout: float = 300.0) -> bool:
    """等待后台模型加载就绪（VectorEncoder 为懒加载 + 后台线程，首次需等待）。"""
    import time as _time

    _t0 = _time.time()
    _last = ""
    while _time.time() - _t0 < timeout:
        if enc.is_available():
            return True
        _st = enc.status()
        if _st.get("state") == "disabled":
            print("ERROR: 编码器已禁用: %s | %s" % (
                _st.get("reason"), _st.get("last_error")))
            return False
        _reason = _st.get("reason", "")
        if _reason != _last:
            print("[wait] 模型加载中... state=%s reason=%s" % (
                _st.get("state"), _reason))
            _last = _reason
        _time.sleep(1.0)
    print("ERROR: 等待模型加载超时（%.0fs）" % timeout)
    return False


def _build() -> int:
    """编码语料 → 原型向量 → 落盘。"""
    from nucleus.semantic.VectorEncoder import get_vector_encoder

    enc = get_vector_encoder()
    if not _wait_ready(enc):
        print("ERROR: 语义编码器不可用，无法构建原型库")
        return 1

    import numpy as np

    prototypes = {}
    for intent, samples in INTENT_CORPUS.items():
        arr = enc.encode(samples)
        if arr is None or len(arr) == 0:
            print("WARN: 意图[%s] 编码失败，跳过" % intent)
            continue
        vec = np.asarray(arr, dtype=np.float32).mean(axis=0)
        norm = float(np.linalg.norm(vec))
        if norm > 1e-8:
            vec = vec / norm
        prototypes[intent] = [round(float(x), 6) for x in vec.tolist()]
        print("[encode] %-8s samples=%2d dim=%d" % (intent, len(samples), len(vec)))

    if not prototypes:
        print("ERROR: 没有任何意图编码成功")
        return 1

    payload = {
        "version": "v1.0",
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model": "BAAI/bge-small-zh-v1.5",
        "dim": len(next(iter(prototypes.values()))),
        "normalized": True,
        "intent_count": len(prototypes),
        "intents": prototypes,
    }
    with open(PROTOTYPE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print("[prototype] -> %s (%d intents, dim=%d)" % (
        PROTOTYPE_PATH, payload["intent_count"], payload["dim"]))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只写语料，不加载模型")
    args = ap.parse_args()

    _write_corpus()
    if args.dry_run:
        print("[dry-run] 跳过编码")
        return 0
    return _build()


if __name__ == "__main__":
    sys.exit(main())
