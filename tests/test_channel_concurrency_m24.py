# -*- coding: utf-8 -*-
"""test_channel_concurrency_m24.py —— 主线第24批门控单测（20 例）。

覆盖任务书 §二 测试清单全部 20 项：
  T1 渠道独立并发(2) / T2 并发满切换(3) / T3 动态调整(4) / T4 用户对话优先(3)
  T5 多轮对话(2) / T6 进度提示(2) / T7 监控统计(2) / 集成(1) / 回归(1)

★全部 mock 渠道调用（`_call_channel`），**不依赖真实网络**；执行时间 < 30 秒。
★管理器是**进程级单例** → 每个用例 setUp/tearDown 显式复位，避免相互污染。
"""
import os
import sys
import threading
import unittest
from collections import deque

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from nucleus.llm.ChannelConcurrency import (  # noqa: E402
    reset_channel_concurrency_manager,
)
from organs.body.PulseLung import PulseLung  # noqa: E402


def _mk_lung(call_impl):
    """轻量实例化 PulseLung（绕过 __init__），注入最小依赖与 mock 渠道调用。"""
    _l = PulseLung.__new__(PulseLung)
    _l._logs = []
    _l._log = lambda lv, msg: _l._logs.append((lv, str(msg)))
    _l._channel_conc = None
    _l._channel_conc_lock = threading.Lock()
    _l._dialog_history = deque(maxlen=10)
    _l._dialog_history_lock = threading.Lock()
    _l._last_progress_ts = 0.0
    _l._progress_callback = None
    _l._channel_health = None
    _l._current_call_is_background = False
    _l._gateway_channel = lambda: None
    _l._is_advanced_task = lambda _m: False
    _l._build_advanced_channel = lambda: None
    _l._get_channel_health = lambda: None
    _l._update_channel_health = lambda *a, **k: None
    _l._record_model_result = lambda *a, **k: None
    _l._call_channel = call_impl
    return _l


# ======================================================================
# ★主线第32批 T1（P2-187）：渠道名与并发值一律**从配置动态推导**
#   背景：第24批测试硬编码 `zhipu/doubao/deepseek` 与 `doubao=5`；
#   星轨 2026-09-12 调整渠道池（doubao 项被 ARK 渠道取代、并发值全变）后失效。
#   此后配置再变，本文件的用例也不会再假失败。
# ======================================================================


def _m32_active_channels():
    """按 priority 升序的可用渠道（来自 config，不缓存，保证热改可见）。"""
    return config.get_active_channels()


def _m32_paid_names():
    """付费渠道名集合（config.PAID_CHANNEL_NAMES；缺失时视为空）。"""
    return set(getattr(config, "PAID_CHANNEL_NAMES", []) or [])


def _m32_split_channels():
    """返回 (付费渠道列表, 免费渠道列表)，各按 priority 升序。"""
    _paid = _m32_paid_names()
    _chs = _m32_active_channels()
    return ([c for c in _chs if c.get("name") in _paid],
            [c for c in _chs if c.get("name") not in _paid])


def _m32_pick_adjustable(min_max: int = 3, exclude_paid: bool = True):
    """挑一个适合「并发上限调整」用例的渠道（max_concurrent >= min_max）。

    参数:
        min_max:      所需的最小配置并发上限（要连续 -1/-2 次，故需留余量）。
        exclude_paid: 是否排除付费渠道（付费渠道通常额度大、并发高，用它测不出边界）。
    返回:
        渠道 dict；无满足条件者返回 None。
    """
    _paid = _m32_paid_names()
    for _c in _m32_active_channels():
        if exclude_paid and _c.get("name") in _paid:
            continue
        try:
            if int(_c.get("max_concurrent", 1)) >= min_max:
                return _c
        except Exception:
            continue
    return None


def _ok_reply(_ch, _prompt, **_kw):
    return "模拟回复"


def _none_reply(_ch, _prompt, **_kw):
    return None


