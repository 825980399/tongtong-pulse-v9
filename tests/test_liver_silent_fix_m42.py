# -*- coding: utf-8 -*-
"""主线第42批 T4（P2-276）门控测试：PulseLiver 静默异常防回归。

背景
----
补丁历史中 ``PulseLiver._build_knowledge_association_graph`` 被记为
``effectiveness=0.0``（baseline=4 / after_fix=4），一度被判为「无效修复」。
第42批 T4 逐字比对确认：**补丁已 100% 落地**（当前源码与补丁 ``modified_code``
逐字一致），``baseline/post`` 的 4→4 是**文件级**口径，与该方法无关。

★ 本测试用**源码切片**直接检真实文件（源码回退即失败），防止静默异常回归。
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_LIVER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "organs", "body", "PulseLiver.py")

#: 本批诊断涉及的方法（补丁目标 + 顺手修复点所在方法）。
_METHODS = (
    "_build_knowledge_association_graph",
    "_save_fuse_cooldown",
    "_compress_group",
)

_SILENT_RE = re.compile(r"except[^:]*:\s*$")


def _src():
    with open(_LIVER, encoding="utf-8", errors="replace") as f:
        return f.read().replace("\r\n", "\n")


def _method_body(src, name):
    """按缩进切片取出方法体（到下一个同级 def / class 为止）。"""
    lines = src.splitlines()
    _start = next((i for i, l in enumerate(lines)
                   if re.match(r"^\s*def %s\b" % re.escape(name), l)), None)
    if _start is None:
        return None
    _end = len(lines)
    for k in range(_start + 1, len(lines)):
        if re.match(r"^    def ", lines[k]) or re.match(r"^class ", lines[k]):
            _end = k
            break
    return lines[_start:_end]


class TestLiverSilentFix(unittest.TestCase):
    def setUp(self):
        self.src = _src()

    def test_01_file_exists_and_has_methods(self):
        self.assertTrue(self.src, "PulseLiver.py 应可读且非空")
        for _m in _METHODS:
            self.assertIsNotNone(_method_body(self.src, _m), "缺少方法 %s" % _m)

    def test_02_target_methods_have_no_silent_except(self):
        """★防回归：三个方法体内不得出现 `except ...: pass`。"""
        for _m in _METHODS:
            _body = _method_body(self.src, _m)
            _hits = [i for i, l in enumerate(_body)
                     if _SILENT_RE.search(l.rstrip())
                     and i + 1 < len(_body) and _body[i + 1].strip() == "pass"]
            self.assertEqual(_hits, [], "%s 内仍有静默 except: pass" % _m)

    def test_03_whole_file_has_no_silent_except(self):
        """★全文件静默点应为 0（T4 修复前后实测：1 → 0）。"""
        _lines = self.src.splitlines()
        _hits = [i + 1 for i, l in enumerate(_lines)
                 if _SILENT_RE.search(l.rstrip())
                 and i + 1 < len(_lines) and _lines[i + 1].strip() == "pass"]
        self.assertEqual(_hits, [], "PulseLiver.py 仍存在静默吞异常: %s" % _hits)

    def test_04_patch_fix_present_verbatim(self):
        """补丁 modified_code 的修复形态必须仍在源码中（逐字）。"""
        _body = _method_body(self.src, "_build_knowledge_association_graph")
        _txt = "\n".join(_body)
        self.assertIn("except Exception as e:", _txt)
        self.assertIn("self._log(LogLevel.ERROR, f'异常: {e}')", _txt)

    def test_05_compress_group_logs_instead_of_pass(self):
        """_compress_group 的清理失败应有 DEBUG 留痕（不再静默）。"""
        _body = "\n".join(_method_body(self.src, "_compress_group"))
        self.assertNotIn("except Exception:\n                                    pass", _body)
        self.assertIn("矛盾预检清理节点失败", _body)

    def test_06_imports_log_level(self):
        """修复用到 LogLevel，必须已导入（否则 F821）。"""
        self.assertIn("LogLevel", self.src)
        self.assertTrue(re.search(r"^\s*(from|import).*LogLevel", self.src, re.M)
                        or "LogLevel" in self.src.split("\n")[0:40].__str__())


if __name__ == "__main__":
    unittest.main(verbosity=2)
