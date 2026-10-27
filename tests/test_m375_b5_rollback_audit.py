# -*- coding: utf-8 -*-
"""★375批刀5：回滚备份失效 —— 标记改「实测」，存量只复核不改账。

实测（本仓 `data/patches/patch_history.json`，375 刀5）：
    applied = 76，**全部** rollback_available=True，**全部**有 backup_path，
    但备份物理可读只有 **20** 条 ⇒ **56/76 物理不可回滚**。

根因不是谁写错：标记是「写入那一刻的真话」，后来 `data/code_backups/` 的保留策略
把旧目录清掉，标记没跟着变；坏在**没有复核口**，于是 `patch_lifecycle` 的
「无回滚能力不得进 activated」硬点被架空。

本刀：新写入点先实测再置标记（不再产出假可回滚）；存量只出复核清单，
**不改数据**（任务书：不改审计池数据，不写 data/ 活体对象）。
"""
import ast
import json
import os

from nucleus.evolution import rollback_audit as RA

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PATCH_MANAGER = os.path.join(ROOT, "nucleus", "reasoning", "PatchManager.py")
MODULE = os.path.join(ROOT, "nucleus", "evolution", "rollback_audit.py")


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ============================ ① 唯一判定口：四种形态


def test_01_empty_backup_path_is_not_rollbackable():
    _ok, _why = RA.backup_present({"backup_path": ""})
    assert _ok is False, "没有备份就是没有，不许当「未知」放过"
    assert _why == "no_backup_path"


def test_02_backup_dir_non_empty(tmp_path):
    _d = tmp_path / "backup_20260101_000000"
    _d.mkdir()
    (_d / "x.py").write_text("x", encoding="utf-8")
    _ok, _why = RA.backup_present({"backup_path": str(_d)})
    assert _ok is True and _why == "backup_dir_ok"


def test_03_backup_dir_empty_is_not_rollbackable(tmp_path):
    _d = tmp_path / "backup_empty"
    _d.mkdir()
    _ok, _why = RA.backup_present({"backup_path": str(_d)})
    assert _ok is False, "空目录里没有可还原的内容"
    assert _why == "backup_dir_empty"


def test_04_backup_file(tmp_path):
    _f = tmp_path / "x.py.patch_backup_1"
    _f.write_text("x", encoding="utf-8")
    assert RA.backup_present({"backup_path": str(_f)}) == (True, "backup_file_ok")


def test_05_backup_missing(tmp_path):
    _ok, _why = RA.backup_present({"backup_path": str(tmp_path / "nope")})
    assert _ok is False and _why == "backup_missing"


def test_06_relative_path_resolved_against_root(tmp_path):
    _d = tmp_path / "b"
    _d.mkdir()
    (_d / "f").write_text("x", encoding="utf-8")
    assert RA.backup_present({"backup_path": "b"}, root=str(tmp_path))[0] is True
    assert RA.backup_present({"backup_path": "b"}, root=str(tmp_path / "elsewhere"))[0] is False


def test_14_default_root_is_project_root(monkeypatch, tmp_path):
    """相对路径的默认解析基准必须是**项目根**，不许是 cwd。

    实测依据：`patch_history.json` 的 76/76 条 `file` 是相对路径，兜底备份分支
    `patch["file"] + ".patch_backup_TS"` 因此也产出相对路径 ⇒ 生产裸调用
    `backup_present(patch)` 时基准若取 cwd，「能否回滚」就随进程启动目录漂移。
    """
    monkeypatch.chdir(tmp_path)
    assert os.path.normcase(RA._PROJECT_ROOT) == os.path.normcase(ROOT), \
        "模块常量与测试算出的项目根不一致"
    assert RA._abs_path("data") == os.path.join(RA._PROJECT_ROOT, "data")
    assert RA._abs_path("x") != os.path.join(str(tmp_path), "x"), \
        "基准跟着 cwd 走了 ⇒ 换启动目录就换结论"


# ============================ ② 存量复核：双向都报


def _fixture(tmp_path):
    _okd = tmp_path / "ok_backup"
    _okd.mkdir()
    (_okd / "f").write_text("x", encoding="utf-8")
    return [
        {"id": "a1", "applied": True, "rollback_available": True, "backup_path": str(_okd)},
        {"id": "a2", "applied": True, "rollback_available": True,
         "backup_path": str(tmp_path / "gone")},
        {"id": "a3", "applied": True, "rollback_available": True, "backup_path": ""},
        {"id": "a4", "applied": True, "rollback_available": False, "backup_path": str(_okd)},
        {"id": "n1", "applied": False, "rollback_available": True, "backup_path": str(_okd)},
    ]


