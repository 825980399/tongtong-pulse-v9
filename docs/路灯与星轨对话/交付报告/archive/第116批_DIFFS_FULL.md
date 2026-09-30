# 第116批 改动 DIFF（全量）

- 比对基准：`.bak_batch116/`（首次改动前备份，含 `_manifest.txt` 5 个文件）
- 统计：**+243 / -21**（按行，unified_diff n=6）
- 行尾保全：所有文件行尾未翻转

---

### `nucleus/reasoning/SafeEvolutionExecutor.py`

> 行尾：备份=CRLF 当前=CRLF

```diff
--- a/nucleus/reasoning/SafeEvolutionExecutor.py
+++ b/nucleus/reasoning/SafeEvolutionExecutor.py
@@ -1734,12 +1734,16 @@
                                     _strategy_ad.report("local_rule", success=False)
                                 except Exception as _exc:
                                     _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
                     else:
                         _module_logger.debug(
                             f"[本地修复] {_organ}.{_method} ({_type}) 补丁生成失败或无变化")
+                        # ★第116批 T-116a①：该主分支此前**零冷却登记**（断2 只覆盖"验证未通过"），
+                        #   导致 Liver×2 每轮被重扫重问、永无冷却。补登记使其进冷却闭环。
+                        self._m114a_register_verify_failure(
+                            self._cooldown_key(_issue), detail="补丁生成失败或无变化")
                 except Exception as _le:
                     _module_logger.error(f"[本地修复] {_organ}.{_method} ({_type}) 异常: {_le}")
             # ★M85-1（第85批 T-85a）：本地学习尝试通道。
             #   对「本地无规则」的问题先做一次**保守低风险**修复尝试
             #   （不替代 LLM 通道 / 不自动应用 / 只入队待审批），并把结果记入
             #   data/patches/local_learning_attempts.jsonl，形成
@@ -2445,13 +2449,15 @@
                     "new_issues": _rafter,
                     "detail": (f"[延迟复验] 重采基线: 修复后错误={_rafter}, "
                                f'{"通过" if _rafter == 0 else "未通过"}'),
                     "undecidable": False,
                 }
                 _patch["runtime_verify_result"] = _rverdict
-                _patch["runtime_verified"] = True
+                # ★第116批 T-116b：顶层 runtime_verified 取嵌套真值（原恒 True，
+                #   与 runtime_verify_result.verified=False 背离 ⇒ C6 污染）。
+                _patch["runtime_verified"] = bool(_rverdict.get("verified"))
                 _patch.pop("reverify_after", None)
                 _patch["reverify_count"] = int(_patch.get("reverify_count", 0)) + 1
                 self._m84_recompute_split(_patch)
                 self._learn_from_verification(_patch, _rverdict)
                 if _rafter == 0:
                     _reverified += 1
@@ -2483,25 +2489,29 @@
                     f"[延迟复验] 到点重采基线: {os.path.basename(_rfile)}.{_rmethod} "
                     f'-> {"runtime_verified" if _rafter == 0 else "runtime_failed"}')
 
             for _patch in _pending:
                 if not _patch.get("needs_runtime_verify"):
                     continue
-                if _patch.get("runtime_verified"):
+                # ★第116批 T-116b：runtime_verified 语义改为"验证通过"，故跳过判据
+                #   补 OR runtime_verify_result 存在（=已验过，无论通过与否），保持原有
+                #   "已验证过即跳过、失败补丁不每轮重试"的行为不变。
+                if _patch.get("runtime_verified") or _patch.get("runtime_verify_result") is not None:
                     # ★M84-3（第84批 T-84a）：历史已验补丁补算 problem_fixed——
                     #   此前本分支直接 continue，使旧补丁永久停在 None（实测 4 条
                     #   PulseKidney 补丁 baseline>0 效果 100% 却无该字段）。
                     self._m84_recompute_split(_patch)
                     continue  # 已验证过，跳过
                 if _patch.get("status") == "needs_reverify":
                     continue  # ★第114批 T-114b①：由延迟复验前置处理，避免重复判定
                 _total += 1
 
                 _result = self.verify_fix_from_logs(_patch)
                 _patch["runtime_verify_result"] = _result
-                _patch["runtime_verified"] = True
+                # ★第116批 T-116b：顶层 runtime_verified 不再无条件置 True（C6 污染根因），
+                #   改为按判定结果落到下方"通过/失败/不可判定"三个分支中。
                 # ★第105批 T-105a：同步顶层 baseline_errors（供放行判据读取，
                 #   避免仅依赖嵌套字段），回退 runtime_verify_result.baseline。
                 if not isinstance(_patch.get("baseline_errors"), (int, float)) or _patch.get("baseline_errors") <= 0:
                     _b = (_result or {}).get("baseline")
                     if isinstance(_b, (int, float)) and _b > 0:
                         _patch["baseline_errors"] = _b
@@ -2512,12 +2522,13 @@
                 #   重算语义拆分，把 problem_fixed 从 None 落到 True/False。
                 self._m84_recompute_split(_patch)
 
                 if _result.get("undecidable"):
                     # ★第114批 T-114b①：baseline=0 不可判定 -> 延迟复验，
                     #   不折叠进 runtime_failed（避免误回滚/误标 needs_repair）。
+                    _patch["runtime_verified"] = False
                     _needs_reverify += 1
                     _patch["status"] = "needs_reverify"
                     _patch["reverify_after"] = time.time() + _reverify_delay
                     _patch["reverify_count"] = int(_patch.get("reverify_count", 0)) + 1
                     _ufile = _patch.get("file", "")
                     _umethod = _patch.get("method", "")
@@ -2526,15 +2537,17 @@
                         f"{_patch['reverify_after']:.0f} 重采基线: "
                         f"{os.path.basename(_ufile)}.{_umethod}")
                     continue
                 if _result["verified"]:
                     _verified += 1
                     _patch["status"] = "runtime_verified"
+                    _patch["runtime_verified"] = True
                 else:
                     _failed += 1
                     _patch["status"] = "runtime_failed"
+                    _patch["runtime_verified"] = False
                     _patch["needs_repair"] = True  # 标记需要重新修复
                     # ★A2修复（主线A）：运行时验证失败且补丁已应用到源码时，
                     #   自动回滚，避免「失败的修复」持续留在活代码里造成损害。
                     #   此前只标记 runtime_failed + needs_repair，但已写入的
                     #   源码改动未撤销，与「自主进化可信」原则相悖。
                     _file = _patch.get("file", "")
@@ -2658,26 +2671,28 @@
             for _patch in _history:
                 # 只验证「已应用 + 待运行时验证 + 尚未运行时验证过」的补丁
                 if not _patch.get("applied"):
                     continue
                 if not _patch.get("needs_runtime_verify"):
                     continue
-                if _patch.get("runtime_verified"):
+                # ★第116批 T-116b：同上，跳过判据补 OR runtime_verify_result 存在。
+                if _patch.get("runtime_verified") or _patch.get("runtime_verify_result") is not None:
                     # ★M84-3（第84批 T-84a）：已验补丁补算 problem_fixed（与
                     #   verify_submitted_patches 同源处置）。
                     self._m84_recompute_split(_patch)
                     continue
                 _total += 1
 
                 _file = _patch.get("file", "")
                 _method = _patch.get("method", "")
                 _applied_at = _patch.get("applied_at", 0)
                 _baseline = _patch.get("baseline_errors", 0)
 
                 if not _file or not _method or not _applied_at:
-                    _patch["runtime_verified"] = True
+                    # ★第116批 T-116b：原恒置 True 与嵌套 verified=False 背离（C6 污染）。
+                    _patch["runtime_verified"] = False
                     _patch["runtime_verify_result"] = {
                         "verified": False, "detail": "缺位置/时间戳，跳过运行时验证"}
                     continue
 
                 # 应用后需至少运行 300 秒才有统计意义
                 _elapsed = time.time() - _applied_at
@@ -2697,13 +2712,14 @@
                     _effectiveness = None
                     _verified_ok = False
                     _detail = (f"应用后错误={_after}, 基线=0(baseline_errors=0)，"
                                f"无错误基线可对比，无法判定修复效果（不适用），"
                                f"运行{_elapsed:.0f}秒")
 
-                _patch["runtime_verified"] = True
+                # ★第116批 T-116b：顶层取实测真值 _verified_ok（原恒 True ⇒ C6 污染）。
+                _patch["runtime_verified"] = bool(_verified_ok)
                 _patch["runtime_verify_result"] = {
                     "verified": _verified_ok,
                     "baseline": _baseline,
                     "after_fix": _after,
                     # ★T-99d：baseline=0 时为 None（无法验证），不参与平均
                     "effectiveness": _effectiveness,
@@ -2813,17 +2829,16 @@
                     self._failure_patterns = {}
                 _key = f"{_type}_{_strategy}_{_fail_reason}"
                 self._failure_patterns[_key] = self._failure_patterns.get(_key, 0) + 1
 
                 # ★第114批 T-114a（断2）：验证失败（本地/LLM 统一学习入口）登记冷却。
                 #   补丁指纹 best-effort 构造，兜底覆盖 LLM 路径；本地路径已在修复落点精确登记。
-                _fp = "|".join([
-                    f"organ:{patch.get('organ') or patch.get('target') or str(patch.get('file', '')).split('/')[-1].replace('.py', '')}",
-                    f"type:{patch.get('type') or patch.get('issue_type') or 'unknown'}",
-                    str(patch.get('description') or f"{patch.get('file', '')}:{patch.get('method', '')}")[:120],
-                ])
+                # ★第116批 T-116a③：双指纹格式统一 —— 原此处为 organ|type|desc 第二格式，
+                #   与本地路径 _cooldown_key（file|method|type）并存 ⇒ 同题两路=两键、冷却被稀释。
+                #   统一为 _cooldown_key(patch)（file|method|type，与断2 同构）。
+                _fp = self._cooldown_key(patch)
                 self._m114a_register_verify_failure(_fp, detail=str(_fail_reason))
                 _module_logger.info(
                     f"[经验学习] 失败模式记录: type={_type}, strategy={_strategy}, "
                     f"reason={_fail_reason}, effectiveness={_effectiveness:.0%}"
                 )
             else:
```

