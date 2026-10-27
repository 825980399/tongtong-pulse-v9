# -*- coding: utf-8 -*-
"""rollback_audit.py —— 回滚可用性的「实测口径」（★375 刀5）

## 问题（本仓真实数据，375 刀5 实测）

    applied = 76 条，**全部** rollback_available=True，**全部**有 backup_path，
    但 backup_path 物理存在的只有 **20** 条 —— **56/76 物理不可回滚**。

## 根因

不是谁写错了：`rollback_available=True` 是**写入那一刻的真话**（当时备份在），
后来 `data/code_backups/` 的保留策略把旧目录清掉，标记没跟着变。
坏在**没有复核口** —— 下游 `patch_lifecycle.record_activation()` 的硬点
（`rollback_available=False` 不得进入 activated，验收 a）读到的是历史值，
于是「物理不可回滚」的补丁照样进 activated。

## 本件做什么（两件，都不碰历史账）

1. `backup_present()` —— 把「可回滚」从**标记**改成**可读实测**；
   新写入点先实测再置标记，不再产出新的「假可回滚」。
2. `audit()` / `reconcile()` —— 存量复核。`reconcile` **默认 dry-run**，
   且**本件不提供任何写盘口**：`data/patches/patch_history.json` 是活体对象，
   375 批任务书明令不改审计池数据 ⇒ 只出清单，落盘与否由裁决决定。

## 口径声明（写进证据件 scope，不许含糊）

- 只判「备份是否可读」，**不判**「备份内容是否等于补丁前内容」——
  备份侧没有原始哈希，硬判就是猜。
- 目录形态（`backup_YYYYMMDD_HHMMSS/`）：目录存在且非空即算可读；
  文件形态（`xxx.patch_backup_TS`）：文件存在即算可读。
- 空 `backup_path`：判「不可回滚」，不是「未知」——没有备份就是没有。
"""
from __future__ import annotations

import os
import time
from typing import Any

from nucleus._silent_except import silent_exc

# ★375刀5：相对路径的解析基准 = **项目根**，不是 cwd。
#   `patch_history.json` 实测 76/76 条 `file` 是相对路径（"organs/..."），
#   兜底备份分支 `patch["file"] + ".patch_backup_TS"` 因此也产出相对路径。
#   基准若取 cwd，「能否回滚」就会随进程启动目录漂移；钉成项目根后
#   生产裸调用与审计脚本传 root 得到同一个答案。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

F_ROLLBACK_AVAILABLE = "rollback_available"
F_BACKUP_PATH = "backup_path"
# 复核回写字段（只在 reconcile(apply=True) 时写到**内存对象**上，本件不落盘）
F_UNAVAILABLE_REASON = "rollback_unavailable_reason"
F_RECONCILED_AT = "rollback_reconciled_at"

SCOPE = (
    "只判备份是否可读（目录非空 / 文件存在），不判备份内容与补丁前内容是否一致；"
    "空 backup_path 判不可回滚；扫描面=data/patches/patch_history.json 的 applied 条目"
)


def _abs_path(p: Any, root: str | None = None) -> str:
    _p = str(p or "").strip()
    if not _p:
        return ""
    if os.path.isabs(_p):
        return _p
    return os.path.join(root or _PROJECT_ROOT, _p)


def backup_present(patch: dict[str, Any] | None, root: str | None = None) -> tuple[bool, str]:
    """`(可回滚, 原因)`。原因串只用于留痕与归类，不参与判定。

    这是本件唯一判定口 —— 生产写入点与审计口都调它，保证「标记」与「实测」
    永远同一个口径（铁律 165「一开关一语义」同型）。
    """
    _bp = str((patch or {}).get(F_BACKUP_PATH) or "").strip()
    if not _bp:
        return False, "no_backup_path"
    _abs = _abs_path(_bp, root)
    if os.path.isdir(_abs):
        try:
            _n = len(os.listdir(_abs))
        except OSError as e:
            silent_exc(e, where="rollback_audit::backup_present.listdir", level="debug")
            return False, "backup_unreadable"
        return (True, "backup_dir_ok") if _n > 0 else (False, "backup_dir_empty")
    if os.path.isfile(_abs):
        return True, "backup_file_ok"
    return False, "backup_missing"


def audit(patches: list[dict] | None, root: str | None = None) -> dict[str, Any]:
    """存量复核（只读）：标记 vs 物理，双向都报。

    为什么双向：只报「标了但不在」会漏掉「没标但在」——
    后者同样是口径错，只是方向相反（会白丢可回滚能力）。
    """
    _applied = [p for p in (patches or []) if isinstance(p, dict) and p.get("applied")]
    _marked = [p for p in _applied if p.get(F_ROLLBACK_AVAILABLE)]
    _marked_ok: list[str] = []
    _marked_bad: list[dict] = []
    _reasons: dict[str, int] = {}
    for _p in _marked:
        _ok, _why = backup_present(_p, root)
        if _ok:
            _marked_ok.append(str(_p.get("id") or ""))
        else:
            _reasons[_why] = _reasons.get(_why, 0) + 1
            _marked_bad.append({"id": str(_p.get("id") or "")[:20],
                                "backup_path": str(_p.get(F_BACKUP_PATH) or "")[-60:],
                                "reason": _why})
    _unmarked_ok = [str(_p.get("id") or "") for _p in _applied
                    if not _p.get(F_ROLLBACK_AVAILABLE) and backup_present(_p, root)[0]]
    return {
        "scope": SCOPE,
        "applied": len(_applied),
        "marked_rollbackable": len(_marked),
        "effective_rollbackable": len(_marked_ok),
        "marked_but_missing": len(_marked_bad),
        "unmarked_but_present": len(_unmarked_ok),
        "missing_reasons": dict(_reasons),
        "missing_sample": _marked_bad[:12],
    }


def reconcile(patches: list[dict] | None, *, apply: bool = False,
              root: str | None = None, now: float | None = None) -> dict[str, Any]:
    """把「标 True 但物理不可读」的标记改口径。

    `apply=False`（默认）：只出清单，**一个字段都不改**。
    `apply=True`：改**传入对象**的字段（`rollback_available=False` +
    `rollback_unavailable_reason` + `rollback_reconciled_at`），方便调用方
    自行决定是否落盘 —— **本函数与整个模块都不写盘**。

    保留历史账：不清空 `backup_path`（它记录的是当年备份位置，删了就再也查不到
    备份本该在哪），只在旁边加原因字段。
    """
    _ts = float(now if now is not None else time.time())
    _changed: list[dict] = []
    for _p in (patches or []):
        if not isinstance(_p, dict) or not _p.get("applied"):
            continue
        if not _p.get(F_ROLLBACK_AVAILABLE):
            continue
        _ok, _why = backup_present(_p, root)
        if _ok:
            continue
        _row = {"id": str(_p.get("id") or ""), "reason": _why,
                "backup_path": str(_p.get(F_BACKUP_PATH) or "")}
        if not apply:
            _changed.append(_row)
            continue
        _p[F_ROLLBACK_AVAILABLE] = False
        _p[F_UNAVAILABLE_REASON] = _why
        _p[F_RECONCILED_AT] = _ts
        _changed.append(_row)
    return {
        "apply": bool(apply),
        "changed": len(_changed),
        "rows": _changed[:20],
        "note": ("dry-run：未改任何字段（本批不改审计池数据）" if not apply
                 else "已改内存对象；本模块不写盘，落盘由调用方裁决"),
    }
