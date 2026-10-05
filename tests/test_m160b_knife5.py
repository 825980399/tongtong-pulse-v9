# -*- coding: utf-8 -*-
"""★第160批 下·下 刀5（N-b 幂等 + N-a 问句前缀补齐）防回归单测。

覆盖任务书 5.1 / 5.2 与验收判据「重复节点 length 下降（35→1）/ N-a 新节点不再出现」：
  5.1 quality_reason 幂等：只输出单个 E1:显式标记(x) <首因>（先剥已有 E1: 前缀再拼）+ ≤200 截断
  5.2 N-a _RE_QUESTION 补「给我讲讲/讲讲/介绍下/说下/说说/讲一讲」等问句前缀，问句不进知识库
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.knowledge.PollutionTagger import PollutionTagger  # noqa: E402


class TestKnife5E1Idempotent(unittest.TestCase):
    """5.1：显式标记 reason 幂等，禁止重复累加 E1: 前缀。"""

    def test_e1_idempotent_no_double_wrap(self):
        _t = PollutionTagger()
        _d = {"node_id": "k1", "value": "x", "keywords": [], "space_path": "/x",
              "quality_flag": "suspect",
              "quality_reason": "E1:显式标记(suspect) 原始原因文本"}
        _flag, _reason = _t.classify(_d)
        self.assertEqual(_flag, "suspect")
        # 只应出现一次 E1:显式标记 前缀（杜绝 35 次累加）
        self.assertEqual(_reason.count("E1:显式标记"), 1, "不应重复累加 E1 前缀: %r" % _reason)
        self.assertTrue(_reason.startswith("E1:显式标记(suspect)"))
        self.assertIn("原始原因文本", _reason)
        # 幂等：再判一次结果完全一致（无累加）
        _flag2, _reason2 = _t.classify(_d)
        self.assertEqual(_reason2, _reason)

    def test_e1_reason_truncated_to_200(self):
        _t = PollutionTagger()
        _long = "很长的原因文本" * 60  # 远超 200
        _d = {"node_id": "k2", "value": "x", "keywords": [], "space_path": "/x",
              "quality_flag": "suspect", "quality_reason": _long}
        _flag, _reason = _t.classify(_d)
        self.assertEqual(_flag, "suspect")
        self.assertLessEqual(len(_reason), 200, "reason 长度须 ≤200: %d" % len(_reason))
        self.assertTrue(_reason.startswith("E1:显式标记(suspect)"))


class TestKnife5NaQuestionPrefix(unittest.TestCase):
    """5.2：新增问句前缀须被 N-a 守卫捕获（S3 suspect，不进知识库）。"""

    def test_na_question_prefixes_caught(self):
        _t = PollutionTagger()
        for _v in ("给我讲讲今天的天气", "讲讲你的故事", "介绍下这个知识点", "说下你的看法"):
            _flag, _reason = _t.classify(
                {"node_id": "k3", "value": _v, "keywords": [], "space_path": "/知识/测试"})
            self.assertEqual(_flag, "suspect", "问句应判 suspect: %r" % _v)
            self.assertTrue(_reason.startswith("S3"), "应为 S3: %r -> %r" % (_v, _reason))

    def test_na_question_mark_still_caught(self):
        """回归：原问号结尾判定不受影响。"""
        _t = PollutionTagger()
        _flag, _reason = _t.classify(
            {"node_id": "k4", "value": "这是什么意思？", "keywords": [], "space_path": "/知识/测试"})
        self.assertEqual(_flag, "suspect")
        self.assertTrue(_reason.startswith("S3"))


if __name__ == "__main__":
    unittest.main()
