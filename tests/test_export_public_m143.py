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
        # ★第145批 T-145b：内部总账 `完整进化路线与技术债务清单_v1.0.md` 已移出白名单
        #   （该文档含批次交付确认/债务编号/第三方评分等内部运行资料），
        #   现断言其**被拒绝**（见 test_03）。
        for rel in ("README.md", "demo-quickstart.md",
                    "项目架构与结构总览_20261003.md",
                    "SECURITY.md", "CONTRIBUTING.md", "CHANGELOG.md"):
            self.assertTrue(ep.docs_allowed(rel), rel)

    def test_02_allow_dirs(self):
        # ★第152批 T152-2：`工具类文档` 整目录已从 PUBLIC_DOCS_ALLOW_DIRS 移除
        #   （目录级白名单收紧为精确文件白名单，docs_allowed 保持 fail-closed）
        #   ⇒ 第153批 T153-3 同步本断言：白名单内两项仍放行，被移除项改判拒绝。
        for rel in ("比赛准备/运行数据卡片_20260927.md",
                    "设计文档/某设计_v1.0.md"):
            self.assertTrue(ep.docs_allowed(rel), rel)
        # fail-closed：未精确列入白名单的目录一律拒绝
        self.assertFalse(ep.docs_allowed("工具类文档/某说明.md"))

    def test_03_unlisted_file_rejected(self):
        # 未在白名单里的根级文档 → 拒绝（fail-closed）
        # ★第145批 T-145b：内部总账加入本列表（原在白名单，属越权公开）
        for rel in ("第三方全面分析报告_20260926.md",
                    "死代码检测报告_8大模块_v2.0.md",
                    "git_commit_hash_mapping.md",
                    "完整进化路线与技术债务清单_v1.0.md"):
            self.assertFalse(ep.docs_allowed(rel), rel)

    def test_04_internal_dir_rejected(self):
        for rel in ("路灯与星轨对话/交付报告/x.md", "分析报告/x.csv",
                    "归档/x.md", "archive/x.md", "验收/x.md",
                    "审查报告/x.md", "性能报告/x.md", "台账/x.csv"):
            self.assertFalse(ep.docs_allowed(rel), rel)


    def test_05_root_public_files_in_package(self):
        # ★第167批 C6：仓根对外文档（SECURITY/CONTRIBUTING/CHANGELOG）须稳定进发布包；
        #   经 PUBLIC_ROOT_FILES 显式匹配，即便未来从 docs 白名单移除也不影响仓根放行。
        for rel in ("SECURITY.md", "CONTRIBUTING.md", "CHANGELOG.md"):
            self.assertTrue(ep.docs_allowed(rel), rel)
            self.assertFalse(ep.should_skip(rel), rel)


class TestPiiScan(unittest.TestCase):
    """PII 复扫器行为。

    注意：本类用**运行时拼接**构造测试串，避免源码中出现真实 PII 字面量
    （否则 TestRealTree.test_01 会扫到本文件自身）。
    """

    @staticmethod
    def _name_gl() -> str:
        return "测试甲"          # 合成测试值（替代真实姓名，防转义形态泄露）

    @staticmethod
    def _phone() -> str:
        return "138" + "1234" + "5678"        # 构造的假手机号

    def test_01_real_name_flagged(self):
        import json
        import tempfile
        # Dxxx-1：真实姓名模式改由脱敏配置（PULSE_OWNER_NAMES）加载，注入后验证。
        # Dxxx-13：隔离本地真实 .owner_pii.json，使合并来源确定（仅 env 注入），
        #          避免依赖/泄露真实属主配置导致断言失真。
        old = os.environ.get("PULSE_OWNER_NAMES")
        old_file = os.environ.get("PULSE_OWNER_PII_FILE")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as cf:
            json.dump({"names": [], "path_hints": []}, cf)
            cfg = cf.name
        os.environ["PULSE_OWNER_NAMES"] = self._name_gl()
        os.environ["PULSE_OWNER_PII_FILE"] = cfg
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                             encoding="utf-8") as fh:
                fh.write("创建者是" + self._name_gl() + "\n")
                p = fh.name
            try:
                hits = ep.scan_text(p)
                self.assertTrue(any("真名" in h[0] for h in hits), hits)
            finally:
                os.unlink(p)
        finally:
            if old is None:
                os.environ.pop("PULSE_OWNER_NAMES", None)
            else:
                os.environ["PULSE_OWNER_NAMES"] = old
            if old_file is None:
                os.environ.pop("PULSE_OWNER_PII_FILE", None)
            else:
                os.environ["PULSE_OWNER_PII_FILE"] = old_file
            os.unlink(cfg)

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
        import json
        import tempfile
        # Dxxx-1：真实路径模式改由脱敏配置（PULSE_OWNER_PATH_HINTS）加载，
        # 注入后验证扫描器对真实项目路径的命中行为。
        # Dxxx-13：隔离本地真实 .owner_pii.json，使合并来源确定（仅 env 注入）。
        old = os.environ.get("PULSE_OWNER_PATH_HINTS")
        old_file = os.environ.get("PULSE_OWNER_PII_FILE")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as cf:
            json.dump({"names": [], "path_hints": []}, cf)
            cfg = cf.name
        os.environ["PULSE_OWNER_PATH_HINTS"] = "C:/test"
        os.environ["PULSE_OWNER_PII_FILE"] = cfg
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                             encoding="utf-8") as fh:
                fh.write('ROOT = "C:/test/demo.md"\n')
                p = fh.name
            try:
                hits = ep.scan_text(p)
                self.assertTrue(any("路径" in h[0] for h in hits), hits)
            finally:
                os.unlink(p)
        finally:
            if old is None:
                os.environ.pop("PULSE_OWNER_PATH_HINTS", None)
            else:
                os.environ["PULSE_OWNER_PATH_HINTS"] = old
            if old_file is None:
                os.environ.pop("PULSE_OWNER_PII_FILE", None)
            else:
                os.environ["PULSE_OWNER_PII_FILE"] = old_file
            os.unlink(cfg)

    def test_05_export_script_self_not_exempt(self):
        # Dxxx-1：扫描器自身不得再豁免，且源码内零真值（自复扫必过）。
        self.assertNotIn("tools/export_public.py", ep.SCAN_EXEMPT_FILES)
        here = os.path.join(_ROOT, "tools", "export_public.py")
        self.assertEqual(ep.scan_text(here), [])


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
