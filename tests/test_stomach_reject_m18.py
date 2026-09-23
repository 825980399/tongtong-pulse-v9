# -*- coding: utf-8 -*-
"""
test_stomach_reject_m18.py —— 第18批 T7：胃拒绝出口事件补全（第17批裁决闭环）

验证两个拒绝出口（极低质量 / 后台学习门槛）都发布了 `stomach.digest.end`，
且 payload 的 result/reason 区分明确、与主出口口径一致、不改变业务逻辑。
"""

import os
import re
import sys
import textwrap
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

_STOMACH = os.path.join(_PROJECT_ROOT, "organs", "body", "PulseStomach.py")


def _iter_publish_blocks(path):
    """从源码切出每个 `tap_publish(...)` 完整调用（括号匹配）。"""
    _src = open(path, encoding="utf-8").read()
    _out, _idx = [], 0
    while True:
        _i = _src.find("tap_publish(", _idx)
        if _i < 0:
            break
        _ls = _src.rfind("\n", 0, _i) + 1
        _depth, _k = 0, _src.index("(", _i)
        while _k < len(_src):
            _ch = _src[_k]
            if _ch == "(":
                _depth += 1
            elif _ch == ")":
                _depth -= 1
                if _depth == 0:
                    break
            _k += 1
        _out.append(_src[_ls:_k + 1])
        _idx = _k + 1
    return _out


def _run_blocks(path, ns_extra):
    _calls = []

    def _capture(name, payload=None, source="", switch_attr="", priority=None):
        _calls.append({"name": name, "payload": payload, "source": source,
                       "switch_attr": switch_attr})
        return True

    import time as _time
    _ns = {"tap_publish": _capture, "time": _time}
    _ns.update(ns_extra)
    for _b in _iter_publish_blocks(path):
        exec(compile(textwrap.dedent(_b), "<tap-block>", "exec"), _ns)
    return _calls


class TestStomachRejectEvents(unittest.TestCase):
    def setUp(self):
        with open(_STOMACH, encoding="utf-8") as f:
            self.src = f.read()
        self.calls = _run_blocks(_STOMACH, {
            "_tap_t0": 0.0, "content": "一段内容", "keywords": ["知识"],
            "_bk_kws": ["知识", "架构"],
        })
        self.ends = [c for c in self.calls if c["name"] == "stomach.digest.end"]

    def test_four_event_points_present(self):
        """1 个 start + 3 个 end（主出口 1 + 拒绝出口 2）。"""
        self.assertEqual(len([c for c in self.calls
                              if c["name"] == "stomach.digest.start"]), 1)
        self.assertEqual(len(self.ends), 3)

    def test_low_quality_reject_publishes_rejected(self):
        _hit = [c for c in self.ends
                if c["payload"].get("reason") == "low_quality"]
        self.assertEqual(len(_hit), 1, "极低质量拦截出口必须有事件")
        self.assertEqual(_hit[0]["payload"]["result"], "rejected")
        self.assertIn("input_len", _hit[0]["payload"])

    def test_below_threshold_reject_publishes_rejected(self):
        _hit = [c for c in self.ends
                if c["payload"].get("reason") == "below_threshold"]
        self.assertEqual(len(_hit), 1, "后台学习门槛出口必须有事件")
        self.assertEqual(_hit[0]["payload"]["result"], "rejected")
        # output_len 取被拒时的有效关键词数
        self.assertEqual(_hit[0]["payload"]["output_len"], 2)

    def test_main_exit_still_success(self):
        _ok = [c for c in self.ends if c["payload"].get("result") == "success"]
        self.assertEqual(len(_ok), 1)

    def test_event_consistent_with_main_exit(self):
        """三处事件的 name/source/switch_attr 必须完全一致。"""
        for _c in self.ends:
            self.assertEqual(_c["source"], "PulseStomach")
            self.assertEqual(_c["switch_attr"], "ENABLE_STOMACH_EVENT_TAP")
        # ★主线第33批 T2（P2-195）：语义=「主出口+2 个拒绝出口都已插桩」，
        #   删点即 <3 仍失败；改用 >= 以免注释提及事件名导致假失败。
        self.assertGreaterEqual(self.src.count('"stomach.digest.end"'), 3)

    def test_publish_blocks_are_business_neutral(self):
        """护栏：拒绝出口的发布块不含 return / raise（不改业务逻辑）。"""
        _blocks = [b for b in _iter_publish_blocks(_STOMACH)
                   if "low_quality" in b or "below_threshold" in b]
        self.assertEqual(len(_blocks), 2)
        for _b in _blocks:
            self.assertNotIn("return", _b)
            self.assertNotIn("raise", _b)

    def test_reject_events_placed_before_returns(self):
        """发布块必须紧邻 return 之前（不改变返回结构）。"""
        _lines = self.src.split("\n")
        for _i, _l in enumerate(_lines):
            if "low_quality" in _l:
                _nxt = "\n".join(_lines[_i:_i + 4])
                self.assertIn("return {", _nxt)
            if "below_threshold" in _l:
                _nxt = "\n".join(_lines[_i:_i + 4])
                self.assertIn("return {", _nxt)

    def test_event_name_convention(self):
        _pat = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")
        self.assertRegex("stomach.digest.end", _pat)


if __name__ == "__main__":
    unittest.main()
