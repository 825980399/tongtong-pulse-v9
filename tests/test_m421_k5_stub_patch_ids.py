"""421 刀5：effect_verify.json 的 10 条非生产测试桩不再计入。

被守护的四条：
  1) 10 条实测桩 id 全部被 `_m418_is_stub_patch` 认出（418 当时只登记了 tp1..tp4）；
  2) 真补丁 id（`patch_*` / `patch_llm_*`）**一个都不能**被误判成桩；
  3) 沙箱路径（`base_dir=tmp`）**不剥桩** —— 单测自己造的桩必须原样保留；
  4) 生产路径才剥（用 mock 模拟生产判据，不碰真文件）。

★铁律 38：全部传 `base_dir=<tmp>`，`__init__` 里就 `_load()`，不传会读生产
  `data/evolution/effect_verify.json`（也就可能在下一次 save 时覆盖它）。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.evolution.EvolutionEffectVerifier import EvolutionEffectVerifier

STUBS = ["p0demo4", "p93", "zw2case1b", "c5", "t101a_probe",
         "int1", "p3d", "pperf", "pno", "pab"]
REAL = ["patch_1788887251_0c6f", "patch_llm_1791967240_fcef",
        "patch_1789373895_72ab"]


class TestK5StubIds(unittest.TestCase):
    def test_01_all_ten_stubs_recognised(self):
        for _p in STUBS:
            self.assertTrue(EvolutionEffectVerifier._m418_is_stub_patch(_p),
                            f"{_p!r} 未被识别为测试桩")

    def test_02_real_patches_not_misjudged(self):
        for _p in REAL:
            self.assertFalse(EvolutionEffectVerifier._m418_is_stub_patch(_p),
                             f"真补丁 {_p!r} 被误判成桩（会被从生产数据里删掉）")

    def test_03_tp_prefix_still_works(self):
        for _p in ("tp1", "tp9", "tp42"):
            self.assertTrue(EvolutionEffectVerifier._m418_is_stub_patch(_p))
        self.assertFalse(EvolutionEffectVerifier._m418_is_stub_patch("tp"))
        self.assertFalse(EvolutionEffectVerifier._m418_is_stub_patch("tpx"))


class TestK5StripScope(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        # 预置一个含桩 + 真补丁的沙箱文件
        with open(os.path.join(self.tmp, 'effect_verify.json'), 'w',
                  encoding='utf-8') as f:
            json.dump({
                "version": 1, "updated": 0,
                "issues": {}, "verdicts": {
                    **{s: {"passed": True} for s in STUBS},
                    **{r: {"passed": False} for r in REAL},
                },
            }, f, ensure_ascii=False)

    def tearDown(self):
        self._td.cleanup()

    def test_04_sandbox_keeps_stubs(self):
        """沙箱路径（单测自己造的桩）必须原样保留 —— 否则单测互相踩。"""
        v = EvolutionEffectVerifier(base_dir=self.tmp)
        self.assertTrue(v._save_path.startswith(self.tmp), '落盘路径未隔离')
        self.assertFalse(v._m418_production_path(), 'tmp 竟被判成生产路径')
        self.assertEqual(len(v._verdicts), 13,
                         '沙箱里桩被剥掉了 ⇒ 会把单测造的数据删光')
        for s in STUBS:
            self.assertIn(s, v._verdicts)

    def test_05_production_path_strips(self):
        """生产路径才剥：用 mock 模拟生产判据。"""
        v = EvolutionEffectVerifier(base_dir=self.tmp)
        with mock.patch.object(EvolutionEffectVerifier, '_m418_production_path',
                               return_value=True):
            _v, _i = v._m418_strip_stubs()
        self.assertEqual(_v, 10, f"应剔除 10 条桩（实得 {_v}）")
        self.assertEqual(len(v._verdicts), len(REAL))
        for s in STUBS:
            self.assertNotIn(s, v._verdicts)
        for r in REAL:
            self.assertIn(r, v._verdicts, f"真补丁 {r!r} 被误删")

    def test_06_strip_is_idempotent(self):
        v = EvolutionEffectVerifier(base_dir=self.tmp)
        with mock.patch.object(EvolutionEffectVerifier, '_m418_production_path',
                               return_value=True):
            v._m418_strip_stubs()
            _v2, _i2 = v._m418_strip_stubs()
        self.assertEqual((_v2, _i2), (0, 0), '第二次剔除应无事可做')

    def test_07_save_keeps_four_keys(self):
        """落盘只能写 4 个顶层键，别把别的字段弄丢。"""
        v = EvolutionEffectVerifier(base_dir=self.tmp)
        v.save()
        with open(v._save_path, encoding='utf-8') as _f:
            _d = json.load(_f)
        self.assertEqual(sorted(_d.keys()), ["issues", "updated", "verdicts", "version"])


if __name__ == '__main__':
    unittest.main()
