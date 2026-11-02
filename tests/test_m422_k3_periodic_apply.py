# -*- coding: utf-8 -*-
"""第422批 刀3 门控单测：main.py 周期 apply（5 分钟一拍）+ 三道防循环闸 + 重试队列。

被测对象：`main.py`
  · `_m422_periodic_apply_tick` / `_m422_apply_guard`
  · `_m422_note_apply_result` / `_m422_load_apply_retry` / `_m422_dead_patch_ids`
  · `get_m422_apply_tick_state`

隔离约束：
  · 重试队列一律传 `path=`（tempfile），**不碰生产** `data/patches/apply_retry_422.json`；
  · `_apply_pending_patches_and_restart` 一律打桩（真身会 sleep+拉新进程，测试里绝不能跑）；
  · 时间一律显式传 `now=`，不 sleep。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as M
from nucleus.reasoning import PatchManager as _pm_mod

_DAY0 = 1793600000.0


class _FakeFramework:
    def __init__(self, forbid_until=0.0):
        self._self_modify_forbidden_until = forbid_until
        self.stopped = 0

    def stop(self):
        self.stopped += 1


class _Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="m422k3_")
        self._lp = os.path.join(self._td, "apply_retry_422.json")
        self._saved = (dict(M._M422_APPLY_TICK), dict(M._M422_APPLY_TICK.get("skipped") or {}))
        M._M422_APPLY_TICK.clear()
        M._M422_APPLY_TICK.update({
            "boot_ts": 0.0, "last_tick_ts": 0.0, "ticks": 0, "fired": 0,
            "skipped": {}, "last_reason": "", "last_count": {}, "last_result": None,
        })
        self._orig = (M._m422_count_approved_pending,
                      M._apply_pending_patches_and_restart)
        M._m422_count_approved_pending = lambda: {"approved": 0, "dead": 0, "live": 0}
        M._apply_pending_patches_and_restart = lambda fw: False

    def tearDown(self):
        M._m422_count_approved_pending, M._apply_pending_patches_and_restart = self._orig
        M._M422_APPLY_TICK.clear()
        M._M422_APPLY_TICK.update(self._saved[0])
        M._M422_APPLY_TICK["skipped"] = self._saved[1]


class TestA_Throttle(_Base):
    """墙钟 300s 节流：首拍起算不算到点，5 分钟后才到点。"""

    def test_01_first_call_arms_only(self):
        _fw = _FakeFramework()
        _r = M._m422_periodic_apply_tick(_fw, now=_DAY0)
        self.assertEqual(_r["status"], "armed", "首拍只起算，不派活也不计到点")
        self.assertEqual(M._M422_APPLY_TICK["boot_ts"], _DAY0)
        self.assertEqual(M._M422_APPLY_TICK["last_tick_ts"], _DAY0)
        self.assertEqual(M._M422_APPLY_TICK["ticks"], 0, "起算不算到点")

    def test_02_not_due_within_5min(self):
        _fw = _FakeFramework()
        M._m422_periodic_apply_tick(_fw, now=_DAY0)      # 起算
        _r = M._m422_periodic_apply_tick(_fw, now=_DAY0 + 200)
        self.assertEqual(_r["status"], "not_due")
        self.assertGreater(_r["remain_sec"], 0)
        self.assertEqual(M._M422_APPLY_TICK["ticks"], 0)

    def test_03_due_after_5min(self):
        _fw = _FakeFramework()
        M._m422_periodic_apply_tick(_fw, now=_DAY0)      # 起算
        _r = M._m422_periodic_apply_tick(_fw, now=_DAY0 + 301)
        self.assertIn(_r["status"], ("idle", "blocked", "applied_no_restart"),
                      f"到点后必须真的走一次判定，实得 {_r!r}")
        self.assertEqual(M._M422_APPLY_TICK["ticks"], 1)
        M._m422_periodic_apply_tick(_fw, now=_DAY0 + 602)
        self.assertEqual(M._M422_APPLY_TICK["ticks"], 2, "再过一个 300s 必须再走一次")


class TestB_Guards(_Base):
    """三道闸：自我修改禁窗 / 重启计数桶锁 / 10 分钟运行窗。"""

    def test_04_self_modify_forbidden_blocks(self):
        _fw = _FakeFramework(forbid_until=_DAY0 + 2000)
        M._m422_periodic_apply_tick(_fw, now=_DAY0)              # 起算
        _r = M._m422_periodic_apply_tick(_fw, now=_DAY0 + 700)   # 已过 600s 运行窗
        self.assertEqual(_r["status"], "blocked")
        self.assertTrue(_r["reason"].startswith("self_modify_forbidden"), _r["reason"])

    def test_05_min_run_window_blocks(self):
        """重启后 600s 内不得再 apply —— 否则"应用→重启→再应用"成环。"""
        _fw = _FakeFramework()
        M._m422_periodic_apply_tick(_fw, now=_DAY0)
        _r = M._m422_periodic_apply_tick(_fw, now=_DAY0 + 301)
        self.assertEqual(_r["status"], "blocked")
        self.assertTrue(_r["reason"].startswith("min_run_window"), _r["reason"])
        self.assertEqual(M._M422_APPLY_TICK["fired"], 0, "被闸拦下不得计入 fired")

    def test_06_restart_bucket_locked_blocks(self):
        _orig = _pm_mod.PatchManager.memory_restart_bucket_state
        _pm_mod.PatchManager.memory_restart_bucket_state = lambda self: {
            "counter": 4, "max": 3, "locked": True, "remain_sec": 3600}
        try:
            _fw = _FakeFramework()
            M._m422_periodic_apply_tick(_fw, now=_DAY0)
            _r = M._m422_periodic_apply_tick(_fw, now=_DAY0 + 3600)
        finally:
            _pm_mod.PatchManager.memory_restart_bucket_state = _orig
        self.assertEqual(_r["status"], "blocked")
        self.assertTrue(_r["reason"].startswith("restart_locked"), _r["reason"])
        self.assertIn("counter=4/3", _r["reason"])

    def test_07_skipped_counter_records_reason_prefix(self):
        _fw = _FakeFramework(forbid_until=_DAY0 + 2000)
        M._m422_periodic_apply_tick(_fw, now=_DAY0)
        M._m422_periodic_apply_tick(_fw, now=_DAY0 + 700)
        _st = M.get_m422_apply_tick_state()
        self.assertEqual(_st["skipped"].get("self_modify_forbidden"), 1, _st["skipped"])


class TestC_FirePath(_Base):
    """放行后：无件 ⇒ idle 不烧计数；有活件 ⇒ 真调入口并 sys.exit 信号；全 dead ⇒ 拦住。"""

    def _pass_guard(self):
        _fw = _FakeFramework()
        M._m422_periodic_apply_tick(_fw, now=_DAY0)          # boot
        return _fw

    def test_08_idle_when_no_approved(self):
        _fw = self._pass_guard()
        M._m422_count_approved_pending = lambda: {"approved": 0, "dead": 0, "live": 0}
        _r = M._m422_periodic_apply_tick(_fw, now=_DAY0 + 3600)
        self.assertEqual(_r["status"], "idle")
        self.assertEqual(_fw.stopped, 0, "没件就不该 stop 框架")
        self.assertEqual(M._M422_APPLY_TICK["fired"], 0)

    def test_09_all_dead_refuses_restart(self):
        """approved 全 dead ⇒ 明确拒绝重启（重启只会空转）= 防无限重启末闸。"""
        _fw = self._pass_guard()
        M._m422_count_approved_pending = lambda: {"approved": 3, "dead": 3, "live": 0}
        _r = M._m422_periodic_apply_tick(_fw, now=_DAY0 + 3600)
        self.assertEqual(_r["status"], "blocked")
        self.assertEqual(_r["reason"], "retry_exhausted")
        self.assertEqual(_fw.stopped, 0)
        self.assertEqual(M._M422_APPLY_TICK["fired"], 0)

    def test_10_fire_when_live(self):
        _fw = self._pass_guard()
        _calls = {"n": 0}

        def _fake_apply(fw):
            _calls["n"] += 1
            return True

        M._m422_count_approved_pending = lambda: {"approved": 2, "dead": 1, "live": 1}
        M._apply_pending_patches_and_restart = _fake_apply
        _r = M._m422_periodic_apply_tick(_fw, now=_DAY0 + 3600)
        self.assertEqual(_r["status"], "applied_restarting")
        self.assertEqual(_calls["n"], 1)
        self.assertEqual(_fw.stopped, 1, "应用前必须先 stop 框架（保存快照）")
        self.assertEqual(M._M422_APPLY_TICK["fired"], 1)


class TestD_RetryQueue(_Base):
    """失败进队列、满 3 次标 dead、成功出队。"""

    def test_11_three_fails_marks_dead(self):
        for _i in (1, 2, 3):
            _n = M._m422_note_apply_result(
                {"details": [{"id": "p1", "file": "a.py", "status": "failed",
                              "reason": "verify_failed"}]},
                path=self._lp, now=_DAY0 + _i)
            self.assertEqual(_n["dead"], 1 if _i == 3 else 0, f"第 {_i} 次")
        _d = M._m422_load_apply_retry(self._lp)
        self.assertTrue(_d["patches"]["p1"]["dead"])
        self.assertEqual(_d["patches"]["p1"]["fails"], 3)
        self.assertIn("p1", M._m422_dead_patch_ids(self._lp))

    def test_12_success_pops_record(self):
        M._m422_note_apply_result(
            {"details": [{"id": "p2", "file": "b.py", "status": "failed"}]},
            path=self._lp, now=_DAY0)
        self.assertIn("p2", M._m422_load_apply_retry(self._lp)["patches"])
        M._m422_note_apply_result(
            {"details": [{"id": "p2", "file": "b.py", "status": "applied"}]},
            path=self._lp, now=_DAY0 + 1)
        self.assertNotIn("p2", M._m422_load_apply_retry(self._lp)["patches"],
                         "成功一次必须出队（'最多重试 3 次'按连续失败算）")

    def test_13_no_detail_no_file(self):
        """空结果 / 缺 id ⇒ 不建文件（不让一次正常 apply 留垃圾）。"""
        M._m422_note_apply_result(None, path=self._lp)
        M._m422_note_apply_result({"details": [{"file": "c.py"}]}, path=self._lp)
        self.assertFalse(os.path.isfile(self._lp))


class TestE_Wiring(unittest.TestCase):
    """接线必须在（源码级）：结果回挂 + 主循环节拍调用。"""

    @classmethod
    def setUpClass(cls):
        _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "main.py")
        with open(_p, encoding="utf-8") as _fh:
            cls._src = _fh.read()

    def test_14_result_stashed_in_apply_entry(self):
        self.assertIn('_M422_APPLY_TICK["last_result"] = _patch_result', self._src)

    def test_15_main_loop_calls_tick(self):
        self.assertIn("_m422_ar = _m422_periodic_apply_tick(framework)", self._src)
        self.assertIn('if _m422_ar.get("status") == "applied_restarting":', self._src)
        _i = self._src.index("_m422_ar = _m422_periodic_apply_tick(framework)")
        _tail = self._src[_i:_i + 300]
        self.assertIn("sys.exit(0)", _tail, "重启信号必须紧跟 sys.exit(0)")

    def test_16_constants_match_taskbook(self):
        self.assertEqual(M._M422_APPLY_INTERVAL_SEC, 300.0, "任务书：5 分钟一次")
        self.assertEqual(M._M422_MIN_RUN_WINDOW_SEC, 600.0, "任务书：至少留 10 分钟运行窗口")
        self.assertEqual(M._M422_APPLY_RETRY_MAX, 3, "任务书：最多重试 3 次")


if __name__ == "__main__":
    unittest.main()
