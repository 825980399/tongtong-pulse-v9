# -*- coding: utf-8 -*-
"""366 后续 · E1：生成侧三要素证据（复用派单侧同源派生）。

守的契约（模块 docstring 定的边界）：
  * 裁决权仍归派单侧 `_m338_evidence_ok`，生成侧只提供**可复算原料**；
  * 派生逻辑复用 `_m338_derive_evidence`（**单点**），本模块不另建 rank/trust 表；
  * risk 走唯一裁决口（只升不降），trust 不得落在"恒 70 默认"（否则派单侧拒收）；
  * 不可判定时**如实报不可复算**，不伪造 value。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from nucleus.evolution import patch_generator as pg
from nucleus.evolution import patch_execution_dispatch as ped


ENTRY_HIGH = {"file": "a/b.py", "method": "run", "issue_type": "sql_injection",
              "severity": "high", "klass": "k", "desc": "d"}


class TestE1Evidence:
    def test_01_risk_escalated_not_downgraded(self):
        """显式"低" + sql_injection ⇒ 判高（只升不降），且 source 走 issue_type 族。"""
        e = dict(ENTRY_HIGH, risk_level="低")
        r = pg._m366_entry_evidence(e)
        ev = r["patch_evidence"]
        assert ev["risk"]["value"] == "高"
        assert ev["risk"]["source"] == "_m280_risk_by_issue_type"
        assert r["evidence_meta"]["risk_source"] == "escalated_by_issue_type"

    def test_02_trust_derived_not_default_70(self):
        """不存在的 trust_score 必须按 issue_type 派生，不得落恒 70（否则派单侧拒收）。"""
        r = pg._m366_entry_evidence(dict(ENTRY_HIGH))
        ev = r["patch_evidence"]
        assert ev["trust"]["value"] == 20.0
        assert ev["trust"]["source"] == "_m280_trust_by_issue_type"
        assert ev["trust"]["source"] != "default"
        assert r["evidence_meta"]["trust_source"] == "_m280_trust_by_issue_type"

    def test_03_same_source_as_dispatch(self):
        """同源：本函数与派单侧派生函数对同一输入的产出**完全一致**（不另建表）。"""
        e = dict(ENTRY_HIGH)
        mine = pg._m366_entry_evidence(e)["patch_evidence"]
        theirs = ped._m338_derive_evidence({
            "issue_type": "sql_injection", "risk_level": "高",
            "method": "run", "file": "a/b.py", "trust_score": 20.0})
        assert mine == theirs
        assert mine["evidence_version"] == theirs["evidence_version"]

    def test_04_all_exits_carry_evidence(self, tmp_path):
        """三态出口都带同一份证据（不是只有 draft_ready 才有）。"""
        (tmp_path / "c.py").write_text("def keep():\n    return 1\n",
                                       encoding="utf-8")
        e_drift = {"file": "c.py", "method": "gone", "issue_type": "sql_injection",
                   "severity": "high", "klass": "k", "desc": "d"}

        def _tp_fail(prompt, **kw):
            return {"ok": False, "err": "boom", "text": ""}

        r1 = pg.generate_patch_draft(e_drift, str(tmp_path),
                                     transport=_tp_fail, now=1.0)
        assert r1["state"] == pg.STATE_TARGET_DRIFTED
        assert isinstance(r1.get("patch_evidence"), dict), "漂移态缺证据"
        assert r1["patch_evidence"]["risk"]["value"] == "高"

        e_ok = dict(e_drift, method="keep")
        r2 = pg.generate_patch_draft(e_ok, str(tmp_path),
                                     transport=_tp_fail, now=1.0)
        assert r2["state"] == pg.STATE_GENERATION_FAILED
        assert isinstance(r2.get("patch_evidence"), dict), "失败态缺证据"
        assert r1["patch_evidence"] == r2["patch_evidence"], "两态证据不一致"

    def test_05_blast_unjudgeable_reports_not_ok(self):
        """图里没有该方法 ⇒ blast 不可判定 ⇒ 如实 ok=False（不伪造 0）。"""
        r = pg._m366_entry_evidence(
            {"file": "nope/zzz.py", "method": "zzz_not_in_graph",
             "issue_type": "silent_exception"})
        ev = r["patch_evidence"]
        assert ev.get("blast") is None
        assert r["evidence_meta"]["ok"] is False
        assert "blast" in r["evidence_meta"]["reason"]

    def test_06_entry_trust_overrides(self):
        """条目自带 trust_score ⇒ 优先用条目值，来源标注 entry。"""
        r = pg._m366_entry_evidence(dict(ENTRY_HIGH, trust_score="95"))
        assert r["patch_evidence"]["trust"]["value"] == 95.0
        assert r["evidence_meta"]["trust_source"] == "entry"

    def test_07_empty_entry_never_fakes(self):
        """空条目 ⇒ 无证据且明确 no_entry，绝不编造三要素。"""
        for bad in ({}, None, "x"):
            r = pg._m366_entry_evidence(bad)
            assert r["patch_evidence"] is None
            assert r["evidence_meta"]["ok"] is False
            assert r["evidence_meta"]["reason"] == "no_entry"

    def test_08_risk_source_explicit_when_higher(self):
        """显式"高" + 低危族 ⇒ 保留显式（只升不降的另一侧：不降级）。"""
        r = pg._m366_entry_evidence(
            {"file": "a/b.py", "method": "run", "issue_type": "periodic_task_no_reentry",
             "risk_level": "高"})
        assert r["patch_evidence"]["risk"]["value"] == "高"
        assert r["evidence_meta"]["risk_source"] == "explicit"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