class _M24Base(unittest.TestCase):
    """统一处理单例复位与配置恢复。"""

    def setUp(self):
        reset_channel_concurrency_manager()
        self._saved = {
            "ENABLE_CHANNEL_CONCURRENCY": getattr(config, "ENABLE_CHANNEL_CONCURRENCY", True),
            "USER_DIALOG_PREFER_PAID": getattr(config, "USER_DIALOG_PREFER_PAID", True),
            "DIALOG_PROGRESS_HINT_CONFIG": getattr(config, "DIALOG_PROGRESS_HINT_CONFIG", {}),
            "DIALOG_HISTORY_TURNS": getattr(config, "DIALOG_HISTORY_TURNS", 5),
            "DIALOG_HISTORY_MAX_TOKENS": getattr(config, "DIALOG_HISTORY_MAX_TOKENS", 2000),
            "DYNAMIC_CONCURRENCY_CONFIG": getattr(config, "DYNAMIC_CONCURRENCY_CONFIG", {}),
        }

    def tearDown(self):
        for _k, _v in self._saved.items():
            setattr(config, _k, _v)
        reset_channel_concurrency_manager()


# ======================================================================
# T1 渠道独立并发（2 例）
# ======================================================================
class TestT1PerChannel(_M24Base):
    def test_01_independent_semaphores_with_config_init(self):
        """每个渠道有独立信号量，且初始值来自配置 max_concurrent。"""
        _l = _mk_lung(_ok_reply)
        _l._channel_concurrency()
        # ★主线第32批 T1：渠道名与并发上限改为从配置动态推导
        _pick = _m32_active_channels()[:3]
        self.assertGreaterEqual(len(_pick), 3, "本用例需要至少 3 个可用渠道")
        _sm = {c["name"]: _l._get_channel_semaphore(c["name"]) for c in _pick}
        self.assertTrue(all(v is not None for v in _sm.values()))
        for _c in _pick:
            self.assertEqual(
                _sm[_c["name"]].max_value, int(_c.get("max_concurrent", 1)),
                f"{_c['name']} 信号量上限应等于配置 max_concurrent")
        self.assertEqual(len({id(v) for v in _sm.values()}), 3,
                         "各渠道必须是独立信号量")

    def test_02_global_semaphore_still_total_limit(self):
        """全局信号量仍作为总上限保护（未被本批改动）。"""
        from nucleus.api_rate_limiter import get_api_limiter
        _st = get_api_limiter().get_stats()
        self.assertEqual(_st["max_concurrent"], 8)
        self.assertIn("in_use", _st)


# ======================================================================
# T2 并发满即切换（3 例）
# ======================================================================
class TestT2FullSwitch(_M24Base):
    def test_03_full_switches_immediately(self):
        """渠道并发满 → 零延迟切换下一个（不调用该渠道）。"""
        _calls = []

        def _spy(ch, prompt, **kw):
            _calls.append(ch.get("name"))

        _l = _mk_lung(_spy)
        _l._channel_concurrency()
        _l._get_channel_semaphore("zhipu").acquire(blocking=False)   # 占满（上限 1）
        try:
            _l._call_via_channels("hi", "m")
        finally:
            _l._get_channel_semaphore("zhipu").release()
        self.assertNotIn("zhipu", _calls, "并发满的渠道不应被调用")
        self.assertTrue(_calls, "应切换到其他渠道")
        self.assertTrue(any("并发已满" in m for _, m in _l._logs),
                        "应记录『并发已满，立即切换』日志")

    def test_04_semaphore_released_on_exception(self):
        """渠道调用抛异常时，许可仍被归还（无泄漏）且轮询继续。"""
        _state = {"n": 0}

        def _boom_then_ok(ch, prompt, **kw):
            _state["n"] += 1
            if _state["n"] == 1:
                raise RuntimeError("模拟渠道异常")
            return "第二个渠道成功"

        _l = _mk_lung(_boom_then_ok)
        _l._channel_concurrency()
        _r = _l._call_via_channels("hi", "m")
        self.assertEqual(_r, "第二个渠道成功")
        _tot = sum(v["current_concurrent"] for v in _l.get_channel_concurrency_stats().values())
        self.assertEqual(_tot, 0, "异常后不得残留许可占用")

    def test_05_all_full_wait_and_retry(self):
        """所有渠道并发满 → 等待后重试一轮。"""
        config.DYNAMIC_CONCURRENCY_CONFIG = {
            **self._saved["DYNAMIC_CONCURRENCY_CONFIG"], "all_channels_full_wait": 0.05,
        }
        _calls = []

        def _spy(ch, prompt, **kw):
            _calls.append(ch.get("name"))

        _l = _mk_lung(_spy)
        _l._channel_concurrency()
        # 占满全部渠道（★主线第32批 T1：从配置动态取全部可用渠道）
        _sems = [_l._get_channel_semaphore(_c["name"])
                 for _c in _m32_active_channels()]
        for _s in _sems:
            for _ in range(_s.max_value):
                _s.acquire(blocking=False)
        try:
            _l._call_via_channels("hi", "m")
        finally:
            for _s in _sems:
                for _ in range(_s.max_value):
                    _s.release()
        self.assertFalse(_calls, "全满时不应发起任何调用")
        self.assertTrue(any("等待" in m and "重试" in m for _, m in _l._logs),
                        "应记录等待重试日志")


