# -*- coding: utf-8 -*-
"""第473批 刀14 门控单测：自动升级窗口的「可应用」口径必须与落地侧一致。

病灶
----
`PulseInnerWorld._check_auto_upgrade` 的判定是：

    _approvable = [p for p in _pending
                   if p.get("status") == "approved"
                   or (_auto_apply and p.get("status") == "verified")]

而落地侧 `main.py` 走的是 `PatchManager.apply_all_pending(only_approved=True)`，
它**只认 `status == "approved"`**（`PatchManager.py:6146` 明文）。

`verified` 的真实含义是「自动批准被拒」——信任分不足 / 审美 D 级 / 核心文件强制人工
（`SafeEvolutionExecutor.py:6225-6251`），不会有任何代码把它提升为 `approved`。
所以旧判定会让维护窗口对着一条永远不会被应用的补丁：
「进入维护模式」→「即将应用补丁并重启验证」→ 实际 apply = 0。

★有意思的是这段代码上方**自己的注释**（v25.0 BRAIN-8）就写着
「避免对仅 verified(待人工审批)补丁误发『即将应用并重启』通知形成假闭环」——
代码推翻了注释。本刀只是让代码回到注释。

现场（`data/patches/pending_patches.json`，2026-11-07）
    1 条 `wont_fix` + 1 条 `verified`
      · patch_llm_1793968480_715f → organs/core/PulseMetricsCollector.py
        trust_score=50（门槛 60）、core 文件 ⇒ 强制人工审批，永远进不了 approved
    ⇒ 旧口径下每晚 03:00 维护窗口都会对着它「进入维护模式」。
"""

from __future__ import annotations

import ast
import os

import nucleus.evolution.graceful_apply as GA

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IW = os.path.join(_ROOT, "organs", "brain", "PulseInnerWorld.py")


# ==================== 一、纯函数口径 ====================


def test_只保留approved():
    _p = [
        {"id": "a", "status": "approved"},
        {"id": "b", "status": "verified"},
        {"id": "c", "status": "pending"},
        {"id": "d", "status": "wont_fix"},
        {"id": "e", "status": "rejected"},
        {"id": "f", "status": "approved"},
    ]
    _ids = [x["id"] for x in GA.m473_appliable_patches(_p)]
    assert _ids == ["a", "f"]


def test_现场那条verified被剔除():
    """照抄现场那条补丁的真实字段：trust 50 / 核心文件 ⇒ 永远等人工。"""
    _p = [{
        "id": "patch_llm_1793968480_715f",
        "file": "organs/core/PulseMetricsCollector.py",
        "status": "verified",
        "trust_score": 50.0,
        "aesthetic_grade": "A",
        "risk_level": "高",
        "issue_type": "non_atomic_write",
    }]
    assert GA.m473_appliable_patches(_p) == []


def test_状态常量被钉死():
    """常量值就是落地侧的判据；改成别的等于改契约。"""
    assert GA.M473_APPLIABLE_STATUS == "approved"


def test_非dict项跳过不崩():
    _p = [None, "x", 1, ("t",), {"id": "a", "status": "approved"}]
    assert [x["id"] for x in GA.m473_appliable_patches(_p)] == ["a"]


def test_空与None安全():
    assert GA.m473_appliable_patches([]) == []
    assert GA.m473_appliable_patches(None) == []
    assert GA.m473_appliable_patches(()) == []


def test_不改入参且返回新列表():
    _a = {"id": "a", "status": "approved"}
    _p = [_a, {"id": "b", "status": "verified"}]
    _out = GA.m473_appliable_patches(_p)
    assert _out is not _p
    assert len(_p) == 2, "入参被就地修改了"
    assert _out[0] is _a


def test_缺status字段不崩():
    assert GA.m473_appliable_patches([{"id": "x"}]) == []


# ==================== 二、IW 接线 ====================


def _iw_tree() -> ast.Module:
    with open(_IW, "r", encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _check_auto_upgrade_node() -> ast.AST:
    for _n in ast.walk(_iw_tree()):
        if isinstance(_n, ast.FunctionDef) and _n.name == "_check_auto_upgrade":
            return _n
    raise AssertionError("PulseInnerWorld 里没有 _check_auto_upgrade")


def test_IW判定改调纯函数():
    """`_approvable` 必须来自 graceful_apply，而不是内联推导式。"""
    _assigns = [
        _n for _n in ast.walk(_check_auto_upgrade_node())
        if isinstance(_n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_approvable" for t in _n.targets)
    ]
    assert len(_assigns) == 1, f"_approvable 赋值次数={len(_assigns)}"
    _v = _assigns[0].value
    assert isinstance(_v, ast.Call), f"_approvable 不是调用: {ast.dump(_v)[:80]}"
    assert isinstance(_v.func, ast.Name) and _v.func.id == "m473_appliable_patches"
    assert len(_v.args) == 1
    assert isinstance(_v.args[0], ast.Name) and _v.args[0].id == "_pending"


def test_IW内不再出现verified字面量():
    """★核心判据：整个 `_check_auto_upgrade` 里不许再有 \"verified\" 参与任何判定/日志。"""
    _bad = [n for n in ast.walk(_check_auto_upgrade_node())
            if isinstance(n, ast.Constant) and n.value == "verified"]
    assert _bad == [], f"verified 字面量残留于行 {[b.lineno for b in _bad]}"


def test_IW导入来自graceful_apply():
    _imps = [
        _n for _n in ast.walk(_check_auto_upgrade_node())
        if isinstance(_n, ast.ImportFrom)
        and _n.module == "nucleus.evolution.graceful_apply"
        and any(_a.name == "m473_appliable_patches" for _a in _n.names)
    ]
    assert len(_imps) == 1, f"导入次数={len(_imps)}"


def test_跳过日志仍引用auto_apply():
    """`_auto_apply` 不能被改成"读了不用"（ruff 会报 F841，且失去运维口径）。"""
    with open(_IW, "r", encoding="utf-8") as fh:
        _src = fh.read()
    _node = _check_auto_upgrade_node()
    _seg = ast.get_source_segment(_src, _node) or ""
    assert "_auto_apply" in _seg
    assert _seg.count("_auto_apply") >= 3, "读取/赋值/使用三处都该在"
