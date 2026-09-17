# -*- coding: utf-8 -*-
"""主线第31批 门控测试：深度思考子进程恒降级修复（T1 / P2-185）。

背景（本批 T0 实测）：
  `ReasoningWorkerPool._execute_reasoning_task` 对 `PulseInnerWorld._deep_think`
  **恒返回**降级标记 `{'status':'degraded','reason':'子进程无知识上下文…'}`
  （第15批 P1-5 架构决策：深度思考依赖主进程内存态 node_pool/knowledge_tree）。
  该标记是 **truthy 的 dict**，而 3 处调用点里只有 1 处（第29批）识别它：
    · 思考纪律·深度通道 / 复杂度过高 → dict 被当结果 → 主进程回退被跳过
  本批统一判定并回退主进程同步执行（带第27批 deadline 预算）。
"""
import os
import sys
import textwrap
import time
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402
from nucleus.reasoning.ReasoningWorkerPool import ReasoningWorkerPool  # noqa: E402

_IW_SRC_PATH = os.path.join(_PROJECT_ROOT, "organs", "brain", "PulseInnerWorld.py")
_IW_SRC = open(_IW_SRC_PATH, encoding="utf-8").read()

# 与 ReasoningWorkerPool._execute_reasoning_task 的真实降级标记保持同构
_DEGRADED = {
    "status": "degraded",
    "reason": "子进程无知识上下文，请改用主进程同步执行深度思考",
    "result": None,
}


class _LL:
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class _Switch:
    """临时改写 config 上的开关（用后复原）。"""

    def __init__(self, **kw):
        self.kw = kw
        self.old = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(config, k, v)
        return False


def _mk_iw():
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw._log = lambda *a, **k: None
    return iw


class TestSubprocResultVerdict(unittest.TestCase):
    """`_m31_accept_subproc_deep_result` 的判定语义（双向门控）。"""

    def test_01_str_passthrough(self):
        self.assertEqual(_mk_iw()._m31_accept_subproc_deep_result("完整思考结果"), "完整思考结果")

    def test_02_degraded_marker_rejected(self):
        """★核心：降级标记（truthy dict）必须被判为不可用。"""
        self.assertIsNone(_mk_iw()._m31_accept_subproc_deep_result(_DEGRADED))

    def test_03_any_dict_rejected(self):
        """任何 dict 都不是可用的深度思考答案（含无 status 的）。"""
        self.assertIsNone(_mk_iw()._m31_accept_subproc_deep_result({"a": 1}))

    def test_04_none_rejected(self):
        self.assertIsNone(_mk_iw()._m31_accept_subproc_deep_result(None))

    def test_05_empty_str_rejected(self):
        self.assertIsNone(_mk_iw()._m31_accept_subproc_deep_result(""))
        self.assertIsNone(_mk_iw()._m31_accept_subproc_deep_result("   ".strip()))

    def test_06_truthy_check_regression(self):
        """★回归护栏：降级标记在布尔语境下为 True —— 正是旧实现的陷阱。"""
        self.assertTrue(bool(_DEGRADED), "降级标记必须是 truthy，否则本问题不存在")
        self.assertIsNone(_mk_iw()._m31_accept_subproc_deep_result(_DEGRADED))

    def test_07_switch_off_restores_old_behavior(self):
        """★灰度关闭：原样返回（含降级 dict），完全回退修复前行为。"""
        with _Switch(ENABLE_DEEP_THINK_SUBPROCESS_FIX=False):
            _r = _mk_iw()._m31_accept_subproc_deep_result(_DEGRADED)
        self.assertEqual(_r, _DEGRADED)

    def test_08_switch_on_is_default(self):
        self.assertTrue(getattr(config, "ENABLE_DEEP_THINK_SUBPROCESS_FIX", False))


