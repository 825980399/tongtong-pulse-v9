# -*- coding: utf-8 -*-
"""第405批 刀4 判据：意图路由断头路修复 + 灰度开关。

背景（402 刀4）：
    `PulseCortex._route_by_intent` 首行
        `route_target = self._intent_routes.get(intent, MouthEvent.SPEAK)`
    赋值后**从不使用**（F841）⇒ `config.INTENT_ROUTES` 整张表是死配置。

本刀口径：
    · `config.ENABLE_INTENT_ROUTES` 默认 **False** ⇒ 与改前逐字节同行为
    · 开关开 ⇒ 兜底分支真的按路由表派发（仅限 payload 契约匹配的目标）
    · 路由表里语义不成立的目标（hands.execute 要 code / system.status.response
      是响应事件）**回落** mouth.speak，并给出 `route_fallback_reason`

判据钉什么：
    · 钉「开关关 = 行为不变」——这是灰度的全部意义
    · 钉「开关开时配置真的改变派发目标」——这是断头路修好的唯一证据
    · 钉「payload 按目标分型」——否则 eyes.search 拿到 content 而不是 query，
      等于修了一个假路由
    · 不钉日志、不钉具体 reason 措辞（只钉它**能不能分辨三种情形**）
"""
from __future__ import annotations

import unittest

import config
from organs.brain.PulseCortex import PulseCortex


def _mk_cortex(intent_routes=None):
    """用 __new__ 造一个只在内存里的 Cortex：不装配器官、不起线程。

    `inner_world = None` 是刻意的 —— 兜底分支（`route_target` 唯一语义成立的
    地方）只有在内在世界未注入时才会走到。
    """
    _e = PulseCortex.__new__(PulseCortex)
    _e._intent_routes = dict(intent_routes if intent_routes is not None
                             else config.INTENT_ROUTES)
    _e._pending_inner_world = {}
    _e.inner_world = None
    _e.autonomous_deriver = None
    _e.node_pool = None
    _e._decision_count = 0
    _e._sent = []
    _e._emit = (lambda et, pl=None, **k:
                _e._sent.append((et, dict(pl or {}))) or "pulse:test")
    _e._log = lambda *a, **k: None
    return _e


class _FlagMixin:
    """把 ENABLE_INTENT_ROUTES 拨到指定值，测试结束必还原。"""

    def _set_flag(self, value: bool):
        _old = getattr(config, "ENABLE_INTENT_ROUTES", False)
        config.ENABLE_INTENT_ROUTES = value
        self.addCleanup(setattr, config, "ENABLE_INTENT_ROUTES", _old)


class TestSwitchExists(unittest.TestCase):
    """开关必须真实存在于 config，且默认关。"""

    def test_01_flag_present_and_default_off(self):
        self.assertTrue(hasattr(config, "ENABLE_INTENT_ROUTES"),
                        "config 里没有 ENABLE_INTENT_ROUTES")
        self.assertIsInstance(config.ENABLE_INTENT_ROUTES, bool)
        self.assertFalse(config.ENABLE_INTENT_ROUTES,
                         "灰度开关必须默认关（不改变默认行为）")

    def test_02_routes_table_still_present(self):
        """INTENT_ROUTES 本身不能被顺手删掉（它是开关打开后的真源）。"""
        self.assertIsInstance(config.INTENT_ROUTES, dict)
        self.assertGreaterEqual(len(config.INTENT_ROUTES), 4)
        self.assertEqual(config.INTENT_ROUTES.get("知识"), "eyes.search")


class TestSwitchOffKeepsLegacyBehaviour(_FlagMixin, unittest.TestCase):
    """开关关：兜底一律 mouth.speak，返回值不多任何字段。"""

    def setUp(self):
        self._set_flag(False)

    def test_10_all_intents_fall_to_mouth_speak(self):
        for _intent in ("身份", "关系", "知识", "代码", "状态", "创作", "规划", "分析"):
            _e = _mk_cortex()
            _r = _e._route_by_intent("cid", _intent, "帮我看看", "用户")
            self.assertEqual(len(_e._sent), 1)
            self.assertEqual(_e._sent[0][0], "mouth.speak",
                             "开关关时意图 %s 没走旧的 mouth.speak" % _intent)
            self.assertEqual(_r.get("target"), "mouth.speak")

    def test_11_legacy_payload_shape_unchanged(self):
        """旧 payload 的键集必须是 content/user_name/source（不许多 correlation_id）。"""
        _e = _mk_cortex()
        _e._route_by_intent("cid", "知识", "查一下", "用户")
        _keys = set(_e._sent[0][1].keys())
        self.assertEqual(_keys, {"content", "user_name", "source"},
                         "开关关时 payload 形状变了: %s" % sorted(_keys))

    def test_12_no_route_diagnostics_when_off(self):
        """开关关时返回值不该出现 route_* 诊断字段（保持旧返回契约）。"""
        _e = _mk_cortex()
        _r = _e._route_by_intent("cid", "知识", "查一下", "用户")
        self.assertNotIn("route_target_requested", _r)
        self.assertNotIn("route_fallback_reason", _r)


