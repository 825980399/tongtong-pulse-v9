# -*- coding: utf-8 -*-
"""239批 隔离单测：检索器影子档接线（零行为验证）。

- 刀3：影子行 available 首现——``retriever=available`` ＋ 条数/score 分布非空。
- 刀4：守护双态——``unavailable``（历史默认态，不改史）与 ``available``（新接线态）均成立。
- 刀2：形状对齐——``_extract_scores`` 同时认 ``score`` 与 ``_score`` 两种命中键。
- 刀1：接线——``_wire_shadow_v2_retriever`` 门开返可调用、门关/工厂异常回落 None。

验证一律走铁律151隔离：清环境 + 单文件 pytest。不实例化完整 PulseLung，
用 ``__new__`` 跳过 ``__init__``（沿用 237/238 既有范式）。
"""
import unittest
import unittest.mock as _mock

from types import SimpleNamespace

from nucleus.shadow_v2_probe import build_shadow_v2_fragment, _extract_scores
from organs.body.PulseLung import PulseLung


def _retriever_returning(hits):
    """返回一个可调用 (query, top_k)->list，模拟 ExperienceRetriever.retrieve。"""
    def _r(query, top_k=3):
        return list(hits)
    return _r


class _FakeLung(PulseLung):
    """跳过完整 __init__，仅继承 _wire_shadow_v2_retriever（239刀1 待测单元）。"""
    def __init__(self):
        # 故意不调用 PulseLung.__init__，_wire_shadow_v2_retriever 不依赖实例属性
        pass


class TestShadowV2Unavailable(unittest.TestCase):
    """刀4 双态·历史默认态：retriever=None -> unavailable（零行为，不改史）。"""
    def test_none_yields_unavailable(self):
        _frag = build_shadow_v2_fragment(None, "测试问题", top_k=3)
        self.assertIn("retriever=unavailable", _frag)
        self.assertIn("条数=0", _frag)

    def test_unavailable_keeps_zero_behavior(self):
        # 历史 340 条日志的 unavailable 表述语义不变（不污染、不翻牌）
        _frag = build_shadow_v2_fragment(None, "任何问题", top_k=3)
        self.assertIn("retriever=unavailable", _frag)
        # 精确标记：不可出现 available 态片段（"unavailable" 含子串 "available"，故查完整标记）
        self.assertNotIn("retriever=available", _frag)


class TestShadowV2Available(unittest.TestCase):
    """刀3 available 首现 + 刀4 双态·新态：retriever=available ＋ 条数/score 分布非空。"""
    def test_available_with_score_key(self):
        _hits = [{"score": 0.5, "id": "a"}, {"score": 0.8, "id": "b"}, {"score": 0.3, "id": "c"}]
        _frag = build_shadow_v2_fragment(_retriever_returning(_hits), "测试问题", top_k=3)
        self.assertIn("retriever=available", _frag)
        self.assertIn("条数=3", _frag)
        self.assertIn("min0.3", _frag)
        self.assertIn("max0.8", _frag)
        self.assertIn("mean", _frag)

    def test_available_with_underscore_score_key(self):
        # 239刀2 形状对齐：真实 ExperienceRetriever.retrieve 返回 _score 键
        _hits = [{"_score": 0.5, "id": "a"}, {"_score": 0.8, "id": "b"}]
        _frag = build_shadow_v2_fragment(_retriever_returning(_hits), "测试问题", top_k=3)
        self.assertIn("retriever=available", _frag)
        self.assertIn("条数=2", _frag)
        self.assertIn("min0.5", _frag)
        self.assertIn("max0.8", _frag)

    def test_available_empty_hits(self):
        _frag = build_shadow_v2_fragment(_retriever_returning([]), "测试问题", top_k=3)
        self.assertIn("retriever=available", _frag)
        self.assertIn("条数=0", _frag)


class TestShadowV2Error(unittest.TestCase):
    """异常被吞，返回 error 片段（不向上抛，零行为承诺）。"""
    def test_retriever_raises_returns_error_fragment(self):
        def _boom(query, top_k=3):
            raise RuntimeError("boom")
        _frag = build_shadow_v2_fragment(_boom, "测试问题", top_k=3)
        self.assertIn("retriever=error", _frag)


class TestShadowV2ExtractScores(unittest.TestCase):
    """刀2 形状对齐：_extract_scores 同时认 score 与 _score。"""
    def test_score_key(self):
        self.assertEqual(_extract_scores([{"score": 0.5}, {"score": 0.8}]), [0.5, 0.8])

    def test_underscore_score_key(self):
        self.assertEqual(_extract_scores([{"_score": 0.5}, {"_score": 0.8}]), [0.5, 0.8])

    def test_mixed_keys(self):
        self.assertEqual(_extract_scores([{"score": 0.5}, {"_score": 0.8}]), [0.5, 0.8])


class TestShadowV2Wiring(unittest.TestCase):
    """刀1 接线：_wire_shadow_v2_retriever 门开返可调用、关门/异常回落 None。"""
    def test_wiring_enabled_returns_callable(self):
        _fake_retriever = SimpleNamespace(
            retrieve=lambda q, k=3: [{"_score": 0.5, "id": "a"}])
        with _mock.patch(
                "nucleus.mnemosyne.experience_retriever.retriever_enabled",
                return_value=True), \
                _mock.patch(
                "nucleus.mnemosyne.experience_retriever.get_retriever",
                return_value=_fake_retriever):
            _l = _FakeLung()
            _r = _l._wire_shadow_v2_retriever()
        self.assertTrue(callable(_r))
        _frag = build_shadow_v2_fragment(_r, "q", top_k=3)
        self.assertIn("retriever=available", _frag)

    def test_wiring_disabled_returns_none(self):
        with _mock.patch(
                "nucleus.mnemosyne.experience_retriever.retriever_enabled",
                return_value=False):
            _l = _FakeLung()
            _r = _l._wire_shadow_v2_retriever()
        self.assertIsNone(_r)


if __name__ == "__main__":
    unittest.main()
