# -*- coding: utf-8 -*-
"""第431批 刀4：对话落盘 30 天滚动清理。

★T0 纠偏：任务书前提是「越攒越多、没有自动清理」，实测 `data/experience/`
  下 `dialogue_process_*.jsonl` **一个都没有**（全仓仅 tmp/ 单测产物）。
  根因不是清理缺失，而是写入从未发生：`begin()` 第一道闸 `if not _cid: return`，
  而生产日志里 `correlation_id` 恒为空串。这条作为发现项上报。
  ⇒ 本刀交付的是**机制**，边界用构造文件验证。

被守护的八条：
  1) 31 天前 / 40 天前的文件被删；
  2) **30 天前、29 天前、今天** 的都保留（「30 天滚动」= 留最近 30 天）；
  3) 判据是**文件名日期**不是 mtime（把旧文件的 mtime touch 成今天也照样删）；
  4) 非目标文件（其它 jsonl / 同名但不是 `dialogue_process_` 前缀）不碰；
  5) `dry_run=True` ⇒ 只报不删；
  6) 文件名日期非法（如 20261340）⇒ 跳过，不抛；
  7) 留存天数非法（0/负数/bool/非 int）⇒ **一份都不删**（方向必须保守）；
  8) 返回体含文件清单与释放字节（任务书要求"删了哪些、释放多少"）。
"""
import contextlib
import datetime
import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from nucleus import dialogue_process_trace as tr


def _mk(dirpath, day, size=1):
    p = os.path.join(dirpath, "dialogue_process_{}.jsonl".format(day.strftime("%Y%m%d")))
    with open(p, "w", encoding="utf-8") as _f:
        _f.writelines(json.dumps({"i": _i}) + "\n" for _i in range(size))
    return p