# ======================================================================
# T3 动态并发调整（4 例）
# ======================================================================
class TestT3Dynamic(_M24Base):
    def _lung(self):
        _l = _mk_lung(_ok_reply)
        _l._channel_concurrency()
        return _l

    def test_06_success_increases_max(self):
        """连续成功达阈值 → 并发上限 +1。"""
        _l = self._lung()
        _sem = _l._get_channel_semaphore("doubao")
        _thr = int(config.DYNAMIC_CONCURRENCY_CONFIG["success_threshold"])
        _base = _sem.max_value
        for _ in range(_thr):
            _l._adjust_channel_concurrency("doubao", True, latency=0.2)
        self.assertEqual(_sem.max_value, _base + 1)

    def test_07_failure_decreases_max(self):
        """失败/限流 → 并发上限立即 -1。"""
        _l = self._lung()
        # ★主线第32批 T1：动态挑一个「配置并发 >= 3」的免费渠道，保证能连续降 2 次
        _ch = _m32_pick_adjustable(min_max=3, exclude_paid=True)
        self.assertIsNotNone(_ch, "本用例需要一个配置 max_concurrent >= 3 的免费渠道")
        _name = _ch["name"]
        _sem = _l._get_channel_semaphore(_name)
        _base = _sem.max_value
        _l._adjust_channel_concurrency(_name, False, is_rate_limit=True)
        self.assertEqual(_sem.max_value, _base - 1)
        _l._adjust_channel_concurrency(_name, False, is_timeout=True)
        self.assertEqual(_sem.max_value, _base - 2)

    def test_08_bounds_protection(self):
        """上下界保护：不低于 min_concurrent，免费渠道不超 free_channel_max。"""
        _l = self._lung()
        _sem = _l._get_channel_semaphore("zhipu")
        for _ in range(20):
            _l._adjust_channel_concurrency("zhipu", False)
        self.assertEqual(_sem.max_value,
                         config.DYNAMIC_CONCURRENCY_CONFIG["min_concurrent"])
        for _ in range(100):
            _l._adjust_channel_concurrency("zhipu", True)
        self.assertEqual(_sem.max_value,
                         config.DYNAMIC_CONCURRENCY_CONFIG["free_channel_max"])

    def test_09_adjust_history_traceable(self):
        """调整历史可追溯（含时间/旧值/新值/原因）。"""
        _l = self._lung()
        _thr = int(config.DYNAMIC_CONCURRENCY_CONFIG["success_threshold"])
        for _ in range(_thr):
            _l._adjust_channel_concurrency("doubao", True, latency=0.2)
        _hist = _l.get_channel_concurrency_stats()["doubao"]["adjust_history"]
        self.assertTrue(_hist)
        self.assertEqual(set(_hist[-1].keys()), {"ts", "old", "new", "reason"})
        self.assertIn("成功", _hist[-1]["reason"])


