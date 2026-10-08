# -*- coding: utf-8 -*-
"""第170批 C8a（O-B4 出站哨兵）配套断言：

· `sanitize_outbound` 对用户可见输出文本执行敏感引用脱敏（密钥 / token /
  Bearer / 手机号 / 邮箱 / face_roster 路径 / identity 路径），命中即掩码。
· 未命中敏感引用时原样返回（幂等、零副作用）。

★既有 sanitizer 单测（test_log_sanitizer_m133.py）不受影响；本批新增出站哨兵 API。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.logging.sanitizer import sanitize_outbound  # noqa: E402


class TestOutboundSentinel(unittest.TestCase):
    def test_masks_phone(self):
        _out = sanitize_outbound("请回拨 13800138000 联系我")
        self.assertIn("<REDACTED_PHONE>", _out)
        self.assertNotIn("13800138000", _out)

    def test_masks_email(self):
        _out = sanitize_outbound("可邮件联系 admin@example.com 处理")
        self.assertIn("<REDACTED_EMAIL>", _out)
        self.assertNotIn("admin@example.com", _out)

    def test_masks_api_key(self):
        _out = sanitize_outbound("凭证 sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ12 已加载")
        self.assertIn("<REDACTED_KEY>", _out)

    def test_no_sensitive_returns_unchanged(self):
        _text = "今天天气不错，我们一起去散步吧"
        self.assertEqual(sanitize_outbound(_text), _text)

    def test_idempotent(self):
        _text = "联系 13800138000 或 admin@example.com"
        _once = sanitize_outbound(_text)
        _twice = sanitize_outbound(_once)
        self.assertEqual(_once, _twice)


if __name__ == "__main__":
    unittest.main()
