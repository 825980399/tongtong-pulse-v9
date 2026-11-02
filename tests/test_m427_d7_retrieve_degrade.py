# -*- coding: utf-8 -*-
"""427 刀7：经验检索 5 处前置早退必须给出**可区分**的降级原因。

守护三件事：
  1) 5 处早退的原因码互不相同（调用方能判「真没命中」还是「链路坏了」）；
  2) 正常命中路径 `degraded` 必须为 False（不许把成功也说成降级）；
  3) `retrieve()` 返回类型仍是 list（6 条既有单测断言 `== []`，不许改形态）。
"""
import os
import sys
import unittest
from typing import ClassVar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.mnemosyne.experience_retriever import ExperienceRetriever


class _DeadEncoder:
    def is_available(self):
        return False

    def encode(self, _t):
        return None

    def encode_one(self, _q):
        return None


class _NullMatrixEncoder:
    def is_available(self):
        return True

    def encode(self, _t):
        return None

    def encode_one(self, _q):
        return None


class _NullVecEncoder:
    def is_available(self):
        return True

    def encode(self, texts):
        import numpy as np
        return np.ones((len(texts), 3), dtype="float32")

    def encode_one(self, _q):
        return None


class _GoodEncoder:
    def is_available(self):
        return True

    def encode(self, texts):
        import numpy as np
        return np.tile(np.array([1.0, 0.0, 0.0], dtype="float32"), (len(texts), 1))

    def encode_one(self, _q):
        import numpy as np
        return np.array([1.0, 0.0, 0.0], dtype="float32")


class _EmptyPool:
    _experiences: ClassVar[list] = []


class _OnePool:
    _experiences: ClassVar[list] = [{"id": "e1", "summary": "L2 语义缓存命中率观察",
                     "timestamp": 1793600000, "activation_count": 5,
                     "decay_rate": 0.1, "quality": 0.9}]


def _run(pool, encoder, query="命中率"):
    r = ExperienceRetriever(pool=pool, encoder=encoder)
    out = r.retrieve_with_status(query)
    return out, r


class TestRetrieveDegrade(unittest.TestCase):
    def test_01_five_early_exits_have_distinct_reasons(self):
        """5 处早退原因码互不相同。"""
        _outs = [
            _run(_EmptyPool(), _GoodEncoder(), "")[0],       # query 空
            _run(_EmptyPool(), _DeadEncoder())[0],           # 编码器不可用
            _run(_EmptyPool(), _GoodEncoder())[0],           # 候选集为空
            _run(_OnePool(), _NullMatrixEncoder())[0],       # 向量化失败
            _run(_OnePool(), _NullVecEncoder())[0],          # query 编码失败
        ]
        for _o in _outs:
            self.assertTrue(_o["empty"], "五种场景都必须返回空")
            self.assertTrue(_o["degraded"], "五种场景都必须标降级")
            self.assertTrue(_o["reason"], "降级必须带原因")
        _codes = [o["reason"].split(":")[0] for o in _outs]
        self.assertEqual(len(set(_codes)), 5, f"原因码必须互不相同，实得: {_codes}")
        self.assertIn("query_empty", _codes)
        self.assertIn("encoder_unavailable", _codes)
        self.assertIn("no_candidates", _codes)
        self.assertIn("vectorize_failed", _codes)
        self.assertIn("query_encode_failed", _codes)

    def test_02_success_path_is_not_degraded(self):
        """正常命中 ⇒ degraded=False、reason 空（不许把成功说成降级）。"""
        out, r = _run(_OnePool(), _GoodEncoder())
        self.assertFalse(out["degraded"])
        self.assertEqual(out["reason"], "")
        self.assertFalse(out["empty"])
        self.assertEqual(len(out["hits"]), 1)
        self.assertFalse(r.last_degrade()["degraded"])

    def test_03_retrieve_return_type_unchanged(self):
        """`retrieve()` 必须仍返回 list（既有 6 条断言 == []）。"""
        r = ExperienceRetriever(pool=_EmptyPool(), encoder=_GoodEncoder())
        out = r.retrieve("命中率")
        self.assertIsInstance(out, list, "返回类型被改了，会炸既有断言")
        self.assertEqual(out, [])

    def test_04_degrade_does_not_mask_pool_failure(self):
        """池读不出来时，不许把原因粉饰成「池里没有经验」。

        这是 P0-09 的正题：`no_candidates` 只能用于「池可读但没条目」。
        """

        class _BrokenPool:
            @property
            def _experiences(self):
                raise RuntimeError("池坏了")

        r = ExperienceRetriever(pool=_BrokenPool(), encoder=_GoodEncoder())
        out = r.retrieve_with_status("命中率")
        self.assertTrue(out["degraded"])
        self.assertNotIn("no_candidates", out["reason"],
                         f"池故障被粉饰成「没条目」了：{out['reason']!r}")
        self.assertIn("RuntimeError", out["reason"], "池故障必须如实报出异常类型")


if __name__ == "__main__":
    unittest.main()
