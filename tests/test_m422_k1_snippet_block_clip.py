# -*- coding: utf-8 -*-
"""第422批 刀1 门控单测：片段按「完整语句块」边界截断。

被测对象：`nucleus/reasoning/SafeEvolutionExecutor.py`
  · `_m422_clip_statement_blocks` / `_m422_block_end` / `_m422_is_block_head`
  · `SafeEvolutionExecutor._read_snippet_from_file`

判据都直接读**真实实现**（不复刻逻辑），源码回退即失败。
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus._silent_except import silent_exc
from nucleus.reasoning.SafeEvolutionExecutor import (
    SafeEvolutionExecutor,
    _m422_block_end,
    _m422_clip_statement_blocks,
    _m422_is_block_head,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tb(code):
    """语句级 try 平衡（与生产 `_m376_try_balance` 同口径的独立复算）。"""
    nt = len(re.findall(r"(?m)^[ \t]*try[ \t]*:", code or ""))
    ne = len(re.findall(r"(?m)^[ \t]*(?:except|finally)\b", code or ""))
    if nt > ne:
        return "unbalanced"
    if nt == 0 and ne > 0:
        return "orphan"
    return "ok"


class TestA_FastPath(unittest.TestCase):
    """A 组：整段放得下 ⇒ 逐字原样（零回归红线）。"""

    def test_01_short_text_verbatim(self):
        _s = "def f():\n    return 1\n"
        self.assertEqual(_m422_clip_statement_blocks(_s, 40, 2000), _s.strip())

    def test_02_empty_input(self):
        self.assertEqual(_m422_clip_statement_blocks("", 40, 2000), "")
        self.assertEqual(_m422_clip_statement_blocks(None, 40, 2000), "")

    def test_03_complete_method_unchanged(self):
        """含 try/except 的完整方法（3 行）也应逐字返回。"""
        _s = ("def g():\n"
              "    try:\n"
              "        return 1\n"
              "    except Exception:\n"
              "        return 0\n")
        _out = _m422_clip_statement_blocks(_s, 40, 2000)
        self.assertEqual(_out, _s.strip())
        self.assertEqual(_tb(_out), "ok")


class TestB_BlockBoundary(unittest.TestCase):
    """B 组：块边界判据（不腰斩 try/if/for/while/with）。"""

    def test_04_block_head_detection(self):
        self.assertTrue(_m422_is_block_head("    try:\n"))
        self.assertTrue(_m422_is_block_head("    if x:\n"))
        self.assertTrue(_m422_is_block_head("    def f():\n"))
        self.assertTrue(_m422_is_block_head("    async def f():\n"))
        # 不是块头
        self.assertFalse(_m422_is_block_head("    return 1\n"))
        self.assertFalse(_m422_is_block_head("    # try:\n"))       # 注释
        self.assertFalse(_m422_is_block_head("    x = {'a': 1}\n"))  # 字典不是块头
        self.assertFalse(_m422_is_block_head("\n"))

    def test_05_try_block_end_includes_handlers(self):
        """`except`/`finally` 与 try 同缩进，但属**同一个**块 —— 不能被当块结束。"""
        _lines = [
            "try:\n",
            "    a()\n",
            "except ValueError:\n",
            "    b()\n",
            "finally:\n",
            "    c()\n",
            "d()\n",
        ]
        self.assertEqual(_m422_block_end(_lines, 0, len(_lines)), 6)

    def test_06_if_else_is_one_block(self):
        _lines = ["if x:\n", "    a()\n", "else:\n", "    b()\n", "c()\n"]
        self.assertEqual(_m422_block_end(_lines, 0, len(_lines)), 4)

    def test_07_nested_block_stops_at_outer(self):
        _lines = [
            "def f():\n",
            "    if x:\n",
            "        a()\n",
            "    return 1\n",
            "def g():\n",
        ]
        self.assertEqual(_m422_block_end(_lines, 0, len(_lines)), 4)


class TestC_Truncation(unittest.TestCase):
    """C 组：超预算时按块整体排除（核心行为变更）。"""

    def _mk_big_method(self):
        """造一个 30 行的 try 块 + 后续语句，总长 > 20 行预算。"""
        _body = "def big():\n    try:\n"
        for _i in range(20):
            _body += f"        x{_i} = {_i}\n"
        _body += "    except Exception:\n        pass\n    return 1\n"
        return _body

    def test_08_never_returns_half_try(self):
        _src = self._mk_big_method()
        _out = _m422_clip_statement_blocks(_src, 10, 2000)
        self.assertEqual(_tb(_out), "ok", f"★不得返回半截 try 块：\n{_out}")

    def test_09_returns_prefix_blocks_only(self):
        """预算不足时只给「完整的前缀块」，不给块内片段。"""
        _src = ("def f():\n"
                "    a = 1\n"
                "    try:\n"
                + "".join(f"        s{i} = {i}\n" for i in range(20))
                + "    except Exception:\n"
                  "        pass\n")
        _out = _m422_clip_statement_blocks(_src, 12, 2000)
        self.assertIn("def f():", _out)
        self.assertIn("a = 1", _out)
        # try 块整块放不下 ⇒ 整体排除
        self.assertNotIn("s0 = 0", _out)
        self.assertEqual(_tb(_out), "ok")

    def test_10_char_budget_also_on_block_boundary(self):
        """字符预算超限时同样按块切（原实现 `[:2000]` 会切断块）。"""
        _src = ("def f():\n"
                "    try:\n"
                "        a = '" + "x" * 3000 + "'\n"
                "    except Exception:\n"
                "        pass\n")
        _out = _m422_clip_statement_blocks(_src, 40, 2000)
        self.assertLessEqual(len(_out), 2000)
        self.assertEqual(_tb(_out), "ok", f"★字符截断也不得腰斩块：\n{_out[:400]}")

    def test_11_oversized_def_falls_back_to_subblocks(self):
        """首块（def）就超预算 ⇒ 退化为「块头 + 内部完整子块」。"""
        _src = ("def outer():\n"
                "    a = 1\n"
                "    if a:\n"
                + "".join(f"        z{i} = {i}\n" for i in range(30))
                + "    return a\n")
        _out = _m422_clip_statement_blocks(_src, 15, 2000)
        self.assertTrue(_out.startswith("def outer():"))
        self.assertIn("a = 1", _out)
        self.assertNotIn("z0 = 0", _out)   # if 块 30 行放不下 ⇒ 整体排除
        self.assertEqual(_tb(_out), "ok")


class TestD_RealCorpus(unittest.TestCase):
    """D 组：真实语料重放（核心活体判据）。"""

    def test_12_compress_group_no_half_try(self):
        """2a 标本源头：PulseLiver._compress_group（230 行）。"""
        _p = os.path.join(_ROOT, "organs", "body", "PulseLiver.py")
        if not os.path.isfile(_p):
            self.skipTest("PulseLiver.py 不存在")
        with open(_p, encoding="utf-8") as _fh:
            _lines = _fh.readlines()
        _st = None
        for i, ln in enumerate(_lines):
            if "def _compress_group(" in ln:
                _st = i
                break
        self.assertIsNotNone(_st, "未找到 _compress_group")
        _out = _m422_clip_statement_blocks("".join(_lines[_st:]), 40, 2000)
        self.assertEqual(_tb(_out), "ok", f"★素材不得残缺：\n{_out}")
        # 改前口径对照（逐字复刻旧实现）
        _old = "".join(_lines[_st:_st + 40]).strip()[:2000]
        self.assertEqual(_tb(_old), "unbalanced", "改前口径应是不平衡（证明本测试有区分力）")

    def test_13_no_half_try_across_core_methods(self):
        """核心目录的真实方法批量重放：不得有一条落回半截 try。"""
        import ast
        _roots = ["organs", "nucleus", os.path.join("base")]
        _bad = []
        _n = 0
        for _r in _roots:
            for dp, dns, fns in os.walk(os.path.join(_ROOT, _r)):
                dns[:] = [x for x in dns
                          if x != "__pycache__" and not x.startswith(".")]
                for fn in fns:
                    if not fn.endswith(".py"):
                        continue
                    fp = os.path.join(dp, fn)
                    try:
                        with open(fp, encoding="utf-8",
                                          errors="ignore") as _fh:
                            src = _fh.read()
                        tree = ast.parse(src)
                    except (OSError, SyntaxError) as _e_read:
                        # 读不到/解析不了的文件不是本用例的判据对象（判据是"截断后
                        # 是否落回半截 try"），跳过即可 —— 但要留痕，静默 continue
                        # 会被 `cw2_t2e_ci_gate_silent_except` 判为静默 handler。
                        silent_exc(_e_read,
                                   where="tests.test_m422_k1::test_13 read_or_parse",
                                   level="debug")
                        continue
                    _ls = src.splitlines(True)
                    for node in ast.walk(tree):
                        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            continue
                        if not any(isinstance(x, ast.Try) for x in ast.walk(node)):
                            continue
                        _n += 1
                        _o = _m422_clip_statement_blocks(
                            "".join(_ls[node.lineno - 1:]), 40, 2000)
                        if _tb(_o) != "ok":
                            _bad.append(f"{os.path.relpath(fp, _ROOT)}::{node.name}")
        self.assertGreater(_n, 100, "样本量过小，判据不可信")
        self.assertEqual(_bad[:5], [],
                         f"★仍有 {len(_bad)}/{_n} 个方法素材残缺"
                         f"（前 5：{_bad[:5]}）")


class TestE_Wiring(unittest.TestCase):
    """E 组：接线门禁 —— `_read_snippet_from_file` 必须真的走新函数。"""

    def test_14_read_snippet_uses_clip(self):
        _exe = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        _exe._project_root = _ROOT
        _p = os.path.join(_ROOT, "organs", "body", "PulseLiver.py")
        if not os.path.isfile(_p):
            self.skipTest("PulseLiver.py 不存在")
        _snip = _exe._read_snippet_from_file(_p, "_compress_group")
        self.assertTrue(_snip, "应返回非空片段")
        self.assertEqual(_tb(_snip), "ok", f"★接线后素材仍残缺：\n{_snip}")

    def test_15_source_wiring_count(self):
        """源码级：新函数在文件里「定义 1 次 + 被调用 2 次」。"""
        _p = os.path.join(_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
        with open(_p, encoding="utf-8") as _fh:
            _src = _fh.read()
        self.assertEqual(_src.count("def _m422_clip_statement_blocks("), 1)
        self.assertEqual(_src.count("_m422_clip_statement_blocks(\n"), 2,
                         "★两处 return 都必须走裁剪函数")
        # 旧的硬截断调用必须消失
        self.assertNotIn('return "".join(_lines[_i:_i + max_lines]).strip()[:2000]', _src)
        self.assertNotIn('return "".join(_lines[:max_lines]).strip()[:2000]', _src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
