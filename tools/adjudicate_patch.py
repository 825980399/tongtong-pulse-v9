# -*- coding: utf-8 -*-
"""第114批 T-114b③c / T-114c：补丁裁决 CLI（收编带外裁决文化）。

将"人工/机器裁决补丁"从散落的带外脚本收口为统一 CLI，供解锁棘轮前的队列裁决
（T-114c）与日常运维使用。

子命令：
    list                      列出补丁（可 --status / --source 过滤）
    show <id>                 查看单条补丁
    obsolete <id> --reason R  裁决为 obsolete（置 status=obsolete + obsolete_reason + obsolete=True）
    approve <id>              裁决为 approved（仅对 pending 有效）
    keep <id>                 记录"保留"裁决（不改状态，仅打 adjudicated 标记）
    unlock-ratchet            解锁棘轮（重置重启计数 + 清冷却标记）——须先于解锁完成队列裁决
                              --require-queue-clean  解锁前校验队列清洁（第116批硬门）
    reject <id> [--reason R]  裁决为 rejected（第116批补口）
    stale-audit               只读：三本账"建议作废单"（第116批补口）
    fix-c6 [--apply]          只读/回填：C6 字段污染（顶层 runtime_verified 取嵌套真值）

所有写操作仅改 data/patches/ 账本（不动 data/knowledge/），下次重启生效。
退出码：0 成功；2 参数/找不到；1 写盘失败。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_OBSOLETE_MARKERS_FALLBACK = ("结构性失效", "obsolete", "已裁", "判定废弃")


def _detect_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get_pm(root: str):
    sys.path.insert(0, root)
    from nucleus.reasoning.PatchManager import PatchManager
    return PatchManager(root)


def _load_all(pm):
    _pending = pm.load_json(pm.get_pending_file(), [])
    _history = pm.load_json(pm.get_history_file(), [])
    _obsolete_path = os.path.join(
        os.path.dirname(pm.get_history_file()), "patch_history_obsolete.json")
    _obsolete = pm.load_json(_obsolete_path, [])
    return _pending, _history, _obsolete, _obsolete_path


def _find(pending, history, patch_id, source, obsolete=None):
    """按账本定位补丁。

    ★第116批 T-116c②/③ 修复：source=='obsolete' 时必须能搜到归档账，
    否则 keep/reject --source obsolete 恒报“未找到补丁”（实测 rc=2）。
    """
    if source == "pending":
        _ledgers = [("pending", pending)]
    elif source == "history":
        _ledgers = [("history", history)]
    elif source == "obsolete":
        _ledgers = [("obsolete", obsolete or [])]
    else:
        _ledgers = [("pending", pending), ("history", history)]
        if obsolete:
            _ledgers.append(("obsolete", obsolete))
    for _name, _lst in _ledgers:
        for _i, _p in enumerate(_lst):
            if isinstance(_p, dict) and str(_p.get("id")) == patch_id:
                return _name, _lst, _i, _p
    return None, None, None, None


def _cmd_list(args, pm, pending, history, obsolete, _op):
    _rows = []
    if args.source in (None, "pending"):
        _rows += [("pending", p) for p in pending]
    if args.source in (None, "history"):
        _rows += [("history", p) for p in history]
    if args.source in (None, "obsolete"):
        _rows += [("obsolete", p) for p in obsolete]
    _shown = 0
    for _src, _p in _rows:
        if not isinstance(_p, dict):
            continue
        if args.status and str(_p.get("status")) != args.status:
            continue
        _shown += 1
        if args.limit and _shown > args.limit:
            break
        print(f"[{_src}] {_p.get('id')} | status={_p.get('status')} | "
              f"file={_p.get('file')} | method={_p.get('method')} | "
              f"obsolete={_p.get('obsolete')}")
    print(f"共 {_shown} 条（source={args.source or 'all'}, status={args.status or 'any'}）")
    return 0


def _cmd_show(args, pm, pending, history, obsolete, _op):
    _name, _lst, _i, _p = _find(pending, history, args.patch_id, None)
    if _p is None:
        # 也查 obsolete 账
        for _i2, _p2 in enumerate(obsolete):
            if isinstance(_p2, dict) and str(_p2.get("id")) == args.patch_id:
                print(json.dumps(_p2, ensure_ascii=False, indent=2))
                return 0
        print(f"未找到补丁: {args.patch_id}", file=sys.stderr)
        return 2
    print(json.dumps(_p, ensure_ascii=False, indent=2))
    return 0


def _save_ledger(pm, name, lst, obsolete_path=None):
    """★第116批 T-116c②/③ 修复：补 obsolete 归档账写回分支。"""
    if name == "pending":
        _path = pm.get_pending_file()
    elif name == "obsolete":
        if not obsolete_path:
            _path = os.path.join(
                os.path.dirname(pm.get_history_file()),
                "patch_history_obsolete.json")
        else:
            _path = obsolete_path
    else:
        _path = pm.get_history_file()
    return pm._save_json(_path, lst)


def _cmd_obsolete(args, pm, pending, history, obsolete, obsolete_path):
    if not args.reason or not args.reason.strip():
        print("obsolete 必须带 --reason", file=sys.stderr)
        return 2
    _name, _lst, _i, _p = _find(pending, history, args.patch_id, args.source)
    if _p is None:
        print(f"未找到补丁: {args.patch_id}", file=sys.stderr)
        return 2
    _p["status"] = "obsolete"
    _p["obsolete"] = True
    _p["obsolete_reason"] = args.reason
    _p["obsolete_at"] = time.time()
    _p["needs_runtime_verify"] = False
    _p["adjudicated"] = True
    _p["adjudicated_at"] = time.time()
    _p["adjudicated_by"] = "adjudicate_patch.cli"
    _p["adjudicated_verdict"] = "obsolete"
    # 移出源账本（pending/history），并入 obsolete 归档，使活跃队列干净
    try:
        _lst.pop(_i)
    except Exception:
        pass
    _seen = {str(o.get("id")) for o in obsolete if isinstance(o, dict)}
    if str(_p.get("id")) not in _seen:
        obsolete.append(_p)
    _ok = _save_ledger(pm, _name, _lst)
    _ok2 = pm._save_json(obsolete_path, obsolete)
    if not (_ok and _ok2):
        print("写盘失败", file=sys.stderr)
        return 1
    print(f"✅ 已裁决 obsolete 并移出活跃队列: {args.patch_id} "
          f"（原账={_name} -> obsolete归档, reason={args.reason}）")
    return 0


def _cmd_approve(args, pm, pending, history, obsolete, _op):
    _name, _lst, _i, _p = _find(pending, history, args.patch_id, args.source or "pending")
    if _p is None:
        print(f"未找到补丁: {args.patch_id}", file=sys.stderr)
        return 2
    if _name != "pending":
        print("approve 仅对 pending 补丁有效", file=sys.stderr)
        return 2
    _p["status"] = "approved"
    _p["adjudicated"] = True
    _p["adjudicated_at"] = time.time()
    _p["adjudicated_by"] = "adjudicate_patch.cli"
    _p["adjudicated_verdict"] = "approved"
    if not _save_ledger(pm, _name, _lst):
        print("写盘失败", file=sys.stderr)
        return 1
    print(f"✅ 已裁决 approved: {args.patch_id}")
    return 0


def _cmd_keep(args, pm, pending, history, obsolete, _op):
    _name, _lst, _i, _p = _find(pending, history, args.patch_id, args.source,
                                obsolete=obsolete)
    if _p is None:
        print(f"未找到补丁: {args.patch_id}", file=sys.stderr)
        return 2
    _p["adjudicated"] = True
    _p["adjudicated_at"] = time.time()
    _p["adjudicated_by"] = "adjudicate_patch.cli"
    _p["adjudicated_verdict"] = "keep"
    if not _save_ledger(pm, _name, _lst, obsolete_path=_op):
        print("写盘失败", file=sys.stderr)
        return 1
    print(f"✅ 已记录保留裁决: {args.patch_id}（状态未改动）")
    return 0


def _cmd_unlock_ratchet(args, pm, pending, history, obsolete, _op):
    # ★第116批 T-116c④：解锁硬门
    if getattr(args, "require_queue_clean", False):
        _dirty = _queue_clean(pending)
        if _dirty:
            print(f"❌ 队列不清洁，拒绝解锁：仍有 {len(_dirty)} 条悬挂项", file=sys.stderr)
            for _d in _dirty[:20]:
                print(f"   - {_d}", file=sys.stderr)
            return 2
        print("✅ 队列清洁校验通过")
    try:
        pm.reset_restart_counter()
        _blocked_path = os.path.join(pm._patch_dir, "restart_blocked_at.txt")
        if os.path.exists(_blocked_path):
            os.remove(_blocked_path)
        print("✅ 棘轮已解锁（重启计数重置 + 冷却标记清除）")
        return 0
    except Exception as _e:
        print(f"解锁棘轮失败: {_e}", file=sys.stderr)
        return 1


def _c6_violations(rows):
    """返回 [(src, idx, patch)]：顶层 runtime_verified=True 但嵌套 verified 为 False。"""
    _out = []
    for _src, _i, _p in rows:
        if not isinstance(_p, dict):
            continue
        if _p.get("runtime_verified") is not True:
            continue
        _rr = _p.get("runtime_verify_result")
        if not isinstance(_rr, dict):
            continue
        if _rr.get("verified") is False:
            _out.append((_src, _i, _p))
    return _out


def _cmd_fix_c6(args, pm, pending, history, obsolete, _op):
    """★第116批 T-116b②：C6 字段污染回填（顶层 runtime_verified 取嵌套真值）。

    默认 dry-run（只报告不写盘）；--apply 才写，且写盘须经 PULSE_FRAMEWORK=1 逃生口
    （WriteGuard fail-closed 默认只读）。
    """
    _rows = ([("pending", i, p) for i, p in enumerate(pending)]
             + [("history", i, p) for i, p in enumerate(history)]
             + [("obsolete", i, p) for i, p in enumerate(obsolete)])
    _bad = _c6_violations(_rows)
    print(f"[fix-c6] 扫描 {len(_rows)} 条，C6 污染 {len(_bad)} 条")
    for _src, _i, _p in _bad:
        print(f"  - {_p.get('id')}  src={_src}  file={_p.get('file')}  "
              f"顶层=True 嵌套={(_p.get('runtime_verify_result') or {}).get('verified')}")
    if not args.apply:
        print("[fix-c6] dry-run：未写盘（加 --apply 执行回填）")
        return 0
    for _src, _i, _p in _bad:
        _p["runtime_verified"] = False
        _p["c6_backfilled_at"] = time.time()
        _p["c6_backfilled_by"] = "adjudicate_patch.cli fix-c6"
    _saved = True
    for _src in ("pending", "history", "obsolete"):
        _lst = {"pending": pending, "history": history, "obsolete": obsolete}[_src]
        if any(_s == _src for _s, _i, _p in _bad):
            _saved = _saved and _save_ledger(pm, _src, _lst)
    if not _saved:
        print("写盘失败（若提示只读，请加 PULSE_FRAMEWORK=1）", file=sys.stderr)
        return 1
    print(f"✅ [fix-c6] 已回填 {len(_bad)} 条")
    return 0


def _stale_reason(p):
    """★T-116c①：内联 is_obsolete + 按龄判 stale（读 PatchAutoApprover 的判据为可选）。"""
    if p.get("obsolete") is True or str(p.get("status")) == "obsolete":
        return "已标记 obsolete"
    _reason = str(p.get("obsolete_reason") or p.get("reason") or "")
    for _m in _OBSOLETE_MARKERS_FALLBACK:
        if _m in _reason:
            return f"理由命中废弃标记: {_m}"
    _ts = p.get("created_at") or p.get("applied_at") or 0
    try:
        _ts = float(_ts or 0)
    except (TypeError, ValueError):
        _ts = 0.0
    if _ts <= 0:
        return "created_at 缺失（第116批 T-116f 已补记新入队时间戳，历史票仍无）"
    _days = (time.time() - _ts) / 86400.0
    if _days > 30:
        return f"超龄 {_days:.0f} 天（>30）"
    return ""


def _cmd_stale_audit(args, pm, pending, history, obsolete, _op):
    """★T-116c①：stale-audit 只读子命令 —— 出三本账"建议作废单"。"""
    _total = 0
    for _name, _lst in (("pending", pending), ("history", history), ("obsolete", obsolete)):
        print(f"\n=== {_name} 账本（{len(_lst)} 条）建议作废单 ===")
        _n = 0
        for _p in _lst:
            if not isinstance(_p, dict):
                continue
            _r = _stale_reason(_p)
            if not _r:
                continue
            _n += 1
            print(f"  - {_p.get('id')}  status={_p.get('status')}  {_r}")
        if _n == 0:
            print("  （无）")
        _total += _n
    print(f"\n[stale-audit] 合计建议作废 {_total} 条（只读，未做任何改动）")
    return 0


def _cmd_reject(args, pm, pending, history, obsolete, _op):
    """★T-116c③：reject 入口（白名单含 rejected 态）。"""
    _name, _lst, _i, _p = _find(pending, history, args.patch_id, args.source,
                                obsolete=obsolete)
    if _p is None:
        print(f"未找到补丁: {args.patch_id}", file=sys.stderr)
        return 2
    _p["status"] = "rejected"
    _p["adjudicated"] = True
    _p["adjudicated_at"] = time.time()
    _p["adjudicated_by"] = "adjudicate_patch.cli"
    _p["adjudicated_verdict"] = "rejected"
    if args.reason:
        _p["adjudicated_reason"] = args.reason
    if not _save_ledger(pm, _name, _lst, obsolete_path=_op):
        print("写盘失败", file=sys.stderr)
        return 1
    print(f"✅ 已裁决 rejected: {args.patch_id}")
    return 0


def _queue_clean(pending):
    """队列清洁判据：顶层 status 为 pending / needs_reverify 的悬挂项。

    ★第116批 T-116c④ 注释校正：'undecidable' 并非顶层 status，
    而是 runtime_verify_result 内的嵌套字段（SafeEvolutionExecutor
    :2305/:2318/:2340），此前 docstring 将其写成 status 属误导。
    本函数据此只按顶层 status 判定；嵌套 undecidable 是否纳入悬挂 -> 待裁决。
    """
    _dirty = []
    for _p in pending:
        if not isinstance(_p, dict):
            continue
        if str(_p.get("status")) in ("pending", "needs_reverify"):
            _dirty.append(str(_p.get("id")))
    return _dirty


def main(argv=None):
    _ap = argparse.ArgumentParser(description="补丁裁决 CLI（T-114b③c / T-114c）")
    _ap.add_argument("--root", default=None, help="项目根目录")
    _sub = _ap.add_subparsers(dest="cmd", required=True)

    _sp = _sub.add_parser("list")
    _sp.add_argument("--status", default=None)
    _sp.add_argument("--source", default=None, choices=["pending", "history", "obsolete"])
    _sp.add_argument("--limit", type=int, default=0)

    _sp = _sub.add_parser("show")
    _sp.add_argument("patch_id")

    _sp = _sub.add_parser("obsolete")
    _sp.add_argument("patch_id")
    _sp.add_argument("--reason", required=True)
    _sp.add_argument("--source", default=None, choices=["pending", "history"])

    _sp = _sub.add_parser("approve")
    _sp.add_argument("patch_id")
    _sp.add_argument("--source", default=None, choices=["pending", "history"])

    _sp = _sub.add_parser("keep")
    _sp.add_argument("patch_id")
    # ★第116批 T-116c②：兼补 T-113a 追溯票通道（obsolete 账本也可记录保留裁决）
    _sp.add_argument("--source", default=None,
                     choices=["pending", "history", "obsolete"])

    _sp = _sub.add_parser("reject")
    _sp.add_argument("patch_id")
    _sp.add_argument("--source", default=None,
                     choices=["pending", "history", "obsolete"])
    _sp.add_argument("--reason", default=None)

    _sp = _sub.add_parser("stale-audit")

    _sp = _sub.add_parser("fix-c6")
    _sp.add_argument("--apply", action="store_true",
                     help="默认 dry-run；加此开关才回填写盘")

    _sp = _sub.add_parser("unlock-ratchet")
    # ★第116批 T-116c④：解锁硬门 —— 解锁前检查队列清洁
    _sp.add_argument("--require-queue-clean", action="store_true",
                     help="解锁前校验队列清洁，存在悬挂项则拒绝解锁")

    _args = _ap.parse_args(argv)
    _root = _args.root or _detect_root()
    _pm = _get_pm(_root)
    _pending, _history, _obsolete, _op = _load_all(_pm)

    _dispatch = {
        "list": _cmd_list,
        "show": _cmd_show,
        "obsolete": _cmd_obsolete,
        "approve": _cmd_approve,
        "keep": _cmd_keep,
        "reject": _cmd_reject,
        "stale-audit": _cmd_stale_audit,
        "fix-c6": _cmd_fix_c6,
        "unlock-ratchet": _cmd_unlock_ratchet,
    }
    return _dispatch[_args.cmd](_args, _pm, _pending, _history, _obsolete, _op)


if __name__ == "__main__":
    sys.exit(main())
