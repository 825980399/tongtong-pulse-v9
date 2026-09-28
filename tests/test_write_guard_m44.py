# -*- coding: utf-8 -*-
"""第44批 T4 门控测试：测试环境污染防护推广（写盘守卫）。

三个推广组件 × 4 例（生产可写 / 测试拒写 / 显式注入可写 / 隔离目录验证）：
* 经验库 `ExperiencePool`
* 补丁历史 `PatchManager`
* 知识库 `PulseSnapshot`

★测试纪律：**任何用例都不得向生产 `data/` 写入** ——
「生产可写」一律在**谓词层**验证（`guard_write(...) is True`），
不真的触发一次生产落盘。
"""
import glob
import pytest
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.data import write_guard as wg  # noqa: E402
from nucleus.mnemosyne.experience_pool import ExperiencePool  # noqa: E402
from nucleus.reasoning.PatchManager import PatchManager  # noqa: E402
from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot  # noqa: E402

_PROD_DATA = os.path.join(_ROOT, "data")
_PROD_EXP = os.path.join(_PROD_DATA, "experience", "experience_pool.json")


class _GuardBase(unittest.TestCase):
    def setUp(self):
        self._orig_test_env = wg.is_test_env
        # ★第51批 T3：新增进程识别打桩点（契约变更后需显式控制）
        self._orig_fw = wg.is_framework_process
        self._orig_guard = getattr(config, "ENABLE_TEST_ENV_WRITE_GUARD", True)
        self._tmp = tempfile.mkdtemp(prefix="m44_t4_")
        wg.reset_warned()

    def tearDown(self):
        wg.is_test_env = self._orig_test_env
        wg.is_framework_process = self._orig_fw
        config.ENABLE_TEST_ENV_WRITE_GUARD = self._orig_guard
        wg.reset_warned()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _set_guard(self, on):
        config.ENABLE_TEST_ENV_WRITE_GUARD = bool(on)

    def _no_test_env(self):
        wg.is_test_env = lambda: False

    def _as_framework(self):
        """★第51批 T3：把当前进程伪装为**框架主进程**（可信写入者）。"""
        wg.is_framework_process = lambda: True

    def _as_plain_script(self):
        """★第51批 T3：把当前进程伪装为**普通脚本**（非可信写入者）。"""
        wg.is_framework_process = lambda: False


# ---------------------------------------------------------------------------
# 0) 守卫模块本体
# ---------------------------------------------------------------------------
class TestWriteGuardModule(_GuardBase):
    def test_01_enabled_by_default(self):
        self.assertTrue(wg.guard_enabled())

    def test_02_production_path_predicate(self):
        self.assertTrue(wg.is_production_data_path(_PROD_EXP))
        self.assertFalse(wg.is_production_data_path(
            os.path.join(_ROOT, "organs", "body", "PulseLung.py")))
        self.assertFalse(wg.is_production_data_path(
            os.path.join(self._tmp, "a.json")))

    def test_03_reject_in_test_env_production(self):
        self.assertTrue(wg.reject_write(_PROD_EXP))

    def test_04_explicit_injection_allows(self):
        self.assertFalse(wg.reject_write(_PROD_EXP, explicit=True))

    def test_05_guard_off_allows(self):
        self._set_guard(False)
        self.assertFalse(wg.reject_write(_PROD_EXP))

    def test_06_non_test_env_script_rejected(self):
        """★第51批 T3 契约变更：非测试环境的**普通脚本** → 默认只读（拒写）。

        原第44批契约「非测试环境一律放行」已被 P2-354 有意变更
        （普通脚本进程此前**完全无防护**，可随意写生产 data/）。
        """
        self._no_test_env()
        self._as_plain_script()
        self.assertTrue(wg.reject_write(_PROD_EXP),
                        "普通脚本进程应默认只读（P2-354）")

    def test_06b_framework_env_allows(self):
        """★第51批 T3：**框架主进程** → 允许写（新契约下的正向断言）。"""
        self._no_test_env()
        self._as_framework()
        self.assertFalse(wg.reject_write(_PROD_EXP))

    def test_07_guard_write_logs_once_and_never_raises(self):
        self.assertFalse(wg.guard_write(_PROD_EXP, component="UnitTest"))
        # 第二次同样拒写（告警去重，不抛异常）
        self.assertFalse(wg.guard_write(_PROD_EXP, component="UnitTest"))

    def test_08_empty_path_not_production(self):
        self.assertFalse(wg.is_production_data_path(""))


