# -*- coding: utf-8 -*-
"""主线第27批 T1/T3 门控测试：深度思考超时保护 + 去重器边界 + JSON 解析边界。

分组：
  1. T1 深度思考超时保护（4 例）
  2. T3.1 RequestDeduplicator 边界（11 例）
  3. T3.2 PulseStomach JSON 解析边界（8 例）

★所有用例只读被测源码行为，不修改业务逻辑；JSON 归档用例写入 tmp/ 隔离目录并清理。
"""
import os
import shutil
import sys
import threading
import time
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from nucleus.field.RequestDeduplicator import (  # noqa: E402
    CLAIMED,
    DUPLICATE,
    WAITING,
    RequestDeduplicator,
)
from organs.body.PulseStomach import PulseStomach  # noqa: E402
from organs.brain.PulseCortex import PulseCortex  # noqa: E402
from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402

# JSON 归档测试用的隔离目录（项目内 tmp/，退出时清理）
_JSON_TMP_DIR = os.path.join(_PROJECT_ROOT, "tmp", "_m27_jsonfail_test")


def teardown_module(module):
    """pytest 识别的模块级清理（★必须以下划线命名）。"""
    shutil.rmtree(_JSON_TMP_DIR, ignore_errors=True)


class _Switch:
    """临时改 config 属性的上下文管理器（异常也保证还原）。"""

    def __init__(self, **kw):
        self._kw = kw

    def __enter__(self):
        self._old = {k: getattr(config, k) for k in self._kw}
        for k, v in self._kw.items():
            setattr(config, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self._old.items():
            setattr(config, k, v)
        return False


# ======================================================================
# 1) T1 深度思考超时保护
# ======================================================================
def _mk_iw():
    """轻量内在世界实例（绕 __init__），只挂被测路径所需属性。"""
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw.node_pool = None
    iw._last_deep_think_partial = None
    iw._deep_think_total_budget = 40.0
    iw._deep_think_max_rounds = 3
    iw._deep_think_timeout = 45.0
    iw._log = lambda *a, **k: None
    iw._generate_essence_inquiry = lambda q, c: "本质A"
    iw._generate_alternative_perspective = lambda q, c: "视角B"
    iw._attempt_framework_transfer = lambda q, c: {"insight": "框架C"}
    iw._deep_followup = lambda q, a, depth=2: "追问E%d" % depth
    iw._generate_deep_insight = lambda q, a: "洞察F"
    iw._clean_inference_output = lambda a, method="": a
    return iw


def _mk_cortex(partial=None):
    """轻量皮层实例（绕 __init__），捕获 emit 的 payload。"""
    c = PulseCortex.__new__(PulseCortex)
    c._pending_inner_world = {
        "ctx:1:abc": {"content": "请写一篇五百字左右的短文并解释原理",
                      "user_name": "星轨", "timestamp": time.time() - 999},
    }
    c._log = lambda *a, **k: None
    c._dialog_should_output = lambda cid: True
    c._dialog_mark_output_done = lambda cid: None
    c._spoke = []
    c._emit = lambda evt, payload, priority=6, layer="L1": c._spoke.append(payload)

    class _FakeIW:
        def get_partial_deep_answer(self, q="", max_age=300.0):
            return partial

    c.inner_world = _FakeIW()
    return c


class TestDeepThinkTimeout(unittest.TestCase):
    """T1：多轮深度思考的时间预算 / 部分结果 / 看门狗兜底。"""

    def test_01_budget_exhausted_keeps_first_round(self):
        """预算耗尽时提前收敛：跳过后续轮次，但**绝不返回空**。"""
        iw = _mk_iw()
        ans = iw._deep_think("请写一篇五百字左右的短文", max_rounds=3,
                             deadline=time.time() - 1)
        self.assertTrue(ans, "提前收敛仍须返回内容")
        self.assertNotIn("第三层", ans, "应跳过第 3 轮")
        partial = iw.get_partial_deep_answer("请写一篇五百字左右的短文")
        self.assertIsNotNone(partial)
        self.assertEqual(partial["rounds"], 1, "应保留已完成的 1 轮")

    def test_02_budget_enough_runs_all_rounds(self):
        """预算充足时完整跑满 3 轮。"""
        iw = _mk_iw()
        ans = iw._deep_think("请写一篇五百字左右的短文", max_rounds=3,
                             deadline=time.time() + 300)
        self.assertIn("第三层", ans)
        self.assertEqual(
            iw.get_partial_deep_answer("请写一篇五百字左右的短文")["rounds"], 3)

    def test_03_partial_cache_prefix_match_and_expiry(self):
        """部分结果缓存：同问题可取、异问题被拒（防串味）、超期失效。"""
        iw = _mk_iw()
        iw._deep_think("我叫小明，在测试缓冲区溢出", max_rounds=1,
                       deadline=time.time() + 300)
        self.assertTrue(iw.get_partial_deep_answer("我叫小明，在测试缓冲区溢出"))
        self.assertIsNone(iw.get_partial_deep_answer("完全无关的问题"))
        iw._last_deep_think_partial["ts"] = time.time() - 999
        self.assertIsNone(iw.get_partial_deep_answer("", max_age=300.0))

    def test_04_watchdog_reuses_partial_and_falls_back_when_off(self):
        """看门狗：有 partial → 复用并记轮次；开关关闭 → 完全回退 41 字原文。"""
        c = _mk_cortex({"rounds": 2, "answer": "初步理解：分三段写。",
                        "elapsed": 38.2, "ts": time.time()})
        c._cleanup_pending_inner_world()
        self.assertEqual(len(c._spoke), 1)
        self.assertIn("初步理解", c._spoke[0]["content"])
        self.assertNotIn("思考超时了", c._spoke[0]["content"])
        self.assertEqual(c._spoke[0]["partial_rounds"], 2)
        self.assertFalse(c._pending_inner_world, "超时条目应被清理")

        with _Switch(ENABLE_DEEP_THINK_TIMEOUT_PROTECTION=False):
            c2 = _mk_cortex({"rounds": 2, "answer": "不该被用到", "ts": time.time()})
            c2._cleanup_pending_inner_world()
            self.assertIn("思考超时了，我暂时没能回答这个问题",
                          c2._spoke[0]["content"], "关闭开关须回退原兜底")
            self.assertNotIn("partial_rounds", c2._spoke[0])


# ======================================================================
# 2) T3.1 RequestDeduplicator 边界
# ======================================================================
class TestRequestDedupBoundary(unittest.TestCase):
    """去重器：三态语义、超时接管、等待者上限、并发唯一性、生命周期。"""

    def test_10_first_claim_then_duplicate_after_complete(self):
        """首次 claimed；complete 后复用窗口内 duplicate。"""
        d = RequestDeduplicator(timeout=5.0, max_wait=5, reuse_ttl=5.0)
        self.assertEqual(d.try_claim("k1"), CLAIMED)
        self.assertTrue(d.complete("k1", "答案"))
        self.assertEqual(d.try_claim("k1"), DUPLICATE)
        self.assertEqual(d.get_result("k1"), "答案")

    def test_11_timeout_takeover(self):
        """owner 超时未 complete → 后续调用接管成为新 owner。"""
        d = RequestDeduplicator(timeout=0.15, max_wait=5, reuse_ttl=0.05)
        self.assertEqual(d.try_claim("k2"), CLAIMED)
        time.sleep(0.25)
        self.assertEqual(d.try_claim("k2"), CLAIMED, "超时应被接管")
        self.assertGreaterEqual(d.stats()["takeover"], 1)

    def test_12_waiters_limit_returns_waiting(self):
        """等待者达到上限后，新调用返回 waiting（不阻塞、防雪崩）。"""
        d = RequestDeduplicator(timeout=5.0, max_wait=1, reuse_ttl=5.0)
        self.assertEqual(d.try_claim("k3"), CLAIMED)
        _res = {}

        def _waiter():
            _res["r"] = d.try_claim("k3")

        t = threading.Thread(target=_waiter, daemon=True)
        t.start()
        time.sleep(0.1)                      # 让线程进入等待（waiters == 1）
        self.assertEqual(d.try_claim("k3"), WAITING, "上限已满应返回 waiting")
        d.complete("k3", "done")
        t.join(timeout=3)
        self.assertEqual(_res.get("r"), DUPLICATE, "等待者醒来应复用结果")

    def test_13_cancel_wakes_waiter_to_take_over(self):
        """owner cancel → 等待者立即接管（不白等到超时）。"""
        d = RequestDeduplicator(timeout=5.0, max_wait=5, reuse_ttl=5.0)
        self.assertEqual(d.try_claim("k4"), CLAIMED)
        _res = {}

        def _waiter():
            _res["r"] = d.try_claim("k4")

        t = threading.Thread(target=_waiter, daemon=True)
        t.start()
        time.sleep(0.1)
        self.assertTrue(d.cancel("k4"))
        t.join(timeout=2)
        self.assertEqual(_res.get("r"), CLAIMED, "cancel 后等待者应接管")
        self.assertFalse(t.is_alive(), "不应等到 timeout")

    def test_14_concurrent_claim_only_one_owner(self):
        """10 线程并发认领同一 key → 恰好 1 个成为 owner，其余全部复用结果。"""
        d = RequestDeduplicator(timeout=3.0, max_wait=20, reuse_ttl=5.0)
        _barrier = threading.Barrier(10)
        _results = []
        _lock = threading.Lock()

        def _worker():
            _barrier.wait()
            _r = d.try_claim("k5")
            with _lock:
                _results.append(_r)

        _threads = [threading.Thread(target=_worker, daemon=True) for _ in range(10)]
        for _t in _threads:
            _t.start()
        time.sleep(0.35)
        d.complete("k5", "唯一答案")
        for _t in _threads:
            _t.join(timeout=5)
        self.assertEqual(_results.count(CLAIMED), 1, "只允许一个 owner：{!r}".format(_results))
        self.assertEqual(_results.count(DUPLICATE), 9)

    def test_15_complete_is_idempotent_and_safe(self):
        """重复 complete / 不存在的 key：返回 False 且不抛异常（锁不泄漏）。"""
        d = RequestDeduplicator(timeout=5.0)
        self.assertEqual(d.try_claim("k6"), CLAIMED)
        self.assertTrue(d.complete("k6", 1))
        self.assertFalse(d.complete("k6", 2), "已完成的条目不应被二次覆盖")
        self.assertEqual(d.get_result("k6"), 1, "结果不得被第二次 complete 改写")
        self.assertFalse(d.complete("不存在", 1))
        self.assertFalse(d.cancel("不存在"))

    def test_16_empty_key_degrades_to_no_dedup(self):
        """空 key：保守降级为「直接执行」，不做去重（绝不改变行为）。"""
        d = RequestDeduplicator(timeout=5.0)
        self.assertEqual(d.try_claim(""), CLAIMED)
        self.assertEqual(d.try_claim(""), CLAIMED, "空 key 每次都放行")
        self.assertFalse(d.complete("", 1))
        self.assertIsNone(d.get_result(""))

    def test_17_reset_and_shutdown_lifecycle(self):
        """reset 清空状态；shutdown 把在途标记为放弃并唤醒等待者。"""
        d = RequestDeduplicator(timeout=5.0)
        d.try_claim("k7")
        d.complete("k7", "x")
        self.assertEqual(d.stats()["cached"], 1)
        d.reset()
        self.assertEqual(d.stats()["cached"], 0, "reset 应清空条目")
        self.assertIsNone(d.get_result("k7"))

        d.try_claim("k8")
        d.shutdown()
        self.assertEqual(d.get_state("k8")["status"], "abandoned")
        self.assertEqual(d.try_claim("k8"), CLAIMED, "shutdown 后可被重新认领")

    def test_18_stats_and_state_fields(self):
        """诊断接口字段完备（供监控/自省消费）。"""
        d = RequestDeduplicator(timeout=7.0, max_wait=3, reuse_ttl=2.0)
        d.try_claim("k9")
        _st = d.stats()
        for _f in ("claimed", "duplicate", "waiting", "takeover", "completed",
                   "cancelled", "expired", "inflight", "cached", "timeout", "max_wait"):
            self.assertIn(_f, _st, "stats 缺字段 {}".format(_f))
        self.assertEqual(_st["inflight"], 1)
        self.assertEqual(_st["timeout"], 7.0)
        _gs = d.get_state("k9")
        self.assertEqual(_gs["status"], "processing")
        self.assertIn("elapsed", _gs)
        self.assertIsNone(d.get_state("不存在"))

    def test_19_different_keys_are_independent(self):
        """不同 request_id 互不影响（用户维度隔离的基础保证）。"""
        d = RequestDeduplicator(timeout=5.0)
        _k_a = "userA::问题X"
        _k_b = "userB::问题X"
        self.assertEqual(d.try_claim(_k_a), CLAIMED)
        self.assertEqual(d.try_claim(_k_b), CLAIMED, "不同用户同提问不得互相合并")
        self.assertTrue(d.complete(_k_a, "A的答案"))
        self.assertEqual(d.get_result(_k_a), "A的答案")
        self.assertIsNone(d.get_result(_k_b), "B 的结果不应被 A 覆盖")

    def test_20_reuse_window_expires(self):
        """结果复用窗口过期后，同 key 应重新成为 owner。"""
        d = RequestDeduplicator(timeout=5.0, max_wait=5, reuse_ttl=0.05)
        d.try_claim("k10")
        d.complete("k10", "old")
        time.sleep(0.1)
        self.assertEqual(d.try_claim("k10"), CLAIMED, "TTL 过期应重新执行")


# ======================================================================
# 3) T3.2 PulseStomach JSON 解析边界
# ======================================================================
class TestJsonExtractBoundary(unittest.TestCase):
    """`_balanced_json_extract` 引号感知的括号平衡提取 + 失败归档。"""

    def test_30_nested_braces(self):
        """嵌套括号完整提取。"""
        _s = '废话前缀 {"a": {"b": "c"}} 后缀'
        self.assertEqual(PulseStomach._balanced_json_extract(_s), '{"a": {"b": "c"}}')

    def test_31_braces_inside_string(self):
        """字符串内的括号不得被计入深度。"""
        _s = '{"desc": "function() { return 1; }"}'
        self.assertEqual(PulseStomach._balanced_json_extract(_s), _s)

    def test_32_escaped_quotes(self):
        """转义引号不得提前结束字符串状态。"""
        _s = '{"key": "value\\"with\\"quotes"}'
        self.assertEqual(PulseStomach._balanced_json_extract(_s), _s)

    def test_33_truncated_json_returns_empty(self):
        """截断（括号不配对）→ 返回空串，交由上游继续尝试其它策略。"""
        self.assertEqual(PulseStomach._balanced_json_extract('{"a": 1, "b":'), "")
        self.assertEqual(PulseStomach._balanced_json_extract("no brace at all"), "")

    def test_34_trailing_comma_still_extracted(self):
        """多余逗号不影响「提取」（修复由 JsonRepair 负责，提取只做配对）。"""
        _s = '{"a": 1, "b": 2,}'
        self.assertEqual(PulseStomach._balanced_json_extract(_s), _s)

    def test_35_empty_and_none_input(self):
        """空串 / None / 纯空白：返回空串且不抛异常。"""
        self.assertEqual(PulseStomach._balanced_json_extract(""), "")
        self.assertEqual(PulseStomach._balanced_json_extract(None), "")
        self.assertEqual(PulseStomach._balanced_json_extract("   \n\t "), "")

    def test_36_large_input_performance(self):
        """10KB 输入的提取耗时 < 0.5s（线性扫描，无回溯）。"""
        _big = '{"data": "' + ("x" * 10240) + '"}'
        _t0 = time.time()
        _out = PulseStomach._balanced_json_extract(_big)
        _cost = time.time() - _t0
        self.assertEqual(_out, _big)
        self.assertLess(_cost, 0.5, "10KB 提取耗时 {:.3f}s 过慢".format(_cost))

    def test_37_report_json_failure_dumps_sample(self):
        """开关开启时归档失败样本（写入隔离目录），关闭时不写。"""
        shutil.rmtree(_JSON_TMP_DIR, ignore_errors=True)
        _st = PulseStomach.__new__(PulseStomach)
        _st._log = lambda *a, **k: None
        with _Switch(ENABLE_STOMACH_JSON_FAILURE_DUMP=True,
                     STOMACH_JSON_FAILURE_DUMP_DIR="tmp/_m27_jsonfail_test",
                     STOMACH_JSON_FAILURE_DUMP_MAX=50):
            _st._report_json_failure('{"坏样本": 1,', ValueError("Expecting ','"), "单测")
        self.assertTrue(os.path.isdir(_JSON_TMP_DIR), "应创建归档目录")
        _files = os.listdir(_JSON_TMP_DIR)
        self.assertEqual(len(_files), 1, "应写入 1 个样本：{!r}".format(_files))
        _body = open(os.path.join(_JSON_TMP_DIR, _files[0]), encoding="utf-8").read()
        self.assertIn("坏样本", _body)

        with _Switch(ENABLE_STOMACH_JSON_FAILURE_DUMP=False):
            _st._report_json_failure('{"另一个": 2,', None, "单测")
        self.assertEqual(len(os.listdir(_JSON_TMP_DIR)), 1, "关闭开关不应再写文件")
        shutil.rmtree(_JSON_TMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
