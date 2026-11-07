# -*- coding: utf-8 -*-
"""473刀18 门控单测：周期 apply 的 `approved=0` 告警三修。

现场（2026-11-07 12:36 ~ 13:01，曈曈 pid=29720）：
  [422刀3][周期apply] approved=0 ⇒ 本轮不应用：pending=1 approved=0
  最近一条 approved=从未（-1.0 小时前）｜建议：检查审批桥是否放行…
    · 每 5 分钟一条 WARNING，一天 288 条
    · 「从未」与「-1.0 小时前」自相矛盾
    · 那 1 条是 `verified`（trust=50<60 + 核心文件）⇒ 在等人批，不是桥断了

隔离约束（沿用 test_m422_k3 的桩法）：
  · `_m422_count_approved_pending` 一律打桩，不读生产补丁池；
  · `_apply_pending_patches_and_restart` / `_spawn_self_restart` 一律打桩（绝不 Popen）；
  · 时间显式传 `now=`，不 sleep。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as M
from nucleus import alert_dedup as AD

_DAY0 = 1793600000.0
_KEY = "m422_apply_no_approved"

with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "main.py"), "r", encoding="utf-8") as _fh:
    _SRC = _fh.read()
_LINES = _SRC.split("\n")


class _FakeFramework:
    def __init__(self, forbid_until=0.0):
        self._self_modify_forbidden_until = forbid_until
        self.stopped = 0

    def stop(self):
        self.stopped += 1


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved = (dict(M._M422_APPLY_TICK), dict(M._M422_APPLY_TICK.get("skipped") or {}))
        M._M422_APPLY_TICK.clear()
        M._M422_APPLY_TICK.update({
            "boot_ts": 0.0, "last_tick_ts": 0.0, "ticks": 0, "fired": 0,
            "skipped": {}, "last_reason": "", "last_count": {}, "last_result": None,
        })
        self._orig = (M._m422_count_approved_pending,
                      M._apply_pending_patches_and_restart,
                      M._spawn_self_restart)
        M._apply_pending_patches_and_restart = lambda fw, **kw: False
        M._spawn_self_restart = lambda reason="self_restart": True
        AD.reset_all()

    def tearDown(self):
        (M._m422_count_approved_pending, M._apply_pending_patches_and_restart,
         M._spawn_self_restart) = self._orig
        M._M422_APPLY_TICK.clear()
        M._M422_APPLY_TICK.update(self._saved[0])
        M._M422_APPLY_TICK["skipped"] = self._saved[1]
        AD.reset_all()

    def _pass_guard(self):
        """把 boot_ts 拨到足够早，让 600s 运行窗与 300s 节流都放行。"""
        _fw = _FakeFramework()
        M._m422_periodic_apply_tick(_fw, now=_DAY0)
        return _fw


class TestA_Switch(_Base):
    """灰度开关：None 与 0 必须是两个意思（缺省开 / 显式关）。"""

    def test_01_缺省为开(self):
        self.assertIs(M._m473_apply_thr_on(), True)

    def test_02_None按缺省开(self):
        with mock.patch("config.ENABLE_M473_APPLY_NOAPPROVED_THROTTLE", None, create=True):
            self.assertIs(M._m473_apply_thr_on(), True)

    def test_03_False显式关(self):
        with mock.patch("config.ENABLE_M473_APPLY_NOAPPROVED_THROTTLE", False, create=True):
            self.assertIs(M._m473_apply_thr_on(), False)

    def test_04_0不被吞成True(self):
        """★核心：`getattr(cfg, X, True) or True` 会把 0 吞掉；本实现必须不吞。"""
        with mock.patch("config.ENABLE_M473_APPLY_NOAPPROVED_THROTTLE", 0, create=True):
            self.assertIs(M._m473_apply_thr_on(), False)


class TestB_ThrottleCurve(_Base):
    """连击曲线：pending>0 时连续 3 次才 WARNING，前两次 DEBUG。"""

    def test_05_前两次debug第三次warning(self):
        _fw = self._pass_guard()
        M._m422_count_approved_pending = lambda: {
            "approved": 0, "dead": 0, "live": 0, "pending": 1}
        _warns = []
        with mock.patch.object(M._logger, "warning",
                               side_effect=lambda *a, **k: _warns.append(a)):
            for _i in range(3):
                _r = M._m422_periodic_apply_tick(_fw, now=_DAY0 + 700 + _i * 301)
                self.assertEqual(_r["status"], "idle")
        self.assertEqual(len(_warns), 1, f"三次调用应只 WARNING 一次，实得 {len(_warns)}")
        _msg = _warns[0][0] % tuple(_warns[0][1:])
        self.assertIn("连续第 3 次", _msg)

    def test_06_过阈值后未到窗口不重复(self):
        _fw = self._pass_guard()
        M._m422_count_approved_pending = lambda: {
            "approved": 0, "dead": 0, "live": 0, "pending": 1}
        _warns = []
        with mock.patch.object(M._logger, "warning",
                               side_effect=lambda *a, **k: _warns.append(a)):
            for _i in range(3):                       # 第 3 次触发 WARNING
                M._m422_periodic_apply_tick(_fw, now=_DAY0 + 700 + _i * 301)
            self.assertEqual(len(_warns), 1)
            M._m422_periodic_apply_tick(_fw, now=_DAY0 + 700 + 3 * 301)   # 第 4 次
            self.assertEqual(len(_warns), 1, "刚报过，1800s 内不该再来一条")

    def test_07_pending为0时不warning且归零(self):
        """pending==0 ⇒ 走 DEBUG（444刀4 既有分级不变）+ 连击归零。"""
        _fw = self._pass_guard()
        M._m422_count_approved_pending = lambda: {
            "approved": 0, "dead": 0, "live": 0, "pending": 0}
        AD.bump_streak(_KEY)          # 先埋一个非零计数
        AD.bump_streak(_KEY)
        self.assertEqual(AD.streak_of(_KEY), 2)
        _warns = []
        with mock.patch.object(M._logger, "warning",
                               side_effect=lambda *a, **k: _warns.append(a)):
            _r = M._m422_periodic_apply_tick(_fw, now=_DAY0 + 3600)
        self.assertEqual(_r["status"], "idle")
        self.assertEqual(len(_warns), 0, "pending==0 不是告警")
        self.assertEqual(AD.streak_of(_KEY), 0, "pending 归零时必须把连击归零")

    def test_08_灰度关闭时次次warning(self):
        """灰度关 ⇒ 逐字回滚 442/444 旧行为（pending>0 每次都 WARNING）。"""
        _fw = self._pass_guard()
        M._m422_count_approved_pending = lambda: {
            "approved": 0, "dead": 0, "live": 0, "pending": 1}
        _warns = []
        with mock.patch("config.ENABLE_M473_APPLY_NOAPPROVED_THROTTLE", False, create=True), \
                mock.patch.object(M._logger, "warning",
                                  side_effect=lambda *a, **k: _warns.append(a)):
            for _i in range(3):
                M._m422_periodic_apply_tick(_fw, now=_DAY0 + 700 + _i * 301)
        self.assertEqual(len(_warns), 3, "关掉灰度就该回到每次都报")


class TestC_MessageFormat(_Base):
    """文案：从未时不能出现小时数（原「-1.0 小时前」）。"""

    def test_09_从未时不显示小时数(self):
        _fw = self._pass_guard()
        M._m422_count_approved_pending = lambda: {
            "approved": 0, "dead": 0, "live": 0, "pending": 1}
        _msgs = []
        with mock.patch.object(M._logger, "warning",
                               side_effect=lambda *a, **k: _msgs.append(a)), \
                mock.patch.object(M._logger, "debug",
                                  side_effect=lambda *a, **k: _msgs.append(a)):
            for _i in range(3):
                M._m422_periodic_apply_tick(_fw, now=_DAY0 + 700 + _i * 301)
        self.assertTrue(_msgs)
        _msg = _msgs[-1][0] % tuple(_msgs[-1][1:])
        self.assertIn("从未", _msg)
        self.assertNotIn("-1.0", _msg, "「从未」不该带 -1.0 小时")
        self.assertNotIn("-1.0 小时前", _msg)

    def test_10_有批准记录时正常显示距今(self):
        _fw = self._pass_guard()
        _now = _DAY0 + 700
        M._m422_count_approved_pending = lambda: {
            "approved": 0, "dead": 0, "live": 0, "pending": 1,
            "last_approved_at": _now - 2.5 * 3600}
        _msgs = []
        with mock.patch.object(M._logger, "warning",
                               side_effect=lambda *a, **k: _msgs.append(a)), \
                mock.patch.object(M._logger, "debug",
                                  side_effect=lambda *a, **k: _msgs.append(a)):
            for _i in range(3):
                M._m422_periodic_apply_tick(_fw, now=_now + _i * 301)
        _msg = _msgs[-1][0] % tuple(_msgs[-1][1:])
        self.assertIn("小时前", _msg)
        self.assertRegex(_msg, r"\d+\.\d 小时前")

    def test_11_文案指向人工批准而非断定桥断(self):
        """原文案一句「检查审批桥是否放行」会把人在等人批的场景引错方向。"""
        self.assertIn("需人工批准", _SRC)
        self.assertIn("verified/pending 不会自动转 approved", _SRC)


class TestD_Wiring(unittest.TestCase):
    """AST/源码接线。"""

    def test_12_负一小时字面量已消失(self):
        self.assertNotIn("else -1.0", _SRC)
        self.assertIn("if _last_appr else None", _SRC)

    def test_13_限流键与参数钉死(self):
        self.assertIn('"m422_apply_no_approved"', _SRC)
        self.assertIn("threshold=3", _SRC)
        self.assertIn("renotify_sec=1800.0", _SRC)

    def test_14_连续次数进了日志(self):
        self.assertIn("连续第 %d 次", _SRC)

    def test_15_异常时照常告警不静默(self):
        """★铁律40 的反向：限流判据自己坏了，绝不能顺势把日志吞掉。"""
        _i = _SRC.index("473刀18 限流")
        _blk = _SRC[_i:_i + 200]
        self.assertIn("_emit422, _streak422 = True, 0", _blk)

    def test_16_两个except都有silent_exc(self):
        for _tag in ("473刀18 限流", "473刀18 reset"):
            _i = _SRC.index(_tag)
            _blk = _SRC[max(0, _i - 260):_i + 60]
            self.assertIn("silent_exc", _blk, f"{_tag} 的 except 缺 silent_exc")

    def test_17_日志调用用变量而非双分支复制(self):
        """★不能写成 warning(...) / debug(...) 两份 —— 会漂移。"""
        _i = _SRC.index("_log444(\n")
        self.assertIn("_log444", _SRC[_i:_i + 600])
        self.assertEqual(_SRC.count("[422刀3][周期apply] approved=0 ⇒ 本轮不应用"), 1)

    def test_18_config有开关(self):
        import config as _c
        self.assertIs(getattr(_c, "ENABLE_M473_APPLY_NOAPPROVED_THROTTLE", None), True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
