# -*- coding: utf-8 -*-
"""
episode_tag.py —— 记忆情景标签（第181批 刀4）

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年10月10日

职责: 为「对话记忆条目 / 脉冲节点」生成并归一化情景标签 episode
机制: 纯函数模块（无状态、无 IO、无 except），供 PulseNode 与 ContextSnapshot 共用
定位: 记忆核心层

episode 结构（固定三键，缺键补默认值，绝不静默丢字段）：
    {"timestamp": float, "context": str, "participants": list[str]}

说明（★第181批 刀4 偏差 D#7）：
    任务书所称「对话节点」在仓库中并不存在——对话记忆以 dict 条目形式存放于
    ContextSnapshot（data/context/conversation_memory.json），不进 PulseNodePool。
    故本批同时支持两种载体：对话条目（dict）与脉冲节点（PulseNode.episode）。
"""
import time

RECENT_CONTEXT_LABEL = "近期上下文"
EPISODE_CONTEXT_MAXLEN = 80


def build_episode(context="", participants=None, timestamp=None):
    """构造归一化的 episode 三元组（时间戳 / 上下文 / 参与者）。"""
    if isinstance(timestamp, (int, float)):
        _ts = float(timestamp)
    else:
        _ts = time.time()
    if isinstance(context, str):
        _ctx = context[:EPISODE_CONTEXT_MAXLEN]
    elif context is None:
        _ctx = ""
    else:
        _ctx = str(context)[:EPISODE_CONTEXT_MAXLEN]
    if isinstance(participants, (list, tuple)):
        _ps = [str(_p) for _p in participants]
    elif participants is None:
        _ps = []
    else:
        _ps = [str(participants)]
    return {"timestamp": _ts, "context": _ctx, "participants": _ps}


def normalize_episode(raw):
    """把任意来源的 episode 归一为三键结构；非 dict → None（表示无情景标签）。"""
    if not isinstance(raw, dict):
        return None
    return build_episode(raw.get("context", ""), raw.get("participants"), raw.get("timestamp"))


def tag_dialog_entry(entry, context=None, participants=None):
    """给一条对话记忆条目打情景标签（就地写入，返回同一对象）。

    幂等：已有 episode 时不覆盖（保留首次写入的场景时间戳，避免重复打标漂移）。
    """
    if not isinstance(entry, dict):
        return entry
    if isinstance(entry.get("episode"), dict):
        return entry
    if context is None:
        context = str(entry.get("question", ""))[:EPISODE_CONTEXT_MAXLEN]
    if participants is None:
        _uname = entry.get("user_name", "")
        participants = [str(_uname)] if _uname else []
    entry["episode"] = build_episode(context, participants, entry.get("timestamp"))
    return entry
