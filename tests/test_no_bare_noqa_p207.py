# -*- coding: utf-8 -*-
"""★P2-207防回潮测试：禁止裸 # noqa（必须带规则码）。

裸 # noqa 会抑制所有规则，无法审计实际抑制了什么。
所有 # noqa 必须带规则码，如 # noqa: F401。
"""
import io
import os
import re
import unittest

# 项目根目录（tests/ 的上一级）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 需要检查的目录
_SCAN_DIRS = ["nucleus", "organs", "base", "tools", "tests"]

# 允许裸 # noqa 的文件白名单（空=不允许）
_ALLOWED_FILES = set()

# 裸 # noqa 模式：行尾的 # noqa，后面没有 :规则码
_BARE_NOQA_PATTERN = re.compile(r"#\s*noqa\s*$", re.MULTILINE)


def _iter_py_files():
    """遍历项目中的 .py 文件。"""
    # 根目录下的 .py 文件
    for fname in os.listdir(_PROJECT_ROOT):
        if fname.endswith(".py"):
            yield os.path.join(_PROJECT_ROOT, fname)
    # 子目录下的 .py 文件
    for d in _SCAN_DIRS:
        dirpath = os.path.join(_PROJECT_ROOT, d)
        if not os.path.isdir(dirpath):
            continue
        for root, _dirs, files in os.walk(dirpath):
            for f in files:
                if f.endswith(".py"):
                    yield os.path.join(root, f)


class TestNoBareNoqaP207(unittest.TestCase):
    """P2-207：禁止裸 # noqa。"""

    def test_no_bare_noqa_in_source(self):
        """源码中不得出现裸 # noqa（必须带规则码）。"""
        violations = []
        for fpath in _iter_py_files():
            rel = os.path.relpath(fpath, _PROJECT_ROOT)
            if rel in _ALLOWED_FILES:
                continue
            try:
                with io.open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
            except (UnicodeDecodeError, OSError):
                continue
            for match in _BARE_NOQA_PATTERN.finditer(content):
                # 计算行号
                line_no = content[: match.start()].count("\n") + 1
                # 提取该行内容
                line_start = content.rfind("\n", 0, match.start()) + 1
                line_end = content.find("\n", match.end())
                if line_end == -1:
                    line_end = len(content)
                line_content = content[line_start:line_end].strip()
                violations.append(f"{rel}:{line_no}: {line_content}")

        if violations:
            self.fail(
                f"发现 {len(violations)} 处裸 # noqa（必须带规则码，如 # noqa: F401）：\n"
                + "\n".join(violations[:20])
                + ("\n..." if len(violations) > 20 else "")
            )

    def test_main_py_noqa_has_rule_code(self):
        """main.py 中的 # noqa 必须带规则码（P2-207重点修复对象）。"""
        main_path = os.path.join(_PROJECT_ROOT, "main.py")
        if not os.path.exists(main_path):
            self.skipTest("main.py not found")
        with io.open(main_path, "r", encoding="utf-8") as f:
            content = f.read()
        bare = _BARE_NOQA_PATTERN.findall(content)
        self.assertEqual(
            len(bare), 0,
            f"main.py 中发现 {len(bare)} 处裸 # noqa"
        )


if __name__ == "__main__":
    unittest.main()
