# -*- coding: utf-8 -*-
"""169批 C2（T-路径归一化-1）门控测试。

锁定不变式：**同一逻辑路径的不同分隔符写法 → 归一后同一 key**。

- normalize_path：反斜杠/正斜杠/双反斜杠三种写法归一到同一串；去尾斜杠；UNC 保留前导双斜杠。
- normalize_relpath：safe_relpath + 归一（取代各调用点手搓的 replace）。
- 接线不变式：被统一的文件中不再出现「safe_relpath(...) 之后手工 replace 分隔符」形态。
"""
import io
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.data.path_utils import normalize_path, normalize_relpath, safe_relpath

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: C2 已统一接线的文件（与交付报告一致）
_WIRED = [
    "tools/dead_code_scan.py",
    "tools/audit_utils.py",
    "tools/batch_backup.py",
    "tools/serp_quality_report.py",
    "nucleus/logger.py",
]


class TestNormalizePath(unittest.TestCase):
    """① 分隔符归一：不同写法 → 同一 key。"""

    def test_01_separator_variants_same_key(self):
        _variants = [
            "nucleus/data/x.py",
            "nucleus\\data\\x.py",
            "nucleus\\\\data\\\\x.py",     # 双反斜杠（JSON 转义后的形态）
        ]
        _keys = {normalize_path(v) for v in _variants}
        self.assertEqual(len(_keys), 1, "不同分隔符应归一到同一 key: %s" % _keys)
        self.assertEqual(_keys.pop(), "nucleus/data/x.py")

    def test_02_mixed_separators_same_key(self):
        self.assertEqual(normalize_path("a\\b/c\\d.py"), "a/b/c/d.py")
        self.assertEqual(normalize_path("a/b\\c/d.py"), "a/b/c/d.py")

    def test_03_trailing_slash_stripped(self):
        self.assertEqual(normalize_path("a/b/"), "a/b")
        self.assertEqual(normalize_path("a/b\\"), "a/b")
        self.assertEqual(normalize_path("/"), "/")      # 根路径保留

    def test_04_unc_prefix_preserved(self):
        """UNC（//server/share）保留前导双斜杠。"""
        self.assertEqual(normalize_path("//server/share/x"), "//server/share/x")
        self.assertEqual(normalize_path("\\\\server\\share\\x"),
                         "//server/share/x")

    def test_05_empty_input(self):
        self.assertEqual(normalize_path(""), "")
        self.assertEqual(normalize_path(None), "")

    def test_06_dict_key_invariant(self):
        """★验收原话：同一逻辑路径不同分隔符 -> 同一 key。"""
        _d = {}
        for _v in ("a/b.py", "a\\b.py", "a\\\\b.py"):
            _d[normalize_path(_v)] = (_d.get(normalize_path(_v), 0) + 1)
        self.assertEqual(list(_d.keys()), ["a/b.py"])
        self.assertEqual(_d["a/b.py"], 3)


class TestNormalizeRelpath(unittest.TestCase):
    """② normalize_relpath = safe_relpath + 归一。"""

    def test_10_equivalent_to_manual_replace(self):
        """与「safe_relpath 后手工 replace」旧写法完全等价（零行为变化）。"""
        _p = os.path.join(_ROOT, "nucleus", "data", "path_utils.py")
        self.assertEqual(normalize_relpath(_p, _ROOT),
                         safe_relpath(_p, _ROOT).replace("\\", "/"))

    def test_11_same_logical_path_same_key(self):
        _tail = os.path.join("nucleus", "data", "x.py")
        _variants = [
            os.path.join(_ROOT, "nucleus", "data", "x.py"),
            _ROOT + "\\nucleus\\data\\x.py",
            _ROOT + "\\\\nucleus\\\\data\\\\x.py",
        ]
        _keys = {normalize_relpath(v, _ROOT) for v in _variants}
        self.assertEqual(len(_keys), 1, "相对化后仍应同一 key: %s" % _keys)
        self.assertEqual(_keys.pop(), "nucleus/data/x.py")
        del _tail

    def test_12_cross_drive_falls_back_to_abs(self):
        """跨盘时与 safe_relpath 一致：降级为绝对路径（且已归一）。"""
        _fake = "C:/tmp/x.py"
        _exp = normalize_path(safe_relpath(_fake, _ROOT))
        self.assertEqual(normalize_relpath(_fake, _ROOT), _exp)


class TestWiringInvariant(unittest.TestCase):
    """③ 接线不变式：被统一的文件不得再出现手搓分隔符归一。"""

    def test_20_no_manual_replace_after_safe_relpath(self):
        _bad = []
        for _rel in _WIRED:
            _p = os.path.join(_ROOT, _rel.replace("/", os.sep))
            if not os.path.isfile(_p):
                continue
            _t = io.open(_p, encoding="utf-8", errors="replace").read()
            if re.search(r"safe_relpath\([^)]*\)\.replace", _t):
                _bad.append(_rel)
        self.assertEqual(_bad, [], "这些文件仍手搓归一: %s" % _bad)

    def test_21_normalize_relpath_used_in_wired(self):
        """被统一的文件确实引用了归一化入口（证明接线落地，非只加函数）。"""
        _missing = []
        for _rel in _WIRED:
            _p = os.path.join(_ROOT, _rel.replace("/", os.sep))
            if not os.path.isfile(_p):
                continue
            _t = io.open(_p, encoding="utf-8", errors="replace").read()
            if "normalize_relpath" not in _t:
                _missing.append(_rel)
        self.assertEqual(_missing, [], "这些文件未接入 normalize_relpath: %s"
                         % _missing)


if __name__ == "__main__":
    unittest.main()
