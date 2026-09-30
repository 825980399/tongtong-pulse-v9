# 第114批 DIFFS_FULL

> 由 `tmp/gen_b114_diffs.py` 生成，比对 `.bak_batch114` 快照与当前工作副本。
> ruff F=0 / py_compile 全过（见交付报告）。

## nucleus/reasoning/SafeEvolutionExecutor.py
> T-114a 进化消化根因六断点 + T-114b① 延迟复验队列

```diff
--- a/before_b114b/nucleus/reasoning/SafeEvolutionExecutor.py
+++ b/nucleus/reasoning/SafeEvolutionExecutor.py
@@ -2304,6 +2304,7 @@
         _after = self._count_errors_for_location(_file, _method, since=_fixed_at)
 
         # 计算修复效果：错误减少率
+        _undecidable = False
         if _baseline > 0:
             _effectiveness = max(0.0, min(1.0, 1.0 - _after / _baseline))
             _verified = _after == 0 or _effectiveness >= 0.8
@@ -2316,6 +2317,7 @@
             # 既不算修复成功，也不参与平均效果计算。
             _effectiveness = None
             _verified = False
+            _undecidable = True
             _detail = (f"修复前错误=0(baseline_errors=0)，无错误基线可对比，"
                        f"无法判定修复效果（不适用）；修复后错误={_after}, "
                        f"运行时长={_elapsed:.0f}秒")
@@ -2334,6 +2336,10 @@
             "effectiveness": _effectiveness,
             "new_issues": _after,
             "detail": _detail,
+            # ★第114批 T-114b①：baseline=0 无错误基线可对比 -> undecidable=True，
+            #   调用方据此把补丁转入 needs_reverify（延迟复验）而非 runtime_failed，
+            #   避免"无法验证"被误折叠为"修复失败"进而误回滚。
+            "undecidable": _undecidable,
         }
 
     def _m84_recompute_split(self, patch: dict[str, Any]) -> bool:
@@ -2412,6 +2418,72 @@
             _failed = 0
             _skipped = 0
             _effects = []
+            _reverified = 0
+            _reverify_failed = 0
+            _needs_reverify = 0
+            _reverify_delay = 3600  # 延迟复验窗口（秒）：到点后重采基线
+
+            # ★第114批 T-114b①：延迟复验前置——每轮开头对到点(>=reverify_after)的
+            #   needs_reverify 补丁重采基线(重数修复后错误)，据实判定，避免 baseline=0
+            #   的"不可判定"被永久折叠进 runtime_failed + 回滚。未到点者跳过，等下轮。
+            _now = time.time()
+            for _patch in _pending:
+                if _patch.get("status") != "needs_reverify":
+                    continue
+                _ra = _patch.get("reverify_after", 0) or 0
+                if _ra and _now < _ra:
+                    continue
+                _rfile = _patch.get("file", "")
+                _rmethod = _patch.get("method", "")
+                _rfixed = _patch.get("fixed_at", 0) or 0
+                _rafter = self._count_errors_for_location(
+                    _rfile, _rmethod, since=_rfixed) if _rfixed else 0
+                _rverdict = {
+                    "verified": _rafter == 0,
+                    "baseline": 0,
+                    "after_fix": _rafter,
+                    "post_apply_errors": _rafter,
+                    "effectiveness": 1.0 if _rafter == 0 else 0.0,
+                    "new_issues": _rafter,
+                    "detail": (f"[延迟复验] 重采基线: 修复后错误={_rafter}, "
+                               f'{"通过" if _rafter == 0 else "未通过"}'),
+                    "undecidable": False,
+                }
+                _patch["runtime_verify_result"] = _rverdict
+                _patch["runtime_verified"] = True
+                _patch.pop("reverify_after", None)
+                _patch["reverify_count"] = int(_patch.get("reverify_count", 0)) + 1
+                self._m84_recompute_split(_patch)
+                self._learn_from_verification(_patch, _rverdict)
+                if _rafter == 0:
+                    _reverified += 1
+                    _patch["status"] = "runtime_verified"
+                else:
+                    _reverify_failed += 1
+                    _patch["status"] = "runtime_failed"
+                    _patch["needs_repair"] = True
+                    if _patch.get("applied"):
+                        try:
+                            _rb = self._patch_manager.rollback_patch(
+                                _patch.get("id", ""))
+                            if _rb.get("ok"):
+                                _patch["rolled_back"] = True
+                                _patch["rolled_back_at"] = time.time()
+                                _module_logger.warning(
+                                    f"[延迟复验] 失败补丁已自动回滚: "
+                                    f"{os.path.basename(_rfile)}.{_rmethod} "
+                                    f"({_rverdict.get('detail','')})")
+                            else:
+                                _module_logger.warning(
+                                    f"[延迟复验] 失败补丁回滚失败: "
+                                    f"{os.path.basename(_rfile)}.{_rmethod} "
+                                    f"reason={_rb.get('reason','')}")
+                        except Exception as _rb_e:
+                            _module_logger.error(
+                                f"[延迟复验] 失败补丁回滚异常: {_rb_e}")
+                _module_logger.info(
+                    f"[延迟复验] 到点重采基线: {os.path.basename(_rfile)}.{_rmethod} "
+                    f'-> {"runtime_verified" if _rafter == 0 else "runtime_failed"}')
 
             for _patch in _pending:
                 if not _patch.get("needs_runtime_verify"):
@@ -2422,6 +2494,8 @@
                     #   PulseKidney 补丁 baseline>0 效果 100% 却无该字段）。
                     self._m84_recompute_split(_patch)
                     continue  # 已验证过，跳过
+                if _patch.get("status") == "needs_reverify":
+                    continue  # ★第114批 T-114b①：由延迟复验前置处理，避免重复判定
                 _total += 1
 
                 _result = self.verify_fix_from_logs(_patch)
@@ -2440,6 +2514,20 @@
                 #   重算语义拆分，把 problem_fixed 从 None 落到 True/False。
                 self._m84_recompute_split(_patch)
 
+                if _result.get("undecidable"):
+                    # ★第114批 T-114b①：baseline=0 不可判定 -> 延迟复验，
+                    #   不折叠进 runtime_failed（避免误回滚/误标 needs_repair）。
+                    _needs_reverify += 1
+                    _patch["status"] = "needs_reverify"
+                    _patch["reverify_after"] = time.time() + _reverify_delay
+                    _patch["reverify_count"] = int(_patch.get("reverify_count", 0)) + 1
+                    _ufile = _patch.get("file", "")
+                    _umethod = _patch.get("method", "")
+                    _module_logger.info(
+                        f"[延迟复验] baseline=0 无法判定，延迟到 "
+                        f"{_patch['reverify_after']:.0f} 重采基线: "
+                        f"{os.path.basename(_ufile)}.{_umethod}")
+                    continue
                 if _result["verified"]:
                     _verified += 1
                     _patch["status"] = "runtime_verified"
@@ -2503,7 +2591,9 @@
 
             _module_logger.info(
                 f"[运行时验证] 批量验证完成: 总数={_total}, "
-                f"通过={_verified}, 失败={_failed}, 平均效果={_avg:.0%}")
+                f"通过={_verified}, 失败={_failed}, 平均效果={_avg:.0%}, "
+                f"延迟复验新入队={_needs_reverify}, 到点复核通过={_reverified}, "
+                f"到点复核失败={_reverify_failed}")
 
             # 记录到验证学习枢纽
             try:
@@ -2530,6 +2620,9 @@
                 "verified": _verified,
                 "failed": _failed,
                 "skipped": _skipped,
+                "needs_reverify": _needs_reverify,
+                "reverified": _reverified,
+                "reverify_failed": _reverify_failed,
                 "avg_effectiveness": round(_avg, 2),
             }
         except Exception as _e:
```