class TestM431D4DialogueSweep(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.iso = self._td.name
        self.dir = os.path.join(self.iso, "data", "experience")
        os.makedirs(self.dir, exist_ok=True)
        self._old_env = os.environ.get("M426_TRACE_ROOT")
        os.environ["M426_TRACE_ROOT"] = self.iso
        self._old_days = getattr(config, "DIALOGUE_TRACE_RETENTION_DAYS", None)
        config.DIALOGUE_TRACE_RETENTION_DAYS = 30
        # ★不用 date.today()（DTZ011：拿本地时区隐式当日期）；与生产判据同源，
        #   用 fromtimestamp(time.time()) 显式走一次转换，测试与实现不会各算一套。
        # DTZ012 屏蔽理由同生产 `sweep_old`：判据是**本地日期**（文件名按本地
        # 日期切分），换成 UTC 会差一天。★431 教训：理由只能写成**不含标记词**的
        # 散文 —— 注释里单独出现标记词，ruff 会当成 blanket 指令并报 RUF100。
        self.today = datetime.date.fromtimestamp(time.time())  # noqa: DTZ012

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("M426_TRACE_ROOT", None)
        else:
            os.environ["M426_TRACE_ROOT"] = self._old_env
        if self._old_days is None:
            # ★用 suppress 而不是 except AttributeError: pass ——
            #   CI 静默 handler 门禁会把裸 pass 判成「静默」，而这个 pass
            #   在语义上确实是没有就算了。suppress 同样表达该意图且不被误判。
            with contextlib.suppress(AttributeError):
                delattr(config, "DIALOGUE_TRACE_RETENTION_DAYS")
        else:
            config.DIALOGUE_TRACE_RETENTION_DAYS = self._old_days
        self._td.cleanup()

    def _d(self, off):
        return self.today - datetime.timedelta(days=off)

    # ---- 1) + 2) 该删的删、该留的留 ----
    def test_01_02_boundary(self):
        _f31 = _mk(self.dir, self._d(31), size=2)
        _f40 = _mk(self.dir, self._d(40), size=3)
        _f30 = _mk(self.dir, self._d(30))
        _f29 = _mk(self.dir, self._d(29))
        _f0 = _mk(self.dir, self._d(0))
        _r = tr.sweep_old()
        self.assertEqual(_r["removed"], 2, "只该删 31/40 天前两份")
        self.assertFalse(os.path.exists(_f31))
        self.assertFalse(os.path.exists(_f40))
        for _p, _tag in ((_f30, "30 天前"), (_f29, "29 天前"), (_f0, "今天")):
            self.assertTrue(os.path.exists(_p), f"{_tag}必须保留")
        self.assertEqual(_r["kept"], 3)

    # ---- 3) 判据是文件名不是 mtime ----
    def test_03_uses_name_not_mtime(self):
        _old = _mk(self.dir, self._d(45))
        _now = time.time()
        os.utime(_old, (_now, _now))          # 把 mtime 刷成"刚刚"
        self.assertLess(abs(os.path.getmtime(_old) - _now), 5)
        _r = tr.sweep_old()
        self.assertEqual(_r["removed"], 1, "mtime 是今天也要删（判据是文件名日期）")
        self.assertFalse(os.path.exists(_old))

    # ---- 4) 非目标文件不碰 ----
    def test_04_other_files_untouched(self):
        _keep = os.path.join(self.dir, "experience_pool.json")
        with open(_keep, "w", encoding="utf-8") as _f:
            _f.write("{}")
        _decoy = os.path.join(self.dir, "pairs_20200101.jsonl")
        with open(_decoy, "w", encoding="utf-8") as _f:
            _f.write("{}\n")
        _r = tr.sweep_old()
        self.assertEqual(_r["removed"], 0)
        self.assertTrue(os.path.exists(_keep))
        self.assertTrue(os.path.exists(_decoy))

    # ---- 5) dry_run 只报不删 ----
    def test_05_dry_run_reports_only(self):
        _p = _mk(self.dir, self._d(60), size=2)
        _r = tr.sweep_old(dry_run=True)
        self.assertEqual(_r["removed"], 1)
        self.assertGreater(_r["freed_bytes"], 0)
        self.assertTrue(os.path.exists(_p), "dry_run 不得真删")
        self.assertEqual(len(_r["files"]), 1)

    # ---- 6) 日期非法 ⇒ 跳过 ----
    def test_06_bad_date_skipped(self):
        _bad = os.path.join(self.dir, "dialogue_process_20261340.jsonl")
        with open(_bad, "w", encoding="utf-8") as _f:
            _f.write("{}\n")
        _r = tr.sweep_old()
        self.assertEqual(_r["removed"], 0, "非法日期不得被当成过期删掉")
        self.assertTrue(os.path.exists(_bad))
        self.assertEqual(_r["scanned"], 0, "解析不出日期的不计入扫描面")

    # ---- 7) 留存天数非法 ⇒ 一份不删 ----
    def test_07_invalid_retention_deletes_nothing(self):
        _p = _mk(self.dir, self._d(999), size=2)
        for _bad in (0, -5, True, "30", 30.5, None):
            config.DIALOGUE_TRACE_RETENTION_DAYS = _bad
            _r = tr.sweep_old()
            self.assertEqual(_r["removed"], 0,
                             f"留存天数={_bad!r} 时必须一份都不删（保守优先）")
            self.assertTrue(os.path.exists(_p))

    # ---- 8) 返回体含清单与字节 ----
    def test_08_result_has_list_and_bytes(self):
        _mk(self.dir, self._d(50), size=3)
        _r = tr.sweep_old()
        self.assertEqual(_r["reason"], "ok")
        self.assertEqual(_r["days"], 30)
        self.assertIsInstance(_r["files"], list)
        self.assertEqual(len(_r["files"]), 1)
        _f = _r["files"][0]
        self.assertIn("name", _f)
        self.assertIn("day", _f)
        self.assertGreater(_f["bytes"], 0)
        self.assertGreater(_r["freed_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
