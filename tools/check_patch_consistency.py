# -*- coding: utf-8 -*-
"""第114批 T-114b②：补丁账本一致性巡检脚本（只读，可接入 CI）。

扫描三本账：
    - data/patches/pending_patches.json      （待审批队列）
    - data/patches/patch_history.json        （已应用/历史）
    - data/patches/patch_history_obsolete.json（结构性失效归档）

七项一致性检查（C1~C7）：
    C1 双账本重复        —— 同一 id 跨 pending/history 重复，或账内 id 自重复
    C2 锚在位           —— 补丁缺 original_code / modified_code / file 锚点
    C3 counter>max+老化开关组合 —— pending 条数 > 老化阈值 且 老化开关关闭（队列无法自排）
    C4 auto_released×目标文件mtime —— 已放行/已应用补丁的目标文件 mtime 早于补丁时间（倒灌风险）
    C5 态白名单          —— status 不在白名单内
    C6 顶层vs嵌套verified背离 —— 顶层 runtime_verified 与嵌套 runtime_verify_result.verified 矛盾
    C7 无reason的obsolete —— obsolete 补丁缺 obsolete_reason / reason

退出码：发现任一问题 -> 1；全部通过 -> 0。便于门禁串联。

用法：
    python tools/check_patch_consistency.py [--root <项目根>] [--json] [--strict]
                                            [--no-import]

★第116批 T-116b③：新增 --no-import —— CI 环境用它强制走「直读 JSON」退化路径，
  不 import PatchManager（避免拉起 config/框架依赖导致门禁受污染或变慢）。
  默认仍优先复用 PatchManager 的真实路径（与生产口径一致）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from nucleus._silent_except import silent_exc

# ===== 态白名单（C5）：历史/生产实测出现过的合法 status =====
#   注：'verified' 为早期代码遗留别名（现框架统一用 'runtime_verified'），
#   生产数据中存在，纳入白名单避免误报；其余为本批及历史代码实测状态。
VALID_STATUSES = {
    "pending", "submitted", "approved", "applied", "verified",
    "runtime_verified", "runtime_failed",
    "needs_repair", "needs_reverify", "rejected", "obsolete",
}

# 锚点必需字段（C2）：源自 PatchManager._M90_PATCH_REQUIRED_FIELDS
REQUIRED_ANCHOR_FIELDS = ("original_code", "modified_code", "file")

_MTIME_TOLERANCE_SEC = 1.0  # C4：文件 mtime 与补丁时间容差


def _detect_root() -> str:
    """tools/check_patch_consistency.py -> 项目根 = tools 的父目录。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_ledgers(root: str, no_import: bool = False):
    """优先复用 PatchManager 的真实路径与加载逻辑；失败则退化为直接读 JSON。

    ★第116批 T-116b③：no_import=True 时跳过 PatchManager，直接按约定路径读三本账，
    供 CI 门禁在无框架依赖的环境下使用（结果口径与退化路径一致）。
    """
    pending, history, obsolete = [], [], []
    pending_path = history_path = obsolete_path = ""
    try:
        if no_import:
            raise RuntimeError("--no-import：按用户要求跳过 PatchManager 直读")
        sys.path.insert(0, root)
        from nucleus.reasoning.PatchManager import PatchManager
        _pm = PatchManager(root)
        pending_path = _pm.get_pending_file()
        history_path = _pm.get_history_file()
        _hist_dir = os.path.dirname(history_path)
        obsolete_path = os.path.join(_hist_dir, "patch_history_obsolete.json")
        pending = _pm.load_json(pending_path, [])
        history = _pm.load_json(history_path, [])
        obsolete = _pm.load_json(obsolete_path, [])
    except Exception:
        _pd = os.path.join(root, "data", "patches")
        pending_path = os.path.join(_pd, "pending_patches.json")
        history_path = os.path.join(_pd, "patch_history.json")
        obsolete_path = os.path.join(_pd, "patch_history_obsolete.json")
        for _p, _out in ((pending_path, pending), (history_path, history),
                         (obsolete_path, obsolete)):
            if os.path.exists(_p):
                try:
                    with open(_p, encoding="utf-8") as _f:
                        _data = json.load(_f)
                    _out.extend(_data if isinstance(_data, list) else [])
                except Exception as e:
                    silent_exc(e, "check_patch_consistency:88:补丁文件读取异常", level="warning")
    return (pending, history, obsolete, pending_path, history_path, obsolete_path)


def _resolve_target(patch: dict, root: str):
    """解析补丁目标文件的绝对路径（兼容绝对路径 / 相对项目根两种磁盘表示）。"""
    _f = patch.get("file", "")
    if not _f:
        return None
    if os.path.isabs(_f) and os.path.exists(_f):
        return _f
    _cand = os.path.join(root, _f)
    if os.path.exists(_cand):
        return _cand
    # 归一化：file 可能是 Windows 绝对路径，去掉盘符后拼根
    _rel = _f.replace("\\", "/")
    for _sep in ("/data/", "/organs/", "/nucleus/", "/config.py"):
        _idx = _rel.find(_sep)
        if _idx >= 0:
            _cand2 = os.path.join(root, _rel[_idx + 1:])
            if os.path.exists(_cand2):
                return _cand2
    return None


