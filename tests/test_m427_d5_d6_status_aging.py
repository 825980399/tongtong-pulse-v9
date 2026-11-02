# -*- coding: utf-8 -*-
"""427 刀5/刀6：状态-真值一致性守卫 + 老化放行 dry-run。

刀5 的三条边界：
  1) `status=runtime_verified` + 嵌套 `verified is False` ⇒ 收敛为 needs_reverify；
  2) `verified is True` / `is None` / 无嵌套字段**一律不动**（None 是合法中间态）；
  3) 收敛不泄漏回调用方内存对象（落盘副本才改）。

刀6 的两条边界：
  4) `pending_aging_preview()` 只读（不改 status、不写盘）；
  5) 开关打开且判据命中 0 条时，`_m94_apply_pending_aging` 不得放行任何补丁。
"""
import os
import shutil
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from nucleus.reasoning import PatchManager as PM


class TestStatusGuard(unittest.TestCase):
    def _p(self, status, verified, pid="x"):
        return {"id": pid, "status": status,
                "runtime_verify_result": ({"verified": verified}
                                          if verified != "<missing>" else {})}

    def test_01_contradiction_is_converged(self):
        """status 说已验证、真值说失败 ⇒ needs_reverify + 留痕。"""
        p = self._p("runtime_verified", False)
        out = PM._m427_converge_status([p])
        self.assertEqual(out[0]["status"], "needs_reverify")
        fix = out[0]["m427_status_fix"]
        self.assertEqual(fix["from"], "runtime_verified")
        self.assertEqual(fix["to"], "needs_reverify")
        self.assertEqual(fix["batch"], "427")

    def test_02_true_and_none_are_untouched(self):
        """True / None / 无字段 一律不动（把 None 当矛盾会误伤 423 恢复条目）。"""
        cases = [("runtime_verified", True), ("runtime_verified", None),
                 ("runtime_verified", "<missing>")]
        for _st, _v in cases:
            p = self._p(_st, _v)
            out = PM._m427_converge_status([p])
            self.assertEqual(out[0]["status"], "runtime_verified",
                             f"verified={_v!r} 不该被收敛")
            self.assertNotIn("m427_status_fix", out[0])

    def test_03_other_status_untouched(self):
        """非 runtime_verified 状态不在本守卫职责内。"""
        for _st in ("approved", "pending", "runtime_failed", "needs_reverify", ""):
            p = self._p(_st, False)
            out = PM._m427_converge_status([p])
            self.assertEqual(out[0]["status"], _st)

    def test_04_caller_object_is_not_mutated(self):
        """收敛必须发生在副本上：调用方内存里的 status 不许被改。"""
        p = self._p("runtime_verified", False)
        PM._m427_converge_status([p])
        self.assertEqual(p["status"], "runtime_verified",
                         "守卫改到了调用方对象（_save_patch_list 的调用方依赖原值）")

    def test_05_non_dict_and_empty_are_safe(self):
        """非 dict 元素、空列表、None 都不得抛。"""
        self.assertEqual(PM._m427_converge_status([]), [])
        self.assertEqual(PM._m427_converge_status(None), None)
        self.assertEqual(PM._m427_converge_status(["x", 1]), ["x", 1])


class TestAgingDryRun(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        os.makedirs(os.path.join(self.tmp, "data"), exist_ok=True)
        shutil.copytree(os.path.join("data", "patches"),
                        os.path.join(self.tmp, "data", "patches"))
        self.pm = PM.PatchManager(self.tmp)

    def tearDown(self):
        self._td.cleanup()

    def test_10_preview_is_read_only(self):
        """preview 只读：跑完 pending 文件字节不变。"""
        _pf = self.pm._pending_file
        with open(_pf, "rb") as _fh:
            _before = _fh.read()
        _prev = self.pm.pending_aging_preview()
        with open(_pf, "rb") as _fh:
            _after = _fh.read()
        self.assertEqual(_before, _after, "preview 写了盘（必须只读）")
        self.assertIn("checked", _prev)
        self.assertIn("eligible", _prev)
        self.assertGreaterEqual(_prev["checked"], 1)

    def test_11_enabled_but_zero_eligible_releases_nothing(self):
        """开关打开、判据命中 0 条 ⇒ 一条都不放行，也不落盘。

        ★这条是「不许为了好看放宽判据」的守门员：本批实测 pending 11 条
          全部卡在 source!=local_rule 或 baseline_errors==0。
        """
        _saved = getattr(config, "ENABLE_PENDING_QUEUE_AGING", None)
        config.ENABLE_PENDING_QUEUE_AGING = True
        try:
            _pending = self.pm._load_patch_list(self.pm._pending_file)
            _n_approved_before = len([p for p in _pending
                                      if isinstance(p, dict) and p.get("status") == "approved"])
            _out = self.pm._m94_apply_pending_aging(_pending)
            _after = self.pm._load_patch_list(self.pm._pending_file)
            _n_approved_after = len([p for p in _after
                                     if isinstance(p, dict) and p.get("status") == "approved"])
        finally:
            # ★427：原来用 try/except AttributeError 兜「属性本就不存在」，
            #   改成 hasattr 显式判断 —— 去掉一个裸 except，语义更直白。
            if _saved is None and hasattr(config, "ENABLE_PENDING_QUEUE_AGING"):
                del config.ENABLE_PENDING_QUEUE_AGING
            else:
                config.ENABLE_PENDING_QUEUE_AGING = _saved
        self.assertTrue(_out["enabled"], "开关打开后 enabled 必须为 True")
        self.assertEqual(_out["eligible"], 0,
                         "本批实测真实队列里合格 0 条；若变成非 0 说明判据被放宽了")
        self.assertEqual(_out["released"], [], "无可放行项却报了 released")
        self.assertEqual(_n_approved_before, _n_approved_after,
                         "无可放行项却改了 approved 计数")


if __name__ == "__main__":
    unittest.main()