# ---------------------------------------------------------------------------
# 1) 经验库 ExperiencePool
# ---------------------------------------------------------------------------
@pytest.mark.production_data
class TestExperiencePoolGuard(_GuardBase):
    def _light(self, pool_file, explicit=False):
        """构造轻量实例（绕 __init__），只挂 _save 需要的属性。"""
        p = ExperiencePool.__new__(ExperiencePool)
        p._m44_base_dir_explicit = explicit
        p._base_dir = os.path.dirname(pool_file)
        p._pool_file = pool_file
        p._experiences = []
        p._total_recorded = 0
        p._total_summarized = 0
        p._total_decayed = 0
        return p

    def test_10_isolated_dir_writes(self):
        fp = os.path.join(self._tmp, "experience_pool.json")
        self._light(fp)._save()
        self.assertTrue(os.path.isfile(fp))
        self.assertIn("experiences", json.load(io.open(fp, encoding="utf-8")))

    def test_11_test_env_production_rejected(self):
        """★判据修正（第45批）：不能断言"生产文件 mtime 不变"。

        ★真因：**曈曈框架正在运行**，会持续写 `data/experience/experience_pool.json`
        → mtime 会被外部改动，旧断言在本环境下**必然误报**。
        改为判定「**我们的写入没有落地**」：
        1. `_save()` 不产生 `<pool_file>.tmp` 暂存文件（该文件只由 `_save()` 创建）；
        2. 生产文件的 `experiences` 记录数**未因本次调用被清零**。
        """
        import json as _json
        def _count():
            try:
                with io.open(_PROD_EXP, encoding="utf-8") as _f:
                    _d = _json.load(_f)
                return len(_d.get("experiences", [])) if isinstance(_d, dict) else -1
            except Exception:
                return None
        _before = _count()
        p = self._light(_PROD_EXP, explicit=False)
        p._save()
        # 1) 未产生暂存文件（守卫在 makedirs 前就 return）
        self.assertFalse(os.path.exists(_PROD_EXP + ".tmp"),
                         "★守卫失效：产生了 .tmp 暂存文件")
        # 2) 记录数未因我们的空池写入而被清零
        _after = _count()
        if _before is not None and _before > 0:
            self.assertNotEqual(_after, 0,
                                "★守卫失效：生产记录被空池覆盖（%s → %s）"
                                % (_before, _after))

    def test_12_explicit_injection_allows(self):
        self.assertTrue(wg.guard_write(_PROD_EXP, explicit=True,
                                       component="ExperiencePool"))
        p = self._light(_PROD_EXP, explicit=True)
        self.assertTrue(p._m44_base_dir_explicit)

    def test_13_guard_off_restores_old_behavior(self):
        self._set_guard(False)
        self.assertTrue(wg.guard_write(_PROD_EXP, component="ExperiencePool"))

    def test_14_init_records_explicitness(self):
        """显式传 base_dir → 记录为显式；不传 → 非显式（运行时解析默认值）。"""
        p1 = ExperiencePool(base_dir=self._tmp)
        self.assertTrue(p1._m44_base_dir_explicit)
        p2 = ExperiencePool.__new__(ExperiencePool)
        ExperiencePool.__init__(p2)          # 走默认路径（不传参）
        self.assertFalse(p2._m44_base_dir_explicit)
        self.assertTrue(os.path.isabs(p2._base_dir))


# ---------------------------------------------------------------------------
# 2) 补丁历史 PatchManager
# ---------------------------------------------------------------------------
@pytest.mark.production_data
class TestPatchManagerGuard(_GuardBase):
    def _pm(self, patch_dir):
        p = PatchManager.__new__(PatchManager)
        p._patch_dir = patch_dir
        return p

    def test_20_isolated_dir_writes(self):
        p = self._pm(self._tmp)
        _ok = p._save_json(os.path.join(self._tmp, "x.json"), {"a": 1})
        self.assertNotEqual(_ok, False)
        self.assertTrue(os.path.isfile(os.path.join(self._tmp, "x.json")))

    def test_21_test_env_production_rejected(self):
        _probe_dir = os.path.join(_PROD_DATA, "_m44_guard_probe")
        _probe = os.path.join(_probe_dir, "probe.json")
        try:
            p = self._pm(_probe_dir)
            self.assertFalse(p._save_json(_probe, {"a": 1}))
            self.assertFalse(os.path.exists(_probe))
        finally:
            if os.path.isdir(_probe_dir):
                shutil.rmtree(_probe_dir, ignore_errors=True)

    def test_22_restart_counter_rejected_in_production(self):
        _probe_dir = os.path.join(_PROD_DATA, "_m44_guard_probe2")
        try:
            p = self._pm(_probe_dir)
            self.assertFalse(p._save_restart_counter(3))
            self.assertFalse(os.path.exists(
                os.path.join(_probe_dir, "restart_count.txt")))
        finally:
            if os.path.isdir(_probe_dir):
                shutil.rmtree(_probe_dir, ignore_errors=True)

    def test_23_guard_off_allows(self):
        self._set_guard(False)
        self.assertTrue(wg.guard_write(
            os.path.join(_PROD_DATA, "patches", "patch_history.json"),
            component="PatchManager"))

    def test_24_restart_counter_writes_in_isolated_dir(self):
        p = self._pm(self._tmp)
        self.assertTrue(p._save_restart_counter(5))
        _fp = os.path.join(self._tmp, "restart_count.txt")
        self.assertEqual(io.open(_fp, encoding="utf-8").read().strip(), "5")


