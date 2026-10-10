# -*- coding: utf-8 -*-
"""第55批 T3 门控测试：path_utils 全库接入（P2-320 跨盘安全）。

背景
----
``os.path.relpath(path, start)`` 在 Windows 跨盘符时抛
``ValueError: path is on mount 'C:', start on mount 'D:'``。项目在 D: 盘，
而 pytest 临时目录 / 用户目录常在 C: 盘 → "把外部路径相对化到项目根"的写法会炸。

第48批声称"排查 35 处改 7 处"，但**实测零调用**（未落地）→ 本批真正接入 13 处。

为何零回归
----------
``safe_relpath`` 同盘时与 ``os.path.relpath`` **行为完全一致**，
仅在跨盘/异常时降级为**绝对路径**（只增加容错，不改变既有语义）。

覆盖
----
① 跨盘不抛异常，返回绝对路径（★核心）
② 跨盘时 os.path.relpath 确实会抛（证明风险真实存在，不是假想）
③ 同盘行为与 os.path.relpath 完全一致（★零回归保证）
④ drive_of / same_drive 正确性
⑤ 9 个文件的接入点源码断言（import + 调用）
⑥ 接入文件无残留 ``os.path.relpath(`` 代码调用（注释不算）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import io  # noqa: E402
import unittest  # noqa: E402

from nucleus.data.path_utils import drive_of, safe_commonpath, safe_relpath, same_drive  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 接入的 9 个文件（第55批 T3）
WIRED = [
    ("nucleus/logger.py", 1),
    ("nucleus/review/CodeAnalyzer.py", 1),
    ("nucleus/review/CodeReviewEngine.py", 3),
    ("nucleus/self_awareness/CallGraphAnalyzer.py", 2),
    ("nucleus/self_awareness/FakeLoopDetector.py", 1),
    ("nucleus/self_awareness/quality_score_v2.py", 1),
    ("tools/batch_backup.py", 2),
    ("tools/cleanup_reports.py", 2),
    ("organs/immune/PulseWhiteCell.py", 1),
]


def _read(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8",
                   errors="replace").read()


class TestPathUtilsM55(unittest.TestCase):
    """path_utils 本身的跨盘语义。"""

    def test_01_cross_drive_no_exception(self):
        """★核心：跨盘不抛异常，返回绝对路径。"""
        _c = os.path.join("C:" + os.sep, "Users", "x", "t", "f.py")
        _d = os.path.join("D:" + os.sep, "proj")
        _out = safe_relpath(_c, _d)
        self.assertEqual(_out, os.path.abspath(_c),
                         "跨盘应降级为绝对路径，实际=%r" % _out)

    def test_02_cross_drive_plain_relpath_raises(self):
        """证明风险真实：原生 relpath 在跨盘时确实抛 ValueError。"""
        if os.name != "nt" or drive_of("C:" + os.sep) == drive_of(_ROOT):
            self.skipTest("非 Windows 或同盘环境，无法构造跨盘场景")
        _c = os.path.join("C:" + os.sep, "Users", "x", "t", "f.py")
        with self.assertRaises(ValueError):
            os.path.relpath(_c, _ROOT)

    def test_03_same_drive_identical_to_relpath(self):
        """★零回归保证：同盘时与 os.path.relpath 完全一致。"""
        _a = os.path.join(_ROOT, "nucleus", "data", "path_utils.py")
        self.assertEqual(safe_relpath(_a, _ROOT), os.path.relpath(_a, _ROOT))
        self.assertEqual(safe_relpath(_a), os.path.relpath(_a))

    def test_04_drive_helpers(self):
        """drive_of / same_drive / safe_commonpath。"""
        _d = drive_of(_ROOT)
        self.assertEqual(drive_of(os.path.join(_ROOT, "a", "b.py")), _d)
        self.assertTrue(same_drive(_ROOT, os.path.join(_ROOT, "x")))
        # 跨盘 commonpath 返回空串（不抛异常）
        _cp = safe_commonpath([os.path.join(_ROOT, "a"),
                               os.path.join("C:" + os.sep, "b")])
        self.assertIsInstance(_cp, str)


class TestWiringM55(unittest.TestCase):
    """接入点源码断言（★按内容，不按行号）。"""

    def test_05_all_files_imported(self):
        """9 个文件都导入了 safe_relpath。"""
        for rel, _ in WIRED:
            self.assertIn("from nucleus.data.path_utils import safe_relpath",
                          _read(rel), "%s 未导入 safe_relpath" % rel)

    def test_06_call_count_matches(self):
        """各文件调用数与接入时一致（防止后续被改回去）。"""
        for rel, expect in WIRED:
            src = _read(rel)
            self.assertGreaterEqual(src.count("_safe_relpath("), expect,
                                    "%s 调用数少于接入时（%d）" % (rel, expect))

    def test_07_no_residual_plain_relpath_in_code(self):
        """代码行不再有原生 os.path.relpath(（注释除外）。"""
        for rel, _ in WIRED:
            for _line in _read(rel).splitlines():
                if _line.lstrip().startswith("#"):
                    continue
                if "os.path.relpath(" in _line and "_safe_relpath" not in _line:
                    # logger.py 的注释除外；此处只看代码行
                    self.fail("%s 仍残留原生 relpath: %s"
                              % (rel, _line.strip()[:80]))


if __name__ == "__main__":
    unittest.main()