## organs/brain/PulseSubconscious.py
> T-114d 潜意识 boot 异步化补漏

```diff
--- a/before_b114b/organs/brain/PulseSubconscious.py
+++ b/organs/brain/PulseSubconscious.py
@@ -41,6 +41,7 @@
 from nucleus.knowledge_noise_filter import is_noise_keyword
 from utils.time_utils import get_current_datetime, get_weather
 from nucleus.const import Event
+from nucleus.runtime_tempo import get_runtime_tempo
 
 
 class PulseSubconscious(BasePulseOrgan):
@@ -467,12 +468,14 @@
             self._user_present = False
         threading.Thread(target=self._detect_user_presence_async, daemon=True).start()
 
-        # 无论摄像头是否可用，都初始化梦境定时器（轻量，同步）
+        # 无论摄像头是否可用，都初始化梦境定时器
+        # ★T-114d：_schedule_dream_timer 整体移后台守护线程，避免运行时 import 卡模块锁阻塞 L0 生命线层
         self._dream_next_time = time.time() + self._dream_interval
-        self._schedule_dream_timer()
-        self._log(LogLevel.INFO, f"梦境推演已初始化 (间隔={self._dream_interval}s, 首次触发={self._dream_interval}s后)")
-
-        self._schedule_next_exploration()
+        threading.Thread(target=self._schedule_dream_timer, daemon=True).start()
+        self._log(LogLevel.INFO, f"梦境推演已初始化（后台调度，间隔={self._dream_interval}s）")
+
+        # ★T-114d：探索定时器同样移后台，boot 立即返回不阻塞 L0 worker
+        threading.Thread(target=self._schedule_next_exploration, daemon=True).start()
         return {"status": "booted", "explore_interval": self._current_interval}
 
     def _detect_user_presence_async(self) -> None:
@@ -3200,7 +3203,6 @@
         #   不修改 _current_interval（内部动态调整逻辑保持不变），仅在调度时乘以 tempo
         _actual_interval = self._current_interval
         try:
-            from nucleus.runtime_tempo import get_runtime_tempo
             _tempo = get_runtime_tempo().get_background_tempo()
             _actual_interval = max(self._min_interval, self._current_interval * _tempo)
         except Exception as e:
@@ -3218,10 +3220,9 @@
         if self._dream_timer:
             self._dream_timer.cancel()
         if not self._user_present:
-            # ★14.49：runtime_tempo 调节梦境间隔
+            # ★14.49：runtime_tempo 调节梦境间隔（import 已提文件顶部，T-114d）
             _dream_actual = self._dream_interval
             try:
-                from nucleus.runtime_tempo import get_runtime_tempo
                 _tempo = get_runtime_tempo().get_background_tempo()
                 _dream_actual = max(60.0, self._dream_interval * _tempo)
             except Exception as e:
```


