# -*- coding: utf-8 -*-
"""主线第78批 T2/P1 门控测试：核心模块静默异常治理。

原问题：main.py / self_inspector.py / runtime_metrics.py / functions/chat/chat_service.py
存在大量 `except ...: pass` 静默吞异常 → 故障无日志、难排查。
修复：转换为 `silent_exc(exc, "文件:行")` 调用（统一 helper，fail-closed 语义不变）。

覆盖：
1. 4 个目标文件不再存在「body 仅为 pass」的静默 except 块。
2. 4 个文件均正确导入 silent_exc helper。
3. silent_exc helper 可调用且不抛异常（等价原 pass 的 fail-closed）。
4. helper 在包内可导入、且为 callable。
"""
import ast
import io
import os
import unittest

from nucleus._silent_except import silent_exc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TARGETS = [
    "main.py",
    "nucleus/self_inspector.py",
    "nucleus/runtime_metrics.py",
    "functions/chat/chat_service.py",
]


def _silent_pass_count(path):
    """统计 body 仅为单个 Pass 的 except handler 数量。"""
    src = io.open(path, encoding="utf-8", errors="replace").read()
    tree = ast.parse(src)
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                n += 1
    return n


class TestSilentExceptM78(unittest.TestCase):
    def test_01_no_silent_pass_in_targets(self):
        for rel in _TARGETS:
            p = os.path.join(ROOT, rel)
            self.assertTrue(os.path.isfile(p), f"目标文件缺失: {rel}")
            self.assertEqual(_silent_pass_count(p), 0,
                             f"{rel} 仍存在静默 except: pass 块（未治理）")

    def test_02_helper_imported_in_targets(self):
        for rel in _TARGETS:
            p = os.path.join(ROOT, rel)
            src = io.open(p, encoding="utf-8", errors="replace").read()
            self.assertIn("silent_exc", src,
                          f"{rel} 未引用 silent_exc helper（静默异常未转换）")

    def test_03_helper_callable_no_raise(self):
        # silent_exc 必须像原 pass 一样不抛异常（fail-closed 语义不变）
        try:
            raise ValueError("probe")
        except ValueError as e:
            # 调用不应再抛出（等价于被吞）
            self.assertIsNone(silent_exc(e, "test_m78:0"))

    def test_04_helper_importable(self):
        self.assertTrue(callable(silent_exc), "silent_exc 必须是可调用对象")

    def test_05_helper_handles_none_gracefully(self):
        # 防御：传入 None 也不抛（避免替换后引入新崩溃）
        self.assertIsNone(silent_exc(None, "test_m78:0"))


if __name__ == "__main__":
    unittest.main()
