# -*- coding: utf-8 -*-
"""411 批刀5 单测：过期观察位归档（m411_archive_expired_watch）。

钉语义（不钉措辞）：
  * **搬出 pending，不是原地改状态** —— 两个状态闸（`zero_break_judge._m407_watch_alive`
    与 `patch_execution_dispatch._m339_o3_dispatch_ready`）都只拦 `expired_pending`；
    若把条目原地改成 `archived` 留在 pending，这 3 条死代码候选**会被真的派单**；
  * `status_history` 必须**保留原有记录再追加**（T0 实测原条目 0/3 有这个字段）；
  * 默认 dry-run：一个字节都不许写；`--apply` 先备份再原子替换；
  * `_state_probe()` 必须**快照/恢复** `zero_break_judge._WATCH_RUNTIME`
    （否则第二次 probe 会看到上一次补进来的位，判出"healthy"的假象）。

本文件用 `importlib` 直接加载工具文件（`tools/` 不是包），并在导入后复原 cwd。
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import types

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load_tool():
    _p = os.path.join(_ROOT, "tools", "m411_archive_expired_watch.py")
    _spec = importlib.util.spec_from_file_location("_m411_archive_tool", _p)
    _m = importlib.util.module_from_spec(_spec)
    _cwd = os.getcwd()
    try:
        _spec.loader.exec_module(_m)
    finally:
        os.chdir(_cwd)  # 工具在模块级 chdir 到项目根，测试里立刻复原
    return _m


aw = _load_tool()


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _rows(n_normal=9, n_expired=3):
    _out = [{"id": "n%02d" % i, "status": "pending", "file": "f%02d.py" % i}
            for i in range(n_normal)]
    _out += [{"id": "x%02d" % i, "status": "expired_pending",
              "file": "dead%02d.py" % i, "created_at": 1_700_000_000.0,
              "reason": "dead_code_candidate"} for i in range(n_expired)]
    return _out


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """把 pending / archive 两个路径都指到隔离目录，并让自愈判据变轻。"""
    _dir = tmp_path / "patches"
    _pend = _dir / "pending_patches.json"
    _arch = _dir / "pending_patches_archive.json"
    monkeypatch.setattr(aw, "PENDING_REL", str(_pend))
    monkeypatch.setattr(aw, "ARCHIVE_REL", str(_arch))
    monkeypatch.setattr(aw, "_state_probe",
                        lambda rows: {"state": "healed",
                                      "n_active": len(rows),
                                      "n_eligible": sum(
                                          1 for r in rows
                                          if isinstance(r, dict)
                                          and r.get("status") == "expired_pending"),
                                      "why": "替身", "skipped": [], "dead_active": []})
    return _pend, _arch


def _argv(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["m411_archive"] + list(args))


# ---------------------------------------------------------------- A 条目标注

def test_01_mark_creates_status_history_when_absent():
    _out = aw._mark({"id": "w1", "status": "expired_pending"}, 1000.0, "测试原因")
    assert _out["status"] == aw.ARCHIVED_STATUS == "archived"
    assert _out["status_before_archive"] == "expired_pending"
    assert _out["archived_at"] == 1000.0
    assert _out["archived_by"] == "411刀5_archive_expired_watch"
    assert _out["archive_reason"] == "测试原因"
    assert len(_out["status_history"]) == 1
    _h = _out["status_history"][0]
    assert _h["status"] == "expired_pending"
    assert _h["by"] == "411刀5_archive_expired_watch"
    assert _h["reason"] == "测试原因"
    assert _h["at"] == 1000.0
    assert _h["at_str"]


def test_02_mark_preserves_existing_history():
    _hist = [{"status": "pending", "at": 1.0},
             {"status": "expired_pending", "at": 2.0, "note": "超龄"}]
    _out = aw._mark({"id": "w2", "status": "expired_pending",
                     "status_history": [dict(x) for x in _hist]}, 1000.0, "r")
    assert len(_out["status_history"]) == 3
    assert _out["status_history"][0] == _hist[0]
    assert _out["status_history"][1] == _hist[1], "原有记录一个字段都不许改"
    assert _out["status_history"][2]["status"] == "expired_pending"


def test_03_mark_keeps_other_fields_untouched():
    _row = {"id": "w3", "status": "expired_pending", "file": "x.py",
            "reason": "dead_code_candidate", "score": 0.7, "created_at": 5.0}
    _out = aw._mark(dict(_row), 1.0, "r")
    for _k, _v in _row.items():
        if _k == "status":
            continue
        assert _out[_k] == _v, "归档不许顺手改字段"


def test_04_mark_tolerates_broken_history():
    _out = aw._mark({"id": "w4", "status": "expired_pending",
                     "status_history": "垃圾"}, 1.0, "r")
    assert isinstance(_out["status_history"], list)
    assert len(_out["status_history"]) == 1


# ---------------------------------------------------------------- B 归档文件形态

def test_11_archive_doc_missing_returns_empty(tmp_path):
    assert aw._archive_doc(str(tmp_path / "nope.json")) == {
        "version": "m411", "items": []}


def test_12_archive_doc_accepts_list_form(tmp_path):
    _p = tmp_path / "a.json"
    _write(_p, [{"id": 1}])
    assert aw._archive_doc(str(_p))["items"] == [{"id": 1}]


def test_13_archive_doc_keeps_dict_form(tmp_path):
    _p = tmp_path / "a.json"
    _write(_p, {"version": "老版本", "items": [{"id": 1}], "extra": 1})
    _d = aw._archive_doc(str(_p))
    assert _d["version"] == "老版本"
    assert _d["items"] == [{"id": 1}]
    assert _d["extra"] == 1


def test_14_archive_doc_refuses_unknown_shapes(tmp_path):
    _bad = tmp_path / "bad.json"
    _bad.write_text("{不是 json", encoding="utf-8")
    assert aw._archive_doc(str(_bad)) is None
    _odd = tmp_path / "odd.json"
    _write(_odd, {"no_items": []})
    assert aw._archive_doc(str(_odd)) is None


# ---------------------------------------------------------------- C 端到端

def test_21_dry_run_writes_nothing(sandbox, monkeypatch, capsys):
    _pend, _arch = sandbox
    _write(_pend, _rows())
    _before = _pend.read_bytes()
    _argv(monkeypatch)
    assert aw.main() == 0
    assert _pend.read_bytes() == _before, "默认 dry-run 一个字节都不许写"
    assert not _arch.exists()
    assert not list(_pend.parent.glob("*.bak_m411_*"))


def test_22_apply_moves_out_of_pending_and_archives(sandbox, monkeypatch, capsys):
    _pend, _arch = sandbox
    _rows_in = _rows(9, 3)
    _write(_pend, _rows_in)
    _argv(monkeypatch, "--apply")
    assert aw.main() == 0

    _left = json.loads(_pend.read_text(encoding="utf-8"))
    assert len(_left) == 9
    assert [r["id"] for r in _left] == ["n%02d" % i for i in range(9)]
    # ★关键：pending 里不许留 status=="archived" 的条目（那会被真的派单）
    assert all(r.get("status") != aw.ARCHIVED_STATUS for r in _left)

    _doc = json.loads(_arch.read_text(encoding="utf-8"))
    assert isinstance(_doc.get("items"), list)
    assert len(_doc["items"]) == 3
    assert _doc.get("last_archived_at")
    for _it in _doc["items"]:
        assert _it["status"] == aw.ARCHIVED_STATUS
        assert _it["status_before_archive"] == "expired_pending"
        assert _it["status_history"] and _it["status_history"][-1]["by"]
        assert _it["file"].startswith("dead")
    # 先备份再动
    assert len(list(_pend.parent.glob("pending_patches.json.bak_m411_*"))) == 1


def test_23_no_targets_touches_nothing(sandbox, monkeypatch, capsys):
    _pend, _arch = sandbox
    _write(_pend, _rows(4, 0))
    _before = _pend.read_bytes()
    _argv(monkeypatch, "--apply")
    assert aw.main() == 0
    assert _pend.read_bytes() == _before
    assert not _arch.exists(), "没有可归档条目时不许创建空归档文件"


def test_24_archive_appends_instead_of_overwriting(sandbox, monkeypatch, capsys):
    _pend, _arch = sandbox
    _write(_pend, _rows(2, 2))
    _write(_arch, {"version": "m411", "items": [{"id": "old1", "status": "archived"}]})
    _argv(monkeypatch, "--apply")
    assert aw.main() == 0
    _doc = json.loads(_arch.read_text(encoding="utf-8"))
    assert len(_doc["items"]) == 3
    assert _doc["items"][0]["id"] == "old1", "老归档必须留在最前面"


def test_25_keep_file_skips_archive_write(sandbox, monkeypatch, capsys):
    _pend, _arch = sandbox
    _write(_pend, _rows(3, 2))
    _argv(monkeypatch, "--apply", "--keep-file")
    assert aw.main() == 0
    assert len(json.loads(_pend.read_text(encoding="utf-8"))) == 3
    assert not _arch.exists()


def test_26_written_files_are_pure_lf(sandbox, monkeypatch, capsys):
    _pend, _arch = sandbox
    _write(_pend, _rows(1, 1))
    _argv(monkeypatch, "--apply")
    assert aw.main() == 0
    for _p in (_pend, _arch):
        _b = _p.read_bytes()
        assert _b.count(b"\r\n") == 0
        assert _b.count(b"\n") > 0


def test_27_missing_pending_fails_without_crash(sandbox, monkeypatch, capsys):
    _pend, _arch = sandbox
    _argv(monkeypatch, "--apply")
    assert aw.main() == 1
    assert not _pend.exists()
    assert not _arch.exists()


def test_28_pending_not_a_list_fails_without_crash(sandbox, monkeypatch, capsys):
    _pend, _arch = sandbox
    _write(_pend, {"rows": []})
    _before = _pend.read_bytes()
    _argv(monkeypatch, "--apply")
    assert aw.main() == 1
    assert _pend.read_bytes() == _before
    assert not _arch.exists()


# ---------------------------------------------------------------- D 自愈判据探针

def test_31_state_probe_restores_watch_runtime(monkeypatch):
    """`self_heal_watch()` 会**原地追加** `_WATCH_RUNTIME` —— 不恢复就会污染下一次判定。"""
    _fake = types.ModuleType("nucleus.evolution.zero_break_judge")
    _fake._WATCH_RUNTIME = [{"id": "原有的位"}]

    def _self_heal(pending_rows=None, history_rows=None):
        _fake._WATCH_RUNTIME.append({"id": "补进来的位"})
        return {"state": "healed", "n_active": 1, "n_eligible": 1,
                "why": "替身", "skipped": [], "dead_active": []}

    _fake.self_heal_watch = _self_heal
    monkeypatch.setitem(sys.modules, "nucleus.evolution.zero_break_judge", _fake)
    import nucleus.evolution as _evo
    monkeypatch.setattr(_evo, "zero_break_judge", _fake, raising=False)

    _r = aw._state_probe([{"id": "a", "status": "pending"}])
    assert _r["state"] == "healed"
    assert _fake._WATCH_RUNTIME == [{"id": "原有的位"}], \
        "必须快照/恢复 _WATCH_RUNTIME"


def test_32_state_probe_returns_none_when_judge_unavailable(monkeypatch, capsys):
    _fake = types.ModuleType("nucleus.evolution.zero_break_judge")

    def _boom(**k):
        raise RuntimeError("判据坏了（替身）")

    _fake._WATCH_RUNTIME = []
    _fake.self_heal_watch = _boom
    monkeypatch.setitem(sys.modules, "nucleus.evolution.zero_break_judge", _fake)
    import nucleus.evolution as _evo
    monkeypatch.setattr(_evo, "zero_break_judge", _fake, raising=False)
    assert aw._state_probe([]) is None, "判据调不通就如实返回 None，不编造状态"


# ---------------------------------------------------------------- E 常量语义

def test_41_status_constants():
    assert aw.TARGET_STATUS == "expired_pending"
    assert aw.ARCHIVED_STATUS == "archived"
    assert aw.PENDING_REL.replace(os.sep, "/").endswith("data/patches/pending_patches.json")
    assert aw.ARCHIVE_REL.replace(os.sep, "/").endswith(
        "data/patches/pending_patches_archive.json")


def test_42_tool_docstring_warns_about_dispatch_gate():
    _s = io.open(os.path.join(_ROOT, "tools", "m411_archive_expired_watch.py"),
                 encoding="utf-8").read()
    assert "_m339_o3_dispatch_ready" in _s, "必须写明「原地改状态会被真的派单」的风险"
    assert "expired_pending" in _s and "archived" in _s
