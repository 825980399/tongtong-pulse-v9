"""188·刀1 隔离单测：语义缓存 L2 只读遥测（命中率/污染率·按场景分桶）。

验证点：
- 场景分类（代码类/闲聊类/推理类）确定性正确；
- store+lookup 同串 → 命中，遥测按场景累加 hits；
- 不同串 → 未命中，遥测按场景累加 misses；
- record_feedback(negative) → 按场景累加 neg，pollution_rate = neg/hits；
- 会话私有串（含时间/指代词）被拒 → 不入库、遥测 miss 累加、不崩；
- telemetry() 输出 hit_rate/pollution_rate；stats() 含 telemetry 快照；
- 零行为变化：命中返回字典形态不变（response/similarity/entry_hash/origin）。

隔离：注入假编码器（不加载 90MB 真模型），monkeypatch _l2_cfg 使 L2 开启且
阈值/容量等取默认。不改动任何既有测试集合。
"""
import math
import sys

import pytest

sys.path.insert(0, ".")

import nucleus.llm.semantic_cache as sc


class _FakeEnc:
    """确定性假编码器：不同文本 → 不同稀疏向量（余弦低）；同文本 → 同向量（余弦=1）。"""

    def encode_one(self, text):
        v = [0.0] * 512
        for i, ch in enumerate(str(text)[:96]):
            v[(ord(ch) * 31 + i * 7) % 512] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]


@pytest.fixture
def l2():
    # 隔离：强制 L2 开启，其余配置取默认
    sc._l2_cfg = lambda name, default: (True if name == "ENABLE_SEMANTIC_CACHE_L2" else default)
    inst = sc.SemanticCacheL2(encoder=_FakeEnc())
    yield inst
    sc.reset_semantic_cache_l2()


def test_scene_classify():
    assert sc._l2_classify_scene("def foo(): return 1 and import os") == "code"
    assert sc._l2_classify_scene("为什么天空是蓝的？请分析原因推导") == "reasoning"
    # 闲聊（无代码/推理标记、无时间/指代词）
    assert sc._l2_classify_scene("我挺喜欢看科幻片你觉得呢") == "chat"
    assert sc._l2_classify_scene("") == "chat"


def test_hit_buckets_by_scene(l2):
    assert l2.store("def add(a,b): return a+b", "resp", origin="user_query") is True
    r = l2.lookup("def add(a,b): return a+b", origin="user_query")  # 同串→命中
    assert r is not None and r["response"] == "resp"
    t = l2.telemetry()
    assert t["code"]["hits"] == 1
    assert t["overall"]["hits"] == 1
    assert t["code"]["hit_rate"] == 1.0


def test_miss_buckets_by_scene(l2):
    l2.store("为什么水会沸腾？请分析原因", "resp", origin="user_query")
    # 不同串（同为推理类）→ 向量不同 → 未命中，落入 reasoning 分桶
    r = l2.lookup("如何论证量子纠缠违背经典直觉的原因", origin="user_query")
    assert r is None
    t = l2.telemetry()
    assert t["reasoning"]["misses"] == 1
    assert t["overall"]["misses"] == 1


def test_feedback_pollution_rate(l2):
    l2.store("def mul(x,y): return x*y", "resp", origin="user_query")
    r = l2.lookup("def mul(x,y): return x*y", origin="user_query")
    assert r is not None
    l2.record_feedback(r["entry_hash"], negative=True)
    t = l2.telemetry()
    assert t["code"]["neg"] == 1
    assert t["code"]["pollution_rate"] == 1.0
    assert t["overall"]["neg"] == 1
    assert t["overall"]["pollution_rate"] == 1.0
    # 正反馈不计入 neg
    l2.record_feedback(r["entry_hash"], negative=False)
    assert l2.telemetry()["code"]["neg"] == 1


def test_session_private_rejected_not_stored(l2):
    # 含时间词 → 会话私有 → 不入库
    assert l2.store("今天天气真不错我们聊聊天吧", "resp", origin="user_query") is False
    r = l2.lookup("今天天气真不错我们聊聊天吧", origin="user_query")
    assert r is None  # 静默回落
    t = l2.telemetry()
    assert t["overall"]["misses"] >= 1  # 拒绝路径计入 miss


def test_telemetry_and_stats_shape(l2):
    l2.store("如何推导欧拉公式？请论证原因", "resp", origin="user_query")
    l2.lookup("如何推导欧拉公式？请论证原因", origin="user_query")
    t = l2.telemetry()
    assert set(t.keys()) == {"overall", "code", "chat", "reasoning"}
    for bucket in t.values():
        assert set(bucket.keys()) == {"hits", "misses", "neg", "hit_rate", "pollution_rate"}
    s = l2.stats()
    assert "telemetry" in s
    assert s["enabled"] is True


def test_lookup_return_shape_unchanged(l2):
    l2.store("def f(): pass", "body", origin="user_query")
    r = l2.lookup("def f(): pass", origin="user_query")
    assert r is not None
    assert set(r.keys()) == {"response", "similarity", "entry_hash", "origin"}
