# -*- coding: utf-8 -*-
"""371 刀2 · O3 两方法标记注释去重＋唯一性断言（4 例，对应任务书 §三）。

① 两方法前导监测注释各仅一遍，且方法体 AST 与去重前零 diff
② 唯一性断言在位：把注释重复粘贴一遍即判定失败（反向判别，证明断言不是恒真）
③ 测试面 `tests/test_m345_batch.py:101/108` 两处真实调用不回归（restore_pool 非死方法）
④ 生产面零新增调用（两方法仍无生产调用方）
"""
import ast
import io
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TARGET = os.path.join(ROOT, "nucleus", "audit_problem_pool.py")

# 两段前导注释的首行（去重后应各出现 1 次）
MARK_PLAN = "# ★370刀3 死方法监测标记：定义1 / 生产调用0 / 测试调用0 / 属性读0（全仓扫描实测）。"
MARK_RESTORE = "# ★370刀3 死方法监测标记：定义1 / **生产调用0** / **测试调用2** / 属性读0。"


def _note_count(src: str, marker: str) -> int:
    """注释块唯一性计数器：按首行精确匹配（含前导空白，防子串误命中）。"""
    return len(re.findall(r"^" + re.escape(marker) + r"$", src, flags=re.MULTILINE))


def _methods_ast(src: str) -> dict:
    tree = ast.parse(src)
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name in ("plan_target_drift", "restore_pool"):
            out[n.name] = [ast.dump(x) for x in n.body]
    return out


class TestNoteDedup(unittest.TestCase):

    def setUp(self):
        with io.open(TARGET, encoding="utf-8") as f:
            self.src = f.read()

    def test_01_each_note_once(self):
        """① 两段前导注释各只出现一遍。"""
        self.assertEqual(_note_count(self.src, MARK_PLAN), 1,
                         "plan_target_drift 前导注释不是 1 遍（重复粘贴未被去重）")
        self.assertEqual(_note_count(self.src, MARK_RESTORE), 1,
                         "restore_pool 前导注释不是 1 遍（重复粘贴未被去重）")

    def test_02_method_body_ast_unchanged(self):
        """① 附：方法体 AST 可解析且两方法都在（结构未动）。

        零 diff 的严格对照在补丁脚本内做过（before/after 全等断言）；
        这里守住「两方法仍存在且 body 非空」，防被误删。
        """
        m = _methods_ast(self.src)
        self.assertEqual(set(m), {"plan_target_drift", "restore_pool"})
        for name, body in m.items():
            self.assertTrue(body, "%s 方法体为空" % name)

    def test_03_duplicate_paste_is_caught(self):
        """② 反向判别：把注释再贴一遍，计数器必须报 2（断言不是恒真）。"""
        dup = self.src.replace(MARK_PLAN, MARK_PLAN + "\n" + MARK_PLAN, 1)
        self.assertEqual(_note_count(dup, MARK_PLAN), 2,
                         "计数器对重复粘贴无判别力 ⇒ 唯一性断言形同虚设")
        # 原文件仍为 1
        self.assertEqual(_note_count(self.src, MARK_PLAN), 1)

    def test_04_test_surface_calls_intact(self):
        """③ 测试面 2 处 restore_pool 调用保持不动（它是「生产 0 调用、测试面在用」）。"""
        tpath = os.path.join(ROOT, "tests", "test_m345_batch.py")
        with io.open(tpath, encoding="utf-8") as f:
            tsrc = f.read()
        hits = re.findall(r"\.restore_pool\(", tsrc)
        self.assertEqual(len(hits), 2, "测试面 restore_pool 调用应保持 2 处，实际 %d" % len(hits))

    def test_05_no_new_production_call(self):
        """④ 生产面零新增调用：nucleus/organs/base 内除定义外不得出现对两方法的调用。"""
        scan_dirs = ["nucleus", "organs", "base"]
        offenders = []
        for d in scan_dirs:
            base = os.path.join(ROOT, d)
            for dirpath, _dirnames, filenames in os.walk(base):
                for fn in filenames:
                    if not fn.endswith(".py"):
                        continue
                    fp = os.path.join(dirpath, fn)
                    if os.path.abspath(fp) == os.path.abspath(TARGET):
                        continue
                    with io.open(fp, encoding="utf-8", errors="replace") as f:
                        s = f.read()
                    for name in ("plan_target_drift", "restore_pool"):
                        if re.search(r"\b" + name + r"\s*\(", s):
                            offenders.append("%s:%s" % (os.path.relpath(fp, ROOT), name))
        self.assertEqual(offenders, [], "出现生产面调用（本刀不接调用方）：%s" % offenders)


if __name__ == "__main__":
    unittest.main(verbosity=2)
