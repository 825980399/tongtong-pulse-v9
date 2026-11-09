# -*- coding: utf-8 -*-
"""482-B1 主动表达止血：4 刀门控单测。

设计口径：
- 测真实源码（PulseMouth._on_initiative），用 __new__ 轻量实例 + 桩隔离 TTS/事件场；
- 每刀都有「开关闭 / 开」双向断言，关闭时逐字回到修复前行为；
- 结构上锁死：AST 防回潮 + 判据有效性自测（旧实现喂同一判据必须红）。
"""
import ast
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus.const import MouthEvent
from organs.motor.PulseMouth import PulseMouth

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOUTH_SRC_PATH = os.path.join(_ROOT, "organs", "motor", "PulseMouth.py")
with open(_MOUTH_SRC_PATH, encoding="utf-8") as _fh:
    _SRC = _fh.read()

# 生产源码 6 个 ChatEvent.INITIATIVE 发射点的真实 payload 形态
_CASES = [
    ("silence_companion", {
        "content": "安静了好一会儿，突然想跟你说说话，我在呢。甲",
        "user_name": "小林", "initiative_type": "silence_companion", "source": "沉默陪伴",
    }),
    ("urge_driven_sharing", {
        "content": "刚才有个念头冒出来，忍不住想分享给你听。乙",
        "user_name": "小林", "initiative_type": "urge_driven_sharing", "source": "表达冲动驱动",
    }),
    ("deep_interaction", {
        "content": "关于依赖这件事，我想和你深入聊一聊。丙",
        "user_name": "小林", "initiative_type": "deep_interaction", "source": "主动深度思考分享",
    }),
    ("rhythm_expression", {
        "content": "最近一直在琢磨技术架构，你有什么想了解的吗。丁",
        "user_name": "小林", "initiative_type": "rhythm_expression", "source": "表达节律驱动",
    }),
    ("deep_sharing", {
        "content": "整理了一段内部积累的想法，想讲给你听。戊",
        "user_name": "小林", "initiative_type": "deep_sharing", "source": "内部积累分享",
    }),
    ("relationship_care", {
        "content": "看你沉默了挺久，有点惦记你，还好吗。己",
        "user_name": "小林", "initiative_type": "relationship_care_greeting",
        "reason": "silence_600s",
    }),
]


def _make_mouth():
    m = PulseMouth.__new__(PulseMouth)
    m._reply_count = 0
    m.organ_name = "嘴巴"
    m._emitted = []
    m._log_lines = []

    def _emit(event_type, payload=None, **kw):
        m._emitted.append((event_type, dict(payload or {})))

    def _log(level, message, error_code=None):
        m._log_lines.append(str(message))

    def _speak_tts(text):
        return None

    def _filter_personality(text):
        return text

    m._emit = _emit
    m._log = _log
    m._speak_tts = _speak_tts
    m._filter_personality = _filter_personality
    return m


def _replies(m):
    return [p for e, p in m._emitted if e == MouthEvent.REPLY]


def _call(payload):
    m = _make_mouth()
    r = m._on_initiative(dict(payload)) or {}
    return m, r


class TestT482B1ContentFallback(unittest.TestCase):
    """482-1：content 兜底。"""

    def test_01_all_six_real_emitters_replied(self):
        """6 个真实发射点（含任务书漏列的 PulseInitiative:223）全部产出回执。"""
        for name, payload in _CASES:
            with self.subTest(case=name):
                m, r = _call(payload)
                self.assertEqual("initiative_replied", r.get("status"), name)
                self.assertEqual(1, len(_replies(m)), name)
                self.assertEqual(payload["content"], _replies(m)[0]["content"], name)

    def test_02_switch_off_returns_to_greeting_only(self):
        """关掉刀1 ⇒ 逐字回到只读 greeting：content 型全部 skipped/空问候。"""
        with mock.patch.object(config, "ENABLE_M482_INITIATIVE_CONTENT_FALLBACK", False):
            for name, payload in _CASES:
                with self.subTest(case=name):
                    m, r = _call(payload)
                    self.assertEqual("skipped", r.get("status"), name)
                    self.assertEqual("空问候", r.get("reason"), name)
                    self.assertEqual([], _replies(m), name)

    def test_03_greeting_still_wins_when_both_present(self):
        """greeting 与 content 同时存在时优先 greeting（发射侧约定不变）。"""
        m, r = _call({"greeting": "你好呀", "content": "另一段内容", "user_name": "小林"})
        self.assertEqual("initiative_replied", r.get("status"))
        self.assertEqual("你好呀", _replies(m)[0]["content"])

    def test_04_empty_both_skipped(self):
        """空两者 ⇒ skipped/空问候，且不发回执、不递增 reply_count。"""
        m, r = _call({"user_name": "小林"})
        self.assertEqual("skipped", r.get("status"))
        self.assertEqual("空问候", r.get("reason"))
        self.assertEqual([], _replies(m))
        self.assertEqual(0, m._reply_count)

    def test_05_source_switch_off_is_literal_old_payload(self):
        """刀3 关闭时 REPLY payload 逐字等于修复前（只有 3 个键、source=initiative）。"""
        with mock.patch.object(config, "ENABLE_M482_INITIATIVE_SOURCE_TRACE", False):
            m, r = _call(_CASES[0][1])
            self.assertEqual("initiative_replied", r.get("status"))
            self.assertEqual(
                {"content": _CASES[0][1]["content"], "user_name": "小林", "source": "initiative"},
                _replies(m)[0],
            )