## nucleus/reasoning/PatchManager.py
> T-114b③a 启动即锁死 WARNING

```diff
--- a/nucleus/reasoning/PatchManager.py
+++ b/nucleus/reasoning/PatchManager.py
@@ -1441,6 +1441,7 @@
         if not _path_ok:
             result["errors"].append(f"补丁路径校验失败: {_path_reason}")
             result["stage"] = "path_check_failed"
+            result["failed_check"] = "path"
             # ★第87批 T-87b：原告警只有原因，无法从日志判断是「哪一类补丁」出的问题
             #   —— 实测 09-18~09-20 共 19 条**完全相同**的文案，定位一次要翻整条
             #   补丁链路。补上来源/类型/方法/file 实值/键名清单，一眼可辨
@@ -1478,6 +1479,7 @@
         if not _completeness["complete"]:
             result["errors"].append(f"补丁完整性检查失败: {_completeness['reason']}")
             result["stage"] = "completeness_check_failed"
+            result["failed_check"] = "completeness"
             return result
 
         tmp_dir = tempfile.mkdtemp(prefix="tongtong_patch_test_")
@@ -1495,6 +1497,7 @@
             if patch["original_code"] not in full_content:
                 result["errors"].append("目标代码片段在源文件中未找到，无法应用补丁")
                 result["stage"] = "code_not_found"
+                result["failed_check"] = "in_copy_match"
                 return result
 
             # ★第92批 T-92c（防御性，默认关闭）：验证侧「基础缩进相等」结构化关。
@@ -1510,6 +1513,7 @@
                         f"基础缩进不一致（未替换）: original={_bo92} modified={_bm92} "
                         f"（原位替换会把类体/函数体提前终止，且语法仍合法）")
                     result["stage"] = "base_indent_guard_failed"
+                    result["failed_check"] = "indent"
                     _module_logger.warning(
                         f"[补丁验证] 基础缩进结构化关拒绝: original={_bo92} "
                         f"modified={_bm92}（source={patch.get('source', '')!r}, "
@@ -1530,6 +1534,7 @@
                 if _st92 and not _st92["ok"]:
                     result["errors"].append(_st92["reason"])
                     result["stage"] = "ast_structure_guard_failed"
+                    result["failed_check"] = "ast_structure"
                     _module_logger.warning(
                         f"[补丁验证] 结构不变量关拒绝: 类方法 {_st92['before']} → "
                         f"{_st92['after']}（-{_st92['ratio']:.1%}）（"
@@ -1557,6 +1562,7 @@
                 #   `TabError` / `IndentationError` / 其他。
                 result["errors"].append(f"语法错误({type(e).__name__}): {e}")
                 result["stage"] = "syntax_check_failed"
+                result["failed_check"] = "syntax"
                 return result
             
             # 4. 编译验证（py_compile，不执行模块级代码，避免框架文件模块级副作用导致误判）
@@ -1571,10 +1577,12 @@
             except py_compile.PyCompileError as _ce:
                 result["errors"].append(f"编译失败: {_ce}")
                 result["stage"] = "compile_check_failed"
+                result["failed_check"] = "syntax"
                 return result
             except Exception as _e:
                 result["errors"].append(f"编译验证异常: {_e}")
                 result["stage"] = "compile_check_error"
+                result["failed_check"] = "syntax"
                 return result
 
             # 5. ★T5(PHASE9) import 检查——补丁修改后的副本能否正常 import（沙箱联动）。
@@ -1590,6 +1598,7 @@
             if not _import_check.get("ok"):
                 result["passed"] = False
                 result["stage"] = "import_check_failed"
+                result["failed_check"] = "import"
                 result["errors"].append(
                     f"import检查失败: {_import_check.get('reason', '未知')}")
                 _module_logger.warning(
@@ -1607,6 +1616,7 @@
                     if not _recheck.get("complete"):
                         result["passed"] = False
                         result["stage"] = "completeness_check_failed"
+                        result["failed_check"] = "completeness"
                         result["errors"].append(
                             f"深验证-二次完整性复核失败: {_recheck.get('reason', '')}")
                         return result
@@ -1628,6 +1638,7 @@
                 if _behavior.get("checked") and not _behavior.get("equivalent"):
                     result["passed"] = False
                     result["stage"] = "behavior_mismatch"
+                    result["failed_check"] = "behavior"
                     result["errors"].append(
                         f"行为等价验证失败: {_behavior.get('reason', '')}")
                     return result
@@ -1643,6 +1654,7 @@
                 if _sig.get("checked") and not _sig.get("consistent"):
                     result["passed"] = False
                     result["stage"] = "signature_mismatch"
+                    result["failed_check"] = "signature"
                     result["errors"].append(
                         f"方法签名一致性验证失败: {_sig.get('reason', '')}")
                     return result
@@ -3547,6 +3559,42 @@
         """人工确认后重置重启计数"""
         self._save_restart_counter(0)
 
+    # ============ ★第114批 T-114b③a：启动即锁死 WARNING ============
+    def _m114b_emit_ratchet_lock_warning(self) -> None:
+        """★第114批 T-114b③a：启动即重报棘轮锁死状态，治"重启后失忆"。
+
+        棘轮锁死 WARNING 此前只在 ``apply_all_pending`` 运行时落日志；若框架
+        重启后尚未触发 apply，该锁死状态不会在任何日志出现 → 运维"失忆"。
+        本方法在启动健康诊断中主动读取棘轮持久化状态并重报 WARNING（锁死中）/
+        INFO（冷却已期满待重试），确保重启后立即可见。只读、零副作用。
+        """
+        try:
+            _counter = self._load_restart_counter()
+            if _counter <= self._max_restart_count:
+                return  # 未达上限，无锁死风险
+            _cooldown_h = self._m113e_restart_cooldown_hours()
+            _cooldown_sec = _cooldown_h * 3600.0
+            _blocked_at = self._load_restart_blocked_at()
+            _now = time.time()
+            _elapsed = (_now - _blocked_at) if _blocked_at > 0 else float("inf")
+            if _blocked_at > 0 and _elapsed < _cooldown_sec:
+                _remain = int(_cooldown_sec - _elapsed)
+                _module_logger.warning(
+                    f"[棘轮锁死·启动即] 重启计数={_counter}（上限"
+                    f"{self._max_restart_count}），仍处于冷却窗口，自动应用已锁死"
+                    f"（剩余约{_remain // 3600}h{(_remain % 3600) // 60}m，"
+                    f"请人工检查补丁队列）")
+                self._m113e_emit_restart_telemetry(_cooldown_h, _remain)
+            else:
+                _module_logger.info(
+                    f"[棘轮锁死·启动即] 重启计数={_counter}（上限"
+                    f"{self._max_restart_count}），冷却窗口已期满，下轮"
+                    f"apply_all_pending 将放行重试一次")
+        except Exception as _e:
+            _module_logger.debug(
+                f"[棘轮锁死·启动即] 状态读取失败（已忽略）: "
+                f"{type(_e).__name__}: {_e}")
+
     # ============ ★T-113e① 棘轮冷却 + 遥测辅助 ============
     def _m113e_restart_cooldown_hours(self) -> float:
         """棘轮冷却窗口（小时）。默认 24h，可由 config / 环境变量覆盖（只读取，不改写开关）。"""
```