### `tools/adjudicate_patch.py`

> 行尾：备份=LF 当前=LF

```diff
--- a/tools/adjudicate_patch.py
+++ b/tools/adjudicate_patch.py
@@ -8,12 +8,16 @@
     list                      列出补丁（可 --status / --source 过滤）
     show <id>                 查看单条补丁
     obsolete <id> --reason R  裁决为 obsolete（置 status=obsolete + obsolete_reason + obsolete=True）
     approve <id>              裁决为 approved（仅对 pending 有效）
     keep <id>                 记录"保留"裁决（不改状态，仅打 adjudicated 标记）
     unlock-ratchet            解锁棘轮（重置重启计数 + 清冷却标记）——须先于解锁完成队列裁决
+                              --require-queue-clean  解锁前校验队列清洁（第116批硬门）
+    reject <id> [--reason R]  裁决为 rejected（第116批补口）
+    stale-audit               只读：三本账"建议作废单"（第116批补口）
+    fix-c6 [--apply]          只读/回填：C6 字段污染（顶层 runtime_verified 取嵌套真值）
 
 所有写操作仅改 data/patches/ 账本（不动 data/knowledge/），下次重启生效。
 退出码：0 成功；2 参数/找不到；1 写盘失败。
 """
 from __future__ import annotations
 
@@ -42,19 +46,28 @@
     _obsolete_path = os.path.join(
         os.path.dirname(pm.get_history_file()), "patch_history_obsolete.json")
     _obsolete = pm.load_json(_obsolete_path, [])
     return _pending, _history, _obsolete, _obsolete_path
 
 
-def _find(pending, history, patch_id, source):
+def _find(pending, history, patch_id, source, obsolete=None):
+    """按账本定位补丁。
+
+    ★第116批 T-116c②/③ 修复：source=='obsolete' 时必须能搜到归档账，
+    否则 keep/reject --source obsolete 恒报“未找到补丁”（实测 rc=2）。
+    """
     if source == "pending":
         _ledgers = [("pending", pending)]
     elif source == "history":
         _ledgers = [("history", history)]
+    elif source == "obsolete":
+        _ledgers = [("obsolete", obsolete or [])]
     else:
         _ledgers = [("pending", pending), ("history", history)]
+        if obsolete:
+            _ledgers.append(("obsolete", obsolete))
     for _name, _lst in _ledgers:
         for _i, _p in enumerate(_lst):
             if isinstance(_p, dict) and str(_p.get("id")) == patch_id:
                 return _name, _lst, _i, _p
     return None, None, None, None
 
@@ -94,15 +107,23 @@
         print(f"未找到补丁: {args.patch_id}", file=sys.stderr)
         return 2
     print(json.dumps(_p, ensure_ascii=False, indent=2))
     return 0
 
 
-def _save_ledger(pm, name, lst):
+def _save_ledger(pm, name, lst, obsolete_path=None):
+    """★第116批 T-116c②/③ 修复：补 obsolete 归档账写回分支。"""
     if name == "pending":
         _path = pm.get_pending_file()
+    elif name == "obsolete":
+        if not obsolete_path:
+            _path = os.path.join(
+                os.path.dirname(pm.get_history_file()),
+                "patch_history_obsolete.json")
+        else:
+            _path = obsolete_path
     else:
         _path = pm.get_history_file()
     return pm._save_json(_path, lst)
 
 
 def _cmd_obsolete(args, pm, pending, history, obsolete, obsolete_path):
@@ -158,40 +179,179 @@
         return 1
     print(f"✅ 已裁决 approved: {args.patch_id}")
     return 0
 
 
 def _cmd_keep(args, pm, pending, history, obsolete, _op):
-    _name, _lst, _i, _p = _find(pending, history, args.patch_id, args.source)
+    _name, _lst, _i, _p = _find(pending, history, args.patch_id, args.source,
+                                obsolete=obsolete)
     if _p is None:
         print(f"未找到补丁: {args.patch_id}", file=sys.stderr)
         return 2
     _p["adjudicated"] = True
     _p["adjudicated_at"] = time.time()
     _p["adjudicated_by"] = "adjudicate_patch.cli"
     _p["adjudicated_verdict"] = "keep"
-    if not _save_ledger(pm, _name, _lst):
+    if not _save_ledger(pm, _name, _lst, obsolete_path=_op):
         print("写盘失败", file=sys.stderr)
         return 1
     print(f"✅ 已记录保留裁决: {args.patch_id}（状态未改动）")
     return 0
 
 
 def _cmd_unlock_ratchet(args, pm, pending, history, obsolete, _op):
+    # ★第116批 T-116c④：解锁硬门
+    if getattr(args, "require_queue_clean", False):
+        _dirty = _queue_clean(pending)
+        if _dirty:
+            print(f"❌ 队列不清洁，拒绝解锁：仍有 {len(_dirty)} 条悬挂项", file=sys.stderr)
+            for _d in _dirty[:20]:
+                print(f"   - {_d}", file=sys.stderr)
+            return 2
+        print("✅ 队列清洁校验通过")
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
 
 
+def _c6_violations(rows):
+    """返回 [(src, idx, patch)]：顶层 runtime_verified=True 但嵌套 verified 为 False。"""
+    _out = []
+    for _src, _i, _p in rows:
+        if not isinstance(_p, dict):
+            continue
+        if _p.get("runtime_verified") is not True:
+            continue
+        _rr = _p.get("runtime_verify_result")
+        if not isinstance(_rr, dict):
+            continue
+        if _rr.get("verified") is False:
+            _out.append((_src, _i, _p))
+    return _out
+
+
+def _cmd_fix_c6(args, pm, pending, history, obsolete, _op):
+    """★第116批 T-116b②：C6 字段污染回填（顶层 runtime_verified 取嵌套真值）。
+
+    默认 dry-run（只报告不写盘）；--apply 才写，且写盘须经 PULSE_FRAMEWORK=1 逃生口
+    （WriteGuard fail-closed 默认只读）。
+    """
+    _rows = ([("pending", i, p) for i, p in enumerate(pending)]
+             + [("history", i, p) for i, p in enumerate(history)]
+             + [("obsolete", i, p) for i, p in enumerate(obsolete)])
+    _bad = _c6_violations(_rows)
+    print(f"[fix-c6] 扫描 {len(_rows)} 条，C6 污染 {len(_bad)} 条")
+    for _src, _i, _p in _bad:
+        print(f"  - {_p.get('id')}  src={_src}  file={_p.get('file')}  "
+              f"顶层=True 嵌套={(_p.get('runtime_verify_result') or {}).get('verified')}")
+    if not args.apply:
+        print("[fix-c6] dry-run：未写盘（加 --apply 执行回填）")
+        return 0
+    for _src, _i, _p in _bad:
+        _p["runtime_verified"] = False
+        _p["c6_backfilled_at"] = time.time()
+        _p["c6_backfilled_by"] = "adjudicate_patch.cli fix-c6"
+    _saved = True
+    for _src in ("pending", "history", "obsolete"):
+        _lst = {"pending": pending, "history": history, "obsolete": obsolete}[_src]
+        if any(_s == _src for _s, _i, _p in _bad):
+            _saved = _saved and _save_ledger(pm, _src, _lst)
+    if not _saved:
+        print("写盘失败（若提示只读，请加 PULSE_FRAMEWORK=1）", file=sys.stderr)
+        return 1
+    print(f"✅ [fix-c6] 已回填 {len(_bad)} 条")
+    return 0
+
+
+def _stale_reason(p):
+    """★T-116c①：内联 is_obsolete + 按龄判 stale（读 PatchAutoApprover 的判据为可选）。"""
+    if p.get("obsolete") is True or str(p.get("status")) == "obsolete":
+        return "已标记 obsolete"
+    _reason = str(p.get("obsolete_reason") or p.get("reason") or "")
+    for _m in _OBSOLETE_MARKERS_FALLBACK:
+        if _m in _reason:
+            return f"理由命中废弃标记: {_m}"
+    _ts = p.get("created_at") or p.get("applied_at") or 0
+    try:
+        _ts = float(_ts or 0)
+    except (TypeError, ValueError):
+        _ts = 0.0
+    if _ts <= 0:
+        return "created_at 缺失（第116批 T-116f 已补记新入队时间戳，历史票仍无）"
+    _days = (time.time() - _ts) / 86400.0
+    if _days > 30:
+        return f"超龄 {_days:.0f} 天（>30）"
+    return ""
+
+
+def _cmd_stale_audit(args, pm, pending, history, obsolete, _op):
+    """★T-116c①：stale-audit 只读子命令 —— 出三本账"建议作废单"。"""
+    _total = 0
+    for _name, _lst in (("pending", pending), ("history", history), ("obsolete", obsolete)):
+        print(f"\n=== {_name} 账本（{len(_lst)} 条）建议作废单 ===")
+        _n = 0
+        for _p in _lst:
+            if not isinstance(_p, dict):
+                continue
+            _r = _stale_reason(_p)
+            if not _r:
+                continue
+            _n += 1
+            print(f"  - {_p.get('id')}  status={_p.get('status')}  {_r}")
+        if _n == 0:
+            print("  （无）")
+        _total += _n
+    print(f"\n[stale-audit] 合计建议作废 {_total} 条（只读，未做任何改动）")
+    return 0
+
+
+def _cmd_reject(args, pm, pending, history, obsolete, _op):
+    """★T-116c③：reject 入口（白名单含 rejected 态）。"""
+    _name, _lst, _i, _p = _find(pending, history, args.patch_id, args.source,
+                                obsolete=obsolete)
+    if _p is None:
+        print(f"未找到补丁: {args.patch_id}", file=sys.stderr)
+        return 2
+    _p["status"] = "rejected"
+    _p["adjudicated"] = True
+    _p["adjudicated_at"] = time.time()
+    _p["adjudicated_by"] = "adjudicate_patch.cli"
+    _p["adjudicated_verdict"] = "rejected"
+    if args.reason:
+        _p["adjudicated_reason"] = args.reason
+    if not _save_ledger(pm, _name, _lst, obsolete_path=_op):
+        print("写盘失败", file=sys.stderr)
+        return 1
+    print(f"✅ 已裁决 rejected: {args.patch_id}")
+    return 0
+
+
+def _queue_clean(pending):
+    """队列清洁判据：顶层 status 为 pending / needs_reverify 的悬挂项。
+
+    ★第116批 T-116c④ 注释校正：'undecidable' 并非顶层 status，
+    而是 runtime_verify_result 内的嵌套字段（SafeEvolutionExecutor
+    :2305/:2318/:2340），此前 docstring 将其写成 status 属误导。
+    本函数据此只按顶层 status 判定；嵌套 undecidable 是否纳入悬挂 -> 待裁决。
+    """
+    _dirty = []
+    for _p in pending:
+        if not isinstance(_p, dict):
+            continue
+        if str(_p.get("status")) in ("pending", "needs_reverify"):
+            _dirty.append(str(_p.get("id")))
+    return _dirty
+
+
 def main(argv=None):
     _ap = argparse.ArgumentParser(description="补丁裁决 CLI（T-114b③c / T-114c）")
     _ap.add_argument("--root", default=None, help="项目根目录")
     _sub = _ap.add_subparsers(dest="cmd", required=True)
 
     _sp = _sub.add_parser("list")
@@ -210,27 +370,47 @@
     _sp = _sub.add_parser("approve")
     _sp.add_argument("patch_id")
     _sp.add_argument("--source", default=None, choices=["pending", "history"])
 
     _sp = _sub.add_parser("keep")
     _sp.add_argument("patch_id")
-    _sp.add_argument("--source", default=None, choices=["pending", "history"])
-
-    _sub.add_parser("unlock-ratchet")
+    # ★第116批 T-116c②：兼补 T-113a 追溯票通道（obsolete 账本也可记录保留裁决）
+    _sp.add_argument("--source", default=None,
+                     choices=["pending", "history", "obsolete"])
+
+    _sp = _sub.add_parser("reject")
+    _sp.add_argument("patch_id")
+    _sp.add_argument("--source", default=None,
+                     choices=["pending", "history", "obsolete"])
+    _sp.add_argument("--reason", default=None)
+
+    _sp = _sub.add_parser("stale-audit")
+
+    _sp = _sub.add_parser("fix-c6")
+    _sp.add_argument("--apply", action="store_true",
+                     help="默认 dry-run；加此开关才回填写盘")
+
+    _sp = _sub.add_parser("unlock-ratchet")
+    # ★第116批 T-116c④：解锁硬门 —— 解锁前检查队列清洁
+    _sp.add_argument("--require-queue-clean", action="store_true",
+                     help="解锁前校验队列清洁，存在悬挂项则拒绝解锁")
 
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
+        "reject": _cmd_reject,
+        "stale-audit": _cmd_stale_audit,
+        "fix-c6": _cmd_fix_c6,
         "unlock-ratchet": _cmd_unlock_ratchet,
     }
     return _dispatch[_args.cmd](_args, _pm, _pending, _history, _obsolete, _op)
 
 
 if __name__ == "__main__":
```

