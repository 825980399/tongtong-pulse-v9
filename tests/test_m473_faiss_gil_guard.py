# -*- coding: utf-8 -*-
"""473 批补 · faiss free-threading 护栏的回归测试。

背景（实测）：`faiss._swigfaiss_avx2` 未声明 freethreading 兼容，首次 import 会把
GIL **永久**启回（PEP 703），且只在 import 期抛一条 RuntimeWarning，之后无日志。
一次 >=5000 候选的检索就足以让整个进程失去 8 线程并行（实测 5.32x -> 1.10x）。

护栏口径：GIL 仍开启 → 允许 faiss（行为与历史一致）；GIL 已关闭 → 默认绕开。
"""

import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.fast_ops import _m473_faiss_gil_guard


class TestFaissGilGuard(unittest.TestCase):
    def test_01_gil_on_allows_faiss(self):
        """GIL 开启（3.12 / 普通构建）⇒ 必须允许，历史行为零变化。"""
        with mock.patch.object(sys, "_is_gil_enabled", lambda: True, create=True):
            self.assertTrue(_m473_faiss_gil_guard(), "GIL 开启时不该拦 faiss")

    def test_02_gil_off_blocks_faiss_by_default(self):
        """GIL 关闭（3.14t）⇒ 默认拦下，改走 _topk_retrieve_cy。"""
        with mock.patch.object(sys, "_is_gil_enabled", lambda: False, create=True):
            self.assertFalse(_m473_faiss_gil_guard(), "free-threading 下默认不该走 faiss")

    def test_03_config_override_wins(self):
        """显式开关是逃生口：确需在 free-threading 下用 faiss 时必须放行。"""
        import config

        with mock.patch.object(sys, "_is_gil_enabled", lambda: False, create=True), \
                mock.patch.object(config, "ALLOW_FAISS_UNDER_FREE_THREADING", True, create=True):
            self.assertTrue(_m473_faiss_gil_guard(), "显式开关必须生效")

    def test_04_no_gil_api_assumes_enabled(self):
        """无 _is_gil_enabled 属性（3.12）⇒ 按 GIL 开启处理，不能误拦。"""
        had = hasattr(sys, "_is_gil_enabled")
        orig = getattr(sys, "_is_gil_enabled", None)
        try:
            if had:
                del sys._is_gil_enabled
            self.assertTrue(_m473_faiss_gil_guard(), "缺少 GIL 查询 API 时应保守允许")
        finally:
            if had:
                sys._is_gil_enabled = orig

    def test_05_condition_is_guarded_in_source(self):
        """★防回退：faiss 分支的条件里必须带护栏，不能被后续改动摘掉。"""
        p = os.path.join(_ROOT, "nucleus", "fast_ops.py")
        with open(p, encoding="utf-8") as f:
            src = f.read()
        idx = src.index("if (_faiss_on and _n >= _faiss_min")
        window = src[idx: idx + 220]
        self.assertIn("_m473_faiss_gil_guard()", window,
                      "faiss 分支条件丢了护栏——GIL 会被静默启回")
        self.assertIn("def _m473_faiss_gil_guard", src, "护栏函数不见了")

    def test_06_real_env_matches_gil_state(self):
        """真实环境自洽：返回值必须等于「GIL 是否开启」。"""
        gil_on = sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True
        if gil_on:
            self.assertTrue(_m473_faiss_gil_guard())
        else:
            self.assertFalse(_m473_faiss_gil_guard(),
                             "本进程 GIL 已关闭，护栏却在放行 faiss")


if __name__ == "__main__":
    unittest.main(verbosity=2)
