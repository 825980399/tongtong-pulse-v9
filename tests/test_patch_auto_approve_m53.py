# -*- coding: utf-8 -*-
"""第53批 T1 门控测试：trust_score 阈值统一（P0-补丁1/3）。

覆盖：
  ① 三处阈值统一为 40（★防漂移：三者必须相等）
  ② 入队自动审批判据（risk<=2 且 trust>=40）
  ③ 阈值运行时读取（非硬编码）→ 改配置即时生效
  ④ EVOLUTION_CONFIG 有意排除在热重载黑名单外（安全设计）
  ⑤ PatchAutoApprover 无生产调用点（deprecated 依据）
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ast
import json
import unittest

import config
import nucleus.evolution.PatchAutoApprover as _paa
from nucleus.evolution.PatchAutoApprover import AUTO_APPROVE_MIN_TRUST

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
              ".release-tmp", ".ruff_cache", ".pytest_cache", "code_backups"}


def _read(rel):
    return open(os.path.join(_ROOT, rel), encoding="utf-8", errors="replace").read()


def _module_const(rel, name):
    """AST 取模块级常量字面量。"""
    tree = ast.parse(_read(rel))
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                and isinstance(n.targets[0], ast.Name) and n.targets[0].id == name:
            return ast.literal_eval(n.value)
    return None


class TestThresholdUnified(unittest.TestCase):
    """★核心：三处信任分门槛必须同值（防再次漂移）。"""

    EXPECTED = 40

    def test_01_config_constant(self):
        self.assertEqual(config.PATCH_AUTO_APPROVE_TRUST_THRESHOLD, self.EXPECTED)

    def test_02_module_constant(self):
        self.assertEqual(AUTO_APPROVE_MIN_TRUST, self.EXPECTED)

    def test_03_evolution_config(self):
        self.assertEqual(
            config.EVOLUTION_CONFIG.get("auto_apply_min_trust"), self.EXPECTED)

    def test_04_all_three_equal(self):
        """★防漂移断言：三处必须相等（不只是各自等于 40）。"""
        a = config.PATCH_AUTO_APPROVE_TRUST_THRESHOLD
        b = AUTO_APPROVE_MIN_TRUST
        c = config.EVOLUTION_CONFIG.get("auto_apply_min_trust")
        self.assertEqual(a, b, "config 常量与 PatchAutoApprover 模块常量不一致")
        self.assertEqual(b, c, "PatchAutoApprover 模块常量与 EVOLUTION_CONFIG 不一致")

    def test_05_runtime_resolver(self):
        self.assertEqual(_paa._trust_threshold(), float(self.EXPECTED))

    def test_06_source_consistency(self):
        """★源码级复核：三处字面量（不依赖 import 缓存）。"""
        self.assertEqual(
            _module_const("config.py", "PATCH_AUTO_APPROVE_TRUST_THRESHOLD"),
            self.EXPECTED)
        self.assertEqual(
            _module_const("nucleus/evolution/PatchAutoApprover.py",
                          "AUTO_APPROVE_MIN_TRUST"),
            self.EXPECTED)

    def test_07_max_risk_unchanged(self):
        self.assertEqual(config.EVOLUTION_CONFIG.get("auto_apply_max_risk"), 2)

    def test_08_auto_apply_enabled_and_narrow_gate(self):
        """★406刀4（2026-10-31 裁决）：总闸由 False 打开，**但必须与窄闸同在**。

        历史：m53 当年裁决「延后第54批，本批不得改动」⇒ 本用例断言 False。
        406 批重新裁决打开，故守卫口径升级为「总闸 ∧ 窄闸」双真：
        只开总闸 = 按 risk_level 直接改源码（S1 关闭要拦的正是这个）；
        只开窄闸 = 通道哑掉。两者缺一都不算安全落地。
        """
        self.assertIs(config.EVOLUTION_CONFIG.get("auto_apply_enabled"), True)
        self.assertIs(getattr(config, "PATCH_M406_NARROW_GATE", None), True,
                      "总闸打开必须以窄闸在位为前提（406刀4）")


class TestEnqueueAutoApprove(unittest.TestCase):
    """入队自动审批判据（与 PatchManager:147-166 同源复算）。"""

    _RISK_MAP = {"极低": 1, "低": 2, "中等": 3, "高": 4}

    def _would(self, risk, trust):
        evo = config.EVOLUTION_CONFIG
        _r = self._RISK_MAP.get(risk, 99) if isinstance(risk, str) else risk
        return (_r <= evo.get("auto_apply_max_risk", 1)) and \
            (float(trust) >= evo.get("auto_apply_min_trust", 60))

    def test_10_low_risk_trust40_passes(self):
        self.assertTrue(self._would("低", 40))

    def test_11_very_low_risk_passes(self):
        self.assertTrue(self._would("极低", 45))

    def test_12_trust_below_threshold_rejected(self):
        self.assertFalse(self._would("低", 39))

    def test_13_high_risk_rejected(self):
        self.assertFalse(self._would("高", 95))

    def test_14_medium_risk_rejected(self):
        self.assertFalse(self._would("中等", 95))

    def test_15_threshold_boundary_exact(self):
        """边界：恰等于门槛应通过（判据是 >=）。"""
        self.assertTrue(self._would("低", config.EVOLUTION_CONFIG["auto_apply_min_trust"]))

    def test_16_history_impact(self):
        """实测对账：修复前 0 条通过 → 修复后 >0 条通过。"""
        p = os.path.join(_ROOT, "data", "patches", "patch_history.json")
        if not os.path.isfile(p):
            self.skipTest("无补丁历史")
        d = json.load(open(p, encoding="utf-8"))
        ps = d["patches"] if isinstance(d, dict) and "patches" in d else d
        _now = sum(1 for x in ps if self._would(x.get("risk_level"), x.get("trust_score", 0)))
        _before = sum(1 for x in ps if self._would(x.get("risk_level"), 0)
                      and float(x.get("trust_score", 0) or 0) >= 60)
        self.assertGreater(_now, 0, "修复后应有补丁可通过入队自动审批")
        self.assertEqual(_before, 0, "修复前（门槛60）应为 0 条")


@pytest.mark.production_data
class TestThresholdSourceWiring(unittest.TestCase):
    """阈值必须**运行时**从 config 读取，而非硬编码。"""

    def _pm_src(self):
        return _read("nucleus/reasoning/PatchManager.py")

    def test_20_reads_evolution_config(self):
        _s = self._pm_src()
        self.assertIn('_evo_cfg_a1.get("auto_apply_min_trust"', _s)
        self.assertIn('_evo_cfg_a1.get("auto_apply_max_risk"', _s)

    def test_21_runtime_import_config(self):
        _s = self._pm_src()
        i = _s.find("_max_risk_a1 = _evo_cfg_a1.get")
        self.assertGreater(i, 0)
        _seg = _s[max(0, i - 900):i]
        self.assertIn("import config as _cfg_a1", _seg,
                      "阈值必须每次入队重新 import config（热生效）")

    def test_22_no_hardcoded_threshold_in_branch(self):
        """入队分支内不得出现裸 `>= 60` 之类的硬编码门槛。"""
        _s = self._pm_src()
        i = _s.find("_min_trust_a1 = _evo_cfg_a1.get")
        _seg = _s[i:i + 1200]
        self.assertNotIn(">= 60", _seg)

    def test_23_doc_comment_present(self):
        self.assertIn("第53批 T1", self._pm_src())


class TestHotReloadDesign(unittest.TestCase):
    """热重载现状：EVOLUTION_CONFIG 被有意排除（安全设计）。"""

    def test_30_blacklisted_by_design(self):
        _bl = getattr(config, "_HOT_RELOAD_BLACKLIST", set())
        self.assertIn("EVOLUTION_CONFIG", _bl,
                      "★EVOLUTION_CONFIG 必须在热重载黑名单（防远程改代码修改权限）")

    def test_31_config_constant_not_blacklisted(self):
        """PATCH_AUTO_APPROVE_TRUST_THRESHOLD 不在黑名单（可经 config 模块属性热改）。"""
        _bl = getattr(config, "_HOT_RELOAD_BLACKLIST", set())
        self.assertNotIn("PATCH_AUTO_APPROVE_TRUST_THRESHOLD", _bl)

    def test_32_callback_registry_exists(self):
        self.assertTrue(callable(getattr(config, "register_hot_reload_callback", None)))


class TestPatchAutoApproverDeprecated(unittest.TestCase):
    """星轨裁决2·选项B：PatchAutoApprover 短期 deprecated（无生产调用点）。"""

    def _iter_py(self):
        for dp, dns, fns in os.walk(_ROOT):
            dns[:] = [x for x in dns if x not in _SKIP_DIRS and not x.startswith(".bak")]
            for f in fns:
                if f.endswith(".py"):
                    yield os.path.join(dp, f)

    #: ★408 刀4：判「生产调用点」的四个符号（类名 + 三个模块级函数）
    _CALL_NAMES = frozenset(("scan_pending", "prune_pending",
                             "get_patch_auto_approver", "PatchAutoApprover"))

    @classmethod
    def _call_sites(cls, tree):
        """AST 里**真的调用**了这四个符号的位置（`Call` 节点，不是"文本里出现过"）。"""
        _out = []
        for _n in ast.walk(tree):
            if not isinstance(_n, ast.Call):
                continue
            _f = _n.func
            _nm = _f.id if isinstance(_f, ast.Name) else getattr(_f, "attr", "")
            if _nm in cls._CALL_NAMES:
                _out.append((_n.lineno, _nm))
        return _out

    def test_40_no_production_call_sites(self):
        """★核心依据：classify/scan_pending/prune_pending/get_patch_auto_approver 无生产调用点。

        ★408 刀4 改 AST 口径：原实现是 `if _pat in _t` **子串**匹配，被
          `tools/ci/replay_conditional_writes.py:82` 的**回放数据串**
          （JSON 里写着 `PatchAutoApprover(_base_dir=…)`）判红 ——
          子串匹配分不清"调用"与"提到名字"。查调用点就该看 `ast.Call`。
        ★同时补两条防腐：① 解析失败的文件不许被悄悄跳过（跳过 = 少扫一个
          还宣布"没有调用点"）；② 扫描面下限，防目录结构一变就空转成假绿。
        """
        _self_rel = os.path.join("nucleus", "evolution", "PatchAutoApprover.py")
        _prod = []
        _parse_err = []
        _scanned = 0
        for fp in self._iter_py():
            rel = os.path.relpath(fp, _ROOT)
            # ★第54批：tmp/ 是排查/补丁脚本目录（ruff.toml 亦 exclude），**不是生产代码**
            if rel == _self_rel or rel.startswith(("tests" + os.sep, "tmp" + os.sep)):
                continue
            _scanned += 1
            _t = open(fp, encoding="utf-8", errors="replace").read()
            try:
                _tree = ast.parse(_t)
            except SyntaxError as _e:
                _parse_err.append("%s:%s" % (rel, _e.lineno))
                continue
            for _ln, _nm in self._call_sites(_tree):
                _prod.append((rel, _ln, _nm))
        self.assertEqual(_parse_err, [],
                         "有文件解析失败 → 扫描不完整：%s" % _parse_err)
        self.assertGreater(_scanned, 200,
                           "扫描面 %d 太小，断言可能是空转" % _scanned)
        self.assertEqual(_prod, [], "PatchAutoApprover 出现生产调用点 → deprecated 结论失效")

    def test_41_module_docstring_marks_deprecated(self):
        _doc = ast.get_docstring(ast.parse(_read("nucleus/evolution/PatchAutoApprover.py"))) or ""
        self.assertIn("deprecated", _doc)
        self.assertIn("第53批", _doc)

    def test_42_classify_docstring_updated(self):
        """classify 的组合判定文档串需说明门槛已统一。"""
        _s = _read("nucleus/evolution/PatchAutoApprover.py")
        self.assertIn("AUTO_APPROVE_MIN_TRUST = 40", _s)


if __name__ == "__main__":
    unittest.main()
