"""421 刀4：dedent 之后剥 markdown 围栏。

被守护的四条：
  1) `_m421_strip_md_fence` 只削**首尾**围栏行，中间内容一字不动；
  2) 无围栏输入**恒等**（等价 `text.strip()`）—— 既有 443 条补丁的历史裁决不变；
  3) 带围栏的**合法**补丁不再被关2 当成语法错（这是本刀的真实收益）；
  4) ★零放水：剥完仍然不平衡/缩进坏的，照样被拒。

★铁律 38：实例用 `__new__` 构造并立刻把 `_project_root` 指到 tmp ——
  `_m377_dump_failed_patch` 会往 `<root>/data/audit/failed_patches` 写正文，
  不隔离就会在生产目录里堆标本文件。
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.reasoning import PatchManager as pm

FENCE = '```'


class TestStripFence(unittest.TestCase):
    def test_01_strip_python_fence(self):
        src = FENCE + 'python\n    def f():\n        return 1\n' + FENCE
        self.assertEqual(pm._m421_strip_md_fence(src),
                         'def f():\n        return 1',
                         '首尾围栏必须剥掉，且正文的 dedent 不归本函数管')

    def test_02_strip_bare_fence(self):
        src = FENCE + '\nx = 1\n' + FENCE
        self.assertEqual(pm._m421_strip_md_fence(src), 'x = 1')

    def test_03_no_fence_is_identity(self):
        src = '    a = 1\n    b = 2\n'
        self.assertEqual(pm._m421_strip_md_fence(src), src.strip())

    def test_04_empty_and_none(self):
        self.assertEqual(pm._m421_strip_md_fence(''), '')
        self.assertEqual(pm._m421_strip_md_fence(None), '')

    def test_05_only_fence(self):
        """只有一对围栏、没有正文 ⇒ 空串（下游关1 会以"内容为空"拒掉）。"""
        self.assertEqual(pm._m421_strip_md_fence(FENCE + 'python\n' + FENCE), '')

    def test_06_middle_fence_untouched(self):
        """正文中间的三反引号是内容，不能动。"""
        src = FENCE + 'python\ns = """' + FENCE + '"""\n' + FENCE
        out = pm._m421_strip_md_fence(src)
        self.assertIn(FENCE, out, '正文中间的三反引号被误删了（会改写语义）')
        self.assertTrue(out.startswith('s = '))
        self.assertFalse(out.endswith(FENCE))

    def test_07_no_fence_left_after_strip(self):
        src = FENCE + 'python\ntry:\n    x = 1\nexcept Exception:\n    pass\n' + FENCE
        self.assertEqual(pm._m421_strip_md_fence(src).count(FENCE), 0)


class TestCompletenessWithFence(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        self.o = pm.PatchManager.__new__(pm.PatchManager)
        self.o._project_root = self.tmp       # ★落档隔离
        self.assertTrue(self.o._project_root.startswith(self.tmp))

    def tearDown(self):
        self._td.cleanup()

    def _check(self, orig, mod):
        return self.o._check_llm_patch_completeness({
            "id": "t421k4", "file": "a.py", "method": "m",
            "original_code": orig, "modified_code": mod,
        })

    def test_08_fenced_valid_patch_passes_gate2(self):
        """★核心收益：合法补丁带围栏时，关2 不再报 SyntaxError。"""
        # ★orig 必须与 mod 长度相近：否则会先被**关3 相似度**（默认 0.3）拒掉，
        #   测不到关2 —— 第一版就是栽在这里（reason 报"相似度过低 0.19"）。
        _orig = ("x = 1\n"
                 "try:\n"
                 "    y = 2\n"
                 "except Exception:\n"
                 "    y = 3\n")
        _body = ("x = 1\n"
                 "try:\n"
                 "    y = 2\n"
                 "except Exception:\n"
                 "    y = 0\n")
        r = self._check(_orig, FENCE + 'python\n' + _body + FENCE)
        self.assertTrue(r["complete"],
                        f"带围栏的合法补丁被拒了（reason={r.get('reason')!r}）")

    def test_09_unfenced_behaviour_unchanged(self):
        """无围栏时与本刀之前**完全一致**（同一段代码，加不加围栏结论相同）。"""
        _orig = ("x = 1\n"
                 "try:\n"
                 "    y = 2\n"
                 "except Exception:\n"
                 "    y = 3\n")
        _body = ("x = 1\n"
                 "try:\n"
                 "    y = 2\n"
                 "except Exception:\n"
                 "    y = 0\n")
        r_plain = self._check(_orig, _body)
        r_fenced = self._check(_orig, FENCE + 'python\n' + _body + FENCE)
        self.assertEqual(r_plain["complete"], r_fenced["complete"])

    def test_10_unbalanced_still_rejected(self):
        """★零放水：剥完仍然 try 不平衡 ⇒ 照样在 2a 被拒。"""
        _bad = "x = 1\ntry:\n    y = 2\n"
        r = self._check("x = 1\n", FENCE + 'python\n' + _bad + FENCE)
        self.assertFalse(r["complete"], 'try 不平衡的补丁被放行了')
        self.assertIn("try", r["reason"])

    def test_11_orphan_handler_still_rejected(self):
        _bad = "x = 1\nexcept Exception:\n    pass\n"
        r = self._check("x = 1\n", FENCE + 'python\n' + _bad + FENCE)
        self.assertFalse(r["complete"])

    def test_12_empty_after_strip_rejected(self):
        """只有围栏 ⇒ 剥完为空 ⇒ 关1 判"内容为空"，不是崩。"""
        r = self._check("x = 1\n", FENCE + 'python\n' + FENCE)
        self.assertFalse(r["complete"])
        self.assertIn("为空", r["reason"])

    def test_13_broken_indent_still_rejected(self):
        """剥围栏后缩进仍坏的，关2 照样报语法错（不会误放）。"""
        _bad = "x = 1\n        y = 2\n"
        r = self._check("x = 1\n", FENCE + 'python\n' + _bad + FENCE)
        self.assertFalse(r["complete"])


if __name__ == '__main__':
    unittest.main()
