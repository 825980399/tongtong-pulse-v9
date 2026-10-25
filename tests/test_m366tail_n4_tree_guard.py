# -*- coding: utf-8 -*-
"""366 后续 · N4 续：全树写入探测（哨兵守卫 v2）。

守的契约：
  * 三件哨兵仍**可还原**（v1 能力不回退）；
  * 全树清单只 stat 不读内容，三态（新增/删除/修改）分得开、可截断；
  * 探测默认开（0.87s），`PULSE_DATA_TREE_GUARD=0` 关；
  * 逐测试归因只在 `=1` 时开（每例 +1.7s，全量回归不能开）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import conftest as cf


class TestTreeGuard:
    def test_01_diff_three_states(self):
        """新增/删除/修改三态必须能分开（混成一堆就没法定位写面）。"""
        b = {"data/a": [1, 100], "data/b": [2, 200], "data/c": [3, 300]}
        a = {"data/a": [1, 100], "data/b": [9, 999], "data/d": [4, 400]}
        d = cf.m366_diff_tree(b, a)
        assert d["added"] == ["data/d"]
        assert d["removed"] == ["data/c"]
        assert d["modified"] == ["data/b"]
        assert d["n_added"] == 1 and d["n_removed"] == 1 and d["n_modified"] == 1
        assert d["dirty"] is True

    def test_02_clean_is_not_dirty(self):
        """全等 ⇒ dirty=False（零误报是这层能常开的前提）。"""
        b = {"data/x": [1, 1]}
        d = cf.m366_diff_tree(b, dict(b))
        assert d["dirty"] is False
        assert d["n_added"] == d["n_removed"] == d["n_modified"] == 0

    def test_03_cap_truncates_but_counts(self):
        """截断只影响明细，**计数必须是真值**（否则报"改了 200 个"骗人）。"""
        b = {}
        a = {"data/f%03d" % i: [1, i] for i in range(250)}
        d = cf.m366_diff_tree(b, a, cap=10)
        assert len(d["added"]) == 10
        assert d["n_added"] == 250

    def test_04_switch_semantics(self, monkeypatch):
        """默认开；=0 关；逐测试归因只认 =1（这两件事不能共用一个旋钮）。"""
        monkeypatch.delenv("PULSE_DATA_TREE_GUARD", raising=False)
        assert cf.m366_tree_guard_enabled() is True
        assert cf.m366_per_test_attribution() is False

        monkeypatch.setenv("PULSE_DATA_TREE_GUARD", "0")
        assert cf.m366_tree_guard_enabled() is False
        assert cf.m366_per_test_attribution() is False

        monkeypatch.setenv("PULSE_DATA_TREE_GUARD", "1")
        assert cf.m366_tree_guard_enabled() is True
        assert cf.m366_per_test_attribution() is True

    def test_05_real_scan_shape(self):
        """真扫一遍：键是相对 posix 路径且以 data/ 开头，值是有序对（只 stat）。"""
        snap = cf.m366_snapshot_tree()
        assert len(snap) > 100, "data/ 扫描面异常小：%d" % len(snap)
        _k = sorted(snap)[:50]
        assert all(k.startswith("data/") for k in _k), _k[:3]
        assert all("\\" not in k for k in _k), "键未归一为 posix"
        _v = snap[_k[0]]
        assert isinstance(_v, list) and len(_v) == 2
        assert all(isinstance(x, int) for x in _v)

    def test_06_empty_inputs_safe(self):
        """空/None 输入不崩、不报脏（守卫自身不能成为噪声源）。"""
        for bad in (None, {}):
            d = cf.m366_diff_tree(bad, bad)
            assert d["dirty"] is False
            assert d["n_before"] == 0

    def test_07_sentinels_still_restorable(self):
        """v1 能力不回退：三件哨兵仍在名单里，且备份/比对函数可用。"""
        assert len(cf._M366_SENTINELS) == 3
        assert all(str(s).startswith("data" + os.sep) or str(s).startswith("data/")
                   for s in cf._M366_SENTINELS)
        assert callable(cf.m366_backup_sentinels)
        assert callable(cf.m366_diff_and_restore)
        snap = cf.m366_snapshot_sentinels()
        assert set(snap) == set(cf._M366_SENTINELS)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
