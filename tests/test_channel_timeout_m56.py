# -*- coding: utf-8 -*-
"""主线第56批 T1/P2-393：渠道级超时覆盖单元测试。

验证：
- ark-seed 系列渠道配置 timeout=120
- 其他渠道不设置 timeout 字段
- 灰度开关关闭时完全退回全局 channel_timeout=30（零回归）
- 渠道级 timeout 优先于全局；非法值安全回退
"""
import unittest

import config
from organs.body.PulseLung import PulseLung


class TestChannelTimeoutM56(unittest.TestCase):
    def _ch(self, **kw):
        d = {"name": "x", "model": "m", "api_url": "http://e", "api_key": "k"}
        d.update(kw)
        return d

    def test_ark_seed_channels_have_timeout_120(self):
        chs = config.REMOTE_API_CHANNELS["default_channels"]
        for ch in chs:
            if ch["name"].startswith("ark-seed"):
                self.assertEqual(ch.get("timeout"), 120, ch["name"])

    def test_other_channels_no_timeout_field(self):
        """★主线第62批 T4-1：契约已演进，原断言过时 —— 改为守住真正的契约。

        第56批（P2-393）只给 ark-seed 系列（思考模型）加了 timeout=120。
        随后批次陆续给更多渠道加了渠道级 timeout（ark-ds-v4-pro / ark-glm-5.2 /
        ark-ds-v4.1-flash / ark-glm-5.3-flash 等），2026-09-15 实测 **12 个渠道中
        9 个带 timeout**。⇒「非 ark-seed 一律不得有 timeout」这条已不成立。

        改为断言真正需要守住的：任何渠道一旦配了 timeout，必须是**合法正数**，
        否则 `_resolve_channel_timeout` 的静默回退会掩盖配置错误。
        """
        for ch in config.REMOTE_API_CHANNELS["default_channels"]:
            if ch["name"].startswith("ark-seed"):
                continue
            if "timeout" not in ch:
                continue
            _t = ch.get("timeout")
            self.assertIsInstance(_t, (int, float),
                                  "{} 的 timeout 非数值: {!r}".format(ch["name"], _t))
            self.assertGreater(float(_t), 0,
                               "{} 的 timeout 非正数: {!r}".format(ch["name"], _t))

    def test_override_on_uses_channel_timeout(self):
        c = self._ch(name="ark-seed-evolving", timeout=120)
        self.assertEqual(PulseLung._resolve_channel_timeout(c, config), 120.0)

    def test_no_timeout_uses_global(self):
        c = self._ch(name="zhipu")
        self.assertEqual(PulseLung._resolve_channel_timeout(c, config), 30.0)

    def test_override_off_returns_global(self):
        c = self._ch(name="ark-seed-evolving", timeout=120)
        old = config.ENABLE_CHANNEL_TIMEOUT_OVERRIDE
        config.ENABLE_CHANNEL_TIMEOUT_OVERRIDE = False
        try:
            self.assertEqual(PulseLung._resolve_channel_timeout(c, config), 30.0)
        finally:
            config.ENABLE_CHANNEL_TIMEOUT_OVERRIDE = old

    def test_global_channel_timeout_default_30(self):
        self.assertEqual(config.REMOTE_API_CHANNELS["health"]["channel_timeout"], 30)

    def test_invalid_channel_timeout_falls_back(self):
        # 灰度开 + 非法 timeout 应回退全局，不抛异常
        c = self._ch(name="ark-seed-evolving", timeout="not-a-number")
        self.assertEqual(PulseLung._resolve_channel_timeout(c, config), 30.0)


if __name__ == "__main__":
    unittest.main()
