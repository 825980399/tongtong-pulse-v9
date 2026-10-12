# -*- coding: utf-8 -*-
"""199刀1 隔离单测：Lung 影子 v2 升级——组装区对 retriever 只读查询，score 分布+条数并入同一条 INFO（含节流键）。

验收：生产日志路径出现影子行（含 要素①/②/③ 与「本会注入」，保留 197刀1）；
且同一条 INFO 内含 [v2只读检索] 片段（retriever 状态 + 条数 + score分布 + 节流键）；
隔离单测含 score分布+条数 断言，且验证 retriever 被只读调用（未改任何状态）。
"""
import unittest

from nucleus._silent_except import silent_exc
from nucleus.const import LogLevel
from organs.body.PulseLung import PulseLung


class TestM199Blade1ShadowV2(unittest.TestCase):
    def _capture_shadow(self, lung, retriever):
        # 避免重依赖：长度控制返回空串、历史为空
        lung._m28_system_length_suffix = lambda prompt: ""
        lung.get_dialog_history = list
        lung._shadow_v2_retriever = retriever
        _records = []
        _orig = lung._log

        def _cap(level, msg, *a, **k):
            _records.append((level, str(msg)))
            try:
                _orig(level, msg, *a, **k)
            except Exception as _exc:
                silent_exc(_exc, where="tests/test_shadow_v2_m199.py:_cap")

        lung._log = _cap
        lung._build_channel_messages("测试 prompt")
        return [(lv, m) for (lv, m) in _records
                if "实时注入影子" in m and "本会注入" in m]

    def test_readonly_query_score_distribution_and_count(self):
        _calls = []

        def _fake_retriever(query, top_k):
            _calls.append((query, top_k))
            return [{"score": 0.91}, {"score": 0.62}, {"score": 0.33}]

        _shadow = self._capture_shadow(PulseLung(organ_name="肺"), _fake_retriever)
        self.assertTrue(_shadow, "未在生产日志路径发现实时注入影子行")
        self.assertTrue(
            any(lv == LogLevel.INFO for lv, m in _shadow),
            "影子行未以 INFO 级输出，生产日志不可见",
        )
        _msg = _shadow[0][1]
        # 197刀1 三要素保留（零回归）
        for _elem in ("要素①", "要素②", "要素③"):
            self.assertIn(_elem, _msg, "影子行缺少 %s" % _elem)
        # 199刀1 只读检索片段并入同一条 INFO
        self.assertIn("[v2只读检索]", _msg)
        self.assertIn("条数=3", _msg)
        self.assertIn("score分布=min0.33/max0.91/mean0.62", _msg)
        self.assertIn("节流键=tk=", _msg)
        # 只读：retriever 被调用且未改变任何状态
        self.assertEqual(_calls, [("测试 prompt", 3)])

    def test_retriever_none_unavailable_zero_behavior(self):
        _shadow = self._capture_shadow(PulseLung(organ_name="肺"), None)
        self.assertTrue(_shadow, "未在生产日志路径发现实时注入影子行")
        _msg = _shadow[0][1]
        self.assertIn("retriever=unavailable", _msg)
        self.assertIn("条数=0", _msg)
        self.assertIn("score分布=无", _msg)
        self.assertIn("节流键=tk=", _msg)


if __name__ == "__main__":
    unittest.main()