# ======================================================================
# T4 用户对话优先付费渠道（3 例）
# ======================================================================
class TestT4PreferPaid(_M24Base):
    def test_10_user_dialog_prefers_paid(self):
        """用户对话 → 付费渠道前置（免费后置兜底）。"""
        _l = _mk_lung(_ok_reply)
        _cand = [{"name": "zhipu", "priority": 1}, {"name": "doubao", "priority": 2},
                 {"name": "deepseek", "priority": 3}]
        _out = _l._prefer_paid_channels(_cand, caller="user_dialog")
        self.assertEqual(_out[0]["name"], "deepseek")
        self.assertEqual(_out[-1]["name"], "doubao", "免费渠道应后置作兜底")

    def test_11_background_prefers_free(self):
        """后台学习 → 不重排（仍按原优先级的免费优先逻辑）。"""
        _l = _mk_lung(_ok_reply)
        _cand = [{"name": "zhipu", "priority": 1}, {"name": "deepseek", "priority": 3}]
        _out = _l._prefer_paid_channels(_cand, caller="background_learning")
        self.assertEqual([c["name"] for c in _out], ["zhipu", "deepseek"])

    def test_12_paid_failure_falls_back_to_free(self):
        """付费渠道失败 → 兜底到免费渠道（调用序列可验证）。"""
        # ★主线第32批 T1：付费/免费渠道均从配置动态取（原硬编码 deepseek/doubao）
        _paid_chs, _free_chs = _m32_split_channels()
        self.assertTrue(_paid_chs, "本用例需要至少 1 个付费渠道")
        self.assertTrue(_free_chs, "本用例需要至少 1 个免费渠道")
        _paid_name = _paid_chs[0]["name"]
        _free_name = _free_chs[0]["name"]

        _calls = []

        def _spy(ch, prompt, **kw):
            _calls.append(ch.get("name"))
            return "免费兜底成功" if ch.get("name") == _free_name else None

        _l = _mk_lung(_spy)
        _r = _l._call_via_channels("hi", "m", caller="user_dialog")
        self.assertEqual(_r, "免费兜底成功")
        self.assertEqual(_calls[0], _paid_name, "用户对话应最先尝试付费渠道")
        self.assertIn(_free_name, _calls, "付费渠道失败后应尝试免费渠道")


# ======================================================================
# T5 多轮对话（2 例）
# ======================================================================
class TestT5History(_M24Base):
    def test_13_messages_include_history(self):
        """_build_channel_messages 包含历史上下文（[system, ...hist, user]）。"""
        _l = _mk_lung(_ok_reply)
        _base = _l._build_channel_messages("第二问")
        self.assertEqual(len(_base), 2, "无历史时应与改造前一致")
        _l.append_dialog_turn("user", "第一问")
        _l.append_dialog_turn("assistant", "第一答")
        _out = _l._build_channel_messages("第二问")
        self.assertEqual([m["role"] for m in _out],
                         ["system", "user", "assistant", "user"])
        self.assertEqual(_out[-1]["content"], "第二问")

    def test_14_history_turns_configurable_and_trimmed(self):
        """历史轮数可配置且超预算时截断（丢弃最旧）。"""
        config.DIALOG_HISTORY_TURNS = 2
        _l = _mk_lung(_ok_reply)
        for _i in range(10):
            _l.append_dialog_turn("user", f"第{_i}问")
            _l.append_dialog_turn("assistant", f"第{_i}答")
        _hist = _l.get_dialog_history()
        self.assertEqual(len(_hist), 4, f"maxlen 应为 turns*2=4，实际 {len(_hist)}")
        self.assertEqual(_hist[-1]["content"], "第9答")
        # 预算截断
        config.DIALOG_HISTORY_MAX_TOKENS = 10
        _trimmed = _l._trim_history_by_budget(_hist)
        self.assertLess(len(_trimmed), len(_hist))
        self.assertLessEqual(sum(len(x["content"]) for x in _trimmed), 30)


# ======================================================================
# T6 进度提示（2 例）
# ======================================================================
class TestT6Progress(_M24Base):
    def test_15_progress_hint_emitted(self):
        """处理过程中有阶段性提示（payload 带 type=progress）。"""
        _l = _mk_lung(_ok_reply)
        _got = []
        _l._progress_callback = lambda p: _got.append(p)
        self.assertTrue(_l.emit_dialog_progress("收到输入"))
        self.assertTrue(_got)
        self.assertEqual(_got[0]["type"], "progress")
        self.assertEqual(_got[0]["stage"], "收到输入")

    def test_16_progress_interval_configurable(self):
        """提示间隔可配置（间隔内抑制）；开关关闭则完全不发布。"""
        _l = _mk_lung(_ok_reply)
        config.DIALOG_PROGRESS_HINT_CONFIG = {"enabled": True, "min_interval": 60.0}
        self.assertTrue(_l.emit_dialog_progress("第一次"))
        self.assertFalse(_l.emit_dialog_progress("紧接着"), "间隔内应被抑制")
        config.DIALOG_PROGRESS_HINT_CONFIG = {"enabled": False}
        _l._last_progress_ts = 0.0
        self.assertFalse(_l.emit_dialog_progress("关闭后"))


