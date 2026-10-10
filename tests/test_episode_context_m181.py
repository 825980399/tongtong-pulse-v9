# -*- coding: utf-8 -*-
"""第181批 刀4：记忆情景标签 + 近期上下文 门控单测（隔离可跑，零写盘 data/）。

覆盖：
  1. episode 三键结构（timestamp / context / participants）
  2. 对话条目自动打标 + 幂等
  3. PulseNode.episode 序列化往返
  4. 旧快照无 episode 键 → None（向前兼容）
  5. 启动加载最近 30 条并标记「近期上下文」
  6. 近期上下文关键词检索
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.mnemosyne.PulseNode import PulseNode  # noqa: E402
from nucleus.mnemosyne import recent_context as rc  # noqa: E402
from nucleus.mnemosyne.episode_tag import build_episode, tag_dialog_entry  # noqa: E402


def test_build_episode_three_keys():
    """episode 必须且仅有三键，类型收敛（participants 恒为 list[str]）。"""
    ep = build_episode("今天聊了路由分层", ["小林", "路灯"], 1700000000.0)
    assert set(ep.keys()) == {"timestamp", "context", "participants"}
    assert ep["timestamp"] == 1700000000.0
    assert ep["context"] == "今天聊了路由分层"
    assert ep["participants"] == ["小林", "路灯"]
    # 脏输入收敛：非 str context / 单值 participants / 缺失 timestamp
    ep2 = build_episode(None, "小林", None)
    assert ep2["context"] == "" and ep2["participants"] == ["小林"]
    assert isinstance(ep2["timestamp"], float) and ep2["timestamp"] > 0


def test_tag_dialog_entry_idempotent():
    """对话条目打标后含 episode；二次打标不覆盖首次时间戳（幂等）。"""
    entry = {"user_name": "小林", "question": "刀1白名单怎么分层的？", "timestamp": 1700000001.0}
    tag_dialog_entry(entry)
    assert isinstance(entry["episode"], dict)
    assert entry["episode"]["participants"] == ["小林"]
    first_ts = entry["episode"]["timestamp"]
    tag_dialog_entry(entry, context="覆盖尝试", participants=["路人"])
    assert entry["episode"]["timestamp"] == first_ts
    # 非 dict 输入零副作用返回原值
    assert tag_dialog_entry("not-a-dict") == "not-a-dict"


def test_pulse_node_episode_roundtrip():
    """PulseNode.attach_episode → to_dict → from_dict 全链路无损。"""
    node = PulseNode(value="对话情景：刀4验收", source_organ="肺")
    assert node.episode is None and node.has_episode() is False
    node.attach_episode("刀4验收对话", ["小林"], 1700000002.0)
    assert node.has_episode() is True
    restored = PulseNode.from_dict(json.loads(json.dumps(node.to_dict())))
    assert restored.has_episode() is True
    assert restored.episode["context"] == "刀4验收对话"
    assert restored.episode["participants"] == ["小林"]
    assert restored.episode["timestamp"] == 1700000002.0


def test_pulse_node_legacy_snapshot_no_episode():
    """旧快照缺 episode 键 → None（绝不因新字段导致加载异常）。"""
    node = PulseNode.from_dict({"value": "旧节点", "evol_level": "L1", "node_id": "node:old"})
    assert node.episode is None
    assert node.has_episode() is False
    assert node.to_dict()["episode"] is None


def test_load_recent_context_limit30_and_mark(tmp_path):
    """35 条 → 取最近 30 条，降序，全部带 episode 与「近期上下文」标记。"""
    users = {"小林": {"memories": []}}
    for i in range(35):
        users["小林"]["memories"].append({
            "user_name": "小林",
            "question": "第%d轮提问" % i,
            "answer_preview": "第%d轮回答" % i,
            "timestamp": 1700000000.0 + i,
        })
    (tmp_path / "conversation_memory.json").write_text(
        json.dumps({"users": users}), encoding="utf-8")
    recent = rc.load_recent_context(limit=30, base_dir=str(tmp_path))
    assert len(recent) == 30
    assert recent[0]["question"] == "第34轮提问"
    assert recent[-1]["question"] == "第5轮提问"
    for item in recent:
        assert item.get("recent_context") is True
        assert item.get("context_label") == rc.RECENT_CONTEXT_LABEL
        assert isinstance(item.get("episode"), dict)
    # 上限裁剪：limit=5 只取 5 条；空目录返回空且不抛
    assert len(rc.load_recent_context(limit=5, base_dir=str(tmp_path))) == 5
    assert rc.load_recent_context(base_dir=str(tmp_path / "none")) == []
    rc.clear_recent_context()


def test_search_recent_context(tmp_path):
    """近期上下文可按关键词检索；空关键词返回空。"""
    users = {"小林": {"memories": [
        {"user_name": "小林", "question": "第1轮提问", "answer_preview": "第1轮回答",
         "timestamp": 1700000001.0},
        {"user_name": "小林", "question": "第2轮提问", "answer_preview": "第2轮回答",
         "timestamp": 1700000002.0},
    ]}}
    (tmp_path / "conversation_memory.json").write_text(
        json.dumps({"users": users}), encoding="utf-8")
    rc.load_recent_context(limit=30, base_dir=str(tmp_path))
    assert len(rc.get_recent_context()) == 2
    hit = rc.search_recent_context("第2轮")
    assert len(hit) == 1 and hit[0]["question"] == "第2轮提问"
    assert rc.search_recent_context("") == []
    rc.clear_recent_context()
    assert rc.get_recent_context() == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
