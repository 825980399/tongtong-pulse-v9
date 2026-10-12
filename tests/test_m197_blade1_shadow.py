# -*- coding: utf-8 -*-
"""197刀1 隔离单测：Lung 系统 prompt 组装区「实时注入三要素」日志化影子。

验收：生产日志路径出现影子行（含 要素①/②/③ 与「本会注入」），且为 INFO 级（生产可见）。
"""
import unittest

from nucleus.const import LogLevel
from organs.body.PulseLung import PulseLung


class TestM197Blade1Shadow(unittest.TestCase):
    def test_shadow_log_emitted_at_info(self):
        _lung = PulseLung(organ_name="肺")
        # 避免重依赖：长度控制返回空串、历史为空
        _lung._m28_system_length_suffix = lambda prompt: ""
        _lung.get_dialog_history = lambda: []

        _records = []
        _orig = _lung._log

        def _cap(level, msg, *a, **k):
            _records.append((level, str(msg)))
            try:
                _orig(level, msg, *a, **k)
            except Exception:
                pass

        _lung._log = _cap
        _lung._build_channel_messages("测试 prompt")

        _shadow = [(lv, m) for (lv, m) in _records
                   if "实时注入影子" in m and "本会注入" in m]
        self.assertTrue(_shadow, "未在生产日志路径发现实时注入影子行（要素①/②/③）")
        self.assertTrue(
            any(lv == LogLevel.INFO for lv, m in _shadow),
            "影子行未以 INFO 级输出，生产日志不可见",
        )
        # 三要素齐备
        _msg = _shadow[0][1]
        for _elem in ("要素①", "要素②", "要素③"):
            self.assertIn(_elem, _msg, "影子行缺少 %s" % _elem)


if __name__ == "__main__":
    unittest.main()
