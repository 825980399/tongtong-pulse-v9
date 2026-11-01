"""421 刀1：经验池读盘失败**保留旧值**、不再静默归零。

被守护的四条：
  1) 坏盘 ⇒ `_experiences` 原值不动、标量不动（原实现直接清空）；
  2) 坏盘 ⇒ `_load_state="degraded"` + `load_error` 非空；
  3) `get_stats()` 暴露 `load_state` / `load_error`（口径进产物）；
  4) 留痕走 `_module_logger.error`，不再是 print（日志里看得见）。

★本文件所有 `ExperiencePool(...)` 都显式传 `base_dir=<tmp>`：
  构造即 `_load()`，不传就会读生产 `data/experience/experience_pool.json`。
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

from nucleus.mnemosyne import experience_pool as ep


class TestK1LoadDegrade(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        self.pool_file = os.path.join(self.tmp, 'experience_pool.json')

    def tearDown(self):
        self._td.cleanup()

    def _pool(self):
        p = ep.ExperiencePool(base_dir=self.tmp)
        # ★隔离断言：落盘路径必须真的在 tmp 下（防「以为隔离了其实没有」）
        self.assertEqual(os.path.dirname(p._pool_file), os.path.abspath(
            os.path.normpath(self.tmp)), '经验池落盘路径未隔离到 tmp')
        return p

    def _write_raw(self, text):
        with open(self.pool_file, 'w', encoding='utf-8') as f:
            f.write(text)

    # ---------- 1. 核心：坏盘不清空 ----------
    def test_01_bad_json_keeps_old_experiences(self):
        _p = self._pool()
        _old = [{"id": "e1", "content": "旧经验一"},
                {"id": "e2", "content": "旧经验二"}]
        _p._experiences = list(_old)
        _p._total_recorded = 7
        # 磁盘上是**坏 JSON**
        self._write_raw("{ this is not json ")

        _p._load()

        self.assertEqual(len(_p._experiences), 2,
                         '读盘失败后旧经验被清空了 —— 这正是本刀要消灭的行为')
        self.assertEqual([e["id"] for e in _p._experiences], ["e1", "e2"])
        self.assertEqual(_p._total_recorded, 7, '标量也被清空了')
        self.assertEqual(_p._load_state, "degraded")
        self.assertIn("读盘失败", _p._load_error or "")

    # ---------- 2. get_stats 暴露 ----------
    def test_02_get_stats_exposes_load_state(self):
        _p = self._pool()
        _p._experiences = [{"id": "keep"}]
        self._write_raw("not-json-at-all")
        _p._load()

        _st = _p.get_stats()
        self.assertEqual(_st["load_state"], "degraded")
        self.assertTrue(_st["load_error"], 'load_error 必须能说明原因')
        # ★关键读数：池里仍有 1 条（保留旧值），而不是 0 条
        self.assertEqual(_st["total_experiences"], 1)

    # ---------- 3. 文件不存在 = 正常首启 ----------
    def test_03_absent_is_not_degraded(self):
        _p = self._pool()
        self.assertFalse(os.path.exists(self.pool_file))
        self.assertEqual(_p._load_state, "absent")
        self.assertIsNone(_p._load_error)
        self.assertEqual(_p.get_stats()["load_state"], "absent")

    # ---------- 4. 正常文件 ----------
    def test_04_good_file_loads_ok(self):
        self._write_raw(json.dumps({
            "version": "v1.0",
            "total_recorded": 3,
            "total_summarized": 1,
            "total_decayed": 2,
            "experiences": [{"id": "a"}, {"id": "b"}],
        }, ensure_ascii=False))
        _p = self._pool()
        self.assertEqual(_p._load_state, "ok")
        self.assertIsNone(_p._load_error)
        self.assertEqual([e["id"] for e in _p._experiences], ["a", "b"])
        self.assertEqual(_p._total_recorded, 3)
        self.assertEqual(_p._total_summarized, 1)
        self.assertEqual(_p._total_decayed, 2)

    # ---------- 5. 走 logger.error，不再 print ----------
    def test_05_logs_via_module_logger_error(self):
        _p = self._pool()
        _p._experiences = [{"id": "keep"}]
        self._write_raw("{ broken")
        with mock.patch.object(ep._module_logger, 'error') as _m:
            _p._load()
        self.assertTrue(_m.called, '降级必须走 _module_logger.error，不能只 print')
        _msg = ' '.join(str(a) for a in _m.call_args[0])
        self.assertIn('保留旧值', _msg)
        self.assertIn('1 条', _msg)

    # ---------- 6. 顶层不是对象 ----------
    def test_06_non_dict_toplevel_is_degraded(self):
        _p = self._pool()
        _p._experiences = [{"id": "keep"}]
        self._write_raw('["a", "b"]')
        _p._load()
        self.assertEqual(_p._load_state, "degraded")
        self.assertIn("顶层不是对象", _p._load_error or "")
        self.assertEqual(len(_p._experiences), 1)

    # ---------- 7. 缺 experiences 键 ----------
    def test_07_missing_experiences_key_is_degraded(self):
        _p = self._pool()
        _p._experiences = [{"id": "keep"}]
        self._write_raw(json.dumps({"version": "v1.0", "total_recorded": 9}))
        _p._load()
        self.assertEqual(_p._load_state, "degraded")
        self.assertIn("experiences", _p._load_error or "")
        self.assertEqual(len(_p._experiences), 1)
        self.assertEqual(_p._total_recorded, 0, '缺键时不应把标量也读进来')

    # ---------- 8. 0 字节文件（原子写中间态）----------
    def test_08_zero_byte_file_is_degraded_not_wiped(self):
        _p = self._pool()
        _p._experiences = [{"id": "keep"}]
        self._write_raw("")
        _p._load()
        self.assertEqual(_p._load_state, "degraded")
        self.assertEqual(len(_p._experiences), 1)


if __name__ == '__main__':
    unittest.main()
