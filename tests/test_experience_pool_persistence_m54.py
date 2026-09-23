# -*- coding: utf-8 -*-
"""第54批 T4（★核心）测试：P2-370 ExperiencePool 合并保存。

背景（实测，第50批）
------------------
框架运行期 `_save()` 以**内存快照整体覆盖**磁盘 JSON → 停机期由 experience_cleanup
写入的清洗标记（is_cleaned / pollution_risk / cleanup_batch / cleanup_at …）只存在于
磁盘、内存没有 → 保存时被抹掉。实测 1140 标记 → 748（-392）。

修复：保存前把磁盘已有、内存缺失的治理字段**合并**回内存（灰度 ENABLE_EXPERIENCE_MERGE_SAVE）。

覆盖
----
① 停机期标记 → 运行期保存后**仍在**（核心场景）
② 内存运行期新判断**优先于**磁盘（不被回退）
③ 关闭灰度 → 复现改造前整体覆盖行为（标记丢失）
④ 磁盘文件不存在 → 正常保存
⑤ 磁盘 JSON 损坏 → 合并返回 0，保存不崩（降级）
⑥ 合并计数正确
⑦ 反复保存不丢标记（★P2-370 的核心回归场景）
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest import mock  # noqa: E402

import config  # noqa: E402
from nucleus.mnemosyne.experience_pool import ExperiencePool  # noqa: E402


def _rmtree(path):
    """★铁律 54：Windows 下 shutil.rmtree(ignore_errors=True) 会静默失败 → 逐文件删。"""
    for _root, _dirs, _files in os.walk(path, topdown=False):
        for _f in _files:
            try:
                os.remove(os.path.join(_root, _f))
            except OSError:
                pass
        for _d in _dirs:
            try:
                os.rmdir(os.path.join(_root, _d))
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass


class TestExperiencePoolPersistenceM54(unittest.TestCase):
    """★第54批 T4：合并保存（P2-370）。"""

    def setUp(self):
        # ★铁律 35：测试不得写生产目录 → base_dir 指向临时目录
        self.tmp = tempfile.mkdtemp(prefix="m54_pool_")
        self.pool = ExperiencePool(base_dir=self.tmp)
        self.pool_file = os.path.join(self.tmp, "experience_pool.json")

    def tearDown(self):
        _rmtree(self.tmp)

    # ---------- 辅助 ----------
    def _write_disk(self, rows):
        """模拟「停机期」写入磁盘（带治理标记）。"""
        with open(self.pool_file, "w", encoding="utf-8") as f:
            json.dump({"version": "v1.0", "updated_at": 0.0, "experiences": rows},
                      f, ensure_ascii=False)

    def _read_disk(self):
        with open(self.pool_file, encoding="utf-8") as f:
            return json.load(f)

    # ---------- 用例 ----------
    def test_01_disk_marks_survive_runtime_save(self):
        """① 停机期写入的清洗标记，运行期保存后必须仍在（核心场景）。"""
        self._write_disk([{"id": "x1", "summary": "旧摘要", "is_cleaned": False,
                           "pollution_risk": "high", "cleanup_batch": 50,
                           "cleanup_at": 1.0}])
        self.pool._experiences = [{"id": "x1", "summary": "新摘要"}]
        self.pool._save()

        rec = self._read_disk()["experiences"][0]
        self.assertEqual(rec["cleanup_batch"], 50, "停机期治理批次号被抹掉了（P2-370 复发）")
        self.assertEqual(rec["pollution_risk"], "high")
        self.assertEqual(rec["summary"], "新摘要", "内存的新内容应照常保存")

    def test_02_memory_mark_not_overwritten_by_disk(self):
        """② 内存运行期的新判断优先于磁盘，不得被回退。"""
        self._write_disk([{"id": "x1", "cleanup_batch": 50, "pollution_risk": "high"}])
        self.pool._experiences = [{"id": "x1", "cleanup_batch": 51, "pollution_risk": "low"}]
        self.pool._save()

        rec = self._read_disk()["experiences"][0]
        self.assertEqual(rec["cleanup_batch"], 51, "内存运行期新判断应优先于磁盘")
        self.assertEqual(rec["pollution_risk"], "low")

    def test_03_switch_off_reproduces_old_behavior(self):
        """③ 关闭灰度 → 复现改造前「整体覆盖」行为（标记丢失）。"""
        self._write_disk([{"id": "x1", "cleanup_batch": 50}])
        self.pool._experiences = [{"id": "x1", "summary": "s"}]
        with mock.patch.object(config, "ENABLE_EXPERIENCE_MERGE_SAVE", False):
            self.pool._save()

        rec = self._read_disk()["experiences"][0]
        self.assertNotIn("cleanup_batch", rec, "关闭开关时应复现改造前的整体覆盖")

    def test_04_no_disk_file_saves_normally(self):
        """④ 磁盘文件不存在（首次保存）→ 正常写入，不崩。"""
        self.pool._experiences = [{"id": "n1", "summary": "s"}]
        self.assertEqual(self.pool._merge_governance_fields_from_disk(), 0)
        self.pool._save()
        self.assertEqual(self._read_disk()["experiences"][0]["id"], "n1")

    def test_05_corrupt_disk_degrades(self):
        """⑤ 磁盘 JSON 损坏 → 合并返回 0，保存降级继续，不抛异常。"""
        with open(self.pool_file, "w", encoding="utf-8") as f:
            f.write("{broken json")
        self.assertEqual(self.pool._merge_governance_fields_from_disk(), 0,
                         "损坏时应返回 0 而不是抛异常")

        self.pool._experiences = [{"id": "c1", "summary": "s"}]
        self.pool._save()  # 不得抛异常
        self.assertEqual(self._read_disk()["experiences"][0]["id"], "c1")

    def test_06_merge_count(self):
        """⑥ 合并计数：只统计真正补齐的字段数。"""
        self._write_disk([{"id": "x1", "is_cleaned": False,
                           "cleanup_batch": 50, "cleanup_at": 1.0}])
        self.pool._experiences = [{"id": "x1", "summary": "s"}]
        self.assertEqual(self.pool._merge_governance_fields_from_disk(), 3)
        # 再合并一次：内存已有值 → 0
        self.assertEqual(self.pool._merge_governance_fields_from_disk(), 0)

    def test_07_marks_survive_repeated_saves(self):
        """⑦ ★P2-370 核心回归：运行期反复保存，标记始终不丢。"""
        self._write_disk([{"id": "x1", "cleanup_batch": 50, "pollution_risk": "high"}])
        self.pool._experiences = [{"id": "x1", "summary": "s"}]
        for _ in range(3):
            self.pool._save()
        rec = self._read_disk()["experiences"][0]
        self.assertEqual(rec["cleanup_batch"], 50)
        self.assertEqual(rec["pollution_risk"], "high")


if __name__ == "__main__":
    unittest.main()
