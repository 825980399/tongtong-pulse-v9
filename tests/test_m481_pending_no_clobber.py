# -*- coding: utf-8 -*-
"""481 刀18/19：approved 被旧快照冲掉 —— 根因闭环的行为级判据。

## 活体证据（logs/pulse.log，两轮重启逐字复现）

13:09:59 pid=1748 / 14:42:18 pid=24888：

    [356单写者] writer=PatchManager ... status_disk={'approved': 1, ...}
    [补丁审批]  index=2 ... grade=A
    [447刀1]    审批桥 retry_468 ... ⇒ approved=True
    [运行时验证] 批量验证完成: 总数=0 ...        ← 这条日志在 `_save_json` **之后**

而外部读盘 `pending_patches.json` 是 `verified:6`、`approved_at` 全无。
⇒ 唯一绕过 356 写口的写者就是 `verify_submitted_patches` 末尾那句
   `_save_json(get_pending_file(), _pending)`：它在锁外读旧快照，且
   「本轮什么都没验」也照写不误，把刚批准的补丁整体冲回 verified。

## 这些测试证明什么

① 零变更 ⇒ 根本不碰盘（少一次纯破坏性的无效写）；
② 真有变更 ⇒ 走唯一写口，审批状态单调保护把 approved 继承回来；
③ 生产源码里那个裸写点已经消失（AST 判据，防回潮）；
④ 判据本身有判别力：把旧代码喂给同一个检查函数必须报红（铁律35）。

## 不证明什么

不证明活体下一轮就一定 `inherited>0`——那要重启后看日志；本文件只证明
「旧快照写回」这条路径在写口这一层已被封死。
"""
import ast
import copy
import hashlib
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from nucleus.evolution import pending_write as pw


def _approved_row(pid="p1"):
    return {"patch_id": pid, "file": "a.py", "method": "m", "status": "approved",
            "approved_source": "auto", "approved_signer": "auto:bridge_447",
            "approved_at": 1700000000.0, "approved_judged_by": "machine",
            "approved_basis": {"gate": "all_pass"}}


def _stale_row(pid="p1"):
    return {"patch_id": pid, "file": "a.py", "method": "m", "status": "verified"}


@pytest.fixture
def clean_write(monkeypatch, tmp_path):
    """隔离写口：pending 放 tmp；406 审计留痕与本刀无关，短路零污染。"""
    p = str(tmp_path / "pending_patches.json")
    monkeypatch.setattr(pw, "_m406_annotate",
                        lambda payload, writer, root=None: {"changed": 0, "written": 0})
    return p