def test_07_audit_counts(tmp_path):
    _r = RA.audit(_fixture(tmp_path), root=str(tmp_path))
    assert _r["applied"] == 4, "非 applied 条目不该计入"
    assert _r["marked_rollbackable"] == 3
    assert _r["effective_rollbackable"] == 1
    assert _r["marked_but_missing"] == 2
    assert _r["missing_reasons"] == {"backup_missing": 1, "no_backup_path": 1}
    assert _r["scope"], "口径必须写进报告（铁律219）"


def test_08_audit_reports_opposite_direction_too(tmp_path):
    """未标 True 但备份其实在 —— 同样算口径错，不许只报一个方向。"""
    _r = RA.audit(_fixture(tmp_path), root=str(tmp_path))
    assert _r["unmarked_but_present"] == 1


# ============================ ③ reconcile：默认不许改东西


def test_09_dry_run_changes_nothing(tmp_path):
    _ps = _fixture(tmp_path)
    _before = json.dumps(_ps, ensure_ascii=False, sort_keys=True)
    _r = RA.reconcile(_ps, apply=False, root=str(tmp_path))
    assert _r["changed"] == 2
    assert json.dumps(_ps, ensure_ascii=False, sort_keys=True) == _before, "dry-run 改了对象"


def test_10_apply_changes_memory_only(tmp_path):
    _ps = _fixture(tmp_path)
    _r = RA.reconcile(_ps, apply=True, root=str(tmp_path), now=1234567890.0)
    assert _r["changed"] == 2
    _by = {p["id"]: p for p in _ps}
    assert _by["a2"]["rollback_available"] is False
    assert _by["a2"]["rollback_unavailable_reason"] == "backup_missing"
    assert _by["a3"]["rollback_unavailable_reason"] == "no_backup_path"
    assert _by["a2"]["rollback_reconciled_at"] == 1234567890.0
    # 备份仍在的那条不许动
    assert _by["a1"]["rollback_available"] is True
    assert "rollback_unavailable_reason" not in _by["a1"]
    # 历史账保留：backup_path 不清空
    assert _by["a2"]["backup_path"], "不许清空 backup_path（那是当年备份位置的唯一记录）"


def test_11_reconcile_is_idempotent(tmp_path):
    _ps = _fixture(tmp_path)
    _r1 = RA.reconcile(_ps, apply=True, root=str(tmp_path), now=1.0)
    _r2 = RA.reconcile(_ps, apply=True, root=str(tmp_path), now=2.0)
    assert _r1["changed"] == 2
    # 改过之后标记已是 False ⇒ 天然幂等（不靠额外计数位）
    assert _r2["changed"] == 0, "第二次不该再改"


# ============================ ④ 本件永不写盘


def test_12_module_never_writes_disk():
    _tree = ast.parse(_read("nucleus/evolution/rollback_audit.py"))
    _bad = []
    for _n in ast.walk(_tree):
        if isinstance(_n, ast.Call):
            _fn = _n.func
            _name = getattr(_fn, "id", None) or getattr(_fn, "attr", None)
            if _name in ("_atomic_save", "dump", "write_text", "write_bytes"):
                _bad.append(_name)
            if _name == "open":
                # 任何 open 都不该出现在本件（只读也不需要：用 os.path 判存在性）
                _bad.append("open")
    assert _bad == [], f"rollback_audit 不该有任何写盘能力：{_bad!r}"


# ============================ ⑤ 生产写入点真的先实测


def test_13_production_sets_flag_from_measurement():
    """`rollback_available` 的赋值必须来自 `bool(_m375_rb_ok)`，不是恒 True。"""
    _src = _read("nucleus/reasoning/PatchManager.py")
    _tree = ast.parse(_src)
    _hits = []
    for _n in ast.walk(_tree):
        if not isinstance(_n, ast.Assign):
            continue
        for _t in _n.targets:
            if (isinstance(_t, ast.Subscript)
                    and isinstance(_t.slice, ast.Constant)
                    and _t.slice.value == "rollback_available"):
                _hits.append(_n.value)
    _applied_site = [v for v in _hits
                     if isinstance(v, ast.Call) and getattr(v.func, "id", "") == "bool"]
    assert _applied_site, "applied 处仍直接赋 True（没走实测）"
    _arg = _applied_site[0].args[0]
    assert getattr(_arg, "id", "") == "_m375_rb_ok", "赋值来源不是实测结果"
    assert "from nucleus.evolution.rollback_audit import" in _src
    assert "backup_present as _m375_bp" in _src