## config.py
> T-114a 冷却分类新增「验证失败·3轮」/「验证失败」档

```diff
--- a/config.py
+++ b/config.py
@@ -1072,6 +1072,15 @@
         # 本地无规则、只能转 LLM：随 patch 规则库扩充有可能变成可修，
         # 故冷却较短（6 小时）后重试。
         "本地无规则·转LLM": 21600.0,
+        # ★第114批 T-114a（治病·断2修复）：「可修类型验证失败」冷却类。
+        #   此前 _cooldown_classify 只认「高危·安全拦截」「本地无规则·转LLM」两类，
+        #   本地/LLM 修复验证失败的题不入冷却 → 每轮全量重扫重问（烛微活体证据
+        #   PulseKidney._m69_kal_query (silent_exception) 五轮同位重现）。现补档：
+        #   同因验证失败累计满 3 轮升 86400s（日级复检），否则按基础档冷却。
+        #   ★顺序关键：必须放在 "验证失败" 之前 —— _cooldown_ttl_for 按前缀匹配，
+        #   先命中更具体的 "验证失败·3轮" 才返回 86400，否则会误用基础档 3600。
+        "验证失败·3轮": 86400.0,
+        "验证失败": 3600.0,
         # 其余未修复原因的兜底冷却时长。
         "_default": 3600.0,
     },
```


## main.py
> T-114b③a 启动健康检查第5项调用棘轮锁死 WARNING（无 before 快照，详见变更说明）

> 无 before 快照（全新文件或 prior-turn 改动未留快照），当前 3938 行，已通过 ruff F + py_compile。完整内容见仓库当前文件。


## nucleus/self_awareness/SelfAwarenessEngine.py
> T-114b③b evolution_health 快照新增 patch_state_machine 字段（无 before 快照，详见变更说明）

> 无 before 快照（全新文件或 prior-turn 改动未留快照），当前 2031 行，已通过 ruff F + py_compile。完整内容见仓库当前文件。


## tools/check_patch_consistency.py
> T-114b② 新增一致性巡检脚本（全新文件）

> 无 before 快照（全新文件或 prior-turn 改动未留快照），当前 340 行，已通过 ruff F + py_compile。完整内容见仓库当前文件。


## tools/adjudicate_patch.py
> T-114b③c + T-114c 新增裁决 CLI（全新文件）

> 无 before 快照（全新文件或 prior-turn 改动未留快照），当前 238 行，已通过 ruff F + py_compile。完整内容见仓库当前文件。