def _disk(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# ==================== ① 零变更不碰盘 ====================

def test_01_unchanged_flush_never_touches_disk(clean_write):
    """核心：锁外旧快照 + 本轮零变更 ⇒ 盘上的 approved 一个字节都不许动。"""
    p = clean_write
    pw.write_pending([_approved_row()], path=p, writer="setup")
    before_sha = _sha(p)

    stale = [_stale_row()]                       # 锁外读到的旧快照
    r = pw.flush_if_changed(stale, copy.deepcopy(stale), path=p, writer="verify")

    assert r["written"] is False
    assert r["ok"] is True and r["why"] == "unchanged"
    assert _sha(p) == before_sha
    assert _disk(p)[0]["status"] == "approved"


def test_02_unchanged_short_circuits_write_pending(clean_write, monkeypatch):
    """零变更必须在调 write_pending 之前就短路（否则仍会触发审计/标签副作用）。"""
    calls = []

    def _fake(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True}

    monkeypatch.setattr(pw, "write_pending", _fake)
    r = pw.flush_if_changed([{"a": 1}], [{"a": 1}], path=clean_write, writer="x")
    assert calls == []
    assert r["written"] is False


def test_03_unchanged_return_contract(clean_write):
    """零变更返回体必须仍是「状态 + 只加不改的口」，调用方不需分支处理。"""
    r = pw.flush_if_changed([{"patch_id": "x"}], [{"patch_id": "x"}],
                            path=clean_write, writer="w")
    assert {"ok", "written", "why", "rows", "writer", "path"} <= set(r)
    assert r["rows"] == 1
    assert r["path"] == clean_write
    assert r["writer"] == "w"


# ==================== ② 真有变更走唯一写口 ====================

def test_04_changed_flush_inherits_approved(clean_write):
    """旧快照 + 本轮一处真实修改 ⇒ 写盘，但 approved 字段必须被继承回来。"""
    p = clean_write
    pw.write_pending([_approved_row()], path=p, writer="setup")

    before = [_stale_row()]
    payload = [_stale_row()]
    payload[0]["runtime_verified"] = True       # 本轮真实变更
    r = pw.flush_if_changed(payload, copy.deepcopy(before), path=p, writer="verify")

    assert r["written"] is True and r["ok"] is True
    row = _disk(p)[0]
    assert row["status"] == "approved"
    assert row["approved_at"] == 1700000000.0
    assert row["approved_source"] == "auto"
    assert row["runtime_verified"] is True


def test_05_changed_flush_calls_single_writer_with_contract(clean_write, monkeypatch):
    """writer / path 必须原样透传给唯一写口（留痕可追到具体写者）。"""
    calls = []

    def _fake(payload, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "rows": 1, "writer": kwargs.get("writer"),
                "why": "", "path": kwargs.get("path")}

    monkeypatch.setattr(pw, "write_pending", _fake)
    r = pw.flush_if_changed([{"a": 2}], [{"a": 1}],
                            path=clean_write, writer="verify")
    assert len(calls) == 1
    assert calls[0]["writer"] == "verify"
    assert calls[0]["path"] == clean_write
    assert r["written"] is True


def test_06_two_layers_are_independent(clean_write, monkeypatch):
    """反证：关掉单调保护、且仍整体写回 ⇒ approved 确实会丢（复现活体事故）。

    说明「零变更不写盘」与「审批单调保护」是两层独立防护，不能互相冒充。
    """
    p = clean_write
    pw.write_pending([_approved_row()], path=p, writer="setup")
    monkeypatch.setattr(pw, "_m480_monotonic_on", lambda: False)
    pw.write_pending([_stale_row()], path=p, writer="verify")
    assert _disk(p)[0]["status"] == "verified"


# ==================== ③ 端到端时序（照抄活体） ====================

def test_07_approval_then_empty_verify_keeps_approved(clean_write):
    """照抄 14:42:18：初始 verified → 审批写口 → 批量验证空轮 ⇒ approved 仍在。"""
    p = clean_write
    pw.write_pending([_stale_row()], path=p, writer="init")

    # 审批桥：读盘 → 改成 approved → 经 _save_patch_list 走写口
    rows = _disk(p)
    rows[0].update({"status": "approved", "approved_source": "auto",
                    "approved_at": 1700000000.0})
    pw.write_pending(rows, path=p, writer="PatchManager")
    assert _disk(p)[0]["status"] == "approved"
    approved_sha = _sha(p)

    # 批量验证：在审批之前就锁外读了旧快照，本轮 _total=0、什么都没改
    old_snapshot = [_stale_row()]
    r = pw.flush_if_changed(old_snapshot, copy.deepcopy(old_snapshot),
                            path=p, writer="SafeEvolutionExecutor.verify_submitted_patches")

    assert r["written"] is False
    assert _sha(p) == approved_sha
    assert _disk(p)[0]["status"] == "approved"


# ==================== ④ 生产源码防回潮（AST，不看注释文本） ====================

_EXECUTOR_REL = os.path.join("nucleus", "reasoning", "SafeEvolutionExecutor.py")


def _bare_save_json_lines(src, func_name="verify_submitted_patches"):
    """返回函数体内 `_save_json(...)` 调用的行号（空列表 = 已收敛）。"""
    tree = ast.parse(src.replace("\r\n", "\n"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == func_name), None)
    assert fn is not None, f"找不到 {func_name}"
    return [n.lineno for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_save_json"]


def _imports_flush_if_changed(src, func_name="verify_submitted_patches"):
    tree = ast.parse(src.replace("\r\n", "\n"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == func_name), None)
    assert fn is not None
    return any(isinstance(n, ast.ImportFrom)
               and (n.module or "").endswith("pending_write")
               and any(a.name == "flush_if_changed" for a in n.names)
               for n in ast.walk(fn))


def test_08_executor_no_bare_pending_write():
    with open(os.path.join(_ROOT, _EXECUTOR_REL), encoding="utf-8") as f:
        src = f.read()
    assert _bare_save_json_lines(src) == [], "verify_submitted_patches 又出现裸 _save_json"
    assert _imports_flush_if_changed(src), "未接 356 唯一写口的 flush_if_changed"


def test_09_ast_guard_has_discriminating_power():
    """铁律35：把**改动前**的旧代码喂给同一判据，必须报红。"""
    old_src = (
        "class X:\n"
        "    def verify_submitted_patches(self):\n"
        "        _pending = []\n"
        "        self._patch_manager._save_json(\n"
        "            self._patch_manager.get_pending_file(), _pending)\n"
    )
    assert _bare_save_json_lines(old_src) == [4]
    assert _imports_flush_if_changed(old_src) is False
