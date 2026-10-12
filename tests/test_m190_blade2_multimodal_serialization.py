# -*- coding: utf-8 -*-
"""190刀2 隔离单测：多模态序列化层零行为扩展（PulseLung._build_channel_messages + image_b64）。

验收：无图时 user.content 仍为字符串（零行为变化）；有图时转 array 形态
（text + image_url data-URL）。零 LLM、不接远端视觉模型。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from organs.body.PulseLung import PulseLung


def _mk_lung():
    """轻量肺实例（绕 __init__），只挂被测路径所需属性。"""
    lung = PulseLung.__new__(PulseLung)
    lung._log = lambda *a, **k: None
    lung._dialog_history = []
    lung.ollama_base_url = "http://127.0.0.1:11434"
    lung.ollama_timeout = 30
    lung._get_correction_hint = lambda q: ""  # 依赖 _correction_memory_lock，打桩
    return lung


class TestBlade2MultimodalSerialization(unittest.TestCase):
    def test_no_image_keeps_string_content(self):
        """无图：user.content 仍是原始字符串 prompt，零行为变化。"""
        msgs = _mk_lung()._build_channel_messages("你好")
        user_msg = msgs[-1]
        self.assertEqual(user_msg["role"], "user")
        self.assertIsInstance(user_msg["content"], str)
        self.assertEqual(user_msg["content"], "你好")

    def test_with_image_converts_to_array(self):
        """有图：user.content 转 array 形态（text + image_url data-URL）。"""
        b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        msgs = _mk_lung()._build_channel_messages("描述这张图", image_b64=b64)
        user_msg = msgs[-1]
        self.assertEqual(user_msg["role"], "user")
        content = user_msg["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(len(content), 2)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[0]["text"], "描述这张图")
        self.assertEqual(content[1]["type"], "image_url")
        url = content[1]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/png;base64,"))
        self.assertIn(b64, url)

    def test_empty_image_b64_treated_as_no_image(self):
        """image_b64 为空串：回落字符串分支（与无图一致）。"""
        msgs = _mk_lung()._build_channel_messages("你好", image_b64="")
        self.assertIsInstance(msgs[-1]["content"], str)


if __name__ == "__main__":
    unittest.main()
