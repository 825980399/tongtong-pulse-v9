# -*- coding: utf-8 -*-
"""T-143d 回归测试：对外发布导出脚本 tools/export_public.py。

锁定三件事：
  1. 排除规则：data/tmp/logs/内部文档/备份/构建产物 一律不入包；
  2. docs 白名单 fail-closed：只放行 PUBLIC_DOCS_ALLOW_* 中列出的文件/目录；
  3. PII 复扫：对导出清单扫描须零命中（含 example.* 保留域豁免、文件级豁免标记）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import export_public as ep

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestExcludeRules(unittest.TestCase):
    """should_skip 的排除语义。"""

    def test_01_runtime_data_excluded(self):
        for rel in ("data/knowledge/snap.json", "tmp/x.py", "logs/pulse.log",
                    "models/bge.onnx"):
            self.assertTrue(ep.should_skip(rel), rel)

    def test_02_internal_docs_excluded(self):
        for rel in (
            "docs/路灯与星轨对话/任务书/第143批.md",
            "docs/分析报告/技术债务台账.csv",
            "docs/归档/LESSONS_LEARNED.md",
            "docs/审查报告/x.md",
            "docs/性能报告/x.md",
        ):
            self.assertTrue(ep.should_skip(rel), rel)

    def test_03_backups_excluded(self):
        for rel in (".bak_batch143/config.py", "nucleus/const.py.bak_mainline8",
                    "tools/x.py.orig"):
            self.assertTrue(ep.should_skip(rel), rel)

    def test_04_build_artifacts_excluded(self):
        for rel in ("nucleus/pulse/build/temp.win-amd64-cpython-312/Release/a.o",
                    "nucleus/field/_oscillon_cy.o", "x.pyd", "y.cp312-win_amd64.pyd"):
            self.assertTrue(ep.should_skip(rel), rel)

    def test_05_code_kept(self):
        for rel in ("main.py", "config.py", "nucleus/const.py",
                    "organs/body/PulseLiver.py", "tests/test_x.py",
                    "tools/export_public.py", "README.md",
                    "base/BasePulseOrgan.py", "pulses/pulse_config.yaml",
                    ".env.example", "requirements.txt"):
            self.assertFalse(ep.should_skip(rel), rel)


class TestDocsWhitelist(unittest.TestCase):
    """docs/ 白名单 fail-closed。"""

    def test_01_allow_files(self):
        for rel in ("README.md", "demo-quickstart.md", "项目架构总览_20260927.md",
                    "项目结构树.md", "完整进化路线与技术债务清单_v1.0.md"):
            self.assertTrue(ep.docs_allowed(rel), rel)

    def test_02_allow_dirs(self):
        for rel in ("比赛准备/运行数据卡片_20260927.md",
                    "设计文档/某设计_v1.0.md",
                    "工具类文档/某说明.md"):
            self.assertTrue(ep.docs_allowed(rel), rel)

    def test_03_unlisted_file_rejected(self):
        # 未在白名单里的根级文档 → 拒绝（fail-closed）
        for rel in ("第三方全面分析报告_20260926.md",
                    "死代码检测报告_8大模块_v2.0.md",
                    "git_commit_hash_mapping.md"):
            self.assertFalse(ep.docs_allowed(rel), rel)

    def test_04_internal_dir_rejected(self):
        for rel in ("路灯与星轨对话/交付报告/x.md", "分析报告/x.csv",
                    "归档/x.md", "archive/x.md", "验收/x.md",
                    "审查报告/x.md", "性能报告/x.md", "台账/x.csv"):
            self.assertFalse(ep.docs_allowed(rel), rel)


class TestPiiScan(unittest.TestCase):
    """PII 复扫器行为。

    注意：本类用**运行时拼接**构造测试串，避免源码中出现真实 PII 字面量
    （否则 TestRealTree.test_01 会扫到本文件自身）。
    """

    @staticmethod
    def _name_gl() -> str:
        return "\u4efb\u6842\u6797"          # 真名（转义构造）

    @staticmethod
    def _phone() -> str:
        return "138" + "1234" + "5678"        # 构造的假手机号

    def test_01_real_name_flagged(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                         encoding="utf-8") as fh:
            fh.write("创建者是" + self._name_gl() + "\n")
            p = fh.name
        try:
            hits = ep.scan_text(p)
            self.assertTrue(any("真名" in h[0] for h in hits), hits)
        finally:
            os.unlink(p)

    def test_02_example_domain_allowed(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as fh:
            fh.write('contact = "admin@' + "example.com" + '"\n')
            p = fh.name
        try:
            self.assertEqual(ep.scan_text(p), [])
        finally:
            os.unlink(p)

    def test_03_file_skip_marker(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as fh:
            fh.write("# -*- coding: utf-8 -*-\n")
            fh.write("# export-" + "scan-skip-file\n")
            fh.write('phone = "' + self._phone() + '"  # 构造夹具\n')
            p = fh.name
        try:
            self.assertEqual(ep.scan_text(p), [])
        finally:
            os.unlink(p)

    def test_04_real_path_flagged(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as fh:
            fh.write('ROOT = "' + "D:" + chr(92) + "xinrenlei" + chr(92)
                     + 'tongtong-pulse-v9"' + "\n")
            p = fh.name
        try:
            hits = ep.scan_text(p)
            self.assertTrue(any("路径" in h[0] for h in hits), hits)
        finally:
            os.unlink(p)

    def test_05_export_script_self_exempt(self):
        self.assertIn("tools/export_public.py", ep.SCAN_EXEMPT_FILES)


class TestRealTree(unittest.TestCase):
    """真实仓库：导出清单本身须零 PII 命中。"""

    def test_01_current_tree_clean(self):
        files = sorted(ep.iter_public_files(_ROOT))
        self.assertGreater(len(files), 100)
        hits = ep.verify_clean(_ROOT, files)
        self.assertEqual(hits, [], f"PII 残留: {hits[:10]}")

    def test_02_no_forbidden_in_list(self):
        files = sorted(ep.iter_public_files(_ROOT))
        for p in files:
            rel = os.path.relpath(p, _ROOT).replace("\\", "/")
            for bad in ("/data/", "/tmp/", "/logs/", "路灯与星轨",
                        "/归档/", "/archive/", "/分析报告/"):
                self.assertNotIn(bad, "/" + rel, rel)


if __name__ == "__main__":
    unittest.main()
