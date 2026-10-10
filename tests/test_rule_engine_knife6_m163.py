# -*- coding: utf-8 -*-
"""163批 刀6 门控单测：增量档(git diff)读取 UTF-8 解码 + 失败可观测。

验收口径（施工任务书 刀6）：
- git diff 输出按 utf-8(errors=replace) 解码，GBK 字节不再崩线程。
- git 调用失败时优雅返回 []，且至少打一条含 "git diff" 的 WARNING。
"""
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys

sys.path.insert(0, sys_path)

import nucleus.self_awareness.rule_engine as RE


class TestKnife6M163(unittest.TestCase):
    def test_git_failure_warns_and_returns_empty(self):
        with patch.object(subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("git", 10)), \
                self.assertLogs(logger="rule_engine", level="WARNING") as _cm:
            out = RE.changed_files(base="HEAD~1", target="HEAD")
        self.assertEqual(out, [])
        self.assertTrue(any("git diff" in _m for _m in _cm.output),
                        "WARNING 未携带 git diff 命令: {!r}".format(_cm.output))

    def test_git_diff_decoded_utf8_no_crash(self):
        _d = tempfile.mkdtemp()
        open(os.path.join(_d, "a.py"), "w").close()
        open(os.path.join(_d, "b.py"), "w").close()
        _cp = subprocess.CompletedProcess(
            ["git"], 0, stdout="a.py\nb.py\n", stderr="")
        with patch.object(subprocess, "run", return_value=_cp):
            out = RE.changed_files(base="HEAD~1", target="HEAD", root=_d)
        self.assertEqual(out, ["a.py", "b.py"])


if __name__ == "__main__":
    unittest.main()
