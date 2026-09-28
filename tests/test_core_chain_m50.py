# -*- coding: utf-8 -*-
"""第50批 T3 门控测试：P2-329 核心链路错配修复

真根因（实测）：判据把「期望接收者」当作**单一硬编码字符串**做子串匹配，
而对话侧真实注册名是「Web对话-人脸监听」/「对话模块-全局回复监听」→ 恒判 mismatch；
且 `chat_to_mouth` 的**事件与方向写反**（用了 `mouth.reply`）。
"""
import io
import os
import sys
import unittest
from types import SimpleNamespace

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config                                                       # noqa: E402
import importlib                                                    # noqa: E402

_MC = importlib.import_module("organs.core.PulseMetricsCollector")
_SRC = io.open(os.path.join(_ROOT, "organs/core/PulseMetricsCollector.py"),
               encoding="utf-8", errors="replace").read().replace("\r\n", "\n")


# ★真实订阅图（源码取证，见各条注释）
REAL_CONDITIONS = {
    "c_visual": {"condition": {"organ_name": "视觉皮层",
                               "event_types": ["eyes.stream_frame",
                                               "eyes.visual_query",
                                               "chat.message"]}},
    "c_mouth": {"condition": {"organ_name": "嘴巴",
                              "event_types": ["mouth.speak", "code.result",
                                              "chat.initiative",
                                              "system.status_request"]}},
    # web_chat.py:658 → "Web对话-人脸监听"
    "c_webchat": {"condition": {"organ_name": "Web对话-人脸监听",
                                "event_types": ["chat.user_presence_detected",
                                                "chat.user_left",
                                                "persona.switched"]}},
    # PulseSubconscious:4654 → 潜意识
    "c_sub": {"condition": {"organ_name": "潜意识",
                            "event_types": ["chat.user_presence_detected"]}},
    # wecom_chat_bridge.py:72 → 企业微信侧监听
    "c_wecom": {"condition": {"organ_name": "企业微信-回复监听",
                              "event_types": ["mouth.reply"]}},
}


def _collector(conditions=None):
    """轻量实例：只挂 `info_field`（含 `_conditions`）。"""
    mc = _MC.PulseMetricsCollector.__new__(_MC.PulseMetricsCollector)
    mc.info_field = SimpleNamespace(_conditions=(
        REAL_CONDITIONS if conditions is None else conditions))
    return mc


