# -*- coding: utf-8 -*-
"""366 后续 · N4 续：写面重定向（REL 常量 → 沙箱绝对路径）。

守的契约：
  * 重定向表是**实测**得来（`logs/full_regression/n4_writemap/`），只有 2 项；
  * 手法是 patch 模块级 REL 常量成绝对路径 —— `os.path.join(root, abs)` 丢弃 root；
  * 生产代码零改动、被测测试零改动；
  * 设计内沙箱面（`data/.t_face_roster/`）单列 `n_designed`，**不计 dirty**；
  * 属性改名/模块不可导入时必须**明示跳过**（不能静默失效）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import conftest as cf


class TestRedirectPlan:
    def test_01_plan_covers_measured_write_sites(self):
        """表里只有实测出的写入点（2 条可重定向 + roster 走 designed 白名单）。"""
        plan = cf.m366_redirect_plan("D:/tmp/x")
        assert len(plan) == 2
        mods = {p[0] for p in plan}
        assert mods == {"nucleus.evolution.criteria_window",
                        "nucleus.evolution.evolution_budget"}
        for _mod, _attr, _tgt, _why in plan:
            assert os.path.isabs(_tgt), _tgt
            assert _why, "每项必须写明为何可安全重定向"
            assert _attr.endswith("_REL"), "只允许 patch REL 常量（不 patch 函数）"

    def test_02_plan_targets_under_tmp(self):
        plan = cf.m366_redirect_plan("D:/tmp/x")
        tails = {os.path.basename(p[2]) for p in plan}
        assert tails == {"converge_voided.jsonl", "evolution_budget.json"}
        assert all("tmp" in p[2].replace("\\", "/") for p in plan)

    def test_03_rel_patched_to_abs(self, m366_redirect_data_writes):
        """fixture 生效后：REL 变成绝对路径，join(root, REL) 仍返回该绝对路径。"""
        from nucleus.evolution import criteria_window as cw
        from nucleus.evolution import evolution_budget as eb

        assert os.path.isabs(cw.CONVERGE_VOIDED_LEDGER_REL)
        assert os.path.isabs(eb._M335_BUDGET_REL)
        # os.path.join 语义：绝对路径在前者之后会丢弃前者 —— 这是本方案的全部依据
        assert os.path.join("D:/any/root", cw.CONVERGE_VOIDED_LEDGER_REL) == \
            cw.CONVERGE_VOIDED_LEDGER_REL
        assert cw.converge_voided_ledger_path().startswith(
            str(m366_redirect_data_writes and "") or "")
        assert not os.path.dirname(cw.converge_voided_ledger_path()).startswith(
            os.path.join(cf.ROOT, "data"))

    def test_04_real_ledger_paths_land_in_tmp(self, m366_redirect_data_writes):
        """两个生产路径函数在重定向后都指到沙箱（不是项目 data/）。"""
        from nucleus.evolution import criteria_window as cw
        from nucleus.evolution import evolution_budget as eb

        _p1 = cw.converge_voided_ledger_path()
        _p2 = eb._m335_budget_path()
        for _p in (_p1, _p2):
            _norm = _p.replace("\\", "/")
            assert "/data/" in _norm
            assert not _norm.startswith(cf.ROOT.replace("\\", "/"))
            assert "pytest" in _norm or "tmp" in _norm.lower(), _norm

    def test_05_designed_prefix_not_dirty(self):
        """设计内沙箱面不计 dirty，但明细里可见（不隐藏）。"""
        b = {}
        a = {"data/.t_face_roster/r.json": [1, 1],
             "data/reports/real.jsonl": [2, 2]}
        d = cf.m366_diff_tree(b, a, ignore=cf._M366_DESIGNED_SUBDIRS)
        assert d["dirty"] is True
        assert d["n_added"] == 1
        assert d["added"] == ["data/reports/real.jsonl"]
        assert d["n_designed"] == 1
        assert d["designed"] == ["data/.t_face_roster/r.json"]

    def test_06_only_designed_means_clean(self):
        """只有设计内写入 ⇒ dirty=False（否则 roster 会长期占着脏位）。"""
        b = {}
        a = {"data/.t_face_roster/r1.json": [1, 1],
             "data/.t_face_roster/r2.json": [2, 2]}
        d = cf.m366_diff_tree(b, a, ignore=cf._M366_DESIGNED_SUBDIRS)
        assert d["dirty"] is False
        assert d["n_designed"] == 2

    def test_07_switch_off_means_no_patch(self, monkeypatch):
        """`PULSE_DATA_REDIRECT=0` 时不 patch（对照实验要能写生产）。"""
        from nucleus.evolution import criteria_window as cw

        _orig = cw.CONVERGE_VOIDED_LEDGER_REL
        monkeypatch.setenv("PULSE_DATA_REDIRECT", "0")
        _gen = cf.m366_redirect_data_writes.__wrapped__(
            __import__("pathlib").Path("D:/tmp/y"), monkeypatch)
        next(_gen)
        assert cw.CONVERGE_VOIDED_LEDGER_REL == _orig
        _gen.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
