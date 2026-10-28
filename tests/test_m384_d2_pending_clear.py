# -*- coding: utf-8 -*-
"""384 刀2：pending_patches.json 清零（停机窗）。

## 结构修正（票面 vs 实测，先说清）

票面写「清零 pending 数组（保留文件结构）」，暗示文件是
`{"pending": [...], "runtime_verified": [...]}`。**实际是顶层 list + 每条带
`status` 字段**，共 11 条（pending **3** 条，不是票面说的 2 条）+ runtime_verified 8 条。

因此本刀的"清零"= **删掉 `status == "pending"` 的条目**，保留 runtime_verified
（它们是「已跑过运行验证、等人工确认」，与"无人推进的 pending"不是一回事）。

## 守卫要点

1. 清零后 `status == "pending"` 的条数必须 **0**；
2. `runtime_verified` 8 条**一条都不能少**（清错面就会把可确认项也丢掉）；
3. 原文件必须先备份（`.bak_384/`），且备份里 11 条齐全 —— 否则不可回滚；
4. 清零脚本必须自带「写前写后 `patch_history.json` sha256 比对」。
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.m384_clear_pending import (  # noqa: E402
    CLEAR_STATUSES, split_rows, survey,
)

PENDING = os.path.join(ROOT, "data", "patches", "pending_patches.json")
BAK = os.path.join(ROOT, ".bak_384", "pending_patches_before_384.json")


def _load(p):
    return json.load(io.open(p, encoding="utf-8"))


# ------------------------------------------------------- ① 清零后的生产状态
def test_m384_d2_no_pending_left():
    """★核心：清零后 `status == "pending"` 的条目数必须为 0。"""
    rows = _load(PENDING)
    left = [r for r in rows
            if isinstance(r, dict) and str(r.get("status") or "") == "pending"]
    assert left == [], "还有 pending 条目：%s" % [r.get("id") for r in left][:5]


def test_m384_d2_runtime_verified_untouched():
    """★反控：runtime_verified 是「等人工确认」，一条都不许少。"""
    rows = _load(PENDING)
    rv = [r for r in rows
          if isinstance(r, dict) and str(r.get("status") or "") == "runtime_verified"]
    assert len(rv) == 8, "runtime_verified 应为 8 条，实际 %d ⇒ 清错面了" % len(rv)


def test_m384_d2_structure_preserved_as_list():
    """保留文件结构：顶层仍是 list（不是被改写成 dict 或空文件）。"""
    rows = _load(PENDING)
    assert isinstance(rows, list)
    assert len(rows) == 8


def test_m384_d2_backup_holds_original_11():
    """★可回滚的前提：备份里必须是清零前的 11 条（含 3 条 pending）。"""
    assert os.path.isfile(BAK), "找不到备份 %s ⇒ 不可回滚" % BAK
    rows = _load(BAK)
    assert isinstance(rows, list)
    assert len(rows) == 11, "备份条数应为 11，实际 %d" % len(rows)
    _by = survey(rows)
    assert _by.get("pending") == 3, "备份里 pending 应为 3 条（票面说 2，实测 3）"
    assert _by.get("runtime_verified") == 8


# ----------------------------------------------------- ② 纯函数行为（可单测）
def test_m384_d2_split_only_drops_pending():
    rows = [
        {"id": "a", "status": "pending"},
        {"id": "b", "status": "runtime_verified"},
        {"id": "c", "status": "verified"},
        {"id": "d"},                    # 无 status ⇒ 按 open 语义保留
        "not-a-dict",                   # 非 dict ⇒ 永久保留（宁留不误删）
    ]
    keep, drop = split_rows(rows)
    assert [r.get("id") if isinstance(r, dict) else r for r in drop] == ["a"]
    assert len(keep) == 4
    assert "not-a-dict" in keep


def test_m384_d2_clear_statuses_is_only_pending():
    """★只清 pending：把 runtime_verified 加进清除面就等于丢可确认项。"""
    assert tuple(CLEAR_STATUSES) == ("pending",)


# ------------------------------------------------ ③ 脚本自带的护栏（源码断言）
def test_m384_d2_script_guards():
    """脚本必须：默认 dry-run / apply 需 --allow-prod / 备份后核 sha256 / 比对 history。"""
    src = io.open(os.path.join(ROOT, "tools", "m384_clear_pending.py"),
                  encoding="utf-8").read()
    assert 'if not _args.apply:' in src, "缺 dry-run 默认分支"
    assert 'if not _args.allow_prod:' in src, "apply 缺少 --allow-prod 二次确认"
    assert "shutil.copy2(PENDING, _bak)" in src, "缺备份"
    assert "_src_sha != _bak_sha" in src, "备份后未核 sha256"
    assert "_hist_after == _before_hist" in src, "未比对 patch_history.json 是否被改"