class _CfgSwitch:
    def __init__(self, **kw):
        self._kw, self._old = kw, {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._old.items():
            if v is None:
                if hasattr(config, k):
                    delattr(config, k)
            else:
                setattr(config, k, v)
        return False


# ============================================================ 别名解析
class TestAliasResolve(unittest.TestCase):
    def test_10_chat_module_has_aliases(self):
        al = _MC.PulseMetricsCollector._m50_resolve_aliases("对话模块")
        self.assertGreater(len(al), 1)
        _low = [a.lower() for a in al]
        for must in ("对话模块", "web对话", "企业微信"):
            self.assertIn(must.lower(), _low)

    def test_11_unknown_name_falls_back_to_self(self):
        self.assertEqual(
            _MC.PulseMetricsCollector._m50_resolve_aliases("未知器官"),
            ("未知器官",))

    def test_12_empty_safe(self):
        self.assertEqual(
            _MC.PulseMetricsCollector._m50_resolve_aliases(""), ())
        self.assertEqual(
            _MC.PulseMetricsCollector._m50_resolve_aliases(None), ())


# ============================================================ 判据
class TestSubscriberMatch(unittest.TestCase):
    def setUp(self):
        self.mc = _collector()

    def test_20_web_chat_matches_chat_module(self):
        """★核心：真实注册名「Web对话-人脸监听」必须匹配「对话模块」。"""
        self.assertTrue(self.mc._m50_subscriber_matches(
            "对话模块", ["Web对话-人脸监听"]))
        self.assertTrue(self.mc._m50_subscriber_matches(
            "对话模块", ["对话模块-全局回复监听"]))
        self.assertTrue(self.mc._m50_subscriber_matches(
            "对话模块", ["企业微信-回复监听"]))

    def test_21_exact_still_matches(self):
        self.assertTrue(self.mc._m50_subscriber_matches("嘴巴", ["嘴巴"]))

    def test_22_empty_subscribers(self):
        self.assertFalse(self.mc._m50_subscriber_matches("对话模块", []))
        self.assertFalse(self.mc._m50_subscriber_matches("对话模块", None))

    def test_23_unrelated_not_matched(self):
        self.assertFalse(self.mc._m50_subscriber_matches(
            "视觉皮层", ["潜意识", "自我认知"]))

    def test_24_case_insensitive(self):
        self.assertTrue(self.mc._m50_subscriber_matches(
            "对话模块", ["CHAT_SERVICE"]))

    def test_25_switch_off_restores_old_behavior(self):
        """零回归：关闭开关 → 回到旧的纯子串匹配（Web对话 不再命中）。"""
        with _CfgSwitch(ENABLE_LINK_STATUS_ALIAS_MATCH=False):
            self.assertFalse(self.mc._m50_subscriber_matches(
                "对话模块", ["Web对话-人脸监听"]))
            self.assertTrue(self.mc._m50_subscriber_matches(
                "对话模块", ["对话模块-全局回复监听"]))


# ============================================================ 三条链路状态
class TestLinkStatus(unittest.TestCase):
    def test_30_all_three_connected_with_real_graph(self):
        """★任务书验收：视觉皮层→对话模块、嘴巴链路均应为 connected。

        使用**源码取证的真实订阅图**。
        """
        links = _collector()._collect_link_status()
        for k, v in links.items():
            self.assertEqual(v["status"], "connected",
                             "%s 判定=%s（期望 connected）" % (k, v["status"]))

    def test_31_chat_to_mouth_uses_mouth_speak(self):
        """★修正：事件应为 `mouth.speak`（对话→嘴巴），而非 `mouth.reply`。"""
        links = _collector()._collect_link_status()
        self.assertEqual(links["chat_to_mouth"]["event"], "mouth.speak")
        self.assertEqual(links["chat_to_mouth"]["emitter"], "对话模块")
        self.assertEqual(links["chat_to_mouth"]["receiver"], "嘴巴")

    def test_32_orphan_when_no_subscriber(self):
        links = _collector({"x": {"condition": {
            "organ_name": "无关器官", "event_types": ["other.event"]}}}
        )._collect_link_status()
        for v in links.values():
            self.assertEqual(v["status"], "orphan")

    def test_33_mismatch_when_only_unrelated_subscribers(self):
        links = _collector({"x": {"condition": {
            "organ_name": "完全无关", "event_types": ["mouth.speak"]}}}
        )._collect_link_status()
        self.assertEqual(links["chat_to_mouth"]["status"], "mismatch")
        self.assertIn("actual_receivers", links["chat_to_mouth"])
        self.assertIn("expected_aliases", links["chat_to_mouth"])

    def test_34_no_info_field_safe(self):
        mc = _MC.PulseMetricsCollector.__new__(_MC.PulseMetricsCollector)
        mc.info_field = None
        links = mc._collect_link_status()
        self.assertEqual(len(links), 3)
        for v in links.values():
            self.assertEqual(v["status"], "unknown")

    def test_35_returns_three_links(self):
        links = _collector()._collect_link_status()
        self.assertEqual(set(links), {"eye_to_visual", "visual_to_chat",
                                      "chat_to_mouth"})

    def test_36_switch_off_reproduces_old_mismatch(self):
        """★零回归 + 根因佐证：关闭开关 → 复现旧的 mismatch。"""
        with _CfgSwitch(ENABLE_LINK_STATUS_ALIAS_MATCH=False):
            links = _collector()._collect_link_status()
        self.assertEqual(links["visual_to_chat"]["status"], "mismatch",
                         "旧实现下确实为 mismatch（根因佐证）")


# ============================================================ 源码接线
class TestWiring(unittest.TestCase):
    def test_40_aliases_defined(self):
        self.assertIn("_LINK_ALIASES", _SRC)
        self.assertIn("Web对话", _SRC)

    def test_41_alias_matcher_present_and_used(self):
        self.assertIn("def _m50_subscriber_matches", _SRC)
        i = _SRC.find("def _collect_link_status")
        self.assertIn("_m50_subscriber_matches", _SRC[i:i + 3000])

    def test_42_switch_read_from_config(self):
        self.assertIn("ENABLE_LINK_STATUS_ALIAS_MATCH", _SRC)
        self.assertTrue(hasattr(config, "ENABLE_LINK_STATUS_ALIAS_MATCH"))

    def test_43_no_new_bare_except_pass(self):
        """★本批改动区域不得引入裸 `except: pass`。

        注：本文件 :389 有一处**既有基线**裸 pass（`.bak_batch50` 基线同为 1），
        故此处只校验**本批新增区域**（`_LINK_ALIASES` 之后）。
        """
        import re
        i = _SRC.find("_LINK_ALIASES")
        self.assertGreater(i, 0)
        _region = _SRC[i:]
        self.assertEqual(
            len(re.findall(r"except\s+[^\n:]+:\s*\n\s*pass\s*(\n|$)", _region)), 0)

    def test_44_ui_labels_unchanged(self):
        """UI 仍显示 畅通/错配/断裂（不改变用户可见语义）。"""
        ui = io.open(os.path.join(_ROOT, "functions/health_ui.py"),
                     encoding="utf-8", errors="replace").read()
        for lab in ("畅通", "错配", "断裂"):
            self.assertIn(lab, ui)


if __name__ == "__main__":
    unittest.main(verbosity=2)
