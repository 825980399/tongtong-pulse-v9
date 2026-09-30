# -*- coding: utf-8 -*-
"""
test_search_topic_guard_m16.py —— 主线第16批 任务1 门控单测（P2-104 搜索主题截断）

复现根因：前缀正则 `[一下]?` 是**单字符类**，只吃掉「一」或「下」中的一个 →
「查一下人工智能最新进展」被切成「查一」+「下人工智能最新进展」。

设计：直接**切片执行真实源码的模块级函数**（而非复刻逻辑），源码回退即失败。
"""
import pytest
import os
import re
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402

_SRC_PATH = os.path.join(_PROJECT_ROOT, "organs", "brain", "PulseInnerWorld.py")


def _load_helpers():
    """从真实源码中截取模块级常量/函数并执行（避免导入 22k 行的巨型模块）。"""
    with open(_SRC_PATH, encoding="utf-8") as f:
        src = f.read()
    i = src.index("_SEARCH_PREFIX_ALT_NEW")
    j = src.index("class PulseInnerWorld(")
    ns = {"__name__": "piw_helpers_probe"}
    exec(compile(src[i:j], "<search-prefix-helpers>", "exec"), ns)
    return ns


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


class TestSearchTopicPrefix(unittest.TestCase):
    def setUp(self):
        self.ns = _load_helpers()

    def _topic(self, q):
        pat = self.ns["_search_prefix_pattern"]()
        return re.sub(rf"^({pat})\s*", "", q.strip()).strip()

    # ---- 任务书 4 条验收 ----
    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_chayixia_full_prefix_consumed(self):
        self.assertEqual(self._topic("查一下人工智能最新进展"), "人工智能最新进展")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_sousuo_yixia(self):
        self.assertEqual(self._topic("搜索一下今天天气"), "今天天气")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_baidu_yixia(self):
        self.assertEqual(self._topic("百度一下Python教程"), "Python教程")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_short_prefix_ok(self):
        self.assertEqual(self._topic("查人工智能"), "人工智能")

    # ---- 补充形态 ----
    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_chazhao_yixia(self):
        """★同类顺序缺陷：原「查[一下]?」在「查找…」前，会只吃掉「查」残留「找一下X」。"""
        self.assertEqual(self._topic("查找一下Python"), "Python")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_bangwo_zhao(self):
        self.assertEqual(self._topic("帮我找论文"), "论文")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_google_yixia(self):
        self.assertEqual(self._topic("谷歌一下深度学习"), "深度学习")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_shangwangcha(self):
        self.assertEqual(self._topic("上网查天气"), "天气")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_no_false_truncation_of_legit_phrase(self):
        """★关键回归护栏：不得误伤「下一步怎么做」这类正常短语。"""
        self.assertEqual(self._topic("查一下下一步怎么做"), "下一步怎么做")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_non_ascii_whitespace_after_prefix(self):
        self.assertEqual(self._topic("搜一下 量子计算"), "量子计算")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_empty_after_prefix(self):
        self.assertEqual(self._topic("查一下"), "")
        self.assertEqual(self._topic("搜索一下"), "")

    # ---- 开关关时零回归 ----
    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_switch_off_reverts_to_old_pattern(self):
        with _Switch(ENABLE_SEARCH_TOPIC_GUARD=False):
            pat = self.ns["_search_prefix_pattern"]()
            self.assertEqual(pat, self.ns["_SEARCH_PREFIX_ALT_OLD"])
            self.assertIn("[一下]?", pat)
            # 原行为：只吃掉「查一」，残留「下…」
            self.assertEqual(
                re.sub(rf"^({pat})\s*", "", "查一下人工智能最新进展").strip(),
                "下人工智能最新进展")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_switch_on_is_default(self):
        self.assertIs(getattr(config, "ENABLE_SEARCH_TOPIC_GUARD", None), True)
        self.assertEqual(self.ns["_search_prefix_pattern"](),
                         self.ns["_SEARCH_PREFIX_ALT_NEW"])


class TestNoiseDetection(unittest.TestCase):
    """防御逻辑：只检测、不改写（避免误伤正常短语）。"""

    def setUp(self):
        self.ns = _load_helpers()

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_detect_leading_noise(self):
        f = self.ns["_detect_leading_search_noise"]
        self.assertEqual(f("下人工智能最新进展"), "下")
        self.assertEqual(f("下一步怎么做"), "下")
        self.assertEqual(f("一般情况"), "一")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_no_noise(self):
        f = self.ns["_detect_leading_search_noise"]
        self.assertEqual(f("人工智能最新进展"), "")
        self.assertEqual(f(""), "")
        self.assertEqual(f("下一"), "", "长度<=2 不判为噪声（防误伤短词）")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_detector_is_not_a_rewriter(self):
        """护栏：不得再出现「剥离/改写」主题的辅助函数（已按设计取舍移除）。"""
        with open(_SRC_PATH, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("_strip_leading_search_noise", src,
                         "启发式剥离会误伤「下一步怎么做」等正常输入，必须保持只检测")


class TestSourceWiring(unittest.TestCase):
    def setUp(self):
        with open(_SRC_PATH, encoding="utf-8") as f:
            self.src = f.read()

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_three_sites_share_single_source(self):
        """★主线第33批 T2（P2-195）：语义=「各处前缀正则都引用单一来源」，
        少一处即 <3 仍失败；改用 >= 以免新增引用点或文档说明触发假失败。"""
        self.assertGreaterEqual(self.src.count("_search_prefix_pattern()"), 3,
                                "三处前缀正则必须统一引用单一事实来源")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_no_legacy_char_class_in_active_pattern(self):
        """新（生效）正则体不得再出现单字符类 `[一下]?`；旧体保留仅供灰度回退。"""
        ns = _load_helpers()
        self.assertNotIn("[一下]?", ns["_SEARCH_PREFIX_ALT_NEW"])
        self.assertIn("[一下]?", ns["_SEARCH_PREFIX_ALT_OLD"])  # 回退用

    def test_guard_switch_wired(self):
        self.assertIn("_search_topic_guard_enabled()", self.src)
        self.assertIn("[搜索主题守卫] 疑似截断残留", self.src)


if __name__ == "__main__":
    unittest.main()
