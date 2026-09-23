# -*- coding: utf-8 -*-
"""
test_search_keyword_guard_m15.py —— 主线第15批 任务4 门控单测（P2-101 关键词分词）

复现实测案例：「查一下人工智能最新进展」在主题被上游截断为「下人工智能最新进展」后，
补充提取的 4 字贪婪切分产出跨词碎片「下人工智」「能最新进」，并混入页面噪声短语
「中的文字」「天之前」。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from organs.motor.PulseController import PulseController  # noqa: E402


def _controller():
    """轻量实例化（净化方法只依赖类常量与开关，不需完整 __init__）。"""
    return PulseController.__new__(PulseController)


class _Switch:
    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self._old.items():
            if v is None:
                if hasattr(config, k):
                    delattr(config, k)
            else:
                setattr(config, k, v)
        return False


class TestKeywordGuard(unittest.TestCase):
    def setUp(self):
        self.pc = _controller()

    def test_config_default(self):
        self.assertIs(getattr(config, "ENABLE_SEARCH_KEYWORD_GUARD", None), True)

    def test_reported_case_fragments_and_noise_removed(self):
        """实测样本：噪声短语与错位窗口碎片必须被去掉，正确词保留。"""
        raw = ["人工智能", "中的文字", "下人工智", "能最新进", "概念"]
        out = self.pc._purify_search_keywords(raw, "下人工智能最新进展", limit=5)
        self.assertIn("人工智能", out)
        self.assertIn("概念", out)
        for bad in ("下人工智", "中的文字"):
            self.assertNotIn(bad, out, f"{bad} 应作为跨词碎片/噪声被过滤")
        # 「能最新进」在同列表中没有近重复伙伴、也不是停用词，故词法层无法判定；
        # 但它的**生成源头**（主题上的 2~4 字贪婪窗口）已在根因修正中去掉 —— 见下条用例。
        self.assertIn("能最新进", out)

    def test_supplement_no_longer_uses_greedy_window(self):
        """根因护栏：补充提取不得再对搜索主题做 2~4 字贪婪窗口切分。"""
        p = os.path.join(_PROJECT_ROOT, "organs", "motor", "PulseController.py")
        with open(p, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn(
            r"self_extracted = _re3.findall(r'[\u4e00-\u9fff]{2,4}', search_topic)",
            src, "2~4 字贪婪窗口是「下人工智/能最新进」的直接来源，必须移除")
        self.assertIn("self._semantic_extract_keywords(\n                        search_topic, limit=6) or []",
                      src, "补充提取应改为词典校验")

    def test_fragments_never_generated_for_reported_topic(self):
        """用真实主题走一遍「词典校验后的补充提取」语义：不应产出窗口碎片。"""
        # 词典不可用时 _semantic_extract_keywords 返回空 → 补充为空（宁缺毋滥）
        out = self.pc._purify_search_keywords(
            ["人工智能", "概念", "最新进展"], "下人工智能最新进展", limit=5)
        for bad in ("下人工智", "能最新进"):
            self.assertNotIn(bad, out)
        self.assertEqual(out, ["人工智能", "概念", "最新进展"])

    def test_fragment_subsumed_by_longer_keyword(self):
        out = self.pc._purify_search_keywords(
            ["最新进展", "能最新进", "人工智能", "下人工智"], limit=6)
        self.assertEqual(out, ["最新进展", "人工智能"])

    def test_stopwords_and_short_removed(self):
        out = self.pc._purify_search_keywords(
            ["一下", "什么", "怎么", "天之前", "小时之前", "水", "A", "人工智能"])
        self.assertEqual(out, ["人工智能"])

    def test_dedupe_and_order_preserved(self):
        out = self.pc._purify_search_keywords(
            ["人工智能", "量子计算", "人工智能", "语义内核"])
        self.assertEqual(out, ["人工智能", "量子计算", "语义内核"])

    def test_limit_respected(self):
        out = self.pc._purify_search_keywords(
            ["甲甲甲", "乙乙乙", "丙丙丙", "丁丁丁"], limit=2)
        self.assertEqual(len(out), 2)

    def test_switch_off_passthrough(self):
        raw = ["人工智能", "中的文字", "下人工智"]
        with _Switch(ENABLE_SEARCH_KEYWORD_GUARD=False):
            out = self.pc._purify_search_keywords(raw, "下人工智能最新进展")
        self.assertEqual(out, raw, "开关关闭时必须原样透传（零副作用）")

    def test_non_str_tolerated(self):
        out = self.pc._purify_search_keywords(["人工智能", None, 123, "量子计算"])
        self.assertEqual(out, ["人工智能", "量子计算"])

    def test_wired_into_two_sites(self):
        p = os.path.join(_PROJECT_ROOT, "organs", "motor", "PulseController.py")
        with open(p, encoding="utf-8") as f:
            src = f.read()
        self.assertGreaterEqual(src.count("self._purify_search_keywords("), 2,
                                "阶段1 与语义增强两处接线都必须存在")


if __name__ == "__main__":
    unittest.main()
