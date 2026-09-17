# -*- coding: utf-8 -*-
"""主线第66批 T4/P2：经验库隔离恢复闭环 门控单测（8例）。

覆盖：清理闭环隔离分级（高/中覆盖隔离、低覆盖恢复）→ 隔离恢复（回主库 /
      is_cleaned=True / cleanup_action=restored_from_quarantine / 数据完整）→ 幂等
      → 已存在 id 跳过 → dry_run 不落盘 → 开关关闭空操作 → 隔离文件删除。

隔离：ExperiencePool 用显式 base_dir（临时目录），写盘守卫放行；生产恢复须停机窗口。
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config                                                     # noqa: E402
from nucleus.mnemosyne.experience_pool import ExperiencePool      # noqa: E402

# 仅由 SERP 样板 token 构成 → 覆盖度 1.0（≥0.9 高置信 → 隔离）
_SERP_ALL = "搜索结果相关搜索广告赞助商链接百度一下相关推荐"
# 仅 1 个样板 token，覆盖度极低 → 应恢复（不隔离）
_SERP_LOW = "搜索结果" + "正常内容" * 20


def _e(**kw):
    d = {"id": kw.pop("id", "e"), "summary": "干净内容" * 8, "timestamp": 1.0}
    d.update(kw)
    return d


class _CfgSwitch:
    def __init__(self, **kw):
        self._kw, self._old = kw, {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._old.items():
            if v is None:
                if hasattr(config, k):
                    delattr(config, k)
            else:
                setattr(config, k, v)
        return False


class TestQuarantineIsolation(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="m66_q4_iso_")
        self.p = ExperiencePool(base_dir=self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _seed(self):
        self.p._experiences = [
            _e(id="q", polluted=True, is_cleaned=False, summary=_SERP_ALL),  # 高覆盖 → 隔离
            _e(id="r", polluted=True, is_cleaned=False, summary=_SERP_LOW),  # 低覆盖 → 恢复
            _e(id="c", polluted=False),                                       # 干净 → 不动
        ]

    def test_10_cleanup_isolates_high_low(self):
        """清理闭环：高覆盖隔离出活跃池、低覆盖恢复、干净不动。"""
        self._seed()
        _res = self.p.run_pollution_cleanup(dry_run=False)
        self.assertEqual(_res["quarantined"], 1, "高覆盖须隔离 1 条")
        self.assertEqual(_res["restored"], 1, "低覆盖须恢复 1 条")
        _ids = {x["id"] for x in self.p._experiences}
        self.assertNotIn("q", _ids, "隔离记录须移出活跃池")
        self.assertIn("r", _ids)
        self.assertIn("c", _ids)
        _qdir = os.path.join(self.d, "_quarantine")
        self.assertTrue(os.path.isdir(_qdir), "隔离目录须创建")
        _files = [f for f in os.listdir(_qdir) if f.startswith("quarantine_")]
        self.assertEqual(len(_files), 1, "应写出 1 个隔离文件")
        _qdoc = json.load(io.open(os.path.join(_qdir, _files[0]), encoding="utf-8"))
        self.assertEqual(len(_qdoc), 1)
        self.assertEqual(_qdoc[0]["id"], "q")


class TestQuarantineRestore(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="m66_q4_res_")
        self.p = ExperiencePool(base_dir=self.d)
        self.p._experiences = [
            _e(id="q", polluted=True, is_cleaned=False, summary=_SERP_ALL),
            _e(id="r", polluted=True, is_cleaned=False, summary=_SERP_LOW),
        ]
        self.p.run_pollution_cleanup(dry_run=False)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_11_restore_basic(self):
        """隔离恢复：记录回主库，is_cleaned=True，action=restored_from_quarantine。"""
        _res = self.p.restore_from_quarantine(dry_run=False)
        self.assertEqual(_res["restored"], 1, "应恢复 1 条隔离记录")
        _ids = {x["id"] for x in self.p._experiences}
        self.assertIn("q", _ids, "恢复记录须回主库")
        _q = [x for x in self.p._experiences if x["id"] == "q"][0]
        self.assertIs(_q["is_cleaned"], True)
        self.assertEqual(_q["cleanup_action"], "restored_from_quarantine")
        self.assertIn("restored_at", _q)

    def test_12_restore_idempotent(self):
        """恢复幂等：隔离文件删除后二次恢复应为 0。"""
        self.p.restore_from_quarantine(dry_run=False)
        _res = self.p.restore_from_quarantine(dry_run=False)
        self.assertEqual(_res["restored"], 0, "二次恢复须幂等（无新增）")

    def test_13_restore_skips_existing_id(self):
        """主池已存在同 id → 跳过，不重复写入。"""
        # 先把 'q' 作为干净记录放回主池，再放置一个同名隔离文件
        self.p._experiences.append(_e(id="q", polluted=False, is_cleaned=True))
        _qdir = os.path.join(self.d, "_quarantine")
        os.makedirs(_qdir, exist_ok=True)
        _stale = os.path.join(_qdir, "quarantine_%d.json" % int(time.time()))
        with io.open(_stale, "w", encoding="utf-8") as _f:
            _f.write(json.dumps([_e(id="q", polluted=True)], ensure_ascii=False))
        _res = self.p.restore_from_quarantine(dry_run=False)
        self.assertEqual(_res["skipped"], 1, "同 id 须跳过")
        self.assertEqual(_res["restored"], 0)

    def test_14_restore_dry_run_no_write(self):
        """dry_run：仅计数，主池不变、隔离文件保留。"""
        _res = self.p.restore_from_quarantine(dry_run=True)
        self.assertEqual(_res["restored"], 1)
        _ids = {x["id"] for x in self.p._experiences}
        self.assertNotIn("q", _ids, "dry_run 不得改写主池")
        _qdir = os.path.join(self.d, "_quarantine")
        self.assertTrue(any(f.startswith("quarantine_") for f in os.listdir(_qdir)),
                        "dry_run 不得删除隔离文件")

    def test_20_switch_off_noop(self):
        """开关关闭 → 空操作（保留改造前行为）。"""
        with _CfgSwitch(ENABLE_EXPERIENCE_QUARANTINE_RESTORE=False):
            _res = self.p.restore_from_quarantine(dry_run=False)
        self.assertEqual(_res["enabled"], False)
        self.assertEqual(_res["restored"], 0)
        _qdir = os.path.join(self.d, "_quarantine")
        self.assertTrue(any(f.startswith("quarantine_") for f in os.listdir(_qdir)),
                        "开关关闭时隔离文件须保留")

    def test_21_restore_data_integrity(self):
        """端到端：恢复后原记录字段完整保留（不丢数据）。"""
        self.p.restore_from_quarantine(dry_run=False)
        _q = [x for x in self.p._experiences if x["id"] == "q"][0]
        for _k in ("id", "summary", "polluted", "cleanup_at"):
            self.assertIn(_k, _q, "恢复记录须保留原字段 %s" % _k)
        self.assertEqual(_q["id"], "q")
        self.assertTrue(_q["summary"].startswith("搜索结果"))

    def test_22_restore_removes_quarantine_file(self):
        """恢复成功后默认删除隔离文件（removed_files≥1）。"""
        _res = self.p.restore_from_quarantine(dry_run=False, remove_quarantine=True)
        self.assertGreaterEqual(_res["removed_files"], 1)
        _qdir = os.path.join(self.d, "_quarantine")
        self.assertFalse(any(f.startswith("quarantine_") for f in os.listdir(_qdir)),
                         "恢复后隔离文件须被清理")


if __name__ == "__main__":
    unittest.main(verbosity=2)
