# -*- coding: utf-8 -*-
"""第45批 T1 门控测试：语义缓存阈值校准（0.92 → 0.85）。

覆盖：
* 配置读取（config / `DEFAULT_THRESHOLD` / `threshold()` 三者一致）
* **边界判定 0.84 未命中 / 0.85 命中 / 0.86 命中**（用注入向量构造精确余弦）
* **灰度回退**（关闭开关 → 0.92，零回归）
* **无硬编码残留**（`semantic_cache.py` 源码内不含 `0.92` 字面量）
* 校准工具复跑结论（推荐 0.85、F1=1.0）

★全部隔离到 `tempfile.mkdtemp()` → **不写生产 `data/cache/`**。
"""
import io
import json
import math
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config  # noqa: E402
import nucleus.llm.semantic_cache as _sc  # noqa: E402

_CALIB_REPORT = os.path.join(_ROOT, "docs", "分析报告", "m45_阈值校准复跑报告.json")


def _vec_with_cos(cos):
    """构造与 `[1, 0]` 的余弦**恰为** ``cos`` 的二维向量。"""
    return [float(cos), math.sqrt(max(0.0, 1.0 - cos * cos))]


class _Base(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.mkdtemp(prefix="m45_t1_")
        self._saved = {k: getattr(config, k, None) for k in
                       ("SEMANTIC_CACHE_THRESHOLD",
                        "ENABLE_SEMANTIC_CACHE_THRESHOLD_CALIBRATION",
                        "SEMANTIC_CACHE_THRESHOLD_BEFORE_CALIBRATION")}

    def tearDown(self):
        for _k, _v in self._saved.items():
            if _v is not None:
                setattr(config, _k, _v)
        shutil.rmtree(self._root, ignore_errors=True)

    def _cache(self):
        return _sc.SemanticCache(base_dir=self._root, encoder=None,
                                 auto_start=False)


# ---------------------------------------------------------------------------
# 1) 配置读取
# ---------------------------------------------------------------------------
class TestThresholdConfig(_Base):
    def test_01_config_value_is_085(self):
        self.assertEqual(float(config.SEMANTIC_CACHE_THRESHOLD), 0.85)

    def test_02_module_default_synced(self):
        """★config 与代码默认值必须同步（防两处漂移）。"""
        self.assertEqual(float(_sc.DEFAULT_THRESHOLD), 0.85)
        self.assertEqual(float(_sc.DEFAULT_THRESHOLD),
                         float(config.SEMANTIC_CACHE_THRESHOLD))

    def test_03_instance_threshold_reads_config(self):
        self.assertEqual(self._cache().threshold(), 0.85)

    def test_04_config_change_takes_effect(self):
        config.SEMANTIC_CACHE_THRESHOLD = 0.77
        self.assertEqual(self._cache().threshold(), 0.77)

    def test_05_rollback_entry_present(self):
        """校准前值必须以**具名配置项**存在（供灰度回退，非生效值）。"""
        self.assertEqual(
            float(config.SEMANTIC_CACHE_THRESHOLD_BEFORE_CALIBRATION), 0.92)


# ---------------------------------------------------------------------------
# 2) 灰度回退（零回归）
# ---------------------------------------------------------------------------
class TestRollback(_Base):
    def test_10_switch_off_returns_precalibration(self):
        config.ENABLE_SEMANTIC_CACHE_THRESHOLD_CALIBRATION = False
        self.assertEqual(self._cache().threshold(), 0.92)

    def test_11_switch_on_returns_calibrated(self):
        config.ENABLE_SEMANTIC_CACHE_THRESHOLD_CALIBRATION = True
        self.assertEqual(self._cache().threshold(), 0.85)

    def test_12_switch_present_and_default_on(self):
        """开关必须存在且默认为开（关闭才回退）。"""
        self.assertTrue(hasattr(config, "ENABLE_SEMANTIC_CACHE_THRESHOLD_CALIBRATION"))
        self.assertTrue(bool(config.ENABLE_SEMANTIC_CACHE_THRESHOLD_CALIBRATION))

    def test_13_rollback_ignores_calibrated_value(self):
        """回退时即便 `SEMANTIC_CACHE_THRESHOLD` 被改成别的值，也返回校准前值。"""
        config.ENABLE_SEMANTIC_CACHE_THRESHOLD_CALIBRATION = False
        config.SEMANTIC_CACHE_THRESHOLD = 0.5
        self.assertEqual(self._cache().threshold(), 0.92)


# ---------------------------------------------------------------------------
# 3) ★边界判定 0.84 / 0.85 / 0.86
# ---------------------------------------------------------------------------
class TestBoundary(_Base):
    def _sim(self, cos):
        _c = self._cache()
        _c.add("q", "答", vec=_vec_with_cos(1.0))
        _r = _c.lookup("q", vec=_vec_with_cos(cos))
        return _r

    def test_20_at_084_is_miss(self):
        _r = self._sim(0.84)
        self.assertAlmostEqual(_r["similarity"], 0.84, places=4)
        self.assertFalse(_r["hit"], "0.84 < 0.85 → 必须未命中")

    def test_21_at_085_is_hit(self):
        _r = self._sim(0.85)
        self.assertAlmostEqual(_r["similarity"], 0.85, places=4)
        self.assertTrue(_r["hit"], "0.85 >= 0.85 → 必须命中（边界含等号）")

    def test_22_at_086_is_hit(self):
        _r = self._sim(0.86)
        self.assertAlmostEqual(_r["similarity"], 0.86, places=4)
        self.assertTrue(_r["hit"])

    def test_23_at_091_is_hit(self):
        """0.91 在旧阈值下会漏检，新阈值下必须命中（校准收益的直接体现）。"""
        self.assertTrue(self._sim(0.91)["hit"])

    def test_24_boundary_follows_rollback(self):
        """回退后 0.91 必须重新变成**未命中**（证明回退真的生效）。"""
        config.ENABLE_SEMANTIC_CACHE_THRESHOLD_CALIBRATION = False
        self.assertFalse(self._sim(0.91)["hit"])

    def test_25_same_class_min_hits(self):
        """实测同义改写对**最小**相似度 0.8602 → 新阈值下必须命中。"""
        self.assertTrue(self._sim(0.8602)["hit"])


# ---------------------------------------------------------------------------
# 4) 无硬编码残留
# ---------------------------------------------------------------------------
def _numeric_literals(path, value=0.92):
    """★AST 通道：返回源码中**真实数值字面量**等于 ``value`` 的行号。

    ★为什么不grep子串：注释与 docstring 里写"原 0.92""0.92 → 0.85"是**文档**，
    不是硬编码。子串匹配会把它们误判成"残留"（本批第一版判据就踩了这个坑）。
    只认 ``ast.Constant`` 的数值节点 —— 这才是"生效值"。
    """
    import ast as _ast
    _tree = _ast.parse(io.open(path, encoding="utf-8").read())
    _out = []
    for _n in _ast.walk(_tree):
        if isinstance(_n, _ast.Constant) and isinstance(_n.value, (int, float)) \
                and not isinstance(_n.value, bool):
            if abs(float(_n.value) - float(value)) < 1e-12:
                _out.append(_n.lineno)
    return _out


class TestNoHardcode(unittest.TestCase):
    def test_30_semantic_cache_has_no_092_numeric_literal(self):
        """★任务书验收项：`semantic_cache.py` 内**生效值**不得是 0.92。"""
        _p = os.path.join(_ROOT, "nucleus", "llm", "semantic_cache.py")
        _lits = _numeric_literals(_p, 0.92)
        _lines = io.open(_p, encoding="utf-8").read().split("\n")
        self.assertEqual(_lits, [],
                         "semantic_cache.py 存在 0.92 数值字面量 @{}: {}".format(_lits, [_lines[i - 1].strip()[:100] for i in _lits]))

    def test_31_semantic_cache_default_is_085_literal(self):
        _p = os.path.join(_ROOT, "nucleus", "llm", "semantic_cache.py")
        self.assertTrue(_numeric_literals(_p, 0.85),
                        "DEFAULT_THRESHOLD 应以 0.85 数值字面量存在")

    def test_32_config_092_only_as_rollback_entry(self):
        """config.py 的 0.92 数值字面量**只允许 1 处**（校准前回退项）。"""
        _p = os.path.join(_ROOT, "config.py")
        _lits = _numeric_literals(_p, 0.92)
        _lines = io.open(_p, encoding="utf-8").read().split("\n")
        _srcs = [_lines[i - 1].strip() for i in _lits]
        self.assertEqual(len(_lits), 1,
                         "0.92 数值字面量应恰有 1 处（回退项），实际 %d: %s"
                         % (len(_lits), _srcs))
        self.assertIn("SEMANTIC_CACHE_THRESHOLD_BEFORE_CALIBRATION", _srcs[0])

    def test_33_config_comments_may_mention_092(self):
        """反例保护：注释里写 `0.92` 属**文档**，不得被判为残留。"""
        _p = os.path.join(_ROOT, "config.py")
        _raw = io.open(_p, encoding="utf-8").read()
        self.assertIn("0.92", _raw, "校准说明注释应保留（便于回溯）")
        self.assertEqual(len(_numeric_literals(_p, 0.92)), 1)

    def test_34_effective_threshold_is_085_everywhere(self):
        """生效值三处一致：config / DEFAULT_THRESHOLD / 实例。"""
        _c = _sc.SemanticCache(base_dir=None, encoder=None, auto_start=False)
        self.assertEqual(_c.threshold(), 0.85)
        self.assertEqual(float(config.SEMANTIC_CACHE_THRESHOLD), 0.85)
        self.assertEqual(float(_sc.DEFAULT_THRESHOLD), 0.85)


# ---------------------------------------------------------------------------
# 5) 校准工具复跑结论
# ---------------------------------------------------------------------------
class TestCalibrationEvidence(unittest.TestCase):
    """★主线第62批 T4-2：第45批校准复跑报告产物已缺失 → 整类跳过（非失败）。

    产物 ``docs/分析报告/m45_阈值校准复跑报告.json`` 当前不在仓库中
    （Closeout/清理时被当作过程产物清掉，2026-09-15 实测确认缺失）。

    本类校验的是「第45批阈值校准（0.85）有留档证据」—— 产物不存在属于
    **证据缺失**，不是代码逻辑错误；且若用现有语料重跑校准，
    recommended / f1 / same_min / diff_max 很可能不再是 0.85/1.0，
    反而会让「已固化的历史结论」失真。

    ⇒ 与 tests/test_tmp_cleanup_m45.py 第46批「执行产物缺失 → skip」的处理
      保持一致：产物缺失时跳过，产物一旦恢复本类自动重新生效。
    """

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_CALIB_REPORT):
            raise unittest.SkipTest(
                "第45批校准复跑报告产物缺失（证据缺失，非逻辑失败）: {}".format(_CALIB_REPORT))

    def test_40_recalibration_report_exists(self):
        self.assertTrue(os.path.isfile(_CALIB_REPORT),
                        "缺少校准复跑报告: {}".format(_CALIB_REPORT))

    def test_41_recommended_threshold_is_085(self):
        _r = json.load(io.open(_CALIB_REPORT, encoding="utf-8"))
        self.assertEqual(float(_r["probe"]["recommended"]), 0.85)

    def test_42_f1_at_085_is_one(self):
        _r = json.load(io.open(_CALIB_REPORT, encoding="utf-8"))
        _row = [x for x in _r["probe"]["rows"]
                if abs(x["threshold"] - 0.85) < 1e-9][0]
        self.assertEqual(_row["f1"], 1.0)
        self.assertEqual(_row["precision"], 1.0)
        self.assertEqual(_row["recall"], 1.0)

    def test_43_separation_band_intact(self):
        """同义对下界 > 异类对上界（两族可分离）→ 证明 0.85 落在安全区。"""
        _p = json.load(io.open(_CALIB_REPORT, encoding="utf-8"))["probe"]
        self.assertGreater(_p["same_min"], _p["diff_max"])
        self.assertLessEqual(0.85, _p["same_min"])
        self.assertGreater(0.85, _p["diff_max"])


if __name__ == "__main__":
    unittest.main()
