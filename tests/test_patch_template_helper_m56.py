# -*- coding: utf-8 -*-
"""★主线第56批 T3/P2-390：补丁脚本模板工具门控单测（覆盖 8 个已知坑）。

不依赖框架重启；全部用临时文件，不触碰生产代码。
"""
import io
import os
import sys
import tempfile
import unittest

# tools/ 非包，直接把模块所在目录加入路径后按模块名导入。
# ★第154批 T154-10：patch_template_helper 已归档至 tools/archive/，
#   旧代码仍指向 tools/ 根目录 ⇒ 裸 import 失败并造成全量 pytest collection error。
#   改为按归档位置定位（并保留 tools/ 作为回退），使本用例恢复有效。
_TOOLS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
_ARCHIVE = os.path.join(_TOOLS, "archive")
for _p in (_ARCHIVE, _TOOLS):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
import patch_template_helper as H  # noqa: E402


def _write(path, text, eol):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace("\n", eol))


class TestDetectEol(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.mkdtemp(prefix="m56_eol_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._d, ignore_errors=True)

    def test_detect_eol_crlf(self):
        """★坑4/5：CRLF 文件正确识别。"""
        p = os.path.join(self._d, "a.py")
        _write(p, "x = 1\ny = 2\n", "\r\n")
        self.assertEqual(H.detect_eol(p), "\r\n")

    def test_detect_eol_lf(self):
        """★坑4/5：LF 文件正确识别。"""
        p = os.path.join(self._d, "b.py")
        _write(p, "x = 1\ny = 2\n", "\n")
        self.assertEqual(H.detect_eol(p), "\n")


class TestEolBytes(unittest.TestCase):
    def test_eol_bytes_ok(self):
        """★坑6：str→bytes 分离正常。"""
        self.assertEqual(H.eol_bytes("\r\n"), b"\r\n")
        self.assertEqual(H.eol_bytes("\n"), b"\n")

    def test_eol_bytes_type_error(self):
        """★坑6：非 str 入参尽早 TypeError。"""
        with self.assertRaises(TypeError):
            H.eol_bytes(123)


class TestSafeRelpath(unittest.TestCase):
    def test_cross_drive_no_raise(self):
        """★坑7：跨盘不抛 ValueError，降级为绝对路径。"""
        _r = H.safe_relpath("C:/a/b.py", "D:/root")
        self.assertEqual(_r, os.path.abspath("C:/a/b.py"))

    def test_same_drive_relative(self):
        """★坑7：同盘返回相对路径（语义不变）。"""
        _r = H.safe_relpath("D:/root/sub/x.py", "D:/root")
        self.assertIn("sub", _r)
        self.assertNotEqual(_r, "D:/root/sub/x.py")  # 应为相对形式


class TestInsertionPoint(unittest.TestCase):
    def test_skips_future_and_syspath(self):
        """★坑2/3：跳过 __future__ 与 sys.path.insert，落在首个真实 import。"""
        _src = ("from __future__ import annotations\n"
                "import os\n"
                "sys.path.insert(0, 'x')\n"
                "\n"
                "def foo(): pass\n")
        _idx = H.find_first_top_import_line(_src)
        _lines = _src.splitlines()
        self.assertEqual(_lines[_idx].strip(), "import os")

    def test_multiline_import_end(self):
        """★坑1：首个 import 为多行时，定位到结束行之后，而非开头行。"""
        _src = ("from x import (\n"
                "    a,\n"
                "    b,\n"
                ")\n"
                "def foo(): pass\n")
        _idx = H.find_first_top_import_line(_src)
        _lines = _src.splitlines()
        # 多行 import 结束于 ")" 行（下标3），插入点应为其之后（下标4=def foo）
        self.assertEqual(_idx, 4)
        self.assertTrue(_lines[_idx].startswith("def foo"))


class TestApplyPatches(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.mkdtemp(prefix="m56_apply_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._d, ignore_errors=True)

    def _crlf_file(self, name="m.py"):
        p = os.path.join(self._d, name)
        _write(p, "a = 1\nb = 2\n", "\r\n")
        return p

    def test_unique_anchor_fail(self):
        """★唯一锚点：文件内出现多次的锚点 → 整体不写盘并抛 AssertionError。"""
        p = os.path.join(self._d, "dup.py")
        _write(p, "x = 1\nx = 1\n", "\r\n")
        with self.assertRaises(AssertionError):
            H.apply_patches(p, [("x = 1\n", "x = 1\n")])

    def test_idempotent_skip(self):
        """★幂等：二次应用整体跳过（不重复写）。"""
        p = self._crlf_file()
        _r1 = H.apply_patches(p, [("b = 2\n", "b = 3\n")],
                               idempotency_tag="# M56_T")
        _r2 = H.apply_patches(p, [("b = 2\n", "b = 3\n")],
                               idempotency_tag="# M56_T")
        self.assertTrue(_r1["applied"])
        self.assertTrue(_r2["skipped"])

    def test_dry_run_no_write(self):
        """★dry_run 不写盘，文件内容不变。"""
        p = self._crlf_file()
        _before = io.open(p, "r", encoding="utf-8", newline="").read()
        _r = H.apply_patches(p, [("b = 2\n", "b = 99\n")], dry_run=True)
        _after = io.open(p, "r", encoding="utf-8", newline="").read()
        self.assertTrue(_r["dry_run"])
        self.assertEqual(_before, _after)

    def test_crlf_preserved(self):
        """★坑4/5：CRLF 文件应用后仍是 CRLF（无裸 LF 引入）。"""
        p = self._crlf_file()
        H.apply_patches(p, [("b = 2\n", "b = 2\nc = 3\n")],
                        idempotency_tag="# M56_T")
        _b = open(p, "rb").read()
        self.assertEqual(_b.count(b"\r\n"), _b.count(b"\n"))


class TestAssertStructure(unittest.TestCase):
    def test_type_ok(self):
        """★坑8：类型断言通过。"""
        H.assert_structure([1, 2], list)

    def test_type_fail(self):
        """★坑8：类型不符抛 AssertionError。"""
        with self.assertRaises(AssertionError):
            H.assert_structure("x", list)

    def test_predicate_ok(self):
        """★坑8：谓词断言通过。"""
        H.assert_structure(5, lambda v: v > 0)

    def test_predicate_fail(self):
        """★坑8：谓词不满足抛 AssertionError。"""
        with self.assertRaises(AssertionError):
            H.assert_structure(0, lambda v: v > 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
