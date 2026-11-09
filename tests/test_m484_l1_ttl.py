# -*- coding: utf-8 -*-
"""484-4 门控：拆 TTL 开关（先红后绿）。

★T0 偏差 D9 与 T0-18：三个键全库不存在、TTL 单位是纳秒；新增毫秒键内部换算；
   （改用 T0-18 前缀：登记册门禁把两位以上 D 编号认作债务票，本编号只是 T0 偏差序号）
保留旧键供回滚；L0 永豁免，L1 非对话 5s / 对话 30s，L2/L3 不变。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus.const import SystemEvent
from nucleus.field.InfoField import InfoField

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "nucleus", "field", "InfoField.py")
_NS = 1_000_000_000


def _read_text(path):
    """统一用上下文管理器读源码：避免 SIM115 / UP020 净增。"""
    with open(path, encoding="utf-8") as _fh:
        return _fh.read()


class _Switch:
    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._old[k] = (hasattr(config, k), getattr(config, k, None))
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, (existed, val) in self._old.items():
            if existed:
                setattr(config, k, val)
            else:
                # __enter__ 必然已 setattr，delattr 不会抛；不写 try/except
                # 以免被 cw2_t2e 静默 except 门禁判为新增静默 handler。
                delattr(config, k)
        return False


class TestL1TTL(unittest.TestCase):
    def setUp(self):
        self.f = InfoField.__new__(InfoField)

    def test_01_config_keys(self):
        self.assertIs(config.PULSE_LAYER.get("disable_ttl_for_l0"), True)
        self.assertIs(config.PULSE_LAYER.get("disable_ttl_for_l1"), False)
        self.assertEqual(config.PULSE_LAYER.get("l1_ttl_ms"), 5000)
        self.assertEqual(getattr(config, "M484_DIALOG_TTL_MS", None), 30000)
        self.assertIs(config.PULSE_LAYER.get("disable_ttl_for_l0_l1"), True,
                      "旧键必须保留供回滚")

    def test_02_verdict_api(self):
        self.assertTrue(hasattr(self.f, "_m484_ttl_verdict"))

    def _v(self, layer, ev, sec):
        return self.f._m484_ttl_verdict(5 * _NS, layer, ev, int(sec * _NS))[0]

    def test_03_l0_never_expires(self):
        self.assertFalse(self._v("L0", SystemEvent.ALARM, 600))

    def test_04_l1_other_5s(self):
        self.assertFalse(self._v("L1", "acc.test.neutral", 4))
        self.assertTrue(self._v("L1", "acc.test.neutral", 6))

    def test_05_l1_dialog_30s(self):
        self.assertFalse(self._v("L1", "chat.message", 20))
        self.assertTrue(self._v("L1", "chat.message", 40))

    def test_06_l2_unchanged(self):
        self.assertTrue(self._v("L2", "acc.test.neutral", 6))
        self.assertFalse(self._v("L2", "acc.test.neutral", 4))
        self.assertTrue(self._v("L3", "acc.test.neutral", 6))

    def test_07_switch_off_is_legacy(self):
        """关掉开关 ⇒ 逐字复现旧表达式（L0/L1 全豁免、L2 按 5s 丢）。"""
        with _Switch(ENABLE_M484_L1_TTL=False):
            self.assertFalse(self._v("L0", SystemEvent.ALARM, 600))
            self.assertFalse(self._v("L1", "acc.test.neutral", 600))
            self.assertTrue(self._v("L2", "acc.test.neutral", 6))

    def test_08_publish_wired_to_verdict(self):
        src = _read_text(_SRC).replace("\r\n", "\n")
        self.assertIn("self._m484_ttl_verdict(", src)
        self.assertIn("步骤0: TTL校验", src)


if __name__ == "__main__":
    unittest.main()