class TestFallbackDeadline(unittest.TestCase):
    """`_m31_deep_fallback_deadline` 与第27批预算口径对齐。"""

    def test_10_deadline_from_reasoning_start(self):
        with _Switch(ENABLE_DEEP_THINK_TIMEOUT_PROTECTION=True,
                     INNER_WORLD_DEEP_THINK_TOTAL_BUDGET_SEC=40.0):
            _d = _mk_iw()._m31_deep_fallback_deadline(1000.0)
        self.assertAlmostEqual(_d, 1040.0, places=3)

    def test_11_none_start_uses_now(self):
        with _Switch(ENABLE_DEEP_THINK_TIMEOUT_PROTECTION=True,
                     INNER_WORLD_DEEP_THINK_TOTAL_BUDGET_SEC=40.0):
            _d = _mk_iw()._m31_deep_fallback_deadline(None)
        self.assertGreater(_d, time.time() + 30)

    def test_12_switch_off_returns_none(self):
        """超时保护关闭 → None（`_deep_think` 自行计时，等价修复前行为）。"""
        with _Switch(ENABLE_DEEP_THINK_TIMEOUT_PROTECTION=False):
            self.assertIsNone(_mk_iw()._m31_deep_fallback_deadline(1000.0))

    def test_13_zero_budget_returns_none(self):
        with _Switch(ENABLE_DEEP_THINK_TIMEOUT_PROTECTION=True,
                     INNER_WORLD_DEEP_THINK_TOTAL_BUDGET_SEC=0):
            self.assertIsNone(_mk_iw()._m31_deep_fallback_deadline(1000.0))


class TestWorkerPoolBypass(unittest.TestCase):
    """`ReasoningWorkerPool.submit` 对深度思考任务短路（省一次必然无效的往返）。"""

    @staticmethod
    def _mk_pool():
        p = ReasoningWorkerPool.__new__(ReasoningWorkerPool)
        p._degraded_sync = True          # 让它必然走同步路径（便于区分）
        p._closed = True
        p._maybe_recover_from_degraded = lambda: False
        p._sync_future = lambda fn, *a, **k: "SYNC_PATH"
        return p

    def test_20_bypass_on_returns_none(self):
        """★核心：bypass 开启时对该任务直接返回 None（调用方判空即回退主进程）。"""
        with _Switch(ENABLE_DEEP_THINK_SUBPROCESS_BYPASS=True):
            self.assertIsNone(self._mk_pool().submit("PulseInnerWorld._deep_think", "q", 3))

    def test_21_bypass_off_goes_through(self):
        """灰度关闭：不短路，走既有降级同步路径（证明短路确实由开关控制）。"""
        with _Switch(ENABLE_DEEP_THINK_SUBPROCESS_BYPASS=False):
            self.assertEqual(self._mk_pool().submit("PulseInnerWorld._deep_think", "q", 3),
                             "SYNC_PATH")

    def test_22_other_tasks_unaffected(self):
        """其他任务不受短路影响（仍走原路径）。"""
        with _Switch(ENABLE_DEEP_THINK_SUBPROCESS_BYPASS=True):
            self.assertEqual(self._mk_pool().submit("FrequencyCodec.encode_batch", []),
                             "SYNC_PATH")

    def test_23_default_on(self):
        self.assertTrue(ReasoningWorkerPool._deep_think_bypass_enabled())


class TestCallSitesWired(unittest.TestCase):
    """★源码层：三处调用点必须都经统一判定方法（防止未来回退）。"""

    def test_30_three_call_sites(self):
        """★主线第32批 T5：改为数**调用形态**（`= self.xxx(`），
        避免 docstring 里的示例文本被计入（原实现直接数裸方法名 → 脆弱）。

        ★主线第33批 T2（P2-195）：`==` 放宽为 `>=` —— 语义是「三处调用点
        **都**必须经统一判定方法」，删掉任意一处即 <3 仍会失败；而未来合法
        新增调用点（如第4处）不该误报。
        """
        self.assertGreaterEqual(
            _IW_SRC.count("= self._m31_accept_subproc_deep_result("), 3)

    def test_31_no_legacy_inline_verdict(self):
        """旧的「子进程返回dict」内联判定应已收敛到统一方法。"""
        self.assertNotIn("子进程返回dict", _IW_SRC)

    def test_32_fallback_deadline_used(self):
        # 两处主进程回退均以 `deadline=self._m31_deep_fallback_deadline(...)` 形态接入
        # ★主线第33批 T2（P2-195）：语义=「回退路径必须带 deadline 预算」，
        #   少一处即 <2 仍失败；放宽为 >= 以免新增回退点时假失败。
        self.assertGreaterEqual(
            _IW_SRC.count("deadline=self._m31_deep_fallback_deadline("), 2)


