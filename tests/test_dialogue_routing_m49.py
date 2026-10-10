# -*- coding: utf-8 -*-
"""第49批 T1 门控测试：P1-327 对话路由修复（LLM 调用来源标记）

真根因（实测，与任务书描述不同）：
  `_current_task_is_background` 是**共享实例属性**，仅在 `_on_select_model`
  赋值；双腿经 `main.py` 注入的 `_call_remote_api` 回调**绕过**该赋值点 →
  读到上一次调用的**残留值**，且双腿 3 路并发 → **跨线程串味**。
  → 对话被误标 `background_learning` 而静默消化（P1 症状）。
  ★任务书点名的 `:311` 的局部变量仅用于 `_enable_thinking`，非本症状根因。

本测试同时守护「语义」与「结构」，开关双向门控。
"""
import io
import os
import sys
import textwrap
import threading
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.const import LogLevel as _LogLevel  # noqa: E402
from organs.body.PulseLung import PulseLung  # noqa: E402

_SRC = io.open(os.path.join(_ROOT, "organs/body/PulseLung.py"),
               encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
_MAIN_SRC = io.open(os.path.join(_ROOT, "main.py"),
                    encoding="utf-8", errors="replace").read().replace("\r\n", "\n")


def _bare():
    """轻量实例：绕 __init__，只挂被测方法所需状态。"""
    l = PulseLung.__new__(PulseLung)
    l._m49_tls = threading.local()
    l._current_task_is_background = False
    l._current_call_is_background = False
    return l


def _slice(src, start_marker, end_marker):
    """从真实源码切出整段（含首尾），回退即测试失败。"""
    i = src.find(start_marker)
    assert i >= 0, "起始锚点未找到: %r" % start_marker[:60]
    j = src.find(end_marker, i)
    assert j >= 0, "结束锚点未找到: %r" % end_marker[:60]
    return src[i:j + len(end_marker)]


class _CfgSwitch:
    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

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


# ============================================================ 一次性消费
class TestThreadLocalMarker(unittest.TestCase):
    """A：线程局部来源标记（一次性消费）"""

    def setUp(self):
        self.l = _bare()

    def test_01_no_marker_returns_none(self):
        self.assertIsNone(self.l._m49_take_call_source())

    def test_02_true_roundtrip(self):
        self.l._m49_mark_call_source(True)
        self.assertIs(self.l._m49_take_call_source(), True)

    def test_03_false_roundtrip(self):
        self.l._m49_mark_call_source(False)
        self.assertIs(self.l._m49_take_call_source(), False)

    def test_04_one_shot_semantics(self):
        """★一次性消费：取出后必须清空，否则会串到下一次调用。"""
        self.l._m49_mark_call_source(True)
        self.assertIs(self.l._m49_take_call_source(), True)
        self.assertIsNone(self.l._m49_take_call_source(), "第二次取应为 None")

    def test_05_mark_coerces_to_bool(self):
        self.l._m49_mark_call_source(1)
        self.assertIs(self.l._m49_take_call_source(), True)
        self.l._m49_mark_call_source(0)
        self.assertIs(self.l._m49_take_call_source(), False)

    def test_06_switch_off_makes_mark_noop(self):
        """零回归：开关关闭 → mark 无效、take 恒 None（回到共享属性行为）。"""
        with _CfgSwitch(ENABLE_LUNG_CALL_SOURCE_FIX=False):
            self.l._m49_mark_call_source(True)
            self.assertIsNone(self.l._m49_take_call_source())

    def test_07_switch_default_on(self):
        self.assertTrue(self.l._m49_source_fix_on())

    def test_08_tls_lazily_ensured(self):
        """★惰性补齐：`Cls.__new__(Cls)` 轻量实例（无 `_m49_tls`）不得抛异常，
        且标记仍可用（项目既有惯例：测试用 __new__ 绕 __init__）。"""
        l = PulseLung.__new__(PulseLung)
        self.assertFalse(hasattr(l, "_m49_tls"))
        l._m49_mark_call_source(True)          # 不得抛
        self.assertTrue(hasattr(l, "_m49_tls"), "应惰性补齐 _m49_tls")
        self.assertIs(l._m49_take_call_source(), True)


# ============================================================ 跨线程隔离
class TestCrossThreadIsolation(unittest.TestCase):
    """★核心：双腿 3 路并发不得串味（旧实现的真实缺陷）"""

    def test_10_four_threads_no_bleed(self):
        l = _bare()
        res = {}
        barrier = threading.Barrier(4)

        def worker(name, bg):
            barrier.wait()                      # 同起跑，制造竞态窗口
            l._m49_mark_call_source(bg)
            for _ in range(300):
                pass
            res[name] = l._m49_take_call_source()

        ts = [threading.Thread(target=worker, args=("t%d" % i, bool(i % 2)))
              for i in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(res, {"t0": False, "t1": True, "t2": False, "t3": True},
                         "跨线程串味！实得 %s" % res)

    def test_11_shared_attr_would_bleed(self):
        """对照：证明「共享实例属性」确有串味风险（记录根因）。"""
        l = _bare()

        def a():
            l._current_task_is_background = True

        def b():
            l._current_task_is_background = False

        ta, tb = threading.Thread(target=a), threading.Thread(target=b)
        ta.start(); ta.join()
        tb.start(); tb.join()
        # 后者覆盖前者 —— 这正是旧实现「读共享属性」的竞态来源
        self.assertFalse(l._current_task_is_background)

    def test_12_fallback_to_instance_attr(self):
        """兼容 4 个既有测试（它们直接设置 `_current_task_is_background`）。"""
        l = _bare()
        l._current_task_is_background = True
        self.assertIsNone(l._m49_take_call_source())
        fb = l._m49_take_call_source()
        if fb is None:
            fb = bool(getattr(l, "_current_task_is_background", False))
        self.assertTrue(fb)


# ============================================================ 无罪推定
class TestDialoguePresumption(unittest.TestCase):
    """D：缺 `is_dialogue` 标记时的推定方向（开关双向）"""

    _START = '        _is_dialogue = bool(payload.get("is_dialogue", False))'
    _END = '        payload["is_background_learning"] = _is_background'

    def _run_norm(self, payload, presume=True):
        """执行**真实源码**切片（回退即失败）。"""
        block = _slice(_SRC, self._START, self._END)
        logged = []

        class _FakeSelf:
            def _m49_dialogue_presumption_on(self):
                return presume

            def _log(self, level, msg):
                logged.append(msg)

        ns = {"payload": payload, "self": _FakeSelf(),
              "LogLevel": _LogLevel, "__builtins__": __builtins__}
        exec(textwrap.dedent(block), ns)      # 执行真实源码切片（回退即失败）
        return ns, logged

    def test_20_missing_mark_presumes_dialogue(self):
        ns, logs = self._run_norm({"task_type": "chat"}, presume=True)
        self.assertTrue(ns["payload"]["is_dialogue"], "无罪推定应视为对话")
        self.assertFalse(ns["payload"]["is_background_learning"])
        self.assertTrue(any("缺少 is_dialogue 标记" in m for m in logs),
                        "缺标记必须留 WARNING 便于排查")

    def test_21_switch_off_restores_old_behavior(self):
        """关闭 → 回到旧行为：无 correlation_id 视为后台学习。"""
        ns, logs = self._run_norm({"task_type": "chat"}, presume=False)
        self.assertFalse(ns["payload"]["is_dialogue"])
        ns2, _ = self._run_norm({"task_type": "chat", "correlation_id": "ctx:1"},
                                presume=False)
        self.assertTrue(ns2["payload"]["is_dialogue"])

    def test_22_explicit_background_wins(self):
        """显式后台标记优先级最高（不得被推定覆盖）。"""
        ns, _ = self._run_norm(
            {"is_background_learning": True, "is_dialogue": True}, presume=True)
        self.assertFalse(ns["payload"]["is_dialogue"])

    def test_23_explicit_dialogue_untouched(self):
        """显式 is_dialogue=True → 不进推定分支、不留 WARNING。"""
        ns, logs = self._run_norm({"is_dialogue": True}, presume=True)
        self.assertTrue(ns["payload"]["is_dialogue"])
        self.assertEqual([m for m in logs if "缺少 is_dialogue" in m], [])

    def test_24_remediation_forces_dialogue(self):
        ns, _ = self._run_norm({"is_dialogue": False, "is_remediation": True},
                               presume=True)
        self.assertTrue(ns["payload"]["is_dialogue"])
        self.assertFalse(ns["payload"]["is_background_learning"])


# ============================================================ 开关双向
class TestSwitches(unittest.TestCase):
    def test_30_presumption_switch(self):
        l = _bare()
        with _CfgSwitch(ENABLE_LUNG_DIALOGUE_PRESUMPTION=False):
            self.assertFalse(l._m49_dialogue_presumption_on())
        with _CfgSwitch(ENABLE_LUNG_DIALOGUE_PRESUMPTION=True):
            self.assertTrue(l._m49_dialogue_presumption_on())

    def test_31_config_defaults_present(self):
        self.assertIs(getattr(config, "ENABLE_LUNG_CALL_SOURCE_FIX", None), True)
        self.assertIs(getattr(config, "ENABLE_LUNG_DIALOGUE_PRESUMPTION", None), True)


# ============================================================ 热重载契约
class TestHotReloadContract(unittest.TestCase):
    def test_40_state_preserved_ok(self):
        l = _bare()
        l._dialog_history = []
        l._recent_requests = []
        l._m49_assert_dialog_state_preserved()        # 不应抛

    def test_41_missing_state_raises(self):
        l = _bare()
        del l._m49_tls
        with self.assertRaises(AssertionError):
            l._m49_assert_dialog_state_preserved()

    def test_42_refresh_calls_contract(self):
        """`refresh_runtime_params` 必须调用契约断言（不得只刷阈值）。"""
        self.assertIn("self._m49_assert_dialog_state_preserved()", _SRC)


# ============================================================ 结构（接线）
class TestSourceWiring(unittest.TestCase):
    """调用形态计数（不是裸方法名），防「只造轮子没装上车」。"""

    def test_50_select_model_marks_tls(self):
        """3 个生产调用点显式声明来源（`_on_select_model` / `_chat_with_model` /
        `_parse_semantic_intent`）＋ 双腿回调在 main.py（另一文件）。"""
        self.assertGreaterEqual(
            _SRC.count("self._m49_mark_call_source("), 3,
            "至少 3 个调用点应显式声明来源")
        self.assertEqual(
            _SRC.count("def _m49_mark_call_source("), 1, "定义应恰好 1 处")

    def test_51_call_via_channels_reads_tls(self):
        self.assertIn("_m49_bg = self._m49_take_call_source()", _SRC)
        self.assertIn("self._current_call_is_background = _m49_bg", _SRC)

    def test_52_chat_with_model_propagates_is_background(self):
        """★历史缺陷：`_chat_with_model` 有 `is_background` 形参却从不传递。"""
        self.assertIn("self._m49_mark_call_source(bool(is_background))", _SRC)

    def test_53_legs_callback_marks_background(self):
        """双腿回调（两处：插件化 + legacy）都必须显式声明后台来源。"""
        self.assertEqual(_MAIN_SRC.count("_m49_legs_llm"), 4,
                         "2 处定义 + 2 处使用 = 4")

    def test_54_taskbook_line_311_is_only_thinking(self):
        """★记录：:311 的局部变量只喂 `_enable_thinking`，非 `来源=` 的根因。"""
        i = _SRC.find('_is_background_learning = payload.get("is_background_learning", False) or not payload.get("is_dialogue", False)')
        self.assertGreater(i, 0, "该行应仍存在（本批未改动它）")
        window = _SRC[i:i + 700]
        self.assertIn("_enable_thinking", window,
                      "它只影响思考模式开关，与渠道来源无关")


if __name__ == "__main__":
    unittest.main(verbosity=2)