def _patch_time(patch: dict) -> float:
    """取补丁最近一次落地时间戳（applied_at/fixed_at/released_at/auto_released_at/created_at）。"""
    _best = 0.0
    for _k in ("applied_at", "fixed_at", "released_at", "auto_released_at",
               "created_at", "saved_at", "submitted_at"):
        _t = patch.get(_k)
        if isinstance(_t, (int, float)) and not isinstance(_t, bool) and _t > _best:
            _best = float(_t)
    return _best


def _check_c1_double_ledger(pending, history):
    """C1 双账本重复。"""
    issues = []
    _pids = [str(p.get("id")) for p in pending if isinstance(p, dict) and p.get("id")]
    _hids = [str(p.get("id")) for p in history if isinstance(p, dict) and p.get("id")]
    _seen = set()
    for _id in _pids:
        if _id in _seen:
            issues.append({"check": "C1", "id": _id,
                           "detail": "pending 账内 id 自重复"})
        _seen.add(_id)
    _hseen = set()
    for _id in _hids:
        if _id in _hseen:
            issues.append({"check": "C1", "id": _id,
                           "detail": "history 账内 id 自重复"})
        _hseen.add(_id)
    # 跨账本：同一 id 同时出现在 pending 与 history
    _cross = set(_pids) & set(_hids)
    for _id in _cross:
        issues.append({"check": "C1", "id": _id,
                       "detail": "同一 id 同时存在于 pending 与 history（双账本重复）"})
    return issues


def _check_c2_anchor(patches, ledger):
    """C2 锚在位。"""
    issues = []
    for _p in patches:
        if not isinstance(_p, dict):
            continue
        _missing = [f for f in REQUIRED_ANCHOR_FIELDS
                    if not _p.get(f) or not str(_p.get(f)).strip()]
        if _missing:
            issues.append({"check": "C2", "ledger": ledger,
                           "id": _p.get("id"),
                           "detail": "缺锚点字段: " + ", ".join(_missing)})
    return issues


def _check_c3_aging_combo(pending, root):
    """C3 counter>max + 老化开关组合。"""
    issues = []
    try:
        sys.path.insert(0, root)
        from nucleus.reasoning.PatchManager import PatchManager
        _max = PatchManager._m94_aging_max_count()
        _aging_on = PatchManager._m94_pending_aging_on()
    except Exception:
        _max, _aging_on = 20, False
    _n = len([p for p in pending if isinstance(p, dict)])
    if _n > _max and not _aging_on:
        issues.append({
            "check": "C3",
            "detail": (f"pending 条数={_n} > 老化阈值={_max} 且 老化开关=OFF "
                       f"→ 队列无法自排，存在无限增长/饥饿风险"),
            "count": _n, "max": _max, "aging_on": _aging_on,
        })
    return issues


def _check_c4_backflow(patches, root, ledger):
    """C4 auto_released / applied 补丁的目标文件 mtime 早于补丁时间 → 倒灌风险。"""
    issues = []
    for _p in patches:
        if not isinstance(_p, dict):
            continue
        if not (_p.get("auto_released") or _p.get("applied")):
            continue
        _pt = _patch_time(_p)
        if _pt <= 0:
            continue
        _target = _resolve_target(_p, root)
        if not _target:
            continue
        try:
            _mtime = os.path.getmtime(_target)
        except Exception:
            continue
        if _mtime < _pt - _MTIME_TOLERANCE_SEC:
            issues.append({
                "check": "C4", "ledger": ledger, "id": _p.get("id"),
                "file": _p.get("file"),
                "detail": (f"目标文件 mtime={_mtime:.0f} 早于补丁时间={_pt:.0f} "
                           f"({_pt - _mtime:.0f}s)，修复未落盘（倒灌风险）"),
            })
    return issues


def _check_c5_status(patches, ledger):
    """C5 态白名单。"""
    issues = []
    for _p in patches:
        if not isinstance(_p, dict):
            continue
        _st = _p.get("status")
        if _st is None:
            continue
        if str(_st) not in VALID_STATUSES:
            issues.append({"check": "C5", "ledger": ledger,
                           "id": _p.get("id"),
                           "detail": f"status={_st!r} 不在白名单"})
    return issues


