# -*- coding: utf-8 -*-
"""第422批 刀2 门控单测：同 (file, method) 连续失败 3 次 ⇒ 冷却 24h。

被测对象：`nucleus/evolution/patch_execution_dispatch.py`
  · `_m422_note_method_fail` / `_m422_note_method_ok`
  · `_m422_cooldown_reason` / `_m422_method_state` / `_m422_pair_of`
  · `_m324_blocked_reason`（新增冷却闸）

隔离约束：全部用 `tempfile.mkdtemp` 独立账本路径，**不碰生产**
`data/patches/method_cooldown.json`；时间一律显式传 `now=`，不 sleep。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.evolution import patch_execution_dispatch as ped

_T0 = 1793000000.0          # 固定基准时刻（模拟用，不取系统时间）
_DAY = 24 * 3600.0
_LIVER = "organs/body/PulseLiver.py"
_METHOD = "_compress_group"


def _patch(pid="p1", file=_LIVER, method=_METHOD):
    return {"id": pid, "file": file, "method": method}


class TestA_Threshold(unittest.TestCase):
    """三击阈值：前两次不冷、第三次冷。"""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="m422k2_a_")
        self._lp = os.path.join(self._td, "method_cooldown.json")

    def _fail(self, i, p=None):
        return ped._m422_note_method_fail(p or _patch(), reason="boom",
                                          category="verify_failed",
                                          now=_T0 + i, path=self._lp)

    def test_01_two_fails_not_cooling(self):
        r1, r2 = self._fail(1), self._fail(2)
        self.assertFalse(r1["entered_cooldown"])
        self.assertFalse(r2["entered_cooldown"])
        self.assertEqual(r2["consec_fail"], 2)
        self.assertEqual(ped._m422_cooldown_reason(_patch(), now=_T0 + 3,
                                                   path=self._lp), "")

    def test_02_third_fail_enters_cooldown(self):
        self._fail(1), self._fail(2)
        r3 = self._fail(3)
        self.assertTrue(r3["entered_cooldown"], "第 3 次必须进冷却")
        self.assertAlmostEqual(r3["cooldown_until"], _T0 + 3 + _DAY, places=1)

    def test_03_reason_is_exact_literal(self):
        """任务书点名 `reason="cooldown_24h"` —— 必须**逐字相等**，不带括号参数。"""
        for i in (1, 2, 3):
            self._fail(i)
        _why = ped._m324_blocked_reason(_patch(), ledger={}, cooldown_path=self._lp,
                                        now=_T0 + 10)
        self.assertEqual(_why, "cooldown_24h")

    def test_04_no_renew_during_cooldown(self):
        """冷却期内再记 5 次失败：一律 ignored，count/until **一点不动**（防自锁）。"""
        for i in (1, 2, 3):
            self._fail(i)
        _before = ped._m422_method_state(_patch(), now=_T0 + 10, path=self._lp)
        for i in range(4, 9):
            _r = self._fail(i)
            self.assertTrue(_r.get("ignored"), "冷却期内必须 ignored")
        _after = ped._m422_method_state(_patch(), now=_T0 + 10, path=self._lp)
        self.assertEqual(_before["consec_fail"], _after["consec_fail"])
        self.assertAlmostEqual(_before["cooldown_until"], _after["cooldown_until"],
                               places=3)


class TestB_Unlock(unittest.TestCase):
    """24h 到期自动解锁，且解锁后要重新攒满 3 次。"""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="m422k2_b_")
        self._lp = os.path.join(self._td, "method_cooldown.json")
        for i in (1, 2, 3):
            ped._m422_note_method_fail(_patch(), reason="boom", now=_T0 + i,
                                       path=self._lp)

    def test_05_auto_unlock_after_24h(self):
        _mid = ped._m422_cooldown_reason(_patch(), now=_T0 + _DAY / 2, path=self._lp)
        self.assertEqual(_mid, "cooldown_24h", "12h 时仍应在冷却中")
        _after = ped._m422_cooldown_reason(_patch(), now=_T0 + _DAY + 10, path=self._lp)
        self.assertEqual(_after, "", "24h 后必须自动解锁")
        _st = ped._m422_method_state(_patch(), now=_T0 + _DAY + 10, path=self._lp)
        self.assertEqual(_st["consec_fail"], 0, "解锁时连续计数必须归零")
        self.assertEqual(_st["cool_count"], 1)

    def test_06_one_fail_after_unlock_not_recool(self):
        """解锁后再失败 1 次**不能**立刻重新冷却（否则等于每 24h 只准试 1 次）。"""
        _r = ped._m422_note_method_fail(_patch(), reason="x", now=_T0 + _DAY + 20,
                                        path=self._lp)
        self.assertFalse(_r["entered_cooldown"])
        self.assertEqual(_r["consec_fail"], 1)
        self.assertEqual(ped._m422_cooldown_reason(_patch(), now=_T0 + _DAY + 21,
                                                   path=self._lp), "")

    def test_07_second_cooldown_after_refill(self):
        for i in (1, 2, 3):
            ped._m422_note_method_fail(_patch(), reason="x", now=_T0 + _DAY + 100 + i,
                                       path=self._lp)
        _st = ped._m422_method_state(_patch(), now=_T0 + _DAY + 200, path=self._lp)
        self.assertTrue(_st["cooling"], "重新攒满 3 次应再次冷却")
        self.assertEqual(_st["cool_count"], 2, "冷却次数应累加（可审计）")

    def test_08_ledger_survives_reload(self):
        """账本落盘可重读（抗重启）—— 换个进程读同一文件，冷却仍然生效。"""
        with open(self._lp, encoding="utf-8") as _fh:
            _raw = json.load(_fh)
        self.assertEqual(_raw["version"], "422.1")
        self.assertIn(f"{_LIVER}::{_METHOD}", _raw["methods"])


class TestC_Reset(unittest.TestCase):
    """成功一次 ⇒ 连续计数归零（"连续"二字的口径）。"""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="m422k2_c_")
        self._lp = os.path.join(self._td, "method_cooldown.json")

    def test_09_ok_resets_and_unlocks(self):
        for i in (1, 2, 3):
            ped._m422_note_method_fail(_patch(), reason="boom", now=_T0 + i,
                                       path=self._lp)
        _r = ped._m422_note_method_ok(_patch(), now=_T0 + 10, path=self._lp)
        self.assertTrue(_r.get("reset"))
        self.assertEqual(ped._m422_cooldown_reason(_patch(), now=_T0 + 11,
                                                   path=self._lp), "")
        _st = ped._m422_method_state(_patch(), now=_T0 + 11, path=self._lp)
        self.assertEqual(_st["consec_fail"], 0)
        self.assertEqual(_st["total_fail"], 3, "total_fail 是审计值，不随成功清零")

    def test_10_ok_without_record_creates_nothing(self):
        """成功是常态：没有账记录时**不建文件**（否则跑通一个方法就留一条垃圾）。"""
        _r = ped._m422_note_method_ok(_patch(file="nucleus/never_failed.py"),
                                      now=_T0, path=self._lp)
        self.assertEqual(_r.get("skipped"), "no_record")
        self.assertFalse(os.path.isfile(self._lp), "不该为成功的方法建账文件")


class TestD_KeyNorm(unittest.TestCase):
    """键必须归一，且空二元组不能拼出假方法 `::`。"""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="m422k2_d_")
        self._lp = os.path.join(self._td, "method_cooldown.json")

    def test_11_file_spellings_share_one_key(self):
        _variants = [_LIVER, _LIVER.replace("/", os.sep), "./" + _LIVER]
        _keys = {ped._m422_pair_key(ped._m422_pair_of({"file": f, "method": _METHOD}))
                 for f in _variants}
        self.assertEqual(_keys, {f"{_LIVER}::{_METHOD}"},
                         f"同一文件的三种写法必须归一到同一个键：{_variants}")

    def test_12_missing_field_never_writes(self):
        """缺 file/method ⇒ 不计数、不建账。

        ★坑：`"%s::%s" % ("", "")` 得到 `"::"`（非空 truthy），
        若键判空失效，单测里的 `{"id": "k2_fail"}` 这类补丁会写出一个
        `::` 的假方法并把无归属失败全堆上去（实测踩过一次）。
        """
        for _bad in ({"id": "x"}, {"id": "y", "file": _LIVER},
                     {"id": "z", "method": _METHOD}):
            _r = ped._m422_note_method_fail(_bad, reason="boom", now=_T0,
                                            path=self._lp)
            self.assertTrue(_r.get("ignored"), f"缺字段必须 ignored: {_bad}")
            self.assertEqual(_r.get("key"), "")
        self.assertFalse(os.path.isfile(self._lp), "缺字段不得建出账文件")


class TestE_NoBypassByNewId(unittest.TestCase):
    """本刀的存在理由：**换个 patch id 也绕不过**（旧口径按 id 計数）。"""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="m422k2_e_")
        self._lp = os.path.join(self._td, "method_cooldown.json")

    def test_13_three_ids_same_method_triggers_cooldown(self):
        """三个不同 id 各失败 1 次 ⇒ 方法级累计 3 次 ⇒ 冷却，第 4 个 id 被拦。

        同一条路径在旧口径（按 patch id）下：每个 id 各 1 次 < cap 2 ⇒ 全部可派，
        这就是"四天 44 次"的成因。本测试用**同一份输入**证明两条口径的判别差异。
        """
        _cap = ped._DISPATCH_FAIL_CAP
        self.assertLess(1, _cap, "前提：单次失败达不到 per-patch cap")
        for _i, _pid in enumerate(("patch_llm_a", "patch_llm_b", "patch_multi_c"), 1):
            _p = _patch(pid=_pid)
            # 旧口径：每个 id 只有 1 次失败 ⇒ 不拦
            self.assertEqual(ped._m324_blocked_reason(_p, ledger={},
                                                      cooldown_path=self._lp,
                                                      now=_T0 + _i), "")
            ped._m422_note_method_fail(_p, reason="boom", now=_T0 + _i, path=self._lp)
        _fourth = _patch(pid="patch_llm_d")
        self.assertEqual(ped._m324_blocked_reason(_fourth, ledger={},
                                                  cooldown_path=self._lp,
                                                  now=_T0 + 4),
                         "cooldown_24h",
                         "第 4 个**新 id** 必须被方法级冷却拦下")


class TestF_Wiring(unittest.TestCase):
    """4 处接线必须在（源码级），并验证 `_write_back` 真的调了。"""

    @classmethod
    def setUpClass(cls):
        _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "nucleus", "evolution", "patch_execution_dispatch.py")
        with open(_p, encoding="utf-8") as _fh:
            cls._src = _fh.read()

    def test_14_all_hooks_present(self):
        _n_fail = self._src.count("_m422_note_method_fail(")
        self.assertGreaterEqual(_n_fail, 3,
                                f"应为 定义1 + _write_back1 + 派单blocked1，实得 {_n_fail}")
        self.assertIn("_m422_note_method_ok(patch)", self._src)
        self.assertIn('if _m422_cooldown_reason(_p):', self._src,
                      "O3 通道必须同口径受冷却约束（否则绕道即绕过）")
        self.assertIn('"cooldown_24h": 0,', self._src, "O3 拒收桶必须可分辨")

    def test_15_write_back_calls_hooks(self):
        """`_write_back` 失败分支调 fail、成功分支调 ok（行为级，非源码字面）。"""
        import nucleus.evolution.patch_execution_dispatch as _ped
        _seen = []
        _orig_f, _orig_o = _ped._m422_note_method_fail, _ped._m422_note_method_ok
        _orig_b, _orig_r = _ped._m331_note_blocked, _ped._m326_reset_fail_ledger
        # 隔离：这两个会写生产 patch_fail_counts.json（既有单测已把 `k2_fail` 写进去过）
        _ped._m422_note_method_fail = lambda p, **k: _seen.append(("fail", p.get("id")))
        _ped._m422_note_method_ok = lambda p, **k: _seen.append(("ok", p.get("id")))
        _ped._m331_note_blocked = lambda *a, **k: 0
        _ped._m326_reset_fail_ledger = lambda *a, **k: 0
        try:
            _ped._write_back(_patch(pid="w1"), {"success": False, "error": "boom"})
            _ped._write_back(_patch(pid="w2"), {"success": True})
        finally:
            _ped._m422_note_method_fail = _orig_f
            _ped._m422_note_method_ok = _orig_o
            _ped._m331_note_blocked = _orig_b
            _ped._m326_reset_fail_ledger = _orig_r
        self.assertEqual(_seen, [("fail", "w1"), ("ok", "w2")])


if __name__ == "__main__":
    unittest.main()
