# -*- coding: utf-8 -*-
"""第138批 T-138d 门控测试：语义缓存 L2（返回级）。

★全部使用**注入的确定性假编码器** —— 不加载 90MB 真模型。
★纯内存层，不写任何生产 data/ 文件。
覆盖：
  * 相同问题第二次问 → 直接命中缓存（核心验收）
  * TTL 1 小时过期 → 不命中
  * 内存 LRU 淘汰（超容量）
  * 四道闸门：置信 / 时效 / 幂等（origin+指代+短prompt） / 质量
  * 灰度开关关闭 → 零副作用
  * 单例 + 便捷入口
"""
import hashlib
import importlib
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

_sc = importlib.import_module("nucleus.llm.semantic_cache")


class _HashEncoder:
    """确定性假编码器：相同文本 → 相同向量（测试用）。"""

    def __init__(self, dim=8):
        self.dim = dim

    def encode_one(self, text):
        if not text:
            return None
        _h = hashlib.sha256(text.encode("utf-8")).digest()
        _v = [float(_h[i % len(_h)]) - 128.0 for i in range(self.dim)]
        _n = sum(x * x for x in _v) ** 0.5 or 1.0
        return [x / _n for x in _v]


def _vec(*vals):
    _n = sum(v * v for v in vals) ** 0.5 or 1.0
    return [v / _n for v in vals]


_LONG_Q = "请详细介绍一下量子计算的基本原理和应用场景"


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved = {k: getattr(config, k, None) for k in
                       ("ENABLE_SEMANTIC_CACHE_L2", "SEMANTIC_CACHE_L2_TTL_SEC",
                        "SEMANTIC_CACHE_L2_CAPACITY", "SEMANTIC_CACHE_L2_MIN_LEN",
                        "SEMANTIC_CACHE_L2_RATIO", "SEMANTIC_CACHE_THRESHOLD")}
        config.ENABLE_SEMANTIC_CACHE_L2 = True

    def tearDown(self):
        for _k, _v in self._saved.items():
            if _v is not None:
                setattr(config, _k, _v)
        _sc.reset_semantic_cache_l2()

    def _l2(self, encoder=None):
        return _sc.SemanticCacheL2(encoder=encoder or _HashEncoder())


class TestCore(_Base):
    def test_01_same_question_second_hit(self):
        """★核心验收：相同问题第二次问 → 直接命中缓存。"""
        _l2 = self._l2()
        self.assertTrue(_l2.store(_LONG_Q, "量子计算的答案是……", origin="user_query"))
        _r = _l2.lookup(_LONG_Q, origin="user_query")
        self.assertIsNotNone(_r)
        self.assertEqual(_r["response"], "量子计算的答案是……")
        self.assertGreaterEqual(_r["similarity"], 0.99)

    def test_02_no_store_no_hit(self):
        _l2 = self._l2()
        self.assertIsNone(_l2.lookup(_LONG_Q, origin="user_query"))

    def test_03_store_then_different_question_miss(self):
        _l2 = self._l2()
        _l2.store(_LONG_Q, "答案", origin="user_query")
        # 完全不同的话题 → 向量正交 → 不命中
        self.assertIsNone(_l2.lookup("明天上海的天气预报怎么样呀", origin="user_query"))


class TestTimeGate(_Base):
    def test_10_ttl_default_1h(self):
        _l2 = self._l2()
        self.assertEqual(_l2.ttl_sec(), 3600.0)

    def test_11_expired_not_hit(self):
        config.SEMANTIC_CACHE_L2_TTL_SEC = 3600
        _l2 = self._l2()
        _l2.store(_LONG_Q, "答案", origin="user_query")
        # 手工把条目 ts 改老（> 1h）→ 懒删 → 不命中
        for _e in _l2._entries.values():
            _e.ts = time.time() - 7200
        self.assertIsNone(_l2.lookup(_LONG_Q, origin="user_query"))

    def test_12_within_ttl_hit(self):
        _l2 = self._l2()
        _l2.store(_LONG_Q, "答案", origin="user_query")
        for _e in _l2._entries.values():
            _e.ts = time.time() - 1800     # 30 分钟内
        self.assertIsNotNone(_l2.lookup(_LONG_Q, origin="user_query"))


class TestMemoryEviction(_Base):
    def test_20_lru_eviction(self):
        config.SEMANTIC_CACHE_L2_CAPACITY = 3
        _l2 = self._l2()
        for _i in range(5):
            _l2.store(f"问题内容很长很长第{_i}号", "答案", origin="user_query")
            time.sleep(0.005)
        self.assertEqual(_l2.stats()["entries"], 3)

    def test_21_lru_by_last_hit(self):
        """LRU 按 last_hit_ts（命中过的热条目不被先淘汰）。"""
        config.SEMANTIC_CACHE_L2_CAPACITY = 3
        _l2 = self._l2()
        _l2.store("第一个足够长的问题内容", "A", origin="user_query")
        time.sleep(0.01)
        _l2.store("第二个足够长的问题内容", "B", origin="user_query")
        time.sleep(0.01)
        _l2.store("第三个足够长的问题内容", "C", origin="user_query")
        _l2.lookup("第一个足够长的问题内容", origin="user_query")  # 刷新 last_hit
        time.sleep(0.01)
        _l2.store("第四个足够长的问题内容", "D", origin="user_query")  # 触发淘汰
        # 第一个被命中刷新过 → 应保留
        self.assertIsNotNone(_l2.lookup("第一个足够长的问题内容", origin="user_query"))


