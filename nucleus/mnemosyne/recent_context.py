# -*- coding: utf-8 -*-
"""
recent_context.py —— 近期上下文加载与检索（第181批 刀4）

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年10月10日

职责: 启动时从对话记忆快照加载最近 N 条对话，标记「近期上下文」并提供检索
机制: 只读加载（零写盘）+ 进程内缓存；全函数零 except（异常面由 safe_read_json 兜底）
定位: 记忆核心层

★第181批 刀4 偏差 D#7：
    任务书称「加载到 L2 工作记忆」——仓库内不存在独立的「L2 工作记忆层」
    （L2 是 PulseNode.evol_level 演化层级，非容器）。本批按「不新建记忆层」约束，
    以进程内近期上下文缓存承载等价能力，并暴露检索接口，不落新文件、不改快照。
"""
import os

from nucleus.data.DataAccessLayer import safe_read_json
from nucleus.mnemosyne.episode_tag import RECENT_CONTEXT_LABEL, tag_dialog_entry

DEFAULT_RECENT_LIMIT = 30
DEFAULT_BASE_DIR = "data/context"
CONVERSATION_FILE = "conversation_memory.json"

_CACHE = []


def _episode_ts(entry):
    """取条目排序时间戳：优先 episode.timestamp，回落条目 timestamp，缺失记 0.0。"""
    _ep = entry.get("episode")
    if isinstance(_ep, dict) and isinstance(_ep.get("timestamp"), (int, float)):
        return float(_ep["timestamp"])
    _ts = entry.get("timestamp")
    if isinstance(_ts, (int, float)):
        return float(_ts)
    return 0.0


def _collect(base_dir, user_name):
    """从快照读取全部对话条目（副本），补齐 user_name 并打情景标签。"""
    _path = os.path.join(base_dir, CONVERSATION_FILE)
    if not os.path.exists(_path):
        return []
    _data = safe_read_json(_path, default={})
    if not isinstance(_data, dict):
        return []
    _out = []
    for _uname, _part in (_data.get("users", {}) or {}).items():
        if user_name is not None and _uname != user_name:
            continue
        if not isinstance(_part, dict):
            continue
        for _mem in (_part.get("memories", []) or []):
            if not isinstance(_mem, dict):
                continue
            _item = dict(_mem)
            if not _item.get("user_name"):
                _item["user_name"] = _uname
            tag_dialog_entry(_item)
            _out.append(_item)
    return _out


def load_recent_context(limit=DEFAULT_RECENT_LIMIT, user_name=None, base_dir=DEFAULT_BASE_DIR):
    """加载最近 limit 条对话（按时间降序），标记「近期上下文」并写入进程内缓存。"""
    global _CACHE
    _all = _collect(base_dir, user_name)
    _all.sort(key=_episode_ts, reverse=True)
    _n = limit if isinstance(limit, int) else DEFAULT_RECENT_LIMIT
    _recent = _all[:max(0, _n)]
    for _item in _recent:
        _item["recent_context"] = True
        _item["context_label"] = RECENT_CONTEXT_LABEL
    _CACHE = _recent
    return _recent


def get_recent_context():
    """返回已缓存的近期上下文（副本，防外部篡改缓存）。"""
    return [dict(_x) for _x in _CACHE]


def search_recent_context(keyword, limit=10):
    """在近期上下文中按关键词检索（question / answer_preview 子串匹配）。"""
    _kw = str(keyword or "").strip()
    if not _kw:
        return []
    _cap = limit if isinstance(limit, int) else 10
    _out = []
    for _item in _CACHE:
        _hay = "{} {}".format(_item.get("question", ""), _item.get("answer_preview", ""))
        if _kw in _hay:
            _out.append(dict(_item))
        if len(_out) >= max(0, _cap):
            break
    return _out


def clear_recent_context():
    """清空近期上下文缓存（测试 / 离场用）。"""
    global _CACHE
    _CACHE = []