class TestSwitchOnMakesTableLive(_FlagMixin, unittest.TestCase):
    """开关开：路由表的配置**真的**改变派发目标 —— 断头路修好的唯一证据。"""

    def setUp(self):
        self._set_flag(True)

    def test_20_knowledge_intent_dispatches_eyes_search(self):
        _e = _mk_cortex()
        _r = _e._route_by_intent("cid", "知识", "量子纠缠是什么", "用户")
        self.assertEqual(_e._sent[0][0], "eyes.search",
                         "知识类意图没有按路由表发 eyes.search（实际 %s）"
                         % _e._sent[0][0])
        self.assertEqual(_r.get("target"), "eyes.search")
        self.assertEqual(_r.get("route_fallback_reason"), "route_table")

    def test_21_eyes_search_payload_uses_query_field(self):
        """★payload 必须分型：PulseEyes 读的是 `query`，不是 `content`。

        这条是"修了个假路由"的防线 —— 只改派发目标不改 payload，
        消费者会拿到空 query 然后 `return {"status":"skipped","reason":"空查询"}`。
        """
        _e = _mk_cortex()
        _e._route_by_intent("cid", "知识", "量子纠缠是什么", "用户")
        _pl = _e._sent[0][1]
        self.assertEqual(_pl.get("query"), "量子纠缠是什么")
        self.assertNotIn("content", _pl,
                         "eyes.search 的 payload 里混进了 speak 的 content 字段")
        self.assertEqual(_pl.get("user_name"), "用户")

    def test_22_code_intent_falls_back_with_reason(self):
        """代码意图：路由表要 hands.execute，但它要 `code` 字段 ⇒ 必须回落并说明。"""
        _e = _mk_cortex()
        _r = _e._route_by_intent("cid", "代码", "写个快排", "用户")
        self.assertEqual(_e._sent[0][0], "mouth.speak",
                         "hands.execute 要 code 字段，不该把自然语言派过去")
        self.assertIn("hands.execute", str(_r.get("route_fallback_reason") or ""),
                      "回落了但没说清为什么: %s" % _r)
        self.assertEqual(_r.get("route_target_requested"), "hands.execute")

    def test_23_status_intent_falls_back_with_reason(self):
        """状态意图：system.status.response 是响应事件，不该由 Cortex 主动发。"""
        _e = _mk_cortex()
        _r = _e._route_by_intent("cid", "状态", "你还好吗", "用户")
        self.assertEqual(_e._sent[0][0], "mouth.speak")
        self.assertIn("system.status.response",
                      str(_r.get("route_fallback_reason") or ""))

    def test_24_identity_intent_still_speaks(self):
        """身份意图在路由表里本来就是 mouth.speak，走的应是 route_table 而非回落。"""
        _e = _mk_cortex()
        _r = _e._route_by_intent("cid", "身份", "你是谁", "用户")
        self.assertEqual(_e._sent[0][0], "mouth.speak")
        self.assertEqual(_r.get("route_fallback_reason"), "route_table",
                         "身份意图被当成回落了（它本来就该走表里的 mouth.speak）")

    def test_25_diagnostics_always_present_when_on(self):
        """开关开时，三种情形（走表/回落/未知意图）都必须能分辨。"""
        _e = _mk_cortex()
        _a = _e._route_by_intent("c1", "知识", "x y", "u")      # 走表
        _b = _e._route_by_intent("c2", "代码", "x y", "u")      # 回落
        _c = _e._route_by_intent("c3", "不存在的意图", "x y", "u")  # 表里没有
        for _r in (_a, _b, _c):
            self.assertIn("route_fallback_reason", _r)
            self.assertIn("route_target_requested", _r)
        self.assertEqual(_c.get("route_target_requested"), "mouth.speak",
                         "未知意图应取默认值 mouth.speak")
        self.assertEqual(_c.get("route_fallback_reason"), "route_table")


class TestDispatchableWhitelist(unittest.TestCase):
    """白名单本身要能被读懂：哪两个可派发、为什么排除了另外两个。"""

    def test_30_whitelist_content(self):
        _wl = PulseCortex._INTENT_DISPATCHABLE
        self.assertIn("mouth.speak", _wl)
        self.assertIn("eyes.search", _wl)
        self.assertNotIn("hands.execute", _wl,
                         "hands.execute 需要 code 字段，不该进兜底白名单")
        self.assertNotIn("system.status.response", _wl,
                         "status.response 是响应事件，不该由 Cortex 主动发")

    def test_31_route_table_targets_covered_or_explained(self):
        """路由表里出现的每个目标，要么在白名单里，要么能被上面两条理由解释。"""
        _targets = set(str(v) for v in config.INTENT_ROUTES.values())
        _explained = {"hands.execute", "system.status.response"}
        for _t in _targets:
            self.assertTrue(
                _t in PulseCortex._INTENT_DISPATCHABLE or _t in _explained,
                "路由表目标 %s 既不可派发、也没有被记录为已知不可派发" % _t)


if __name__ == "__main__":
    unittest.main(verbosity=2)