# ---------------------------------------------------------------------------
# 3) 知识库 PulseSnapshot
# ---------------------------------------------------------------------------
class TestPulseSnapshotGuard(_GuardBase):
    def _ps(self, snapshot_path):
        p = PulseSnapshot.__new__(PulseSnapshot)
        p.snapshot_path = snapshot_path
        return p

    def test_30_production_path_rejected(self):
        p = self._ps(os.path.join(_PROD_DATA, "knowledge",
                                  "pulse_knowledge_snapshot.json"))
        self.assertFalse(p._m44_write_allowed())

    def test_31_isolated_path_allowed(self):
        p = self._ps(os.path.join(self._tmp, "snap.json"))
        self.assertTrue(p._m44_write_allowed())

    def test_32_guard_off_allows(self):
        self._set_guard(False)
        p = self._ps(os.path.join(_PROD_DATA, "knowledge",
                                  "pulse_knowledge_snapshot.json"))
        self.assertTrue(p._m44_write_allowed())

    def test_33_non_test_env_script_rejected(self):
        """★第51批 T3 契约变更：普通脚本进程 → PulseSnapshot 不得写生产。"""
        self._no_test_env()
        self._as_plain_script()
        p = self._ps(os.path.join(_PROD_DATA, "knowledge",
                                  "pulse_knowledge_snapshot.json"))
        self.assertFalse(p._m44_write_allowed())

    def test_33b_framework_env_allows(self):
        """★第51批 T3：框架主进程 → PulseSnapshot 允许写（正向断言）。"""
        self._no_test_env()
        self._as_framework()
        p = self._ps(os.path.join(_PROD_DATA, "knowledge",
                                  "pulse_knowledge_snapshot.json"))
        self.assertTrue(p._m44_write_allowed())

    def test_34_save_returns_false_in_test_env(self):
        """save() 在测试环境 + 生产路径下必须直接返回 False（不落盘）。"""
        p = PulseSnapshot.__new__(PulseSnapshot)
        p.snapshot_path = os.path.join(_PROD_DATA, "knowledge",
                                       "pulse_knowledge_snapshot.json")
        self.assertFalse(p.save())


# ---------------------------------------------------------------------------
# 4) 污染归档
# ---------------------------------------------------------------------------
@pytest.mark.production_data
class TestPollutionArchive(unittest.TestCase):
    def test_40_archive_exists_with_manifest(self):
        _base = os.path.join(_ROOT, "data", "_archive", "test_pollution")
        self.assertTrue(os.path.isdir(_base), "归档目录不存在")
        _mans = glob.glob(os.path.join(_base, "*", "MANIFEST.json"))
        self.assertTrue(_mans, "缺少 MANIFEST.json")
        self._man = json.load(io.open(_mans[0], encoding="utf-8"))

    def test_41_manifest_records_moved_and_deferred(self):
        self.test_40_archive_exists_with_manifest()
        self.assertGreaterEqual(len(self._man["moved"]), 1)
        self.assertGreaterEqual(len(self._man["extracted"]), 1)
        self.assertTrue(self._man.get("notes"))

    def test_42_stub_archive_non_empty(self):
        self.test_40_archive_exists_with_manifest()
        _d = os.path.dirname(glob.glob(
            os.path.join(_ROOT, "data", "_archive", "test_pollution",
                         "*", "MANIFEST.json"))[0])
        _stubs = [x for x in self._man["extracted"] if "llm_traces" in x["src"]]
        self.assertTrue(_stubs)
        self.assertGreater(_stubs[0]["stubs"], 0)
        self.assertTrue(os.path.isfile(
            os.path.join(_d, "calls_20260913_test_stubs.jsonl")))

    def test_43_no_switch_off_regression_in_config(self):
        self.assertTrue(getattr(config, "ENABLE_TEST_ENV_WRITE_GUARD", False))


if __name__ == "__main__":
    unittest.main()

# _m51_t3_sync