### `nucleus/reasoning/PatchManager.py`

> 行尾：备份=CRLF 当前=CRLF

```diff
--- a/nucleus/reasoning/PatchManager.py
+++ b/nucleus/reasoning/PatchManager.py
@@ -858,12 +858,17 @@
         保存待应用的补丁到队列。
         ★v23.0增强：自动补齐元数据 + 去重检查。
         """
         # ★v23.0新增：补全元数据
         if "saved_at" not in patch:
             patch["saved_at"] = time.time()
+        # ★第116批 T-116f：入队补记真实创建时间戳。此前 pending 全量 created_at=0，
+        #   导致只能做集合差分、无法按时间排序/判龄（stale-audit 亦因此判不了超龄）。
+        #   历史票仍为 0，需另做一次性回填（本批未做，避免追溯改写）。
+        if not patch.get("created_at"):
+            patch["created_at"] = time.time()
         if "status" not in patch:
             patch["status"] = "pending"
         if "reason" not in patch:
             patch["reason"] = patch.get("description", "未说明修改原因")
         if "source_diagnosis" not in patch:
             patch["source_diagnosis"] = patch.get("source", "未知诊断源")
```

### `nucleus/evolution/PatchAutoApprover.py`

> 行尾：备份=LF 当前=LF

```diff
--- a/nucleus/evolution/PatchAutoApprover.py
+++ b/nucleus/evolution/PatchAutoApprover.py
@@ -6,12 +6,18 @@
 设计: 路灯、小林、星轨
 日期: 2026年9月11日
 
 职责: 基于规则的补丁自动审批与风险评估
 机制: 基于PatchAutoApprover类实现，包含10个核心方法
 定位: 进化治理层
+
+★第116批 T-116c⑤ 状态标注 —— 审批面 deprecated（本批删 0 行）:
+  AutoApprover 的「检测面」（is_obsolete / 按龄判废）已由裁决 CLI 的
+  `adjudicate_patch.py stale-audit` 只读子命令收编（内联同判据，出三本账建议作废单）。
+  ★处置约定：stale-audit 连续两个周期「无独有捕获」（即它发现的项 AutoApprover 也全部发现）
+    后，才物理删除本模块的审批面；本批不做删除。
 
 ★第53批 T1（P0-补丁3）状态标注 —— 短期 deprecated（星轨裁决2·选项B）:
   本模块的审批/清理入口（classify / scan_pending / prune_pending）经全库排查
   **无生产调用点**（仅 record_evolution_round 被 SafeEvolutionExecutor 调用）。
   当前生产侧自动审批由 PatchManager 入队时的内联逻辑承担。
   本模块保留用于人工审计与长期集成评估；两处信任分门槛已统一为 40。
```

