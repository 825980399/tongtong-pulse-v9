# -*- coding: utf-8 -*-
"""第49批 T4/T5 门控测试

T4：path_utils 接入（第48批已接 3 处 + 本批新增 2 处）
T5：exclude_dirs 统一来源（★语义等价性：三模块排除集必须与原定义逐字一致）
"""
import ast
import importlib
import io
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.data import exclude_dirs as _ed  # noqa: E402
from nucleus.data.path_utils import safe_relpath, same_drive  # noqa: E402

# ★包 `__init__` 导出的同名符号是**类**，`from pkg import X` 会拿到类而非模块
#   （项目既有坑，第18/22/26批均踩过）→ 取模块必须用 importlib。
_cg = importlib.import_module("nucleus.self_awareness.CallGraphAnalyzer")
_fl = importlib.import_module("nucleus.self_awareness.FakeLoopDetector")
_pc = importlib.import_module("nucleus.self_awareness.ProductionConsumptionMatcher")

# ---------------------------------------------------------------------------
# ★第53批修复：基线不再依赖 `.bak_batch49`
#   背景：`.bak_batch49` 是 git-ignored 的 scratch 目录（`.gitignore` 含
#   `.bak_batch*/`），已于 2026-09-14 22:52 的外部清理中与其余 `.bak_batch*`
#   一并删除 → 本类 4 例「语义等价性」校验全部失效（取不到基线）。
#   处置：把 pre-batch-49 的**原始集合固化为常量**，来源可复现：
#       git show 62c94fa:<path>      # 62c94fa = 「第23-43批累计变更」
#   已用 `git diff --stat 62c94fa 2f07bdc -- <这 3 个文件>` 核实：它们在
#   43→53 之间**仅**被第49批 T5（统一来源改造）改过 ⇒ 62c94fa 版本即基线。
#   固化后本校验不再依赖任何可被清理的目录；豁免强度不降（仍为 `==`）。
# ---------------------------------------------------------------------------
_BASE49_FLE_EXCLUDE = frozenset([
    ".git", ".pytest_cache", "__pycache__", "data", "logs", "models",
    "node_modules", "tests", "tmp", "venv",
])
_BASE49_PCM_EXCLUDE = frozenset([
    ".git", ".pytest_cache", "__pycache__", "data", "logs", "models",
    "node_modules", "tests", "tmp", "venv",
])
_BASE49_PCM_DISK_EXCLUDE = frozenset([
    ".git", ".pytest_cache", ".ruff_cache", "__pycache__", "code_backups",
    "hardware", "models", "node_modules", "tmp", "venv",
])
_BASE49_CGA_EXCLUDE = frozenset([
    ".git", ".pytest_cache", ".venv", "__pycache__", "data", "docs",
    "hardware", "logs", "models", "node_modules", "test", "tests", "tmp",
    "venv",
])

_BAK = os.path.join(_ROOT, ".bak_batch49")


