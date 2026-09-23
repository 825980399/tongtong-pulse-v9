# -*- coding: utf-8 -*-
"""第54批 T6 门控测试：LLM 补丁验证失败修复（根因3 + 根因6）。

背景
----
运行日志频繁出现「补丁验证失败已丢弃: 补丁完整性检查失败: 语法错误」。
深度根因分析定位 6 个根因，本批修复其中 2 个：

* **根因3**：LLM 生成补丁的 system prompt 没要求纯 ASCII → LLM 输出中文标点。
  修复：prompt 增加「只输出Python代码 / 纯ASCII字符 / 不要中文标点 / 引号括号匹配 / 不输出解释」。
* **根因6**：归一化失败后返回**修改后的**代码（_cur），可能引入新问题。
  修复：归一化失败后返回**原始**代码（_code）。

覆盖
----
① prompt 已强化且受灰度开关控制（源码级）
② prompt 选择逻辑同构复现（开关开/关）
③ 归一化失败 → 返回**原始**代码（★真实调用 `_clean_llm_code`）
④ 关闭开关 → 回退返回归一化后代码（改造前行为，零回归）
⑤ 语法合法时不改动一个字符（回归保护）
"""
import contextlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib  # noqa: E402
import io  # noqa: E402
import unittest  # noqa: E402

import config  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SEE_REL = os.path.join("nucleus", "reasoning", "SafeEvolutionExecutor.py")

# ★铁律 33：包 __init__ 可能导出同名类 → 用 importlib 取模块
_SEE_MOD = importlib.import_module("nucleus.reasoning.SafeEvolutionExecutor")
_SEE_CLS = getattr(_SEE_MOD, "SafeEvolutionExecutor")

_OLD_PROMPT = "你是曈曈的代码自学习助手，负责生成精确的代码修复建议。"
_NEW_PROMPT = (
    "你是曈曈的代码自学习助手，负责生成精确的Python代码修复建议。"
    "只输出Python代码，使用纯ASCII字符，不要使用中文标点。"
    "确保语法正确，所有引号和括号必须匹配。不要输出解释文字，只输出代码。"
)


def _read(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8", errors="replace").read()


def _pick_prompt(cfg):
    """★与生产 `_call()` 内 prompt 选择**同构**（改生产须同步改本函数）。"""
    prompt = _OLD_PROMPT
    if getattr(cfg, "ENABLE_LLM_PATCH_ASCII_PROMPT", True):
        prompt = _NEW_PROMPT
    return prompt


@contextlib.contextmanager
def _cfg_switch(attr, value):
    """临时改写 config 属性，退出还原（★铁律 49：相关断言必须写在块内）。"""
    had = hasattr(config, attr)
    old = getattr(config, attr, None)
    setattr(config, attr, value)
    try:
        yield
    finally:
        if had:
            setattr(config, attr, old)
        else:
            delattr(config, attr)


class TestLlmPatchCleanupM54(unittest.TestCase):
    """★第54批 T6：LLM 补丁生成 prompt + 归一化返回策略。"""

    def setUp(self):
        # ★巨型类轻量实例化：`__new__` 绕开 __init__（不触发任何 IO / 线程）
        self.exe = _SEE_CLS.__new__(_SEE_CLS)

    # ---------------- 根因3：prompt ----------------
    def test_01_prompt_reinforced_in_source(self):
        """生产源码已使用强化 prompt，且受灰度开关控制。"""
        src = _read(_SEE_REL)
        self.assertIn("ENABLE_LLM_PATCH_ASCII_PROMPT", src, "缺少灰度开关判据")
        self.assertIn("使用纯ASCII字符", src, "prompt 未要求纯 ASCII")
        self.assertIn("不要输出解释文字", src, "prompt 未要求只输出代码")
        self.assertIn('{"role": "system", "content": _sys_prompt}', src,
                      "system 消息未改用变量（仍是硬编码字面量）")

    def test_02_prompt_switch_on_off(self):
        """开关开 → 新 prompt；开关关 → 旧 prompt（断言写在 with 块内）。"""
        self.assertEqual(_pick_prompt(config), _NEW_PROMPT)
        with _cfg_switch("ENABLE_LLM_PATCH_ASCII_PROMPT", False):
            self.assertEqual(_pick_prompt(config), _OLD_PROMPT,
                             "关闭开关应回退到旧 prompt")

    # ---------------- 根因6：归一化失败返回原始代码 ----------------
    def test_03_return_original_on_normalize_fail(self):
        """★真实调用：归一化后语法仍不合法 → 返回**原始**代码（含全角标点）。"""
        # 全角括号会被归一化，但 `return 1 +` 使语法**始终**不合法
        raw = "def f（x）：\n    return 1 + "
        out = self.exe._clean_llm_code(raw)
        self.assertTrue(any(ch in out for ch in "（）"),
                        "应返回原始代码（保留全角括号），实际=%r" % out)
        self.assertEqual(out.strip(), raw.strip())

    def test_04_switch_off_returns_normalized(self):
        """关闭开关 → 回退为返回归一化后的代码（改造前行为，零回归）。"""
        raw = "def f（x）：\n    return 1 + "
        with _cfg_switch("ENABLE_LLM_PATCH_RETURN_ORIGINAL", False):
            out = self.exe._clean_llm_code(raw)
            self.assertFalse(any(ch in out for ch in "（）"),
                             "关闭开关时应返回归一化后代码，实际=%r" % out)
            self.assertIn("(x)", out, "归一化应把全角括号转为半角")

    def test_05_valid_code_untouched(self):
        """★回归保护：语法合法的代码内容一个字符都不改。

        注：`_strip_code_fence` 会剥掉首尾空白（**既有行为**，非本批改动）→ 比较 strip 后。
        """
        raw = "def f(x):\n    return x + 1\n"
        self.assertEqual(self.exe._clean_llm_code(raw).strip(), raw.strip())


if __name__ == "__main__":
    unittest.main()
