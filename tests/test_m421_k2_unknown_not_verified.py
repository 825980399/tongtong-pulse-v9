"""421 刀2：unknown 验证不再记 effect_verified=True。

被守护的五条：
  1) `effect="unknown"` ⇒ `effect_verified=False`（原实现无条件 True）；
  2) `verify_unjudged` / `verify_total` 计数正确，`get_stats()` 暴露判定率；
  3) 正常结论（improved/neutral/…）仍记 True —— 不误伤；
  4) `uncertain` **仍算已验证**：它是日志对比算完后的明确分类，不是"没算出来"；
  5) 判不出结果的补丁**不会无限重试**（`_M421_MAX_UNJUDGED_RETRY` 上限）。

★隔离：`ParamPatchManager()` 构造即 `_load_history()` 读 `_PATCH_HISTORY_PATH`，
  跑完还会 `_save_history()` 写同一路径 ⇒ 必须在实例化**之前**改模块常量，
  否则会读/写生产 `data/param_patch_history.json`（铁律 38 的同型事故）。
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.evolution import ParamPatchManager as ppm


class _FakeFuture:
    def __init__(self, payload):
        self._payload = payload

    def result(self, timeout=None):
        return self._payload


class _FakePool:
    """按顺序吐出预置结果；用尽后重复最后一条。"""

    def __init__(self, payloads):
        self._q = list(payloads)

    def submit(self, _fn, _patch_info, _log_file):
        _p = self._q.pop(0) if len(self._q) > 1 else self._q[0]
        return _FakeFuture(_p)


class _Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        # ★必须在实例化之前改模块常量（构造里就会 load）
        self._orig_hist = ppm._PATCH_HISTORY_PATH
        ppm._PATCH_HISTORY_PATH = os.path.join(self.tmp, 'param_patch_history.json')
        self.mgr = ppm.ParamPatchManager()
        # 铁律 38 落地：断言落盘路径确实被隔离
        self.assertTrue(ppm._PATCH_HISTORY_PATH.startswith(self.tmp),
                        '历史文件路径未隔离到 tmp')

    def tearDown(self):
        ppm._PATCH_HISTORY_PATH = self._orig_hist
        self._td.cleanup()

    def _patch(self, pid='p1', unjudged=None):
        p = {"id": pid, "param": "x", "status": "applied",
             "applied_at": time.time() - 120, "effect_verified": False}
        if unjudged is not None:
            p["effect_unjudged"] = unjudged
        self.mgr._applied_patches.append(p)
        return p

    def _run(self, effect_payload, n_patches=1):
        """用假 worker 池跑一轮独立进程验证。"""
        _fake_mod = mock.MagicMock()
        _fake_mod.get_reasoning_pool.return_value = _FakePool(
            [effect_payload] * max(1, n_patches))
        with mock.patch.dict(sys.modules,
                             {'nucleus.reasoning.ReasoningWorkerPool': _fake_mod}):
            return self.mgr.verify_effect_in_process(log_file='ignored.log')


class TestK2UnknownNotVerified(_Base):
    def test_01_unknown_is_not_verified(self):
        p = self._patch()
        self._run({"effect": "unknown"})
        self.assertIs(p["effect_verified"], False,
                      'unknown 被记成已验证 —— 这正是本刀要消灭的"没验证也算验证过"')
        self.assertEqual(p["effect_result"], "unknown")

    def test_02_counts_are_tracked(self):
        self._patch()
        self._run({"effect": "unknown"})
        st = self.mgr.get_stats()
        self.assertEqual(st["verify_total"], 1)
        self.assertEqual(st["verify_unjudged"], 1)
        self.assertEqual(st["verify_judged_rate"], 0.0)

    def test_03_normal_effect_still_verified(self):
        p = self._patch()
        self._run({"effect": "improved", "recommendation": "keep"})
        self.assertIs(p["effect_verified"], True)
        st = self.mgr.get_stats()
        self.assertEqual(st["verify_total"], 1)
        self.assertEqual(st["verify_unjudged"], 0)
        self.assertEqual(st["verify_judged_rate"], 1.0)

    def test_04_stats_rate_mixed(self):
        # ★一轮只挂一个待验证补丁：`max_patches=5` 会把队列里合格的**全部**提交，
        #   两个一起挂会在一轮内被同一条假结果处理掉，测不到"混合"。
        self._patch('a')
        self._run({"effect": "improved"})
        self._patch('b')
        self._run({"effect": "unknown"})
        st = self.mgr.get_stats()
        self.assertEqual(st["verify_total"], 2)
        self.assertEqual(st["verify_unjudged"], 1)
        self.assertEqual(st["verify_judged_rate"], 0.5)

    def test_05_uncertain_is_still_verified(self):
        """`uncertain` 是算完了的明确分类（error_delta==0 且 warning_delta>0）。"""
        p = self._patch()
        self._run({"effect": "uncertain", "recommendation": "observe"})
        self.assertIs(p["effect_verified"], True,
                      'uncertain 不该被并入"未判定" —— 它是有结论的')
        self.assertEqual(self.mgr.get_stats()["verify_unjudged"], 0)

    def test_06_missing_effect_key_defaults_unknown(self):
        p = self._patch()
        self._run({"recommendation": "keep"})
        self.assertIs(p["effect_verified"], False)

    def test_07_none_and_empty_effect_are_unjudged(self):
        p = self._patch()
        self._run({"effect": None})
        self.assertIs(p["effect_verified"], False)
        p2 = self._patch('p2')
        self.mgr._applied_patches[-1]["effect_verified"] = False
        self._run({"effect": ""})
        self.assertIs(p2["effect_verified"], False)

    def test_08_unjudged_counter_per_patch(self):
        p = self._patch()
        self._run({"effect": "unknown"})
        self._run({"effect": "unknown"})
        self.assertEqual(p.get("effect_unjudged"), 2)


class TestK2RetryCap(_Base):
    def test_09_retry_cap_stops_resubmission(self):
        """判不出结果的补丁试满上限后不再进入待验证队列。"""
        self._patch('exhausted', unjudged=ppm._M421_MAX_UNJUDGED_RETRY)
        r = self._run({"effect": "unknown"})
        self.assertEqual(r.get("submitted", 0), 0,
                         '已达上限的补丁仍被重新提交 ⇒ 会打满 worker 池')
        self.assertIn("没有需要验证", r.get("note", ""))

    def test_10_below_cap_still_submitted(self):
        self._patch('below', unjudged=ppm._M421_MAX_UNJUDGED_RETRY - 1)
        r = self._run({"effect": "unknown"})
        self.assertEqual(r.get("submitted"), 1)


if __name__ == '__main__':
    unittest.main()