# ======================================================================
# T7 监控统计（2 例）
# ======================================================================
class TestT7Stats(_M24Base):
    def test_17_stats_available(self):
        """各渠道统计信息可获取（含并发/上限/成功失败/延迟）。"""
        _l = _mk_lung(_ok_reply)
        _l._channel_concurrency()
        _st = _l.get_channel_concurrency_stats()
        self.assertIn("zhipu", _st)
        _z = _st["zhipu"]
        for _k in ("current_concurrent", "max_concurrent", "success_count",
                   "failure_count", "timeout_count", "rate_limit_count",
                   "avg_latency", "adjust_history"):
            self.assertIn(_k, _z)
        self.assertIsInstance(_l.summary_channel_concurrency(), str)

    def test_18_stats_updated_after_adjust(self):
        """动态调整后统计信息更新（上限与成功率随调用变化）。"""
        _l = _mk_lung(_ok_reply)
        _l._channel_concurrency()
        _thr = int(config.DYNAMIC_CONCURRENCY_CONFIG["success_threshold"])
        for _ in range(_thr):
            _l._adjust_channel_concurrency("doubao", True, latency=0.4)
        _l._adjust_channel_concurrency("doubao", False, is_rate_limit=True)
        _d = _l.get_channel_concurrency_stats()["doubao"]
        self.assertEqual(_d["success_count"], _thr)
        self.assertEqual(_d["failure_count"], 1)
        self.assertEqual(_d["rate_limit_count"], 1)
        self.assertIsNotNone(_d["avg_latency"])
        self.assertTrue(_d["adjust_history"])


# ======================================================================
# 集成 + 回归（2 例）
# ======================================================================
class TestIntegration(_M24Base):
    def test_19_full_flow(self):
        """集成：并发满 → 切换 → 动态调整 → 成功。"""
        config.DYNAMIC_CONCURRENCY_CONFIG = {
            **self._saved["DYNAMIC_CONCURRENCY_CONFIG"], "all_channels_full_wait": 0.05,
        }
        # ★主线第32批 T1：付费/免费渠道均从配置动态取（原硬编码 deepseek/doubao）
        _paid_chs, _free_chs = _m32_split_channels()
        self.assertTrue(_paid_chs, "本用例需要至少 1 个付费渠道")
        self.assertTrue(_free_chs, "本用例需要至少 1 个免费渠道")
        _paid_name = _paid_chs[0]["name"]
        _free_name = _free_chs[0]["name"]

        _calls = []

        def _spy(ch, prompt, **kw):
            _calls.append(ch.get("name"))
            return "成功" if ch.get("name") == _free_name else None

        _l = _mk_lung(_spy)
        _l._channel_concurrency()
        # 占满该付费渠道的**全部**许可 —— 只占 1 个并不会让它“满”
        _sem_ds = _l._get_channel_semaphore(_paid_name)
        _n = _sem_ds.max_value
        for _ in range(_n):
            _sem_ds.acquire(blocking=False)
        try:
            _r = _l._call_via_channels("hi", "m", caller="user_dialog")
        finally:
            for _ in range(_n):
                _sem_ds.release()
        self.assertEqual(_r, "成功")
        self.assertNotIn(_paid_name, _calls, "付费渠道并发满 → 应被跳过（T2）")
        self.assertIn(_free_name, _calls, f"应切换并最终由 {_free_name} 成功")
        _st = _l.get_channel_concurrency_stats()
        self.assertGreaterEqual(
            _st[_free_name]["success_count"] + _st[_free_name]["failure_count"], 1)
        self.assertEqual(sum(v["current_concurrent"] for v in _st.values()), 0,
                         "流程结束不得残留许可占用")

    def test_20_regression_single_turn_unchanged(self):
        """回归：无历史 + 总开关关闭时，单轮对话行为与改造前一致。"""
        # ① 无历史时 messages 与改造前逐字段一致
        _l = _mk_lung(_ok_reply)
        _msgs = _l._build_channel_messages("你好")
        self.assertEqual(len(_msgs), 2)
        self.assertEqual(_msgs[0]["role"], "system")
        self.assertEqual(_msgs[1], {"role": "user", "content": "你好"})
        # ② 总开关关闭 → 完全走旧路径（无并发控制、无统计）
        config.ENABLE_CHANNEL_CONCURRENCY = False
        reset_channel_concurrency_manager()
        _l2 = _mk_lung(_ok_reply)
        self.assertIsNone(_l2._channel_concurrency())
        self.assertEqual(_l2.get_channel_concurrency_stats(), {})
        self.assertEqual(_l2._call_via_channels("hi", "m"), "模拟回复")
        # ③ 关闭时进度提示/历史仍可用（互不影响）
        self.assertTrue(_l2._build_channel_messages("x"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
