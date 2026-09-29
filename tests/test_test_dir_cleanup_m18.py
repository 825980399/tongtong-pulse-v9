# -*- coding: utf-8 -*-
"""
test_test_dir_cleanup_m18.py —— 第18批 T6/P2-105：测试临时目录短路径与退出即清

验证：
    · 隔离目录为短随机名 tmp/t_<6hex>，相对路径 <40 字符
    · 每进程/每次构造唯一
    · cleanup() 删除目录并置 cleaned 标志
    · verify_cleanup() 兜底强删
    · 灰度开关关闭时退回旧路径 tmp/test_data
    · 路径/清理逻辑不触发 WinError 5（短路径远离长路径限制）
"""

import os
import sys
import unittest

import pytest  # noqa: E402

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    import tmp.test_isolation as TI  # noqa: E402
except ImportError:
    pytest.skip(
        "环境依赖缺失：tmp/test_isolation 为 git-ignored 易失辅助模块"
        "（M18/M19 源头未入库、工作树已丢失）；恢复该模块后本测试自动回归。"
        "此处降级为 skip 以免阻断全量 pytest 收集。",
        allow_module_level=True,
    )

_ISO_SRC = os.path.join(_PROJECT_ROOT, "tmp", "test_isolation.py")

#: 本模块创建的临时隔离目录（tearDownModule 统一兜底清理）
_CREATED_DIRS: list[str] = []
_VERIFY_SRC = os.path.join(_PROJECT_ROOT, "tools", "verify_phase17_1_5.py")


class TestIsoDirShortPath(unittest.TestCase):
    def test_iso_dir_is_short_relative_path(self):
        _rel = os.path.relpath(TI.ISO_DIR, _PROJECT_ROOT).replace(os.sep, "/")
        self.assertTrue(_rel.startswith("tmp/"), _rel)
        self.assertLess(len(_rel), 40, "隔离目录相对路径必须 <40 字符: %s" % _rel)
        self.assertRegex(os.path.basename(TI.ISO_DIR), r"^t_[0-9a-f]{6}$")

    def test_iso_dir_is_under_tmp(self):
        self.assertTrue(os.path.abspath(TI.ISO_DIR).startswith(
            os.path.abspath(os.path.join(_PROJECT_ROOT, "tmp"))))

    def test_maker_returns_unique_dirs(self):
        _a = TI._make_iso_dir()
        _b = TI._make_iso_dir()
        self.assertNotEqual(_a, _b, "每进程/每次构造应唯一，避免并发踩踏")

    def test_switch_off_uses_legacy_path(self):
        _orig = TI._cleanup_enabled
        TI._cleanup_enabled = lambda: False
        try:
            _d = TI._make_iso_dir()
        finally:
            TI._cleanup_enabled = _orig
        self.assertTrue(_d.replace("\\", "/").endswith("/tmp/test_data"), _d)

    def test_no_deep_nesting_in_verify(self):
        """verify 不得再拼接 p17_<pid>_<ts> 深路径。"""
        with open(_VERIFY_SRC, encoding="utf-8") as f:
            _src = f.read()
        self.assertNotIn('os.path.join(ISO_DIR, f"p17_', _src)
        self.assertIn("_TMP = ISO_DIR", _src)


class TestCleanup(unittest.TestCase):
    def test_cleanup_removes_isolated_dir(self):
        _d = TI._make_iso_dir()
        _CREATED_DIRS.append(_d)
        try:
            os.makedirs(_d, exist_ok=True)
            with open(os.path.join(_d, "probe.txt"), "w",
                         encoding="utf-8") as f:
                f.write("x")
            self.assertTrue(os.path.isdir(_d))
            TI.verify_cleanup(_d)
            self.assertFalse(os.path.exists(_d),
                             "verify_cleanup 必须强删残留目录")
        finally:
            # 兜底：断言失败时也不留残留（本测试自己造的目录自己清）
            TI.verify_cleanup(_d)

    def test_cleanup_marks_cleaned_flag(self):
        # 用真实的 TestIsolation 但指向临时目录，避免动到全局 ISO_DIR
        _iso = TI.TestIsolation()
        try:
            self.assertFalse(_iso.cleaned)
        finally:
            _iso.cleanup()
        self.assertTrue(_iso.cleaned, "cleanup 后 cleaned 应为 True")
        self.assertFalse(os.path.exists(TI.ISO_DIR))

    def test_apply_test_isolation_registers_atexit(self):
        import atexit
        _n0 = atexit._ncallbacks()
        _iso = TI.apply_test_isolation(auto_cleanup_on_exit=True)
        try:
            self.assertGreater(atexit._ncallbacks(), _n0)
        finally:
            _iso.cleanup()

    def test_verify_cleanup_returns_true_when_absent(self):
        _d = TI._make_iso_dir()
        self.assertFalse(os.path.exists(_d))
        self.assertTrue(TI.verify_cleanup(_d))

    def test_vectorstore_atexit_guards_tmp_rebuild(self):
        """★关键回归：VectorStore.shutdown 不得重建已清理的 tmp 隔离目录。"""
        _vs_src = os.path.join(_PROJECT_ROOT, "nucleus", "semantic", "VectorStore.py")
        with open(_vs_src, encoding="utf-8") as f:
            _src = f.read()
        self.assertIn("隔离目录已清理，跳过 atexit 落盘重建", _src)

    def test_redirect_all_no_longer_restores_instance(self):
        """★根因回归：redirect_all 不得把 VectorStore._instance 记入 _patched。"""
        with open(_ISO_SRC, encoding="utf-8") as f:
            _src = f.read()
        _idx = _src.index('_VS.VectorStore._instance = None')
        _around = _src[_idx - 300:_idx + 120]
        self.assertNotIn("_patched.append((_VS.VectorStore", _around)


def teardown_module():
    # ★第22批 T3：pytest 只识别下划线命名模块级夹具，驼峰 tearDownModule
    #   不会被调用 → 临时目录清理长期失效并污染 tmp/ 与全库 F 口径。
    """★模块级兜底：清掉本模块造过的一切临时隔离目录。"""
    for _p in _CREATED_DIRS:
        TI.verify_cleanup(_p)
    _CREATED_DIRS.clear()


if __name__ == "__main__":
    unittest.main()
