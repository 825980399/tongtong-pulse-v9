# -*- coding: utf-8 -*-
"""B156-10 运行核验 · 语义缓存 hit_count 字段回写门控测试。

验证「命中口径」修复（任务书：hit_count 死置 → 字段回写）：
  1. 同 hash 二次 add 不再把命中数清零（`add` 保留既有 hit_count）；
  2. `flush()` 将累加的 hit_count 真正落盘到 semantic_cache.jsonl；
  3. 跨重载（新实例重新 _load）后 hit_count 保留。

使用注入式假编码器（返回固定向量），不加载真模型、不触碰生产 data/cache。
"""
import hashlib
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.llm.semantic_cache import SemanticCache


class _FakeEncoder:
    """返回固定向量，保证同文本余弦 = 1.0（≥ 阈值 → 命中）。"""

    def encode_one(self, text):
        return [1.0] * 64


def _hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8", "ignore")).hexdigest()[:16]


def _disk_hit_count(base_dir: str, prompt: str):
    _fp = os.path.join(base_dir, "semantic_cache.jsonl")
    _h = _hash(prompt)
    with open(_fp, encoding="utf-8") as f:
        for _line in f:
            _line = _line.strip()
            if not _line:
                continue
            _d = json.loads(_line)
            if _d.get("hash") == _h:
                return _d.get("hit_count", 0)
    return None


def test_hit_count_preserved_and_written_back():
    _tmp = tempfile.mkdtemp()
    _c = SemanticCache(base_dir=_tmp, encoder=_FakeEncoder(), auto_start=False)
    try:
        assert _c.add("同一个问题", "应答一") is True
        # 第一次命中：内存 hit_count 应 = 1
        _out = _c.lookup("同一个问题")
        assert _out is not None and _out["hit"] is True
        assert _out["hit_count"] == 1
        # 同 hash 二次写入（模拟 _handle 流程里 add 跟进），不应清零命中数
        assert _c.add("同一个问题", "应答二") is True
        # 落盘回写
        _c.flush()
        # 磁盘上的 hit_count 必须是 1（而非被 0 覆盖）
        assert _disk_hit_count(_tmp, "同一个问题") == 1
        # 跨重载：新实例重新加载后应保留落盘的 hit_count
        _c2 = SemanticCache(base_dir=_tmp, encoder=_FakeEncoder(), auto_start=False)
        _c2._load()
        assert _c2._entries[_hash("同一个问题")].hit_count == 1
    finally:
        _c.close()