### `tools/check_patch_consistency.py`

> 行尾：备份=LF 当前=LF

```diff
--- a/tools/check_patch_consistency.py
+++ b/tools/check_patch_consistency.py
@@ -16,12 +16,17 @@
     C7 无reason的obsolete —— obsolete 补丁缺 obsolete_reason / reason
 
 退出码：发现任一问题 -> 1；全部通过 -> 0。便于门禁串联。
 
 用法：
     python tools/check_patch_consistency.py [--root <项目根>] [--json] [--strict]
+                                            [--no-import]
+
+★第116批 T-116b③：新增 --no-import —— CI 环境用它强制走「直读 JSON」退化路径，
+  不 import PatchManager（避免拉起 config/框架依赖导致门禁受污染或变慢）。
+  默认仍优先复用 PatchManager 的真实路径（与生产口径一致）。
 """
 from __future__ import annotations
 
 import argparse
 import json
 import os
@@ -44,17 +49,23 @@
 
 def _detect_root() -> str:
     """tools/check_patch_consistency.py -> 项目根 = tools 的父目录。"""
     return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
 
 
-def _load_ledgers(root: str):
-    """优先复用 PatchManager 的真实路径与加载逻辑；失败则退化为直接读 JSON。"""
+def _load_ledgers(root: str, no_import: bool = False):
+    """优先复用 PatchManager 的真实路径与加载逻辑；失败则退化为直接读 JSON。
+
+    ★第116批 T-116b③：no_import=True 时跳过 PatchManager，直接按约定路径读三本账，
+    供 CI 门禁在无框架依赖的环境下使用（结果口径与退化路径一致）。
+    """
     pending, history, obsolete = [], [], []
     pending_path = history_path = obsolete_path = ""
     try:
+        if no_import:
+            raise RuntimeError("--no-import：按用户要求跳过 PatchManager 直读")
         sys.path.insert(0, root)
         from nucleus.reasoning.PatchManager import PatchManager
         _pm = PatchManager(root)
         pending_path = _pm.get_pending_file()
         history_path = _pm.get_history_file()
         _hist_dir = os.path.dirname(history_path)
@@ -266,16 +277,20 @@
 def main(argv=None):
     _ap = argparse.ArgumentParser(description="补丁账本一致性巡检（C1-C7）")
     _ap.add_argument("--root", default=None, help="项目根目录（默认自动探测）")
     _ap.add_argument("--json", action="store_true", help="输出 JSON")
     _ap.add_argument("--strict", action="store_true",
                      help="C3 队列级组合也计为失败（默认仅逐条问题计失败）")
+    # ★第116批 T-116b③：CI 直读退化开关
+    _ap.add_argument("--no-import", dest="no_import", action="store_true",
+                     help="不 import PatchManager，直接读三本账 JSON（CI 友好）")
     _args = _ap.parse_args(argv)
 
     _root = _args.root or _detect_root()
-    _pending, _history, _obsolete, _pp, _hp, _op = _load_ledgers(_root)
+    _pending, _history, _obsolete, _pp, _hp, _op = _load_ledgers(
+        _root, no_import=_args.no_import)
 
     _all = {
         "pending": _pending,
         "history": _history,
         "obsolete": _obsolete,
     }
@@ -311,12 +326,13 @@
 
     if _args.json:
         print(json.dumps({"summary": _summary, "issues": _issues},
                          ensure_ascii=False, indent=2))
     else:
         print(f"[一致性巡检] 项目根: {_root}")
+        print(f"  账本来源: {'直读JSON(--no-import)' if _args.no_import else 'PatchManager(默认)'}")
         print(f"  pending={len(_pending)} history={len(_history)} "
               f"obsolete={len(_obsolete)}")
         print(f"  问题总数: {len(_issues)}  按检查: {_by_check or '无'}")
         if _issues:
             print("  --- 明细 ---")
             for _i in _issues:
```
