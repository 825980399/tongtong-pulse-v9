# -*- coding: utf-8 -*-
"""两套账（absorb_trace ↔ verification_learning）**只读核对**（493.21 方案A）。

两套账分别记什么（493.18 口径）：
  - `data/self_index/absorb_trace.jsonl`：QICA 实际吸收了什么规则
    （rule_id / concept / path / source / result / downstream_delta /
     verification_learning_id）。
  - `data/verification_learning.json`：验证学习枢纽登记了什么验证-学习事件
    （id / applied / absorb_trace_id / ...）。

★493.21 刀1 方案A（烛微第十八阶段 + 洞鉴联合裁决）：**两本账天然不同粒度、
不同来源，不再硬造双向 ID、不再做「对账脚本强制对齐」**。原因：vl 的
`record()` 每条全量 `_save()` 覆盖整个文件，外部对账写进去的条目不在活体
内存列表里，下一次 record() 就被抹掉（实测分钟级丢失），反复修反复被覆盖。

因此：
  - `verification_learning_id` / `absorb_trace_id` 两个字段**保留但允许 None**；
  - 本模块只提供 `check_alignment()` 只读观测与 `reconcile()` 只读报告，
    **不向任何一本账注入条目、不写任何文件**；
  - 两本账各自独立记录，不互相覆盖。
"""
from __future__ import annotations

import json
import os

from nucleus._silent_except import silent_exc

_AT_REL = os.path.join("data", "self_index", "absorb_trace.jsonl")
_VL_REL = "data/verification_learning.json"
_APPLIED = ("applied", "applied_correction_edge")


def load_applied_rules(path: str = None) -> list[dict]:
    """读 absorb_trace 里 result=applied* 的规则行（含原始行内容）。"""
    _p = path or _AT_REL
    _out = []
    if not os.path.isfile(_p):
        return _out
    try:
        with open(_p, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _r = json.loads(_line)
                except Exception as _e:
                    silent_exc(_e, where="ledger_reconcile.load_applied_rules.json",
                               level="debug")
                    continue
                if _r.get("result") in _APPLIED:
                    _out.append(_r)
    except Exception as _e:
        silent_exc(_e, where="ledger_reconcile.load_applied_rules", level="debug")
    return _out


def check_alignment(at_rows: list[dict], vl_entries: list[dict]) -> dict:
    """双向核对，返回统计与不一致的 rule_id 列表（不改动任何数据）。"""
    _by_id = {e.get("id"): e for e in vl_entries}
    _by_at = {e.get("absorb_trace_id"): e for e in vl_entries
              if e.get("absorb_trace_id")}
    _aligned, _missing = [], []
    for _r in at_rows:
        _rid = _r.get("rule_id")
        _v = _by_id.get(_r.get("verification_learning_id"))
        _ok = (bool(_v) and _v.get("absorb_trace_id") == _rid
               and _by_at.get(_rid) is _v)
        (_aligned if _ok else _missing).append(_rid)
    return {
        "applied_rules": len(at_rows),
        "vl_applied_true": sum(1 for e in vl_entries if e.get("applied") is True),
        "vl_with_absorb_trace_id": len(_by_at),
        "bidirectional": len(_aligned),
        "missing_rule_ids": _missing,
    }


def _write_at_rows(path: str, rows: list[dict]) -> None:
    """原子重写 absorb_trace（回填 verification_learning_id 用）。"""
    _tmp = path + ".tmp"
    with open(_tmp, "w", encoding="utf-8") as _f:
        for _r in rows:
            _f.write(json.dumps(_r, ensure_ascii=False) + "\n")
    os.replace(_tmp, path)


def reconcile(at_path: str = None, vl_path: str = None, hub=None) -> dict:
    """493.21 方案A：**只读**核对报告，不做任何写入/强制对齐。

    返回 {before, policy, note}。``hub`` 参数保留兼容旧调用；传入则用其内存
    entries，否则直接读磁盘 vl 文件。两本账各自独立记录、关联字段允许 None，
    因此「双向=0」是**正常状态**而非故障，不再补登条目。
    """
    _at = at_path or _AT_REL
    _vl = vl_path or _VL_REL
    if hub is not None:
        _vl_rows = list(hub._entries)
    else:
        _vl_rows = []
        if os.path.isfile(_vl):
            try:
                _vl_rows = json.load(open(_vl, encoding="utf-8")).get("entries", [])
            except Exception as _e:
                silent_exc(_e, where="ledger_reconcile.reconcile.read_vl",
                           level="debug")
    _at_rows = load_applied_rules(_at)
    _stat = check_alignment(_at_rows, _vl_rows)
    return {
        "before": _stat,
        "after": _stat,           # 只读：不变
        "repaired": [],           # 只读：永不注入
        "policy": "two_independent_ledgers",
        "note": ("493.21 方案A：两本账不同粒度/不同来源，不做强制双向 ID 对齐；"
                 "verification_learning_id / absorb_trace_id 允许为 None"),
    }
