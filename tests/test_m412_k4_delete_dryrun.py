# -*- coding: utf-8 -*-
"""412 批刀4 单测：O3 delete_only 判据 + dry-run 报表（契约 412.4）。

钉语义（不钉措辞）：
  * **识别 ≠ 放行**：`diff_kind()` 认得出 `delete_only`，但**默认不进窄闸**
    （任务书/红线：本批只 dry-run 不真 apply）⇒ 开闸走独立开关；
  * 只删不增的判据是"**无新增有效行** 且 **有效行变少**"；
  * 报表在**问题层**算（O3 条目没有 diff，补丁层只有 1 条样本）；
  * **397 教训**：调用图"零入度"≠ 真孤儿 ⇒ 文件必须在图内；覆盖不可判 ⇒ `None`；
  * 图不可用 ⇒ `eligible=None`（**不报 0**），dry-run 永远 `applied=0`。
"""

from __future__ import annotations

import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.evolution import o3_delete_dryrun as dd  # noqa: E402
from nucleus.evolution import patch_risk_gate as G  # noqa: E402


class _FakeGraph:
    def __init__(self, iso, nodes=None):
        self._iso = list(iso)
        self._nodes = list(nodes or [])
        self.calls = 0

    def get_isolated_functions(self):
        self.calls += 1
        return list(self._iso)

    def _ensure(self):
        return {"nodes": list(self._nodes)}


def _iso(name="m1", file="nucleus/x.py", public=False, entry=False):
    return {"id": "%s::%s" % (file, name), "name": name, "file": file, "line": 10,
            "type": "method", "public_api": public, "entry_like": entry}


def _idx(orphans=None, files=None):
    _o = list(orphans or [])
    _n = [{"file": f} for f in (files if files is not None
                                else sorted({str(x["file"]) for x in _o}))]
    return dd.isolated_index(_FakeGraph(_o, _n))


def _entry(**kw):
    _d = {"id": "e1", "issue_type": "dead_code", "status": "open",
          "file": "nucleus/x.py", "method": "m1", "line": 10}
    _d.update(kw)
    return _d


def _src(rel):
    _s = io.open(os.path.join(_ROOT, rel), encoding="utf-8", newline="").read()
    return _s.replace("\r\n", "\n")


# ---------------------------------------------------------------- A diff_kind 识别

def test_01_delete_only_recognised():
    _rec = {"file": "nucleus/x.py",
            "original_code": "def a():\n    return 1\n\ndef b():\n    return 2\n",
            "modified_code": "def a():\n    return 1\n"}
    assert G.diff_kind(_rec) == G.DELETE_ONLY_KIND


def test_02_added_line_means_not_delete_only():
    _rec = {"file": "nucleus/x.py",
            "original_code": "def a():\n    return 1\n",
            "modified_code": "def a():\n    return 2\n"}
    assert G.diff_kind(_rec) != G.DELETE_ONLY_KIND, "改了一行 ⇒ 不是只删不增"


def test_03_comment_only_still_wins():
    _rec = {"file": "nucleus/x.py",
            "original_code": "def a():\n    return 1\n",
            "modified_code": "def a():\n    # 注释\n    return 1\n"}
    assert G.diff_kind(_rec) == "comment_only"


def test_04_delete_only_not_in_narrow_kinds_by_default():
    """★默认不开闸：认得出，但**不放行**（本批红线：不真 apply）。"""
    assert G.DELETE_ONLY_KIND not in G.narrow_kinds()
    assert G.narrow_kinds() == ("comment_only", "doc_only", "test_only")


def test_05_delete_only_gate_switch(monkeypatch):
    monkeypatch.setattr(config, "PATCH_M412_DELETE_ONLY_GATE", True, raising=False)
    assert G.DELETE_ONLY_KIND in G.narrow_kinds()
    assert G.gate_status()["delete_only_enabled"] is True
    monkeypatch.setattr(config, "PATCH_M412_DELETE_ONLY_GATE", False, raising=False)
    assert G.DELETE_ONLY_KIND not in G.narrow_kinds()


def test_06_delete_only_not_eligible_when_gate_closed():
    _rec = {"file": "nucleus/x.py",
            "original_code": "def a():\n    return 1\n\ndef b():\n    return 2\n",
            "modified_code": "def a():\n    return 1\n"}
    assert G.diff_kind(_rec) == G.DELETE_ONLY_KIND
    assert G.judge(_rec)["eligible"] is False, "闸没开 ⇒ 不许放行删代码"


# ---------------------------------------------------------------- B 孤儿索引

def test_11_index_builds_orphans_and_coverage():
    _i = _idx([_iso("m1", "nucleus/x.py")], files=["nucleus/x.py", "nucleus/y.py"])
    assert _i["state"] == "ok"
    assert ("nucleus/x.py", "m1") in _i["orphans"]
    assert _i["files"] == {"nucleus/x.py", "nucleus/y.py"}


def test_12_empty_graph_is_empty_not_ok():
    assert _idx([], files=[])["state"] == "empty"


def test_13_broken_graph_is_unavailable():
    class _Bad:
        def get_isolated_functions(self):
            raise RuntimeError("图坏了")

    _i = dd.isolated_index(_Bad())
    assert _i["state"] == "unavailable"
    assert _i["reason"].startswith("graph_failed")


# ---------------------------------------------------------------- C 单条判据

