# -*- coding: utf-8 -*-
"""427 刀1/刀2：剥围栏顺序 + 片段可解析预检。

守护三件事：
  1) `_m427_strip_fence_lines` 只删首尾围栏行，**不碰缩进**；
  2) 对无围栏输入是**恒等变换**（历史补丁归一结果逐字节不变 ⇒ 零裁决回归）；
  3) 2a 之前先判语法：截断片段报「生成片段不完整」，不再让 try 平衡闸背锅；
     语法合法但 try 失衡的，仍然报「try 块不平衡」（不许把真因吞掉）。

★铁律 38：`_project_root` 必须指向 tmp —— `_m377_dump_failed_patch` 会往
  `<root>/data/audit/failed_patches` 写正文，不隔离就在生产目录堆标本。
"""
import os
import sys
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.reasoning import PatchManager as pm

FENCE = "```"


def _mk(tmp: str):
    """隔离实例：只设落档根，不碰生产路径。"""
    _o = pm.PatchManager.__new__(pm.PatchManager)
    _o._project_root = tmp
    return _o


def _patch(orig: str, mod: str, pid: str = "p1") -> dict:
    return {"id": pid, "file": "organs/body/Demo.py", "method": "demo",
            "issue_type": "code_optimization", "source": "llm_multi_file",
            "risk_level": "中", "original_code": orig, "modified_code": mod}


class TestStripFenceLines(unittest.TestCase):
    def test_01_strips_fence_without_touching_indent(self):
        """围栏行删掉，正文的 8 空格缩进**原样保留**（交给 dedent 去统一）。"""
        src = FENCE + "python\n        if x:\n            return 1\n" + FENCE
        out = pm._m427_strip_fence_lines(src)
        self.assertEqual(out, "        if x:\n            return 1",
                         "围栏必须删；但缩进一个空格都不许动（否则 dedent 失效）")

    def test_02_no_fence_is_identity(self):
        """无围栏 ⇒ 恒等（连 strip 都不做）—— 这是零回归的根据。"""
        for _src in ("    a = 1\n    b = 2\n", "a = 1", "", "\n\n    x = 1\n"):
            self.assertEqual(pm._m427_strip_fence_lines(_src), _src)

    def test_03_indented_fence_also_stripped(self):
        """围栏行**带前导空格**时同样要剥（421 版 `.startswith` 会漏掉这种）。"""
        src = "    " + FENCE + "python\n        x = 1\n    " + FENCE
        self.assertEqual(pm._m427_strip_fence_lines(src), "        x = 1")

    def test_04_middle_fence_is_content(self):
        """正文中间的三反引号是内容，不能动。"""
        src = FENCE + 'python\ns = """' + FENCE + '"""\n' + FENCE
        out = pm._m427_strip_fence_lines(src)
        self.assertIn(FENCE, out, "正文中间的三反引号被误删（会改写语义）")


class TestParsePrecheck(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        self.o = _mk(self.tmp)

    def tearDown(self):
        self._td.cleanup()

    def test_10_truncated_fragment_reports_incomplete(self):
        """截断在 try 中间的片段 ⇒ 「生成片段不完整」，且真因是缺 except。"""
        _orig = ("    def demo():\n        try:\n            x = 1\n"
                 "        except Exception:\n            pass\n        return x\n")
        _mod = "        try:\n            x = 2\n"      # ← 只到 try 体就断了
        _r = self.o._check_llm_patch_completeness(_patch(_orig, _mod))
        self.assertFalse(_r["complete"])
        self.assertTrue(_r["reason"].startswith("生成片段不完整"),
                        f"主因必须是片段不完整，实得: {_r['reason']}")
        self.assertIn("expected 'except' or 'finally'", _r["reason"],
                      "真因（真截断）必须留在文案里，供人一眼看出该查生成侧")

    def test_11_syntax_ok_but_try_unbalanced_still_says_try(self):
        """语法能过、但 try 少一个 handler ⇒ 仍报 try 不平衡（不许被预检吞掉）。

        ★这条是「预检不许越权」的反向守卫：语法关只判语法，不替 try 平衡表态。
        """
        _mod = ("        try:\n            x = 2\n        return x\n"
                "        y = 3\n")   # try 缺 handler ⇒ SyntaxError
        _r = self.o._check_llm_patch_completeness(_patch("    def demo():\n        return 0\n", _mod))
        self.assertFalse(_r["complete"])
        # 该形态本身也是语法错，故主因仍是片段不完整 —— 但 try 统计必须附上
        self.assertIn("try 块不平衡", _r["reason"],
                      "确实也不平衡时，try 统计要作为补充诊断保留（不许丢信息）")

    def test_12_precheck_off_falls_back_to_old_order(self):
        """开关关闭 ⇒ 回到 376 行为（2a 先跑，reason 以 try 不平衡开头）。"""
        _mod = ("        try:\n            x = 2\n        return x\n        y = 3\n")
        _p = _patch("    def demo():\n        return 0\n", _mod)
        _on = self.o._check_llm_patch_completeness(dict(_p))
        _saved = pm._m427_parse_precheck_on
        pm._m427_parse_precheck_on = lambda: False
        try:
            _off = self.o._check_llm_patch_completeness(dict(_p))
        finally:
            pm._m427_parse_precheck_on = _saved
        self.assertTrue(_on["reason"].startswith("生成片段不完整"))
        self.assertFalse(_off["reason"].startswith("生成片段不完整"),
                         f"关掉开关后不该再走预检，实得: {_off['reason']}")
        self.assertFalse(_on["complete"] or _off["complete"], "两种口径都必须拒收")


class TestNormalizeZeroRegression(unittest.TestCase):
    def test_20_fence_first_equals_old_when_no_fence(self):
        """无围栏时，新式归一 == 421 原式（逐字节），零裁决回归的硬根据。"""
        _samples = [
            "        if x:\n            return 1\n",
            "    def demo():\n        try:\n            x = 1\n"
            + "        except Exception:\n            pass\n",
            "",
            "x = 1",
        ]
        for _src in _samples:
            _old = pm._m421_strip_md_fence(textwrap.dedent(_src))
            _new = textwrap.dedent(pm._m427_strip_fence_lines(_src)).strip()
            self.assertEqual(_old, _new, f"无围栏输入改了裁决: {_src!r}")

    def test_21_fence_case_is_now_dedented(self):
        """带围栏时新式能真正 dedent 到最小缩进（421 版做不到）。"""
        _src = FENCE + "python\n        if x:\n            return 1\n" + FENCE
        _new = textwrap.dedent(pm._m427_strip_fence_lines(_src)).strip()
        self.assertEqual(_new, "if x:\n    return 1",
                         "剥围栏后 dedent 必须生效（这正是 421 的失效点）")


if __name__ == "__main__":
    unittest.main()