def _read(p):
    return io.open(p, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")


def _old_set(rel_path, var_name):
    """从 .bak_batch49 基线中用 AST 取回**改动前**的集合字面量。

    ★注意 `frozenset((...))` 是 Call 节点而非字面量 → 需取内层 tuple
      （否则会静默返回 None，把真实校验变成 skip）。
    """
    p = os.path.join(_BAK, rel_path.replace("/", os.sep))
    if not os.path.isfile(p):
        return None
    tree = ast.parse(_read(p))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if not (isinstance(t, ast.Name) and t.id == var_name):
                continue
            v = node.value
            try:
                if (isinstance(v, ast.Call)
                        and getattr(v.func, "id", "") == "frozenset"
                        and v.args):
                    return set(ast.literal_eval(v.args[0]))
                return set(ast.literal_eval(v))
            except Exception:
                return None
    return None


# ============================================================ T5 语义等价
class TestExcludeSetsEquivalent(unittest.TestCase):
    """★核心：集中来源不得改变任何模块的排除语义。"""

    def test_10_source_scan_dirs_matches_fakeloop_original(self):
        """★第53批：基线改为固化常量（原读 .bak_batch49 —— 该 scratch 目录已被清理）。"""
        self.assertEqual(set(_fl._EXCLUDE_DIRS), set(_BASE49_FLE_EXCLUDE),
                         "FakeLoopDetector 排除集被改变")

    def test_11_source_scan_dirs_matches_pcm_original(self):
        self.assertEqual(set(_pc._EXCLUDE_DIRS), set(_BASE49_PCM_EXCLUDE),
                         "ProductionConsumptionMatcher 排除集被改变")

    def test_12_disk_exclude_matches_pcm_original(self):
        self.assertEqual(set(_pc._DISK_EXCLUDE_DIRS), set(_BASE49_PCM_DISK_EXCLUDE),
                         "磁盘通道排除集被改变")

    def test_13_call_graph_matches_original(self):
        self.assertEqual(set(_cg._EXCLUDE_DIRS), set(_BASE49_CGA_EXCLUDE),
                         "CallGraphAnalyzer 排除集被改变")

    def test_16_bak_corroboration_when_available(self):
        """★额外佐证：`.bak_batch49` 仍存在时，与固化常量逐字比对（双通道确认）。

        目录缺失 → 跳过（**该 scratch 目录本就可能被清理**，不作为失败）。
        """
        if not os.path.isdir(_BAK):
            self.skipTest(".bak_batch49 已被清理（scratch），固化常量已独立生效")
        for _rel, _var, _const in (
                ("nucleus/self_awareness/FakeLoopDetector.py", "_EXCLUDE_DIRS",
                 _BASE49_FLE_EXCLUDE),
                ("nucleus/self_awareness/ProductionConsumptionMatcher.py",
                 "_EXCLUDE_DIRS", _BASE49_PCM_EXCLUDE),
                ("nucleus/self_awareness/ProductionConsumptionMatcher.py",
                 "_DISK_EXCLUDE_DIRS", _BASE49_PCM_DISK_EXCLUDE),
                ("nucleus/self_awareness/CallGraphAnalyzer.py", "_EXCLUDE_DIRS",
                 _BASE49_CGA_EXCLUDE)):
            _old = _old_set(_rel, _var)
            if _old is None:
                self.fail("基线目录存在但集合未提取到 → 佐证失效")
            self.assertEqual(_old, set(_const), "%s 与固化常量不一致" % _var)

    def test_14_call_graph_is_superset_of_source(self):
        self.assertTrue(_ed.SOURCE_SCAN_DIRS <= _ed.CALL_GRAPH_EXCLUDED)

    def test_15_disk_channel_contains_data_logs(self):
        """★磁盘通道必须**含** data/logs（它们正是数据目录）。"""
        self.assertIn("data", _ed.DATA_DIRS)
        self.assertNotIn("data", _ed.DISK_SCAN_EXCLUDED)
        self.assertNotIn("logs", _ed.DISK_SCAN_EXCLUDED)

    def test_16_source_channel_excludes_data_logs(self):
        self.assertIn("data", _ed.SOURCE_SCAN_DIRS)
        self.assertIn("logs", _ed.SOURCE_SCAN_DIRS)


# ============================================================ T5 接线
class TestExcludeWiring(unittest.TestCase):
    def test_20_imports_used(self):
        for rel in ("nucleus/self_awareness/CallGraphAnalyzer.py",
                    "nucleus/self_awareness/FakeLoopDetector.py",
                    "nucleus/self_awareness/ProductionConsumptionMatcher.py"):
            src = _read(os.path.join(_ROOT, rel))
            self.assertIn("from nucleus.data.exclude_dirs import", src, rel)

    def test_21_no_local_set_definitions_left(self):
        """★任务书诉求：各模块不再各自定义排除集合。"""
        for rel, var in (
            ("nucleus/self_awareness/CallGraphAnalyzer.py", "_EXCLUDE_DIRS"),
            ("nucleus/self_awareness/FakeLoopDetector.py", "_EXCLUDE_DIRS"),
            ("nucleus/self_awareness/ProductionConsumptionMatcher.py", "_EXCLUDE_DIRS"),
            ("nucleus/self_awareness/ProductionConsumptionMatcher.py", "_DISK_EXCLUDE_DIRS"),
        ):
            src = _read(os.path.join(_ROOT, rel))
            self.assertNotIn("%s = {" % var, src, "%s 仍有本地集合字面量" % rel)
            self.assertNotIn("%s = frozenset((" % var, src, "%s 仍有本地集合字面量" % rel)

    def test_22_module_import_still_works(self):
        """三个模块可导入（无循环导入 / 无 NameError）。"""
        self.assertTrue(hasattr(_cg, "_EXCLUDE_DIRS"))
        self.assertTrue(hasattr(_fl, "_EXCLUDE_DIRS"))
        self.assertTrue(hasattr(_pc, "_EXCLUDE_DIRS"))

    def test_23_describe_contract(self):
        d = _ed.describe()
        for k in ("vcs", "cache", "copy", "data", "backup_prefixes"):
            self.assertIn(k, d)


# ============================================================ T4 path_utils
class TestPathUtilsWiring(unittest.TestCase):
    CALLERS = [
        "tools/tmp_backup.py",
        "tools/serp_pollution_analyzer.py",
        "tools/backfill_patch_verification_split.py",
        "tools/audit_utils.py",
        "tmp/test_isolation.py",
    ]

    def test_30_all_callers_import(self):
        _checked = 0
        for rel in self.CALLERS:
            _p = os.path.join(_ROOT, rel)
            if not os.path.isfile(_p):
                # git-ignored 易失件缺失时跳过该项（与 m18/m19/m22 一致）
                continue
            src = _read(_p)
            # ★第169批 C2：归一入口 normalize_relpath 亦为合法接线形态
            #   （其内委托 safe_relpath，跨盘安全语义不变）→ 两者皆可。
            self.assertTrue(
                ("nucleus.data.path_utils import safe_relpath" in src)
                or ("nucleus.data.path_utils import normalize_relpath" in src),
                "%s 未从 nucleus.data.path_utils 接入安全入口" % rel)
            _checked += 1
        self.assertGreater(_checked, 0, "无任何调用方可供校验")

    def test_31_at_least_five_callers(self):
        """★任务书前提（零调用）不成立：第48批已接 3 处，本批 +2。
        B156-1 T-A03：第 5 个调用方 tmp/test_isolation.py 为 git-ignored 易失件，
        缺失时不计入（与 m18/m19/m22 一致降级），仅校验 4 个已跟踪调用方全部在场。"""
        _committed = self.CALLERS[:-1]  # 排除 git-ignored 易失件
        n = 0
        for rel in _committed:
            if os.path.isfile(os.path.join(_ROOT, rel)):
                n += 1
        self.assertGreaterEqual(n, 4, "已跟踪调用方应全部存在")

    def test_32_third_party_import_line_precedes_use(self):
        """audit_utils：导入必须在使用之前（E402 安全）。"""
        src = _read(os.path.join(_ROOT, "tools/audit_utils.py"))
        # ★第169批 C2：audit_utils 改走 normalize_relpath（归一入口，内委托 safe_relpath）
        _imp = max(src.find("from nucleus.data.path_utils import safe_relpath"),
                   src.find("from nucleus.data.path_utils import normalize_relpath"))
        _use = max(src.find("rel = safe_relpath(dp, root)"),
                   src.find("rel = normalize_relpath(dp, root)"))
        self.assertGreaterEqual(_imp, 0, "未找到 path_utils 导入行")
        self.assertGreaterEqual(_use, 0, "未找到调用行")
        self.assertLess(_imp, _use)

    def test_33_same_drive_unchanged_semantics(self):
        """同盘时与原生 `os.path.relpath` 完全一致（零行为变化）。"""
        p = os.path.join(_ROOT, "nucleus", "data", "path_utils.py")
        self.assertEqual(safe_relpath(p, _ROOT),
                         os.path.relpath(p, _ROOT))

    def test_34_cross_drive_degrades(self):
        """跨盘时降级为绝对路径且不抛异常。"""
        if same_drive(os.path.abspath(_ROOT), "C:\\"):
            self.skipTest("当前环境项目不在 D: 盘")
        import tempfile
        _t = tempfile.gettempdir()
        try:
            _r = safe_relpath(_t, _ROOT)
        except Exception as e:
            self.fail("safe_relpath 不应抛异常: %s" % e)
        self.assertTrue(os.path.isabs(_r))

    def test_35_no_bare_relpath_in_audit_utils_walk(self):
        src = _read(os.path.join(_ROOT, "tools/audit_utils.py"))
        self.assertNotIn('os.path.relpath(dp, root)', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