class TestT482B1InnerGate(unittest.TestCase):
    """482-2：闸门预留（不改变现有行为）。"""

    def test_06_inner_autonomous_suppressed(self):
        m, r = _call({**_CASES[0][1], "is_inner_autonomous": True})
        self.assertEqual("suppressed", r.get("status"))
        self.assertEqual("inner_autonomous", r.get("reason"))
        self.assertEqual([], _replies(m))

    def test_07_background_learning_suppressed(self):
        m, r = _call({**_CASES[0][1], "is_background_learning": True})
        self.assertEqual("suppressed", r.get("status"))
        self.assertEqual([], _replies(m))

    def test_08_real_emitters_do_not_carry_gate_markers(self):
        """★风险红线：6 个真实发射点都不带这两个标记 ⇒ 闸门默认放行。"""
        for name, payload in _CASES:
            with self.subTest(case=name):
                self.assertNotIn("is_inner_autonomous", payload, name)
                self.assertNotIn("is_background_learning", payload, name)
                _, r = _call(payload)
                self.assertNotEqual("suppressed", r.get("status"), name)

    def test_09_gate_switch_off_passes_even_with_marker(self):
        with mock.patch.object(config, "ENABLE_M482_INITIATIVE_INNER_GATE", False):
            m, r = _call({**_CASES[0][1], "is_inner_autonomous": True})
            self.assertEqual("initiative_replied", r.get("status"))
            self.assertEqual(1, len(_replies(m)))

    def test_10_gate_matches_on_speak_wording(self):
        """与 _on_speak:166 同口径：同一对 payload 键。"""
        tree = ast.parse(_SRC)
        marker = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_on_speak":
                seg = ast.get_source_segment(_SRC, node) or ""
                if "is_inner_autonomous" in seg and "is_background_learning" in seg:
                    marker = True
        self.assertTrue(marker, "_on_speak 的闸门键必须与刀2 一致")


class TestT482B1SourceTrace(unittest.TestCase):
    """482-3：来源标记透传（送达率可测前提）。"""

    def test_11_five_initiative_types_each_have_reply(self):
        """验收③：按 initiative_type 分组，5 类各自有回执。"""
        seen = {}
        for name, payload in _CASES:
            m, r = _call(payload)
            self.assertEqual("initiative_replied", r.get("status"), name)
            p = _replies(m)[0]
            seen[str(p.get("initiative_type"))] = p.get("source")
        self.assertGreaterEqual(len([k for k in seen if k]), 5)
        for k in ("silence_companion", "urge_driven_sharing", "deep_interaction",
                  "rhythm_expression", "deep_sharing"):
            self.assertIn(k, seen)

    def test_12_source_keeps_initiative_prefix(self):
        """验收②的 initiative_* 口径 + chat_service._source_icon 的 "initiative" 子串。"""
        for name, payload in _CASES:
            with self.subTest(case=name):
                _, r = _call(payload)
                self.assertEqual("initiative_replied", r.get("status"))
        m, _ = _call(_CASES[0][1])
        p = _replies(m)[0]
        self.assertTrue(str(p["source"]).startswith("initiative_"), p["source"])
        self.assertIn("initiative", str(p["source"]).lower())
        self.assertIn("silence_companion", str(p["source"]))

    def test_13_original_source_name_preserved(self):
        m, _ = _call(_CASES[0][1])
        self.assertEqual("initiative", _replies(m)[0].get("initiative_source"))

    def test_14_missing_type_falls_back_to_unknown(self):
        m, r = _call({"content": "没有类型字段的主动表达", "user_name": "小林"})
        self.assertEqual("initiative_replied", r.get("status"))
        self.assertEqual("initiative_unknown", _replies(m)[0]["source"])


