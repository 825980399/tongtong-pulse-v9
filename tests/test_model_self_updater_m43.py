# -*- coding: utf-8 -*-
"""主线第43批 T3（P1-256）门控测试：模型自更新器框架。

覆盖：铁律（内在模型输出永不入训练集）/ 版本管理 / 退化防护回滚 / 检查 / 裁剪 / 开关。
★ 全部使用隔离目录（注入 base_dir），不触碰生产 data/。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import tempfile
import unittest

import config
from nucleus.llm.model_self_updater import (
    DEFAULT_BASE_DIR,
    ORIGIN_INTERNAL_MODEL,
    ModelSelfUpdater,
    get_updater,
    internal_model_origin,
    is_internal_model_output,
    reset_updater,
    training_guard,
    updater_enabled,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def _pytest_tmp_root():
    """★主线第46批 T4（P2-301/307）：测试隔离目录改用【系统临时目录】。

    背景：旧实现 ``mkdtemp(prefix=..., dir=<项目>/tmp)`` 会在项目 ``tmp/`` 下
    持续留下隔离目录（``m41_t2_*`` / ``m43dq_*`` / ``_m27_*`` ...），tearDown
    清不干净时就变成"历史残留"，并与清理工具互相打架。

    现在改为系统临时目录下的 ``pulse_pytest/``：
      * 不污染项目 ``tmp/`` → 清理判据不再需要为它们开特例
      * 由操作系统回收 → 残留不再累积
      * 可用环境变量 ``PULSE_TEST_TMP_ROOT`` 覆盖（调试/隔离用）
    """
    _env = os.environ.get("PULSE_TEST_TMP_ROOT")
    if _env:
        _d = _env
    else:
        # ★与项目同盘：Windows 跨盘 shutil.move 会 copy+unlink →
        #   触发沙箱删除配额（m41 实测教训）。故不用 tempfile.gettempdir()，
        #   改用项目根下的 .pytest_tmp/（同盘 + 不污染 tmp/）。
        _d = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".pytest_tmp")
    os.makedirs(_d, exist_ok=True)
    return _d


_TMP_ROOT = _pytest_tmp_root()


def _safe_rmtree(path):
    if not os.path.isdir(path):
        return
    for dp, dn, fn in os.walk(path, topdown=False):
        for i in range(0, len(fn), 50):
            for f in fn[i:i + 50]:
                try:
                    os.remove(os.path.join(dp, f))
                except BaseException as e:
                    print("cleanup warn: %s: %s" % (type(e).__name__, e))
        try:
            os.rmdir(dp)
        except BaseException as e:
            print("cleanup warn: %s: %s" % (type(e).__name__, e))


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="m43msu_", dir=_TMP_ROOT)
        self.q = {"v": 85.0}
        self.u = ModelSelfUpdater(base_dir=self.dir,
                                  quality_fn=lambda: self.q["v"],
                                  now_fn=lambda: 1700000000.0)

    def tearDown(self):
        _safe_rmtree(self.dir)


# ============================================================ ★铁律
class TestTrainingGuard(unittest.TestCase):
    def test_01_origin_internal_model_is_output(self):
        self.assertTrue(is_internal_model_output({"origin": ORIGIN_INTERNAL_MODEL}))

    def test_02_channel_internal_model_is_output(self):
        self.assertTrue(is_internal_model_output({"channel": ORIGIN_INTERNAL_MODEL}))

    def test_03_flag_is_output(self):
        self.assertTrue(is_internal_model_output({"_internal_model": True}))
        self.assertTrue(is_internal_model_output({"is_model_output": True}))

    def test_04_model_prefix_is_output(self):
        self.assertTrue(is_internal_model_output({"origin": "model_cache"}))
        self.assertTrue(is_internal_model_output({"origin": "model_retrieval"}))

    def test_05_normal_records_kept(self):
        self.assertFalse(is_internal_model_output({"origin": "user_query"}))
        self.assertFalse(is_internal_model_output({"origin": "evolution_task"}))
        self.assertFalse(is_internal_model_output({"origin": "system_internal"}))

    def test_06_non_dict_is_false(self):
        for bad in (None, "x", 3, []):
            self.assertFalse(is_internal_model_output(bad))

    def test_07_guard_filters(self):
        _recs = [{"origin": "user_query"}, {"origin": ORIGIN_INTERNAL_MODEL},
                 {"origin": "model_x"}, {"origin": "evolution_task"},
                 {"_internal_model": True}]
        _kept = training_guard(_recs)
        self.assertEqual(len(_kept), 2)
        self.assertEqual([r["origin"] for r in _kept], ["user_query", "evolution_task"])

    def test_08_guard_bad_input(self):
        for bad in (None, "x", 3):
            self.assertEqual(training_guard(bad), [])

    def test_09_origin_helper(self):
        self.assertEqual(internal_model_origin(), ORIGIN_INTERNAL_MODEL)


# ============================================================ 版本管理
class TestVersioning(_Base):
    def test_10_initial_state(self):
        self.assertIsNone(self.u.current_version())
        self.assertEqual(self.u.list_versions(), [])
        self.assertEqual(self.u.current_quality(), 85.0)

    def test_11_incremental_creates_version(self):
        _r = self.u.incremental_update(quality_score=86.0)
        self.assertEqual(_r["status"], "ok")
        self.assertEqual(_r["version"], "v1.0.1")
        self.assertEqual(_r["parent"], "v1.0.0")
        self.assertEqual(len(self.u.list_versions()), 1)

    def test_12_version_bump_patch(self):
        self.u.incremental_update(quality_score=86.0)
        self.u.incremental_update(quality_score=86.0)
        self.assertEqual(self.u.current_version()["version"], "v1.0.2")

    def test_13_full_retrain_bumps_minor(self):
        self.u.incremental_update(quality_score=86.0)
        _r = self.u.full_retrain(quality_score=87.0)
        self.assertEqual(_r["status"], "planned")
        self.assertEqual(_r["version"], "v1.1.0")
        self.assertIn("plan", _r)
        self.assertIn("不执行", " ".join(_r["plan"]))

    def test_14_index_persisted(self):
        self.u.incremental_update(quality_score=86.0)
        _p = self.u.index_path()
        self.assertTrue(os.path.isfile(_p))
        _d = json.load(open(_p, encoding="utf-8"))
        self.assertEqual(_d["current"], "v1.0.1")

    def test_15_prune_keeps_limit(self):
        for _ in range(6):
            self.u.incremental_update(quality_score=86.0)
        self.assertEqual(len(self.u.list_versions()), self.u.keep_versions())
        self.assertEqual(self.u.keep_versions(), 3)

    def test_16_prune_keeps_current(self):
        for _ in range(6):
            self.u.incremental_update(quality_score=86.0)
        _cur = self.u.current_version()["version"]
        self.assertTrue(any(v["version"] == _cur for v in self.u.list_versions()))

    def test_17_rollback_to_previous(self):
        self.u.incremental_update(quality_score=86.0)      # v1.0.1
        self.u.incremental_update(quality_score=86.0)      # v1.0.2
        _r = self.u.rollback()
        self.assertEqual(_r["status"], "ok")
        self.assertEqual(_r["to"], "v1.0.1")

    def test_18_rollback_no_previous(self):
        _r = self.u.rollback()
        self.assertEqual(_r["status"], "no_previous")

    def test_19_rollback_named_target(self):
        self.u.incremental_update(quality_score=86.0)
        self.u.incremental_update(quality_score=86.0)
        _r = self.u.rollback("v1.0.1")
        self.assertEqual(_r["status"], "ok")
        self.assertEqual(self.u.current_version()["version"], "v1.0.1")

    def test_20_rollback_unknown_target(self):
        _r = self.u.rollback("v9.9.9")
        self.assertEqual(_r["status"], "not_found")


# ============================================================ 退化防护
class TestDegradeGuard(_Base):
    def test_30_rolls_back_on_large_drop(self):
        self.u.incremental_update(quality_score=86.0)       # v1.0.1
        _r = self.u.incremental_update(quality_score=70.0)  # -18.6% > 10%
        self.assertEqual(_r["status"], "rolled_back")
        self.assertIn("下降", _r["reason"])
        # 版本未推进
        self.assertEqual(self.u.current_version()["version"], "v1.0.1")

    def test_31_allows_small_drop(self):
        self.u.incremental_update(quality_score=86.0)
        _r = self.u.incremental_update(quality_score=80.0)  # -7% < 10%
        self.assertEqual(_r["status"], "ok")

    def test_32_force_overrides(self):
        self.u.incremental_update(quality_score=86.0)
        _r = self.u.incremental_update(quality_score=10.0, force=True)
        self.assertEqual(_r["status"], "ok")

    def test_33_limit_reads_config(self):
        self.assertTrue(hasattr(config, "MODEL_SELF_UPDATER_DEGRADE_LIMIT"))
        self.assertAlmostEqual(self.u.degrade_limit(),
                               float(config.MODEL_SELF_UPDATER_DEGRADE_LIMIT))

    def test_34_rollback_counter(self):
        self.u.incremental_update(quality_score=86.0)
        self.u.incremental_update(quality_score=10.0)
        self.assertGreaterEqual(self.u.stats()["rollbacks"], 1)


# ============================================================ 检查与接线
class TestCheckAndStats(_Base):
    def test_40_check_for_updates_shape(self):
        _r = self.u.check_for_updates()
        for _k in ("status", "enabled", "current_version", "new_samples",
                   "new_feedback", "incremental_due", "full_retrain_due"):
            self.assertIn(_k, _r)

    def test_41_check_with_probe(self):
        _u = ModelSelfUpdater(base_dir=self.dir,
                              data_probe=lambda: {"new_samples": 12, "new_feedback": 3},
                              now_fn=lambda: 1700000000.0)
        _r = _u.check_for_updates()
        self.assertEqual(_r["new_samples"], 12)
        self.assertTrue(_r["incremental_due"])

    def test_42_stats_shape(self):
        for _k in ("enabled", "base_dir", "current", "version_count",
                   "keep_versions", "degrade_limit", "increments", "retrains"):
            self.assertIn(_k, self.u.stats())

    def test_43_rejected_model_output_counted(self):
        _recs = [{"origin": "user_query"}, {"origin": ORIGIN_INTERNAL_MODEL}]
        _r = self.u.incremental_update(quality_score=86.0, samples=_recs)
        self.assertEqual(_r["kept_samples"], 1)
        self.assertEqual(_r["rejected_model_output"], 1)

    def test_44_switch_off(self):
        _bak = config.ENABLE_MODEL_SELF_UPDATER
        try:
            config.ENABLE_MODEL_SELF_UPDATER = False
            self.assertFalse(updater_enabled())
            self.assertEqual(self.u.incremental_update()["status"], "disabled")
            self.assertEqual(self.u.full_retrain()["status"], "disabled")
            self.assertEqual(self.u.rollback()["status"], "disabled")
        finally:
            config.ENABLE_MODEL_SELF_UPDATER = _bak

    def test_45_production_path_rejected_in_test_env(self):
        """★pytest 环境下写生产 data/ 必须被拒。"""
        _u = ModelSelfUpdater(base_dir=DEFAULT_BASE_DIR)
        self.assertFalse(_u._writable())
        _r = _u.incremental_update(quality_score=90.0)
        self.assertFalse(_r["saved"])

    def test_46_singleton_reset(self):
        reset_updater()
        _a = get_updater()
        self.assertIs(_a, get_updater())
        reset_updater()
        self.assertIsNot(_a, get_updater())
        reset_updater()

    def test_47_tools_entry_exists(self):
        self.assertTrue(os.path.isfile(
            os.path.join(_ROOT, "tools", "run_model_self_update.py")))

    def test_48_daily_scheduler_wired(self):
        _src = open(os.path.join(_ROOT, "nucleus", "self_awareness", "DailyScheduler.py"),
                    encoding="utf-8").read()
        self.assertIn("model_self_updater", _src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