def _check_c6_verified_divergence(patches, ledger):
    """C6 顶层 runtime_verified 与嵌套 runtime_verify_result.verified 背离。"""
    issues = []
    for _p in patches:
        if not isinstance(_p, dict):
            continue
        _rvr = _p.get("runtime_verify_result")
        if not isinstance(_rvr, dict):
            continue
        _nested = _rvr.get("verified")
        if not isinstance(_nested, bool):
            continue
        _top = _p.get("runtime_verified")
        # 顶层字段可能是污染字段（被无条件置 True）；与嵌套真值矛盾即告警
        if isinstance(_top, bool) and _top != _nested:
            issues.append({
                "check": "C6", "ledger": ledger, "id": _p.get("id"),
                "detail": (f"顶层 runtime_verified={_top} 与嵌套 "
                           f"runtime_verify_result.verified={_nested} 背离"),
            })
        elif _top is None and _nested is True:
            # 嵌套为真但顶层缺失（可能被污染字段逻辑漏置）
            issues.append({
                "check": "C6", "ledger": ledger, "id": _p.get("id"),
                "detail": ("嵌套 runtime_verify_result.verified=True 但顶层 "
                           "runtime_verified 缺失"),
            })
    return issues


def _check_c7_obsolete_reason(patches, ledger):
    """C7 无 reason 的 obsolete。"""
    issues = []
    for _p in patches:
        if not isinstance(_p, dict):
            continue
        _is_obs = _p.get("obsolete") is True or str(_p.get("status")) == "obsolete"
        if not _is_obs:
            continue
        _reason = _p.get("obsolete_reason") or _p.get("reason")
        if not _reason or not str(_reason).strip():
            issues.append({"check": "C7", "ledger": ledger,
                           "id": _p.get("id"),
                           "detail": "obsolete 补丁缺 obsolete_reason / reason"})
    return issues


def main(argv=None):
    _ap = argparse.ArgumentParser(description="补丁账本一致性巡检（C1-C7）")
    _ap.add_argument("--root", default=None, help="项目根目录（默认自动探测）")
    _ap.add_argument("--json", action="store_true", help="输出 JSON")
    _ap.add_argument("--strict", action="store_true",
                     help="C3 队列级组合也计为失败（默认仅逐条问题计失败）")
    # ★第116批 T-116b③：CI 直读退化开关
    _ap.add_argument("--no-import", dest="no_import", action="store_true",
                     help="不 import PatchManager，直接读三本账 JSON（CI 友好）")
    _args = _ap.parse_args(argv)

    _root = _args.root or _detect_root()
    _pending, _history, _obsolete, _pp, _hp, _op = _load_ledgers(
        _root, no_import=_args.no_import)

    _all = {
        "pending": _pending,
        "history": _history,
        "obsolete": _obsolete,
    }

    _issues = []
    _issues += _check_c1_double_ledger(_pending, _history)
    _issues += _check_c2_anchor(_pending, "pending")
    _issues += _check_c2_anchor(_history, "history")
    _issues += _check_c3_aging_combo(_pending, _root)
    _issues += _check_c4_backflow(_pending, _root, "pending")
    _issues += _check_c4_backflow(_history, _root, "history")
    _issues += _check_c5_status(_pending, "pending")
    _issues += _check_c5_status(_history, "history")
    _issues += _check_c6_verified_divergence(_pending, "pending")
    _issues += _check_c6_verified_divergence(_history, "history")
    _issues += _check_c7_obsolete_reason(_history, "history")
    _issues += _check_c7_obsolete_reason(_obsolete, "obsolete")

    # 统计
    _by_check = {}
    for _i in _issues:
        _by_check[_i["check"]] = _by_check.get(_i["check"], 0) + 1

    _summary = {
        "root": _root,
        "pending_count": len(_pending),
        "history_count": len(_history),
        "obsolete_count": len(_obsolete),
        "issues_total": len(_issues),
        "issues_by_check": _by_check,
        "paths": {"pending": _pp, "history": _hp, "obsolete": _op},
    }

    if _args.json:
        print(json.dumps({"summary": _summary, "issues": _issues},
                         ensure_ascii=False, indent=2))
    else:
        print(f"[一致性巡检] 项目根: {_root}")
        print(f"  账本来源: {'直读JSON(--no-import)' if _args.no_import else 'PatchManager(默认)'}")
        print(f"  pending={len(_pending)} history={len(_history)} "
              f"obsolete={len(_obsolete)}")
        print(f"  问题总数: {len(_issues)}  按检查: {_by_check or '无'}")
        if _issues:
            print("  --- 明细 ---")
            for _i in _issues:
                _tag = _i.get("check")
                _id = _i.get("id", "-")
                print(f"  [{_tag}] id={_id}: {_i.get('detail')}")
        else:
            print("  ✅ 全部 C1-C7 检查通过")

    # 退出码：有逐条问题 -> 1；仅 C3 队列组合 -> 依 --strict
    _hard = [i for i in _issues if i["check"] != "C3"]
    if _hard:
        return 1
    if _by_check.get("C3") and _args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