def test_21_all_three_conditions_give_eligible():
    _i = _idx([_iso("m1", "nucleus/x.py")], files=["nucleus/x.py"])
    _j = dd.judge_entry(_entry(), _i)
    assert _j["eligible"] is True
    assert _j["reasons"] == []


def test_22_non_o3_type_rejected():
    _i = _idx([_iso()], files=["nucleus/x.py"])
    assert dd.judge_entry(_entry(issue_type="long_method"), _i)["eligible"] is False
    assert "not_o3_type" in dd.judge_entry(_entry(issue_type="long_method"), _i)["reasons"]


def test_23_claimed_or_fixed_not_touched():
    _i = _idx([_iso()], files=["nucleus/x.py"])
    for _st in ("claimed", "fixed", "stale"):
        assert dd.judge_entry(_entry(status=_st), _i)["eligible"] is False


def test_24_file_outside_graph_is_rejected():
    """★397 教训：没采到 ≠ 没人调用 ⇒ 必须拒。"""
    _i = _idx([_iso("m1", "nucleus/x.py")], files=["nucleus/x.py"])
    _j = dd.judge_entry(_entry(file="nucleus/zzz.py"), _i)
    assert _j["eligible"] is False
    assert any("not_in_graph" in r for r in _j["reasons"])


def test_25_public_api_and_entry_like_rejected():
    _i = _idx([_iso("pub", "nucleus/x.py", public=True)], files=["nucleus/x.py"])
    assert dd.judge_entry(_entry(method="pub"), _i)["eligible"] is False
    _i2 = _idx([_iso("run", "nucleus/x.py", entry=True)], files=["nucleus/x.py"])
    assert dd.judge_entry(_entry(method="run"), _i2)["eligible"] is False


def test_26_core_file_never_eligible():
    _i = _idx([_iso("get_x", "config.py")], files=["config.py"])
    _j = dd.judge_entry(_entry(file="config.py", method="get_x"), _i)
    assert _j["eligible"] is False
    assert "core_file" in " ".join(_j["reasons"])


def test_27_unknown_coverage_is_none_not_false():
    """★`None`（判不了）与 `False`（判了不行）必须可分。"""
    _i = {"state": "ok", "orphans": {("nucleus/x.py", "m1"): _iso()}, "files": None}
    _j = dd.judge_entry(_entry(), _i)
    assert _j["eligible"] is None
    assert "graph_coverage_unknown" in _j["reasons"]


def test_28_graph_unavailable_is_none():
    _i = {"state": "unavailable", "orphans": {}, "files": None, "reason": "x"}
    assert dd.judge_entry(_entry(), _i)["eligible"] is None


# ---------------------------------------------------------------- D 报表

def _report(entries, graph=None, **kw):
    _g = graph or _FakeGraph([_iso("m1", "nucleus/x.py")], [{"file": "nucleus/x.py"}])
    return dd.report(entries=entries, graph=_g, **kw)


def test_31_report_counts_eligible_and_samples():
    _r = _report([_entry(), _entry(id="e2", issue_type="long_method")])
    assert _r["dry_run"] is True and _r["applied"] == 0, "★本批绝不真 apply"
    assert _r["o3_total"] == 1
    assert _r["eligible"] == 1
    assert _r["samples"][0]["id"] == "e1"


def test_32_report_reason_distribution():
    _r = _report([_entry(id="a", file="nucleus/zzz.py"),
                  _entry(id="b", status="claimed")])
    assert _r["eligible"] == 0
    assert _r["rejected"], "拒因必须分类列出，只给个总数没法判断误判率"


def test_33_empty_graph_gives_none_not_zero():
    """★图空 ⇒ 报 None（0 会被读成"没有可放行的"，那是假结论）。"""
    _r = _report([_entry()], graph=_FakeGraph([], []))
    assert _r["graph"]["state"] == "empty"
    assert _r["eligible"] is None
    assert _r["reason"]


def test_34_switch_off_means_unavailable(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M412_O3_DELETE_DRYRUN", False, raising=False)
    _r = _report([_entry()])
    assert _r["graph"]["state"] == "unavailable"
    assert _r["eligible"] is None


def test_35_no_entries_is_not_a_false_zero():
    _r = dd.report(entries=[], graph=_FakeGraph([_iso()], [{"file": "nucleus/x.py"}]))
    assert _r["o3_total"] == 0 and _r["eligible"] == 0
    assert _r["entries_state"] == "ok" and _r["scanned"] == 0


def test_36_basis_is_recorded():
    _r = _report([_entry()])
    assert "O3判死族" in _r["basis"] and "非核心文件" in _r["basis"]
    assert _r["version"].startswith("412.")


# ---------------------------------------------------------------- E 静态

def test_41_o3_types_match_audit_pool():
    """两处 O3 判死族必须同集（383 刀4 定的规矩，别让它们分叉）。"""
    import nucleus.audit_problem_pool as ap
    assert tuple(dd.O3_DEAD_TYPES) == tuple(ap._M383_O3_DEAD_TYPES)


def test_42_judge_uses_current_narrow_kinds():
    _s = _src("nucleus/evolution/patch_risk_gate.py")
    assert "narrow_kinds()" in _s
    assert "DELETE_ONLY_KIND" in _s


def test_43_version_marker():
    assert dd.version().startswith("412.")