class TestCallSiteRealExec(unittest.TestCase):
    """★真实源码切片 exec：降级标记 → 主进程同步执行被真正走通。

    比文本断言强 —— 切片内是**真实代码**，源码回退即测试失败。
    """

    _START = "            _deep_result = None\n"
    _END = "            # 第三步：综合知识检索和深度思考结果\n"

    def _run(self, subproc_result):
        _i = _IW_SRC.index(self._START)
        _j = _IW_SRC.index(self._END, _i) + len(self._END)
        _seg = textwrap.dedent(_IW_SRC[_i:_j])

        _calls = []
        _iw = _mk_iw()

        class _Fut:
            def result(self, timeout=None):
                return subproc_result

            def cancel(self):
                pass

        class _Pool:
            def submit(self, *a, **k):
                return _Fut()

        _iw._reasoning_pool = _Pool()
        _iw._deep_think_timeout = 5
        _iw._deep_think = lambda q, max_rounds=3, deadline=None: (
            _calls.append({"q": q, "deadline": deadline}),
            "主进程深度思考：完整答案" if subproc_result is _DEGRADED else "x")[1]

        # 切片来自方法体，需补齐其外层作用域变量
        _ns = {"self": _iw, "time": time, "LogLevel": _LL,
               "hasattr": hasattr, "Exception": Exception,
               "question": "测试问题：数字生命的意义是什么？",
               "_reasoning_start_time": time.time()}
        exec(compile(_seg, "<m31-slice>", "exec"), _ns)
        return _ns.get("_deep_result"), _calls

    def test_40_degraded_falls_back_to_main_process(self):
        """★核心：子进程返回降级标记 → 主进程同步执行被真正调用。"""
        with _Switch(ENABLE_DEEP_THINK_SUBPROCESS_BYPASS=False,
                     ENABLE_DEEP_THINK_SUBPROCESS_FIX=True,
                     ENABLE_DEEP_THINK_TIMEOUT_PROTECTION=True,
                     INNER_WORLD_DEEP_THINK_TOTAL_BUDGET_SEC=40.0):
            _r, _calls = self._run(_DEGRADED)
        self.assertEqual(len(_calls), 1, "主进程 _deep_think 应被调用恰好 1 次")
        self.assertEqual(_r, "主进程深度思考：完整答案")

    def test_41_degraded_carries_deadline_budget(self):
        """主进程回退必须带 deadline（第27批预算口径）。"""
        with _Switch(ENABLE_DEEP_THINK_SUBPROCESS_BYPASS=False,
                     ENABLE_DEEP_THINK_SUBPROCESS_FIX=True,
                     ENABLE_DEEP_THINK_TIMEOUT_PROTECTION=True,
                     INNER_WORLD_DEEP_THINK_TOTAL_BUDGET_SEC=40.0):
            _r, _calls = self._run(_DEGRADED)
        self.assertIsNotNone(_calls[0]["deadline"])

    def test_42_bypass_on_skips_submit_entirely(self):
        """bypass 开启（默认）时 pool.submit 返回 None → 直接走主进程。"""
        _iw = _mk_iw()
        _iw._reasoning_pool = ReasoningWorkerPool.__new__(ReasoningWorkerPool)
        with _Switch(ENABLE_DEEP_THINK_SUBPROCESS_BYPASS=True):
            self.assertIsNone(_iw._reasoning_pool.submit("PulseInnerWorld._deep_think", "q", 3))

    def test_43_switch_off_reproduces_old_bug(self):
        """★灰度关闭时复现旧行为：降级 dict 被当作结果（回归对照）。"""
        with _Switch(ENABLE_DEEP_THINK_SUBPROCESS_BYPASS=False,
                     ENABLE_DEEP_THINK_SUBPROCESS_FIX=False):
            _r, _calls = self._run(_DEGRADED)
        self.assertEqual(_r, _DEGRADED, "关闭开关应还原为把 dict 当结果")
        self.assertEqual(len(_calls), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
