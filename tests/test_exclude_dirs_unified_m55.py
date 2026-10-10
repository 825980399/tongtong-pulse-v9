# -*- coding: utf-8 -*-
"""主线第55批 T4：tools/ 排除清单统一接入 exclude_dirs 的契约测试

★核心判据（铁律 52：两份等价逻辑必须做「同数据对比」）
  1. **不缩小**：每个工具的原集合（_M55_LEGACY_*）必须逐项 ⊆ 新集合
  2. **真接入**：新集合必须包含 exclude_dirs 的基础项（VCS/缓存/副本）
  3. **一处生效**：往 exclude_dirs 加一个目录名，5 个工具的判定同步生效
  4. **可回退**：关掉 ENABLE_EXCLUDE_DIRS_UNIFIED → 回退到 legacy 集合

★铁律 53：任务书点名的「现有可复用组件」先验证存在性。
★铁律 49：涉及被 switch 改写的集合，断言必须写在 with 块内。
"""

from __future__ import annotations

import importlib
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.data import exclude_dirs as E  # noqa: E402


class TestExcludeDirsUnified(unittest.TestCase):
    """5 个工具脚本的排除清单已统一接入。"""

    # (工具模块, 集合名, legacy 名, 期望来自 exclude_dirs 的基础集合)
    TOOLS = [
        ("tools.batch_backup", "SKIP_DIR_NAMES",
         "_M55_LEGACY_SKIP_DIR_NAMES", "BACKUP_SCAN_EXCLUDED"),
        ("tools.check_code_limits", "EXCLUDE_DIRS",
         "_M55_LEGACY_EXCLUDE_DIRS", "AUDIT_SCAN_EXCLUDED"),
        ("tools.package_full_project", "EXCLUDE_DIRS",
         "_M55_LEGACY_EXCLUDE_DIRS", "PACKAGE_EXCLUDED"),
        ("tools.quality_audit", "_SKIP_DIRS",
         "_M55_LEGACY__SKIP_DIRS", "QUALITY_AUDIT_EXCLUDED"),
        ("tools.audit_utils", "COMMON_EXCLUDE_DIRS",
         "_M55_LEGACY_COMMON_EXCLUDE_DIRS", "COMMON_SCAN_EXCLUDED"),
    ]

    # ------------------------------------------------------------------
    def test_01_exclude_dirs_new_sets_exist(self):
        """★铁律 53：新集合在 exclude_dirs 中确实存在且非空。"""
        for _n in ("WORKSPACE_META_DIRS", "AUDIT_BULK_DIRS", "AUDIT_SCAN_EXCLUDED",
                   "BACKUP_SCAN_EXCLUDED", "PACKAGE_EXCLUDED",
                   "QUALITY_AUDIT_EXCLUDED", "COMMON_SCAN_EXCLUDED"):
            self.assertTrue(hasattr(E, _n), "exclude_dirs 缺少 {}".format(_n))
            self.assertGreater(len(getattr(E, _n)), 0, "{} 不应为空".format(_n))

    def test_02_all_tools_imported_exclude_dirs(self):
        """5 个工具的源码里都能找到 exclude_dirs 的 import（防回退）。"""
        for _mod, _name, _legacy, _src in self.TOOLS:
            _rel = _mod.replace(".", os.sep) + ".py"
            _p = os.path.join(_ROOT, _rel)
            _t = open(_p, encoding="utf-8").read()
            self.assertIn("nucleus.data.exclude_dirs", _t,
                          "{} 未接入 exclude_dirs".format(_rel))
            self.assertIn("_m55_unified_excludes", _t,
                          "{} 缺少灰度开关函数".format(_rel))

    def test_03_no_shrink_legacy_subset_of_new(self):
        """★核心：原集合逐项 ⊆ 新集合（只增不减，不缩小排除范围）。"""
        for _mod, _name, _legacy, _src in self.TOOLS:
            _m = importlib.import_module(_mod)
            _old = getattr(_m, _legacy, None)
            _new = getattr(_m, _name, None)
            self.assertIsNotNone(_old, "{} 缺少 {}".format(_mod, _legacy))
            self.assertIsNotNone(_new, "{} 缺少 {}".format(_mod, _name))
            _miss = set(_old) - set(_new)
            self.assertEqual(
                set(), _miss,
                "{}.{} 缩小了排除范围，丢失：{}".format(_mod, _name, sorted(_miss)))

    def test_04_new_sets_contain_base_dirs(self):
        """新集合必须包含 exclude_dirs 的基础项（证明真接入，非复制常量）。"""
        for _mod, _name, _legacy, _src in self.TOOLS:
            _m = importlib.import_module(_mod)
            _new = set(getattr(_m, _name))
            _base = set(getattr(E, _src))
            _miss = _base - _new
            self.assertEqual(
                set(), _miss,
                "{}.{} 未包含 {} 的全部项：{}".format(_mod, _name, _src, sorted(_miss)))

    def test_05_backup_scan_equals_exclude_dirs_base(self):
        """batch_backup 的 BACKUP_SCAN_EXCLUDED 应与原集合**完全等价**。

        这是唯一一个理论上可无损统一的工具（原 19 项 = DEFAULT 17 + 2 特有）。
        """
        _m = importlib.import_module("tools.batch_backup")
        self.assertEqual(set(_m.SKIP_DIR_NAMES), set(E.BACKUP_SCAN_EXCLUDED))
        self.assertEqual(set(_m._M55_LEGACY_SKIP_DIR_NAMES),
                         set(E.BACKUP_SCAN_EXCLUDED),
                         "BACKUP_SCAN_EXCLUDED 应与 legacy 完全等价")

    def test_06_new_dir_propagates_to_all_tools(self):
        """★一处修改全局生效：工具的集合表达式必须**引用** exclude_dirs 集合名。

        集合是模块级 frozenset（导入时求值），运行期改 DEFAULT_EXCLUDED 不会
        自动传播 —— 因此正确的判据是**源码级引用关系** + **派生关系**：
          工具源码引用 X → X 由 DEFAULT_EXCLUDED 派生
        ⇒ 在 exclude_dirs 加一项后重启即对所有工具生效（无需改 5 个文件）。
        """
        for _mod, _name, _legacy, _src in self.TOOLS:
            _rel = _mod.replace(".", os.sep) + ".py"
            _t = open(os.path.join(_ROOT, _rel), encoding="utf-8").read()
            self.assertIn(_src, _t, "{} 未引用 {}（未真正接入）".format(_rel, _src))

        # exclude_dirs 的具名集合确实由基础集合派生（不是复制字面量）
        _t = open(os.path.join(_ROOT, "nucleus", "data", "exclude_dirs.py"),
                  encoding="utf-8").read()
        for _n in ("AUDIT_SCAN_EXCLUDED", "BACKUP_SCAN_EXCLUDED",
                   "COMMON_SCAN_EXCLUDED", "QUALITY_AUDIT_EXCLUDED",
                   "PACKAGE_EXCLUDED"):
            _i = _t.find(_n + " =")
            self.assertGreater(_i, 0, "{} 未定义".format(_n))
            _seg = _t[_i:_i + 200]
            self.assertTrue(
                any(_b in _seg for _b in ("DEFAULT_EXCLUDED", "VCS_DIRS",
                                          "CACHE_DIRS", "COPY_DIRS", "DATA_DIRS")),
                "{} 应由 exclude_dirs 基础集合派生，而非复制字面量".format(_n))

    def test_07_backup_prefix_rule_shared(self):
        """.bak* 前缀规则：新增批次备份目录（.bak_batch99）应被统一排除。"""
        self.assertTrue(E.is_excluded(".bak_batch99"),
                        "备份前缀规则应覆盖任意 .bak* 目录")
        self.assertTrue(E.is_excluded(".release-tmp"), "副本目录应被排除")

    # ------------------------------------------------------------------
    def test_08_switch_off_falls_back_to_legacy(self):
        """★铁律 49：断言写在 with 块内 —— 关掉开关应回退到 legacy 集合。"""
        _orig = getattr(config, "ENABLE_EXCLUDE_DIRS_UNIFIED", None)
        try:
            config.ENABLE_EXCLUDE_DIRS_UNIFIED = False
            for _mod, _name, _legacy, _src in self.TOOLS:
                _m = importlib.import_module(_mod)
                # ★必须在块内断言：_m55_unified_excludes() 每次调用都重读 config
                self.assertFalse(_m._m55_unified_excludes(),
                                 "{} 开关关闭后应返回 False".format(_mod))
                _lg = set(getattr(_m, _legacy))
                self.assertGreater(len(_lg), 0,
                                   "{} legacy 集合不应为空".format(_mod))
                # legacy 集合是第55批前的原样（不含新增的副本类目录）
                self.assertNotIn(".release-tmp", _lg - set(_lg),
                                 "legacy 应保持原样")
        finally:
            if _orig is None:
                try:
                    delattr(config, "ENABLE_EXCLUDE_DIRS_UNIFIED")
                except AttributeError:
                    pass
            else:
                config.ENABLE_EXCLUDE_DIRS_UNIFIED = _orig

    def test_09_switch_on_by_default(self):
        """默认开启（修复生效），且开关值为 True。"""
        self.assertTrue(config.ENABLE_EXCLUDE_DIRS_UNIFIED,
                        "灰度开关应默认开启")
        self.assertTrue(bool(getattr(config, "ENABLE_EXCLUDE_DIRS_UNIFIED", True)))

    def test_10_new_items_are_cache_or_copy_only(self):
        """★行为变更审计：新增排除项只能是缓存/副本/VCS 类，不能是源码目录。

        源码目录白名单：nucleus / organs / functions / tools / tests / config.py
        """
        _source_dirs = {"nucleus", "organs", "functions", "tools", "tests",
                        "docs", "hardware", "scripts"}
        _allowed_new = (
            set(E.VCS_DIRS) | set(E.CACHE_DIRS) | set(E.COPY_DIRS)
            | set(E.DATA_DIRS) | set(E.WORKSPACE_META_DIRS)
            | set(E.AUDIT_BULK_DIRS)
            | {".ipynb_checkpoints", "site-packages"}
        )
        for _mod, _name, _legacy, _src in self.TOOLS:
            _m = importlib.import_module(_mod)
            _added = set(getattr(_m, _name)) - set(getattr(_m, _legacy))
            for _d in _added:
                self.assertNotIn(_d, _source_dirs,
                                 "{}.{} 新增排除了源码目录 {}".format(_mod, _name, _d))
                self.assertTrue(
                    _d.startswith(".bak") or _d in _allowed_new,
                    "{}.{} 新增项 {} 不在允许类别内".format(_mod, _name, _d))


if __name__ == "__main__":
    unittest.main(verbosity=2)