class TestGates(_Base):
    def test_30_origin_gate_store(self):
        """非 user_query 不写入。"""
        _l2 = self._l2()
        self.assertFalse(_l2.store(_LONG_Q, "答案", origin="system_internal"))
        self.assertFalse(_l2.store(_LONG_Q, "答案", origin="evolution_task"))

    def test_31_origin_gate_lookup(self):
        _l2 = self._l2()
        _l2.store(_LONG_Q, "答案", origin="user_query")
        self.assertIsNone(_l2.lookup(_LONG_Q, origin="system_internal"))

    def test_32_short_prompt_not_cached(self):
        _l2 = self._l2()
        self.assertFalse(_l2.store("在吗", "在的", origin="user_query"))

    def test_33_deictic_skipped(self):
        _l2 = self._l2()
        self.assertFalse(_l2.store("上面那个问题再详细说说", "答案", origin="user_query"))
        self.assertIsNone(_l2.lookup("上面那个问题再详细说说", origin="user_query"))

    def test_34_time_marker_skipped(self):
        _l2 = self._l2()
        self.assertFalse(_l2.store("今天上海天气怎么样呢", "答案", origin="user_query"))

    def test_35_uuid_skipped(self):
        _l2 = self._l2()
        self.assertFalse(_l2.store("请处理任务 550e8400-e29b-41d4 的结果",
                                   "答案", origin="user_query"))

    def test_36_confidence_gate(self):
        config.SEMANTIC_CACHE_THRESHOLD = 0.99
        _l2 = self._l2()
        _l2.store(_LONG_Q, "答案", origin="user_query")
        # 用一个非常相近但非同一的向量（cos 高但 < 0.99 阈值）
        _near = _sc.SemanticCacheL2(encoder=_HashEncoder())
        _near._entries = _l2._entries
        self.assertIsNone(_near.lookup(_LONG_Q + "补充", origin="user_query"))

    def test_37_empty_response_rejected(self):
        _l2 = self._l2()
        self.assertFalse(_l2.store(_LONG_Q, "", origin="user_query"))

    def test_38_ratio_gate(self):
        config.SEMANTIC_CACHE_L2_RATIO = 0.0     # 全不分桶 → 全部拒绝
        _l2 = self._l2()
        _l2.store(_LONG_Q, "答案", origin="user_query")
        self.assertIsNone(_l2.lookup(_LONG_Q, origin="user_query"))


class TestDisabled(_Base):
    def test_40_switch_off_zero_side_effect(self):
        config.ENABLE_SEMANTIC_CACHE_L2 = False
        _l2 = self._l2()
        self.assertFalse(_l2.store(_LONG_Q, "答案", origin="user_query"))
        self.assertIsNone(_l2.lookup(_LONG_Q, origin="user_query"))
        self.assertEqual(_l2.stats()["entries"], 0)


class TestSingleton(_Base):
    def test_50_singleton(self):
        _a = _sc.get_semantic_cache_l2()
        _b = _sc.get_semantic_cache_l2()
        self.assertIs(_a, _b)

    def test_51_lookup_store_helpers(self):
        _c = _sc.get_semantic_cache_l2()
        _c._encoder_override = _HashEncoder()
        self.assertTrue(_sc.store_l2(_LONG_Q, "答案", origin="user_query"))
        _r = _sc.lookup_l2(_LONG_Q, origin="user_query")
        self.assertIsNotNone(_r)
        self.assertEqual(_r["response"], "答案")

    def test_52_helpers_never_raise_when_disabled(self):
        config.ENABLE_SEMANTIC_CACHE_L2 = False
        self.assertIsNone(_sc.lookup_l2(_LONG_Q, origin="user_query"))
        self.assertFalse(_sc.store_l2(_LONG_Q, "答案", origin="user_query"))


class TestL1Compat(_Base):
    def test_60_lookup_with_content_flag(self):
        """L1 lookup 新增 with_content：默认 False 不返回内容；True 才返回。"""
        _c = _sc.SemanticCache(base_dir=None, encoder=_HashEncoder(), auto_start=False)
        _c._base_dir_override = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".pytest_tmp", "m138_l2_l1compat")
        _c.add(_LONG_Q, "L1答案", vec=_vec(1, 0, 0, 0, 0, 0, 0, 0))
        _r_default = _c.lookup(_LONG_Q, vec=_vec(1, 0, 0, 0, 0, 0, 0, 0))
        self.assertNotIn("response", _r_default)          # L1 红线不变
        _r_content = _c.lookup(_LONG_Q, vec=_vec(1, 0, 0, 0, 0, 0, 0, 0),
                               with_content=True)
        self.assertEqual(_r_content.get("response"), "L1答案")

    def test_61_import_surface(self):
        for _n in ("SemanticCacheL2", "get_semantic_cache_l2",
                   "lookup_l2", "store_l2", "reset_semantic_cache_l2"):
            self.assertTrue(hasattr(_sc, _n), _n)


if __name__ == "__main__":
    unittest.main()
