#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""405 刀6 判据：L6 温冷快照加年龄上限（超过则按 L5 同口径 SKIP）。

## 修的是什么

404 刀3 的七层面板里 `check_l6` **只看结构不看年龄**：schema 对、warm/cold 非空
就 PASS。实测停机 4.4h（age=15904.7s）面板仍报 L6 PASS，与 L5 报 SKIP 自相矛盾
—— 同一份"框架停机"的事实，一层说"不用判"，一层说"一切正常"。
这类 PASS 比 FAIL 更危险：它让人误以为停机期间口径是可信的。

## 判据设计的三条硬约束（照 L5 的教训，不是照任务书字面）

1. **年龄取 max(mtime_age, timestamp_age)**
   只看 mtime ⇒ 文件被 touch 一下就"变新鲜"；
   只看 timestamp ⇒ 写盘卡住但内容旧时看不出来。L5 就是这么做的，逐字同口径。
2. **结构判据优先于年龄判据**
   schema 错 / 缺 warm / 全空 是真故障，必须 FAIL。若先判年龄，
   「结构坏了且同时超龄」会被降级成 SKIP —— 那是把真故障藏起来。
3. **超龄时不许判 FAIL**
   本文件**没有 pid 字段**（实测只有 schema/timestamp/warm/cold/*_age_s/*_at），
   没法像 L5 那样用自报 pid 做硬证据；只能枚举进程，而枚举会被
   `git commit -- main.py` 这类命令行字面骗到 ⇒ 只能当线索，判不了就 SKIP。
   三个 reason 必须可分辨：framework_down / psutil_unavailable / running_unverified。
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402
from tools.m404_layer_health_check import (  # noqa: E402
    DEFAULT_L6_MAX_AGE_S, FAIL, PASS, SKIP, check_l6, make_context,
)


def _purge_dir(path):
    if not path or not os.path.isdir(path):
        return
    try:
        shutil.rmtree(path)
    except OSError as e:
        silent_exc(e, where="test_m405_k6._purge_dir", level="debug")


class _Base(unittest.TestCase):
    """造一个只有 data/monitor 的空工程根，写小时快照进去。"""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="m405k6_")
        os.makedirs(os.path.join(self._td, "data", "monitor"))
        self._p = os.path.join(self._td, "data", "monitor",
                               "health_snapshot_hourly.json")
        self._now = time.time()

    def tearDown(self):
        _purge_dir(self._td)

    def _snap(self, ts_offset=0.0, **kw):
        """写一份合法快照；`ts_offset` 让自报 timestamp 偏离 now（负数=更旧）。"""
        _d = {"schema": "snapshot_tiers/403.v1",
              "warm": {"organs": {"total": 66}},
              "cold": {"knowledge_evolution": {}},
              "warm_age_s": 0.1, "cold_age_s": 62.5,
              "timestamp": self._now + ts_offset}
        _d.update(kw)
        with open(self._p, "w", encoding="utf-8") as f:
            json.dump(_d, f, ensure_ascii=False)
        return _d

    def _ctx(self, **kw):
        kw.setdefault("framework_running", False)
        return make_context(root=self._td, now=self._now, **kw)


# ---------------------------------------------------------------------------
# 1) 常量与上下文接线
# ---------------------------------------------------------------------------
class TestWiring(_Base):

    def test_01_default_max_age_is_3700(self):
        """任务书 405 刀6 定的口径：3700s（≈1h2min，比小时周期宽 100s）。"""
        self.assertEqual(DEFAULT_L6_MAX_AGE_S, 3700.0)

    def test_02_context_carries_l6_max_age(self):
        self.assertEqual(self._ctx(l6_max_age=123.0)["l6_max_age"], 123.0)

    def test_03_context_default_is_none_so_default_applies(self):
        """ctx 里 None 表示"没覆盖"，由 check_l6 回落到常量（与 snapshot_max_age 同构）。"""
        self.assertIsNone(self._ctx()["l6_max_age"])


# ---------------------------------------------------------------------------
# 2) 年龄判据三态
# ---------------------------------------------------------------------------
class TestAgeVerdict(_Base):

    def test_10_fresh_snapshot_passes(self):
        self._snap()
        _r = check_l6(self._ctx())
        self.assertEqual(_r["status"], PASS)
        self.assertEqual(_r["evidence"]["max_age_source"], "default")
        self.assertLess(_r["evidence"]["age_s"], DEFAULT_L6_MAX_AGE_S)

    def test_11_stale_and_framework_down_skips(self):
        """真实形态：停机 4.4h。改前这里是 PASS（404 的真实读数），改后必须 SKIP。"""
        self._snap(ts_offset=-16000.0)
        _r = check_l6(self._ctx(framework_running=False))
        self.assertEqual(_r["status"], SKIP)
        self.assertEqual(_r["reason"], "framework_down")

    def test_12_stale_and_framework_running_does_not_fail(self):
        """★不许判 FAIL：进程枚举会被命令行字面骗到（`git commit -- main.py`）。

        这条钉住「宁可 SKIP 也不假阳性」——404 刀4 在 L5 上栽过一次（is_framework_running
        按 cmdline basename 匹配，git commit 自己就命中）。
        """
        self._snap(ts_offset=-16000.0)
        _r = check_l6(self._ctx(framework_running=True))
        self.assertEqual(_r["status"], SKIP)
        self.assertEqual(_r["reason"], "running_unverified")

    def test_13_stale_and_psutil_unavailable_skips(self):
        self._snap(ts_offset=-16000.0)
        _r = check_l6(self._ctx(framework_running=None))
        self.assertEqual(_r["status"], SKIP)
        self.assertEqual(_r["reason"], "psutil_unavailable")

    def test_14_three_skip_reasons_are_distinguishable(self):
        """三个 reason 不能合并成一个 —— 处置方式不同（去开机 / 装 psutil / 人工看）。"""
        self._snap(ts_offset=-16000.0)
        _reasons = {check_l6(self._ctx(framework_running=v))["reason"]
                    for v in (False, None, True)}
        self.assertEqual(len(_reasons), 3, "三个 reason 应互不相同：%r" % (_reasons,))

    def test_15_cli_override_makes_it_pass(self):
        """--l6-max-age 覆盖生效，且 evidence 标明来源是 cli。"""
        self._snap(ts_offset=-16000.0)
        _r = check_l6(self._ctx(l6_max_age=999999.0))
        self.assertEqual(_r["status"], PASS)
        self.assertEqual(_r["evidence"]["max_age_source"], "cli")
        self.assertEqual(_r["evidence"]["max_age_s"], 999999.0)

    def test_16_boundary_exactly_at_limit_passes(self):
        """≤ 上限算新鲜（边界闭区间，与 L5 的 `_snap_age <= _max` 一致）。"""
        self._snap(ts_offset=-3700.0)
        _r = check_l6(make_context(root=self._td, now=self._now,
                                   framework_running=True, l6_max_age=3700.0))
        # mtime 那条会更小，max 取 timestamp 的 3700.0 ⇒ 恰好 ≤ 上限
        self.assertEqual(_r["status"], PASS)
        self.assertLessEqual(_r["evidence"]["age_s"], 3700.0)


# ---------------------------------------------------------------------------
# 3) 年龄必须取 max(mtime, timestamp) —— 两个方向都要钉
# ---------------------------------------------------------------------------
class TestAgeTakesMoreConservative(_Base):

    def test_20_timestamp_old_but_mtime_new(self):
        """★文件刚写过（mtime 新），但内容自报很旧 ⇒ 必须按旧的判。

        只读 mtime 的实现会在这里 PASS —— 那等于"touch 一下就变健康"。
        """
        self._snap(ts_offset=-20000.0)
        _r = check_l6(self._ctx(framework_running=True))
        self.assertEqual(_r["status"], SKIP)
        self.assertGreaterEqual(_r["evidence"]["age_s"], 19000.0)
        self.assertLess(_r["evidence"]["age_from_file_s"], 60.0,
                        "本用例前提是 mtime 很新；若不新则用例失效")

    def test_21_mtime_old_but_timestamp_new(self):
        """文件被 touch 成很旧，但内容自报很新 ⇒ 必须按旧的判。

        只读 timestamp 的实现会在这里 PASS —— 那等于"内容自报啥就信啥"。
        """
        self._snap(ts_offset=0.0)
        _old = self._now - 20000.0
        os.utime(self._p, (_old, _old))
        _r = check_l6(self._ctx(framework_running=True))
        self.assertEqual(_r["status"], SKIP)
        self.assertGreaterEqual(_r["evidence"]["age_s"], 19000.0)
        self.assertLess(_r["evidence"]["age_from_ts_s"], 60.0,
                        "本用例前提是 timestamp 很新；若不新则用例失效")


# ---------------------------------------------------------------------------
# 4) 结构判据优先于年龄（不许因超龄把真故障降级成 SKIP）
# ---------------------------------------------------------------------------
class TestStructureBeforeAge(_Base):

    def test_30_wrong_schema_still_fails_even_if_stale(self):
        self._snap(ts_offset=-20000.0, schema="别的/1.0")
        _r = check_l6(self._ctx(framework_running=False))
        self.assertEqual(_r["status"], FAIL, "结构坏了必须 FAIL，不能被超龄降级")
        self.assertIn("schema", _r["detail"])

    def test_31_missing_warm_still_fails_even_if_stale(self):
        self._snap(ts_offset=-20000.0, warm=None)
        _r = check_l6(self._ctx(framework_running=False))
        self.assertEqual(_r["status"], FAIL)

    def test_32_both_empty_still_fails_even_if_stale(self):
        self._snap(ts_offset=-20000.0, warm={}, cold={})
        _r = check_l6(self._ctx(framework_running=False))
        self.assertEqual(_r["status"], FAIL)
        self.assertIn("都是空", _r["detail"])

    def test_33_missing_file_still_fails(self):
        _r = check_l6(self._ctx())
        self.assertEqual(_r["status"], FAIL)

    def test_34_unparseable_still_fails(self):
        with open(self._p, "w", encoding="utf-8") as f:
            f.write("{ 这不是 json")
        _r = check_l6(self._ctx())
        self.assertEqual(_r["status"], FAIL)


# ---------------------------------------------------------------------------
# 5) evidence 契约（排查时全靠它）
# ---------------------------------------------------------------------------
class TestEvidence(_Base):

    def test_40_evidence_exposes_both_age_sources(self):
        self._snap(ts_offset=-100.0)
        _ev = check_l6(self._ctx())["evidence"]
        for _k in ("age_s", "age_from_file_s", "age_from_ts_s",
                   "max_age_s", "max_age_source"):
            self.assertIn(_k, _ev, "evidence 缺 %s" % _k)

    def test_41_evidence_records_no_pid_field(self):
        """★钉住"本文件没有 pid 字段"这个事实。

        这是超龄只能 SKIP、不能像 L5 那样拿 pid 判 FAIL 的**唯一原因**。
        将来若有人给快照加了 pid，这条会红 —— 那时应当去补 pid 核验分支，
        而不是把断言删掉。
        """
        self._snap()
        _ev = check_l6(self._ctx())["evidence"]
        self.assertIs(_ev["has_pid_field"], False,
                      "小时快照开始带 pid 了 ⇒ 应补 pid 硬证据核验分支（见 check_l6 注释）")

    def test_42_evidence_records_framework_running_when_stale(self):
        self._snap(ts_offset=-20000.0)
        _ev = check_l6(self._ctx(framework_running=False))["evidence"]
        self.assertIs(_ev["framework_running"], False)


# ---------------------------------------------------------------------------
# 6) 与 L5 的口径一致性（同一份停机事实，两层不该一个 PASS 一个 SKIP）
# ---------------------------------------------------------------------------
class TestSameVerdictAsL5(_Base):

    def test_50_both_skip_when_framework_down(self):
        """L5 与 L6 在同一个"框架停机"输入下必须都给 SKIP + framework_down。

        改前实测：L5 SKIP(framework_down) 而 L6 PASS(age=15904.7s) —— 自相矛盾。
        """
        from tools.m404_layer_health_check import check_l5
        self._snap(ts_offset=-20000.0)
        _hs = os.path.join(self._td, "data", "monitor", "health_snapshot.json")
        with open(_hs, "w", encoding="utf-8") as f:
            json.dump({"timestamp": self._now - 20000.0, "pid": 99999999,
                       "beat_count": 3}, f)
        _ctx = self._ctx(framework_running=False)
        _r5 = check_l5(_ctx)
        _r6 = check_l6(_ctx)
        self.assertEqual(_r5["status"], SKIP)
        self.assertEqual(_r5["reason"], "framework_down")
        self.assertEqual(_r6["status"], SKIP)
        self.assertEqual(_r6["reason"], "framework_down")


if __name__ == "__main__":
    unittest.main()
