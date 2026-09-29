# -*- coding: utf-8 -*-
"""
第151批 T151-1 · _enhance_answer 特征化测试（拆分前行为锁定 + 拆分后守恒校验）

目的：在将 God 方法 `_enhance_answer`（L13575-13894）纯结构拆分成编排器+helper 之前/之后，
锁定其「当前」可观测行为。本测试聚焦「契约级」守恒：
  - 始终返回 str（不崩、不返回 None）
  - 内部内容过滤（_sanitize_internal_content）生效：'我了解到，' 前缀被剥离且正文保留
  - 推理类方法（deriver_*）路径：跳过表达增强早退与全部随机点缀块，输出确定、无内部泄露
  - 表达增强失败回退路径：_expression_enhancer 抛异常后仍走完整下游流水线且不崩

策略：
- 用 method='deriver_xxx' 触发 _is_pure_inference_output=True，跳过所有随机点缀分支 → 输出确定，
  使「拆分前/后同一输入输出一致」成为可重复守恒校验。
- override `self._expression_enhancer` 为抛异常的桩，验证 fallback 路径。
- `_insight_board=None`（所有 board.post 均被守卫跳过）。

共 4 例；须先于下刀（T151-5）全绿，下刀后再跑同套测试确认语义不变。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault(
    "PULSE_LOG_FILE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "test_iw_ea.log"),
)

from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402


class _RaisingEnhancer:
    """模拟表达增强模块调用失败，触发 _enhance_answer 的回退路径。"""
    def enhance(self, **kwargs):
        raise RuntimeError("injected failure to trigger fallback")


class TestEnhanceAnswerSplit(unittest.TestCase):
    def setUp(self):
        self.iw = PulseInnerWorld("内在世界")
        self.iw._insight_board = None
        self.iw._expression_enhancer = None

    # ---- 内部内容过滤（确定性格） ----
    def test_01_internal_prefix_stripped(self):
        # '我了解到，' 前缀须被剥离且正文保留（_sanitize_internal_content 确定性行为）
        ans = "我了解到，这是真正的回答内容"
        out = self.iw._enhance_answer(ans, "问题", "deriver_test", 0.3)
        self.assertIsInstance(out, str)
        self.assertNotIn("我了解到，", out)
        self.assertIn("这是真正的回答内容", out)

    # ---- 短输入 + 推理方法：无早退/无随机点缀，契约不崩 ----
    def test_02_short_inference_no_crash(self):
        ans = "简短回答"
        out = self.iw._enhance_answer(ans, "问题", "deriver_test", 0.1)
        self.assertIsInstance(out, str)
        # 推理类：下游随机点缀块均被 not _is_inference_output 守卫跳过
        self.assertNotIn("[核心智慧]", out)

    # ---- 表达增强失败回退：完整下游流水线不崩 ----
    def test_03_enhancer_fallback_full_pipeline(self):
        self.iw._expression_enhancer = _RaisingEnhancer()
        ans = "这是一段足够长的回答内容，用于触发表达增强模块，随后因异常回退到原有逻辑继续处理下游。"
        out = self.iw._enhance_answer(ans, "问题", "casual_chat", 0.3)
        self.assertIsInstance(out, str)
        self.assertNotIn("[核心智慧]", out)

    # ---- 长输入 + 推理方法：确定性格（无随机点缀），返回 str 且无内部泄露 ----
    def test_04_long_inference_deterministic(self):
        # 构造一段不含内部标记的较长回答（>30 字，触发部分下游块但无随机点缀）
        ans = ("这是一个关于内心成长的较长回答，探讨在陪伴中如何理解自己的情绪，"
               "并且学会在不确定里保持温和与耐心，让对话自然地流动下去。")
        out1 = self.iw._enhance_answer(ans, "问题", "deriver_test", 0.5)
        out2 = self.iw._enhance_answer(ans, "问题", "deriver_test", 0.5)
        self.assertIsInstance(out1, str)
        self.assertIsInstance(out2, str)
        # 推理类路径无随机点缀 → 同输入两次输出应一致（确定性守恒）
        self.assertEqual(out1, out2)
        self.assertNotIn("[核心智慧]", out1)


if __name__ == "__main__":
    unittest.main()
