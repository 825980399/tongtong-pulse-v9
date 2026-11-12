# -*- coding: utf-8 -*-
"""493.20 刀2：两套账（absorb_trace ↔ verification_learning）对账。

两套账分别记什么（493.18 口径）：
  - `data/self_index/absorb_trace.jsonl`：QICA 实际吸收了什么规则
    （rule_id / concept / path / source / result / downstream_delta /
     verification_learning_id）。
  - `data/verification_learning.json`：验证学习枢纽登记了什么验证-学习事件
    （id / applied / absorb_trace_id / ...）。

关联契约（双向可追溯）：
  absorb_trace 行的 `verification_learning_id` == 某 vl entry 的 `id`，
  且该 vl entry 的 `absorb_trace_id` == 该行的 `rule_id`。

问题（任务书 §Background）：活体旧代码进程用自身内存里不带交叉引用的
entries 周期性整表覆盖 verification_learning.json ⇒ 关联丢失。本模块提供
**可复跑、幂等**的对账：双向核对，不一致的用生产 API `hub.record(...)` 重新
登记，并把新 vl_id 回填到 absorb_trace。重启加载新代码后，
`_save()` 兜底保留 `applied` / `absorb_trace_id` 字段，不再丢失。
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
    """双向核对并修复不一致（幂等）。返回 {before, after, repaired, ...}。

    修复动作：对每个缺失关联的 rule_id，用生产 API
    `hub.record(..., absorb_trace_id=rule_id, remediation_triggered=True)` 重新
    登记一条 applied=True 的学习事件，再把生成的新 vl_id 回填到 absorb_trace 行。
    已对齐的规则零动作。
    """
    _at = at_path or _AT_REL
    _vl = vl_path or _VL_REL
    if hub is None:
        from nucleus.mnemosyne.verification_learning_hub import VerificationLearningHub
        hub = VerificationLearningHub(file_path=_vl)

    _at_rows = load_applied_rules(_at)
    # 读磁盘全部行（含非 applied），回填时保持其它行不动
    _all_rows = []
    if os.path.isfile(_at):
        with open(_at, encoding="utf-8") as _f:
            for _l in _f:
                _l = _l.strip()
                if _l:
                    _all_rows.append(json.loads(_l))

    _before = check_alignment(_at_rows, list(hub._entries))
    _repaired = []
    if _before["missing_rule_ids"]:
        for _r in _at_rows:
            _rid = _r.get("rule_id")
            if _rid not in _before["missing_rule_ids"]:
                continue
            # 幂等：hub 内存里若已有该 rule_id 的条目（本轮刚登记）不重复
            _existing = [e for e in hub._entries
                         if e.get("absorb_trace_id") == _rid]
            if _existing:
                _new_id = _existing[0].get("id")
            else:
                _new_id = hub.record(
                    organ="qica", task_type="rule_absorb",
                    input_summary=("QICA 吸收规则 %s: %s=%s"
                                   % (_rid, _r.get("concept"), _r.get("path"))),
                    local_result={"rule_id": _rid, "concept": _r.get("concept"),
                                  "path": _r.get("path")},
                    confidence=0.9, relevance_score=0.9,
                    needs_verification=False, api_better=False,
                    lesson=("规则吸收落地（493.20 对账补登）: "
                            + str(_r.get("downstream_delta"))[:150]),
                    remediation_triggered=True,
                    origin="ledger_reconcile",
                    absorb_trace_id=_rid,
                )
            # 回填 absorb_trace 的 verification_learning_id
            for _row in _all_rows:
                if _row.get("rule_id") == _rid:
                    _row["verification_learning_id"] = _new_id
            _repaired.append(_rid)
        if _repaired:
            _write_at_rows(_at, _all_rows)

    _after = check_alignment(load_applied_rules(_at), list(hub._entries))
    return {"before": _before, "after": _after, "repaired": _repaired}