class TestT482B1Dedup(unittest.TestCase):
    """482-4：同 user_name + 正文前 40 字，窗口内只发一次。"""

    def test_15_same_key_within_window_suppressed(self):
        m = _make_mouth()
        r1 = m._on_initiative(dict(_CASES[0][1])) or {}
        r2 = m._on_initiative(dict(_CASES[0][1])) or {}
        self.assertEqual("initiative_replied", r1.get("status"))
        self.assertEqual("skipped", r2.get("status"))
        self.assertIn("重复", str(r2.get("reason", "")))
        self.assertEqual(1, len(_replies(m)))

    def test_16_different_text_not_suppressed(self):
        m = _make_mouth()
        m._on_initiative(dict(_CASES[0][1]))
        m._on_initiative(dict(_CASES[1][1]))
        self.assertEqual(2, len(_replies(m)))

    def test_17_different_user_not_suppressed(self):
        m = _make_mouth()
        m._on_initiative(dict(_CASES[0][1]))
        p2 = dict(_CASES[0][1], user_name="另一位")
        m._on_initiative(p2)
        self.assertEqual(2, len(_replies(m)))

    def test_18_window_expiry_allows_again(self):
        """注入极短窗口（0.01s）⇒ 第二次可再次发送，证明窗口真的生效。"""
        with mock.patch.object(config, "M482_INITIATIVE_DEDUP_SEC", 0.0):
            m = _make_mouth()
            m._on_initiative(dict(_CASES[0][1]))
            r2 = m._on_initiative(dict(_CASES[0][1])) or {}
            self.assertEqual("initiative_replied", r2.get("status"))
            self.assertEqual(2, len(_replies(m)))

    def test_19_switch_off_sends_duplicates(self):
        with mock.patch.object(config, "ENABLE_M482_INITIATIVE_DEDUP", False):
            m = _make_mouth()
            m._on_initiative(dict(_CASES[0][1]))
            m._on_initiative(dict(_CASES[0][1]))
            self.assertEqual(2, len(_replies(m)))

    def test_20_dedup_state_lazy_created(self):
        """__new__ 轻量实例不跑 __init__ ⇒ 去重表必须惰性补齐。"""
        m = _make_mouth()
        self.assertFalse(hasattr(m, "_m482_initiative_dedup"))
        m._on_initiative(dict(_CASES[0][1]))
        self.assertIsInstance(getattr(m, "_m482_initiative_dedup", None), dict)


class TestT482B1StructuralGuards(unittest.TestCase):
    """AST 防回潮 + 判据有效性自测。"""

    def _initiative_src(self):
        tree = ast.parse(_SRC)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_on_initiative":
                return ast.get_source_segment(_SRC, node) or ""
        self.fail("_on_initiative 不存在")

    def test_21_four_switches_wired_in_source(self):
        seg = self._initiative_src()
        for name in ("ENABLE_M482_INITIATIVE_CONTENT_FALLBACK",
                     "ENABLE_M482_INITIATIVE_INNER_GATE",
                     "ENABLE_M482_INITIATIVE_SOURCE_TRACE",
                     "ENABLE_M482_INITIATIVE_DEDUP"):
            with self.subTest(switch=name):
                self.assertIn(name, seg, name)

    def test_22_content_fallback_literal_present(self):
        seg = self._initiative_src()
        self.assertIn('payload.get("greeting") or payload.get("content") or ""', seg)

    def test_23_no_bare_write_to_config_in_mouth(self):
        """开关只从 config 读，不在器官里改全局值。"""
        seg = self._initiative_src()
        self.assertNotIn("config.", seg.replace("_m482_cfg(", ""))

    def test_24_judgement_self_check_old_source_is_red(self):
        """★判据有效性：把修复前函数体喂同一判据，必须判红（防自欺）。"""
        old_seg = (
            '    def _on_initiative(self, payload: dict) -> dict[str, Any]:\n'
            '        """收到主动问候内容，直接输出"""\n'
            '        greeting = payload.get("greeting", "")\n'
            '        user_name = payload.get("user_name", "用户")\n'
            '        if not greeting:\n'
            '            return {"status": "skipped", "reason": "空问候"}\n'
        )
        self.assertNotIn('payload.get("greeting") or payload.get("content") or ""', old_seg)
        self.assertNotIn("ENABLE_M482_INITIATIVE_CONTENT_FALLBACK", old_seg)
        seg = self._initiative_src()
        self.assertIn('payload.get("greeting") or payload.get("content") or ""', seg)
        self.assertIn("ENABLE_M482_INITIATIVE_CONTENT_FALLBACK", seg)

    def test_25_real_emitters_payload_shape_guard(self):
        """风险红线：潜意识 5 点 + PulseInitiative:223 均不得带闸门标记。"""
        sc_path = os.path.join(_ROOT, "organs", "brain", "PulseSubconscious.py")
        with open(sc_path, encoding="utf-8") as fh:
            sc_src = fh.read()
        # 只允许在 _on_speak 之外的发射块不带标记：直接断言发射 payload 里无该键
        for key in ("is_inner_autonomous", "is_background_learning"):
            with self.subTest(key=key):
                # 潜意识文件里这两个键可以出现在别的函数（不能一刀切为 0），
                # 因此断言「ChatEvent.INITIATIVE 发射块」内不出现
                for marker in ("ChatEvent.INITIATIVE, {",):
                    idx = 0
                    cnt = 0
                    while True:
                        idx = sc_src.find(marker, idx)
                        if idx < 0:
                            break
                        block = sc_src[idx:idx + 420]
                        self.assertNotIn(key, block, f"{key} 出现在潜意识发射块")
                        idx += len(marker)
                        cnt += 1
                    self.assertGreaterEqual(cnt, 5)


if __name__ == "__main__":
    unittest.main()
