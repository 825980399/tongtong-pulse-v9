# -*- coding: utf-8 -*-
"""第116批 T-116b / T-116c / T-116f 语义门禁单测（离线、隔离、不读生产数据）。

设计原则（呼应工程铁律：契约基线须固化进测试文件）：
  - 不读 data/patches 生产账本、不读 tmp/，全部断言基于「源码结构契约」+「tmp 隔离副本」。
  - 源码结构断言用于锁死"生成器不再复污染"这类无法用数据证明的性质。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_SEE_REL = os.path.join("nucleus", "reasoning", "SafeEvolutionExecutor.py")
_CHK_REL = os.path.join("tools", "check_patch_consistency.py")
_ADJ_REL = os.path.join("tools", "adjudicate_patch.py")
_PM_REL = os.path.join("nucleus", "reasoning", "PatchManager.py")


def _read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


class T116bC6WritePoints(unittest.TestCase):
    """T-116b①：C6 污染根因 —— 顶层 runtime_verified 不得无条件置 True。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(_SEE_REL)
        cls.lines = cls.src.split("\n")

    def test_01_no_bare_true_assignment(self):
        """所有 `runtime_verified" = True` 必须紧跟在 status 置 runtime_verified 的通过分支内。"""
        bad = []
        for i, ln in enumerate(self.lines):
            stripped = ln.strip()
            if stripped != '_patch["runtime_verified"] = True':
                continue
            window = "\n".join(self.lines[max(0, i - 3):i])
            if '"status"] = "runtime_verified"' not in window:
                bad.append((i + 1, stripped))
        self.assertEqual([], bad, f"存在无条件置 True 的写点（C6 污染根因）：{bad}")

    def test_02_false_write_points_exist(self):
        """失败分支必须显式写 False，而非沿用旧值。"""
        cnt = sum(1 for ln in self.lines
                  if ln.strip() == '_patch["runtime_verified"] = False')
        self.assertGreaterEqual(
            cnt, 2, f"失败分支显式写 False 的写点应 >= 2，实际 {cnt}")

    def test_03_truth_source_from_nested(self):
        """顶层真值必须来自嵌套 verdict / 本地判据，不得来自常量。"""
        self.assertIn('_patch["runtime_verified"] = bool(_rverdict.get("verified"))', self.src)
        self.assertIn('_patch["runtime_verified"] = bool(_verified_ok)', self.src)

    def test_04_guard_uses_runtime_verify_result(self):
        """语义改为'验证通过'后，跳过守卫必须补判 runtime_verify_result，否则失败补丁每轮重试。"""
        cnt = 0
        for ln in self.lines:
            if '_patch.get("runtime_verified")' in ln and \
                    '_patch.get("runtime_verify_result") is not None' in ln:
                cnt += 1
        self.assertGreaterEqual(
            cnt, 2, f"补判 runtime_verify_result 的守卫应 >= 2 处，实际 {cnt}")


class T116b3ConsistencyGate(unittest.TestCase):
    """T-116b③：CI 断言 —— C6 已属硬失败；脚本需 --no-import 直读退化开关。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(_CHK_REL)

    def test_01_no_import_flag_exists(self):
        self.assertIn('"--no-import"', self.src)
        self.assertIn("dest=\"no_import\"", self.src)

    def test_02_load_ledgers_has_no_import_param(self):
        self.assertIn("def _load_ledgers(root: str, no_import: bool = False):", self.src)

    def test_03_call_site_passes_flag(self):
        self.assertIn("no_import=_args.no_import", self.src)

    def test_04_c6_is_hard_fail(self):
        """C6 必须计入硬失败（退出码 1），否则'入交付门禁'为空话。"""
        self.assertIn('_hard = [i for i in _issues if i["check"] != "C3"]', self.src)


class T116cCliObsoleteChannel(unittest.TestCase):
    """T-116c②/③：keep / reject --source obsolete 必须真的能定位到归档账。"""

    @classmethod
    def setUpClass(cls):
        # 注意：不要把函数挂成类属性——从 self 访问会被绑定成实例方法（多出 self 实参）。
        from tools import adjudicate_patch as _ap
        cls.ap = _ap

    def test_01_find_obsolete(self):
        _obs = [{"id": "zz1", "file": "x.py"}]
        name, _lst, idx, p = self.ap._find([], [], "zz1", "obsolete", obsolete=_obs)
        self.assertEqual("obsolete", name)
        self.assertIsNotNone(p)
        self.assertEqual("zz1", p.get("id"))
        self.assertEqual(0, idx)

    def test_02_find_default_not_search_obsolete(self):
        """未显式指定 source 时，行为保持 114b 原状（不误改旧语义）。"""
        _obs = [{"id": "zz2", "file": "x.py"}]
        name, _lst, _i, p = self.ap._find([], [], "zz2", None, obsolete=_obs)
        self.assertIsNotNone(p, "显式带 obsolete 时仍应兜底搜到")
        self.assertEqual("obsolete", name)

    def test_03_save_ledger_obsolete_writes_given_path(self):
        from nucleus.reasoning.PatchManager import PatchManager
        tmp = tempfile.mkdtemp(prefix="m116c_")
        try:
            pm = PatchManager(tmp)
            dst = os.path.join(tmp, "obsolete_ledger.json")
            ok = self.ap._save_ledger(pm, "obsolete", [{"id": "zz3"}],
                                      obsolete_path=dst)
            self.assertTrue(ok, "obsolete 账写回应成功")
            with open(dst, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual([{"id": "zz3"}], data)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class T116fCreatedAt(unittest.TestCase):
    """T-116f：入队时补记 created_at（此前 pending 全量 0，无法判龄）。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(_PM_REL)

    def test_01_backfill_code_exists(self):
        self.assertIn("if not patch.get(\"created_at\"):", self.src)
        self.assertIn('patch["created_at"] = time.time()', self.src)

    def test_02_backfill_inside_save_pending_patch(self):
        """补记必须落在 save_pending_patch 的元数据补全段（saved_at 之后、status 之前）。"""
        lines = self.src.split("\n")
        try:
            i_fn = next(i for i, ln in enumerate(lines)
                        if ln.startswith("    def save_pending_patch("))
        except StopIteration:
            self.fail("未找到 save_pending_patch 定义")
        seg = "\n".join(lines[i_fn:i_fn + 40])
        self.assertIn("if not patch.get(\"created_at\"):", seg)
        self.assertIn('patch["created_at"] = time.time()', seg)
        i_saved = seg.find('patch["saved_at"] = time.time()')
        i_created = seg.find('patch["created_at"] = time.time()')
        self.assertLess(i_saved, i_created, "created_at 补记应位于 saved_at 之后")


if __name__ == "__main__":
    unittest.main()
