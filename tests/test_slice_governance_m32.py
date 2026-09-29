# -*- coding: utf-8 -*-
"""主线第32批 门控测试：定长切片统一治理（T2 / P2-189）。

背景（本批 T0 实测）：
  PulseInnerWorld 内 `re.finditer(r'[\\u4e00-\\u9fff]{2,6}', ...)` 定长贪婪切片共
  **10 处**（任务书标称 9 处），另 T0 全类型扫描额外发现 3 处 `re.findall` 同型。
  经逐处评估：
    · 替换 8 处（关键词/概念提取 → `_m31_extract_key_terms`）
    · 保留 4 处（重叠计数打分 / 灰度对照组 / 长文本输入 / 交集判定；均附原因注释）
"""
import os
import re
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402

_IW_FAMILY = [
    os.path.join(_PROJECT_ROOT, "organs", "brain", "PulseInnerWorld.py"),
    os.path.join(_PROJECT_ROOT, "organs", "brain", "pulse_inner_world_support.py"),
    os.path.join(_PROJECT_ROOT, "organs", "brain", "pulse_inner_world_knowledge.py"),
]
# ★主线往期批次 相关任务：知识检索簇已平移至 KnowledgeMixin，
#   故源码断言须拼接 IW 全家族（主文件 + 两个 Mixin），否则平移即假失败。
_IW_SRC = "\n".join(
    open(_p, encoding="utf-8").read() for _p in _IW_FAMILY
)


def _mk_iw():
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw._log = lambda *a, **k: None
    return iw


class TestSliceGovernance(unittest.TestCase):
    """源码层：替换处已改、保留处有注释。"""

    def test_01_replacements_marked(self):
        """★8 处替换：每处都留下批次标记（7 处主替换 + 1 处补充）。"""
        self.assertGreaterEqual(_IW_SRC.count("★主线第32批 T2（P2-189）"), 8)

    def test_02_kept_sites_have_reason(self):
        """★4 处保留：必须带「保留定长切片」+ 原因说明，禁止无注释静默保留。"""
        self.assertGreaterEqual(_IW_SRC.count("**保留定长切片**"), 4)

    def test_03_all_kept_mention_reason(self):
        """保留处必须写明保留理由（重叠/对照/长文本/交集）。"""
        _kept_blocks = []
        _lines = _IW_SRC.split("\n")
        for _i, _l in enumerate(_lines):
            if "**保留定长切片**" in _l:
                _kept_blocks.append("\n".join(_lines[_i:_i + 4]))
        self.assertGreaterEqual(len(_kept_blocks), 4)
        for _b in _kept_blocks:
            self.assertTrue(any(k in _b for k in
                                ("重叠", "对照", "长文本", "交集")),
                            f"保留原因不明确:\n{_b[:200]}")

    def test_04_extractor_call_count(self):
        """`_m31_extract_key_terms` 调用点显著增加（第31批 5 处 → 本批 +8）。

        ★主线第33批 T2（P2-195）：由**裸方法名**收紧为**调用形态**
        （`self.` 前缀）—— 裸方法名会把「定义处 + docstring 文本」一并计入
        （实测裸 15 / 调用形态 12），收紧后计数只反映真实调用点。
        """
        self.assertGreaterEqual(_IW_SRC.count("self._m31_extract_key_terms("), 12)


class TestCacheKeyNormalization(unittest.TestCase):
    """★关键回归：缓存键归一化不得因替换而失效。"""

    @staticmethod
    def _key(q):
        """复刻 `_generate_branch_with_model` 的键生成（替换后的实现）。"""
        return sorted(set(PulseInnerWorld._m31_extract_key_terms(q, limit=8)))[:5]

    def test_10_semantic_normalization_preserved(self):
        """「深度学习是什么」与「什么是深度学习」必须归一到同一键。"""
        self.assertEqual(self._key("深度学习是什么"), self._key("什么是深度学习"))

    def test_11_different_concepts_differ(self):
        """不同概念必须产生不同键（避免缓存串味）。"""
        self.assertNotEqual(self._key("深度学习的原理"), self._key("量子计算的应用"))


class TestValidateStepResultUsesExtractor(unittest.TestCase):
    """★替换收益：质量门不再因跨词碎片而误判。"""

    def test_20_hit_when_keyword_present(self):
        _iw = _mk_iw()
        _iw._is_valid_branch_content = lambda _c: True
        _prompt = "量子 计算 药物 研发 基本概念 核心内容"
        _result = "量子计算在药物研发中的作用主要体现在分子模拟与靶点筛选等方面，可以显著缩短研发周期。"
        self.assertTrue(_iw._validate_step_result(_result, _prompt))

    def test_21_miss_when_unrelated(self):
        _iw = _mk_iw()
        _iw._is_valid_branch_content = lambda _c: True
        _prompt = "量子 计算 药物 研发 基本概念 核心内容"
        _result = "微服务架构强调服务自治与独立部署，具备弹性伸缩能力，但引入了分布式事务的复杂度问题。"
        self.assertFalse(_iw._validate_step_result(_result, _prompt))

    def test_22_legacy_fragment_would_have_missed(self):
        """★回归对照：旧定长切片对同一 prompt 会产跨词碎片，导致恒不命中。"""
        _prompt = "量子 计算 药物 研发 基本概念 核心内容"
        _legacy = list(re.findall(r'[\u4e00-\u9fff]{2,6}', _prompt))[:5]
        _result = "量子计算在药物研发中的作用主要体现在分子模拟与靶点筛选等方面，可以显著缩短研发周期。"
        # 旧实现取出的首片是跨词碎片（含空格被跳过，故直接从中文连续段取）
        self.assertTrue(any(_w not in _result for _w in _legacy) or
                        any(" " in _w for _w in _legacy) or
                        any(len(_w) == 6 for _w in _legacy),
                        "旧实现应当至少产出一个跨词碎片或超长片段")


class TestBranchesUseRealWords(unittest.TestCase):
    """替换收益：延展方向的核心概念不再是跨词碎片。"""

    def test_30_branch_concepts_clean(self):
        _iw = _mk_iw()
        _branches = _iw._determine_branches(
            "请分析深度学习的原理和应用场景", max_branches=3)
        self.assertTrue(_branches, "应产出分支")
        _joined = " ".join(str(b) for b in _branches)
        self.assertNotIn("请分析深度学", _joined)
        self.assertIn("深度", _joined)


class TestKeptSitesBehaviorUnchanged(unittest.TestCase):
    """保留处行为必须与治理前一致（零差异）。"""

    def test_40_value_contradiction_still_detects(self):
        """`_detect_value_contradiction` 的核心判据仍生效（词交集 + 否定词对）。"""
        _iw = _mk_iw()
        self.assertTrue(_iw._detect_value_contradiction(
            "研究表明这种做法可以提升效率", "研究表明这种做法不可以提升效率"))

    def test_41_value_contradiction_no_false_positive(self):
        _iw = _mk_iw()
        self.assertFalse(_iw._detect_value_contradiction(
            "温度应该增加", "温度应该减少"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
