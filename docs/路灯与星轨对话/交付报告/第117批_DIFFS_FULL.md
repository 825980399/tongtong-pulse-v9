# 第117批 改动 DIFF（全量）

- 比对基准：`.bak_batch117/`（首次改动前备份，扁平名 `rel.replace('/','__')`）
- 统计：**+227 / -13**（按行，unified_diff n=6）
- 行尾保全：全部文件行尾未翻转（SEE=CRLF，其余=LF）

---

### `utils/pulse_tracer.py`

> 行尾：备份=LF 当前=LF

```diff
--- a/utils/pulse_tracer.py
+++ b/utils/pulse_tracer.py
@@ -3,12 +3,13 @@
 版本: v10 PulseNet
 设计: 路灯、小林、星轨
 日期: 2026年9月9日
 修复: 2026-09-10 星轨 — 原子写+频率限制+default类型修复，解决JSON解析刷屏
 """
 
+import atexit
 import os
 import threading
 import time
 from nucleus.data.DataAccessLayer import safe_read_json, safe_write_json
 
 _MAX_EVENTS = 200
@@ -16,12 +17,22 @@
 _FLUSH_INTERVAL = 2.0  # 写入频率限制：每2秒最多一次，避免竞态条件
 _events = []
 _lock = threading.Lock()
 _output_path = None
 _orphan_path = None
 _last_flush_time = 0.0
+# ★第117批 T-117a（方案A）：占闸专用锁。刻意与 _events 的 _lock 分开，
+#   避免「拿事件快照」与「抢刷闸」互相排队（原实现的 convoy 正是锁串联造成）。
+_gate_lock = threading.Lock()
+# ★第117批 T-117a（方案B）：后台守护刷。
+#   红线：★禁止在 import 期起线程/做 IO —— 只能由 log_emit/log_receive 懒启动。
+_flusher_thread = None
+_flusher_started = False
+_flusher_lock = threading.Lock()
+_flusher_stop = threading.Event()
+_FLUSHER_TICK = 0.5  # 后台刷巡检间隔（秒）；真闸仍是 _FLUSH_INTERVAL=2.0
 
 def _get_output_path():
     global _output_path
     if _output_path is None:
         base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
         _output_path = os.path.join(base, "data", "monitor", "pulse_trace_events.json")
@@ -46,12 +57,13 @@
         "matched_organs": []
     }
     with _lock:
         _events.append(event)
         if len(_events) > _MAX_EVENTS:
             _events.pop(0)
+    _ensure_flusher()  # ★T-117a 方案B：运行期懒启动（import 期绝不起线程）
 
 def log_receive(organ_name, event_type, source_organ):
     """记录脉冲接收事件"""
     with _lock:
         for evt in reversed(_events):
             if evt.get("type") == "emit" and evt.get("event_type") == event_type:
@@ -64,12 +76,13 @@
             "organ": organ_name,
             "event_type": event_type,
             "from": source_organ,
         })
         if len(_events) > _MAX_EVENTS:
             _events.pop(0)
+    _ensure_flusher()  # ★T-117a 方案B：运行期懒启动（import 期绝不起线程）
 
 def _detect_orphans(events, min_age=1.0):
     """检测孤儿脉冲：发射超过min_age秒且无接收的emit事件"""
     orphans = []
     now = time.time()
     for evt in events:
@@ -85,25 +98,95 @@
                 "layer": evt.get("layer", ""),
                 "pulse_id": evt.get("pulse_id", ""),
                 "error": "孤儿脉冲: 发射后无器官接收"
             })
     return orphans
 
-def flush_to_file():
+def _claim_flush_gate() -> bool:
+    """★第117批 T-117a（方案A）：原子占闸 —— 判过的瞬间即占位。
+
+    原实现是**头判尾更**：本函数开头判「距上次 >= _FLUSH_INTERVAL？」、
+    却在函数**末尾**（完成两轮 safe_write_json 之后）才更新 _last_flush_time。
+    闸非原子 => 突发窗内 k 个并发者全部判「该我刷」，k 份近乎同内容的重复写
+    去排同一把 per-path 锁 = **写放大 k×**，且无一是必要 IO（烛微实测 19:35 块
+    参与者 11 个线程，等待者=器官接收线程，卡在业务之前 → 拖死器官主循环）。
+
+    改为「判 + 更」在同一把 _gate_lock 内一次完成：一个 2s 窗口内恒定只有 1 个
+    写者能穿越，写放大 11× → 1×。
+    """
+    global _last_flush_time
+    with _gate_lock:
+        _now = time.time()
+        if _now - _last_flush_time < _FLUSH_INTERVAL:
+            return False
+        _last_flush_time = _now  # 判完立刻占闸
+        return True
+
+
+def _flusher_loop():
+    """★第117批 T-117a（方案B）：后台守护刷循环。
+
+    器官主循环（BasePulseOrgan 的 emit/handle）不再承担同步 IO；
+    真正的写盘发生在这里，频率仍受 _claim_flush_gate 的 2s 闸约束。
+    """
+    while not _flusher_stop.is_set():
+        _flusher_stop.wait(_FLUSHER_TICK)
+        if _flusher_stop.is_set():
+            break
+        if not _events:
+            continue  # 无事件则不做无谓 IO
+        try:
+            flush_to_file()
+        except Exception as _fe:
+            print(f"[WARNING] pulse_tracer.flusher: "
+                  f"{type(_fe).__name__}: {_fe}")
+
+
+def _ensure_flusher():
+    """★第117批 T-117a（方案B）：首次记录事件时懒启动后台刷线程。
+
+    ★红线：本函数**只能**由 log_emit / log_receive 在运行期调用；
+    模块 import 期绝不起线程、绝不做 IO（潜意识 :466 案反复验证的教训）。
+    """
+    global _flusher_thread, _flusher_started
+    if _flusher_started:
+        return
+    with _flusher_lock:
+        if _flusher_started:
+            return
+        _flusher_started = True
+        _flusher_thread = threading.Thread(
+            target=_flusher_loop, name="PulseTracerFlusher", daemon=True)
+        _flusher_thread.start()
+
+
+def _flush_at_exit():
+    """进程退出前终刷（atexit）—— 绕过 2s 闸，但不为无事件做无谓 IO。"""
+    try:
+        if not _events:
+            return
+        _flusher_stop.set()
+        flush_to_file(force=True)
+    except Exception:
+        pass
+
+
+atexit.register(_flush_at_exit)
+
+
+def flush_to_file(force: bool = False):
     """将当前事件列表写入JSON文件，同时更新孤儿脉冲记录
     
     优化：
     1. 频率限制：每2秒最多写入一次，避免频繁IO和竞态条件
     2. 原子写：使用safe_write_json，避免写入中途被读取导致解析失败
     3. 类型修复：default=[]而不是default={}
     """
-    global _last_flush_time
-    
-    # 频率限制：避免每个脉冲都写文件
-    now = time.time()
-    if now - _last_flush_time < _FLUSH_INTERVAL:
+    # ★第117批 T-117a（方案A）：原「本处判、函数尾更」的头判尾更已改为原子占闸，
+    #   详见 _claim_flush_gate 的注释。force=True（atexit 终刷）绕过 2s 闸。
+    if not force and not _claim_flush_gate():
         return
     
     try:
         path = _get_output_path()
         orphan_path = _get_orphan_path()
         os.makedirs(os.path.dirname(path), exist_ok=True)
@@ -144,10 +227,10 @@
         # 限制总数
         if len(existing_orphans) > _MAX_ORPHANS:
             existing_orphans = existing_orphans[-_MAX_ORPHANS:]
         
         # 原子写孤儿记录
         safe_write_json(orphan_path, existing_orphans, backup=False)
-        
-        _last_flush_time = now
+        # ★第117批 T-117a：此处的 _last_flush_time 更新已删除（头判尾更的根因），
+        #   占位统一在 _claim_flush_gate 内「判完即占」完成。
     except Exception as e:
         print(f"[WARNING] pulse_tracer.py:152: {type(e).__name__}: {e}")
```

### `base/BasePulseOrgan.py`

> 行尾：备份=LF 当前=LF

```diff
--- a/base/BasePulseOrgan.py
+++ b/base/BasePulseOrgan.py
@@ -304,16 +304,18 @@
                 layer=layer,
             )
             # 脉冲追踪（先获取pulse_id再使用）
             try:
                 import config
                 if getattr(config, 'DEBUG_PULSE_TRACE', False):
-                    from utils.pulse_tracer import flush_to_file, log_emit
+                    # ★第117批 T-117a（方案B）：同步 flush_to_file() 已移除 ——
+                    #   它让器官发射线程直面 per-path 写锁（19:35 块实测 11 个参与者
+                    #   排队），写盘改由 tracer 内部 daemon 线程承担。
+                    from utils.pulse_tracer import log_emit
                     _trace_id = pulse.get("pulse_id", "") if isinstance(pulse, dict) else ""
                     log_emit(self.organ_name, event_type, layer, _trace_id)
-                    flush_to_file()
             except Exception:
                 self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
             
             # 发布脉冲并返回ID（正常路径，不依赖异常）
             self.info_field.publish(pulse)
             pulse_id = pulse.get("pulse_id", "") if isinstance(pulse, dict) else ""
@@ -469,17 +471,18 @@
                 self._log(LogLevel.INFO, "熔断冷却结束，进入恢复状态")
         
         # 脉冲追踪模式：记录接收事件
         try:
             import config
             if getattr(config, 'DEBUG_PULSE_TRACE', False):
-                from utils.pulse_tracer import log_receive, flush_to_file
+                # ★第117批 T-117a（方案B）：同步 flush_to_file() 已移除（同上），
+                #   器官接收线程不再卡在业务之前的写锁上。
+                from utils.pulse_tracer import log_receive
                 event_type = pulse.get("event_type", "?")
                 source = pulse.get("source_organ", "?")
                 log_receive(self.organ_name, event_type, source)
-                flush_to_file()
         except Exception:
             self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
         # === 执行业务逻辑（锁外，其他线程可同时进入） ===
         try:
             # ★v9.x TimeCore：time.tick 脉冲走专用钩子 on_time_tick，不进入 on_pulse 主逻辑
             if pulse.get("event_type") == SystemEvent.TIME_TICK:
```

### `nucleus/reasoning/SafeEvolutionExecutor.py`

> 行尾：备份=CRLF 当前=CRLF

```diff
--- a/nucleus/reasoning/SafeEvolutionExecutor.py
+++ b/nucleus/reasoning/SafeEvolutionExecutor.py
@@ -24,18 +24,49 @@
 from nucleus.reasoning.PatchManager import PatchManager
 
 # ★第九批 B-3：置信度证据化——由「硬编码常数」改为
 #   0.9 × 该类型历史成功率系数 × 证据强度系数（开关关闭时原值返回）
 from nucleus.reasoning.SelfCalibrator import evidence_confidence as _evidence_conf
 from nucleus.data.DataAccessLayer import safe_read_json
+# ★第117批 T-117d①：跨盘安全 relpath（path_utils 只依赖 os，无循环导入风险）
+from nucleus.data.path_utils import safe_relpath as _safe_relpath
 from nucleus.api_rate_limiter import get_llm_call_config, api_rate_limited
 import config  # ★主线第59批 T2：问题发现器路径过滤需读取 config 运行时配置
 from config import DEFAULT_BENEFIT_SCORE as _DEF_BENEFIT_SCORE  # ★第55批 T1
 
 
 _module_logger = get_module_logger("SafeEvolutionExecutor")
+
+# ★第117批 T-117d①（烛微 N4）：项目根（供问题身份键做路径归一）
+_PROJECT_ROOT = os.path.dirname(
+    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
+
+
+def _normalize_file_key(file_val: str) -> str:
+    """★第117批 T-117d①（N4）：把 file 字段归一成「形制唯一」的键。
+
+    背景（烛微 117 §2-N4 实证）：补丁账本里同一目标文件**三种形制并存** ——
+        ``organs\\brain\\PulseSubconscious.py``（反斜杠·相对）
+        ``organs/brain/PulseSubconscious.py``（正斜杠·相对）
+        ``D:\\...\\organs\\brain\\PulseSubconscious.py``（反斜杠·绝对）
+    于是同一个题在「去重 / 分组 / 冷却」三处被算成 2~3 个不同的键，
+    冷却计数被稀释、去重失效 —— 双指纹格式已于 T-116a③ 统一，
+    但**路径形制**这一层此前没归一。
+
+    归一四步：normpath（消 ``.``/``..``/重复分隔符）→ safe_relpath(项目根)
+    （绝对→相对，跨盘安全降级）→ normcase（消大小写）→ 分隔符统一 ``/``。
+    """
+    if not file_val:
+        return ""
+    try:
+        _p = os.path.normpath(str(file_val))
+        _p = _safe_relpath(_p, _PROJECT_ROOT)
+        _p = os.path.normcase(_p)
+        return _p.replace(os.sep, "/").replace("\\", "/")
+    except Exception:
+        return str(file_val)
 
 # ★第86批 T-86a（P0）：LLM 修复补丁零产出根因修复 —— 推理模型 token 预算。
 #   根因（已实测复现）：REMOTE_API_CONFIG 指向的 deepseek-v4-flash 属**推理模型**，
 #   响应先产出 reasoning_content 再产出 content。原 max_tokens=1500 被推理解析
 #   全部吃光 → finish_reason=length、content 为空串、completion_tokens 打满 1500，
 #   再由 `if not answer: return None` 把整条 LLM 补丁通道变成恒零产出且无任何日志。
@@ -1708,12 +1739,17 @@
                         _verify = self._patch_manager.verify_in_copy(_local_patch)
                         _local_patch["verification"] = _verify
                         _local_patch["source"] = "local_rule"
                         if _verify.get("passed"):
                             _local_verified += 1
                             _local_verify_passed = True
+                            # ★第117批 T-117b：成功即出清同指纹连败计数（断5 棘轮自愈）。
+                            #   失败侧在下方 else 分支递增，成功侧此前零出清 ⇒ 单向棘轮。
+                            self._m114a_clear_ratchet(
+                                self._cooldown_key(_issue),
+                                reason="本地修复验证通过")
                             # ★主线C(C1)：本地规则修复成功，报告到 AdaptiveDecision
                             if _strategy_ad is not None:
                                 try:
                                     _strategy_ad.report("local_rule", success=True)
                                 except Exception as _exc:
                                     _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
@@ -2843,12 +2879,18 @@
                 )
             else:
                 _module_logger.info(
                     f"[经验学习] 成功模式记录: type={_type}, strategy={_strategy}, "
                     f"effectiveness={_effectiveness:.0%}"
                 )
+                # ★第117批 T-117b：补丁验证通过 —— 出清同指纹连败计数。
+                #   与上方失败分支的 _m114a_register_verify_failure 严格对称：
+                #   此处是「本地/LLM 统一学习入口」的成功侧，一处覆盖全部补丁路径
+                #   （含延迟复验 :2461 与主验证循环 :2576 两处调用）。
+                self._m114a_clear_ratchet(
+                    self._cooldown_key(patch), reason="补丁验证通过")
 
             # 记录到验证学习枢纽
             try:
                 from nucleus.mnemosyne.verification_learning_hub import (
                     get_verification_learning_hub,
                 )
@@ -4673,13 +4715,16 @@
             但**并不能提高修复成功率**——这些日志类问题依然没有 method，
             依然取不到代码片段。修复率问题的根因另在别处（见 D6）。
         """
         _f = str(issue.get("file", "") or "")
         _m = str(issue.get("method", "") or "")
         if _f or _m:
-            return (_f, _m, str(issue.get("type", "") or ""))
+            # ★第117批 T-117d①：file 先做形制归一（详见 _normalize_file_key），
+            #   使「反斜杠相对 / 正斜杠相对 / 绝对」三种形制收敛为同一键。
+            return (_normalize_file_key(_f), _m,
+                    str(issue.get("type", "") or ""))
         # 无代码位置：退化为 器官 + 类型 + 描述摘要
         _o = str(issue.get("organ", "") or "")
         _t = str(issue.get("type", "") or "")
         _d = str(issue.get("description", "") or issue.get("message", "") or "")
         return (f"organ:{_o}", f"type:{_t}", _d[:120])
 
@@ -4787,12 +4832,44 @@
             _module_logger.info(
                 f"[验证失败冷却] 指纹={fp[:80]} 验证未通过({detail})，"
                 f"同因累计{_rnd}轮→冷却{int(_ttl)}s"
                 f'{"（升86400s/日级复检）" if _rnd >= 3 else ""}')
         except Exception as _e:
             _module_logger.debug(f"[验证失败冷却] 登记异常(已忽略): {type(_e).__name__}: {_e}")
+
+    def _m114a_clear_ratchet(self, fp: str, reason: str = "") -> None:
+        """★第117批 T-117b（N1）：成功侧出清 —— 同指纹连败计数归零（棘轮自愈）。
+
+        背景（烛微 §2-N1 实测）：断5 换源后判据源 = `_no_fix_cooldown_rounds`，
+        计数只在 `_m114a_register_verify_failure` 里**递增**，成功侧**无任何出清点**。
+        于是某指纹累计 >=3 连败后被 `_m114a_should_skip_ask` 永久跳过；计数还会
+        随 `_m114a_save_cooldown` 落盘 ⇒ **跨重启永续**，棘轮单向、无自愈出口。
+        而该函数 docstring 自称判据是「无成功记录」——成功了也不重置，名实不符。
+
+        修法：修复/验证成功的分支调用本方法，把该指纹连败计数清零，
+        棘轮从此可逆（「3 连败 → 1 次成功 → 重新获得 LLM 问询资格」）。
+
+        ★刻意不动 `_no_fix_cooldown`（deadline 表）：那是「冷却隔离」的另一套
+        机制（:1447 到期自动解冻），与断5 棘轮无关；并入本批会扩大改动面。
+        """
+        if not isinstance(self, SafeEvolutionExecutor):
+            # ★T-115d：占位/Dummy 实例不应承担冷却落盘职责
+            return
+        try:
+            _had = int(self._no_fix_cooldown_rounds.get(fp, 0))
+            if _had <= 0:
+                return  # 无连败记录：零开销短路，不产生日志噪音
+            self._no_fix_cooldown_rounds.pop(fp, None)
+            self._m114a_save_cooldown()
+            _module_logger.info(
+                f"[棘轮重置] 指纹={fp[:80]} 连败清零（{_had}→0）"
+                f"{('·' + reason) if reason else ''}"
+                f"——该指纹恢复本轮 LLM 问询资格")
+        except Exception as _e:
+            _module_logger.debug(
+                f"[棘轮重置] 出清异常(已忽略): {type(_e).__name__}: {_e}")
 
     def _m114a_should_skip_ask(self, issue: dict[str, Any]) -> bool:
         """★断5（T-115d 指纹级降档）：同指纹问题若已累计 ≥3 轮验证失败且无成功记录，
         跳过本轮 LLM 问询，阻断确定性回环烧 LLM。
 
         判据源改用断2/断6 维护的 _no_fix_cooldown_rounds（真实指纹计数，非空集 hub），
```

### `nucleus/logger.py`

> 行尾：备份=LF 当前=LF

```diff
--- a/nucleus/logger.py
+++ b/nucleus/logger.py
@@ -640,12 +640,54 @@
     用于 PulseSnapshot、InfoField 等核心模块。
     """
     _init_root_logger()
     return logging.getLogger(f"pulse.module.{module_name}")
 
 
+# ========== ★第117批 T-117d② / R4-B22：冒烟隔离规矩 ==========
+SMOKE_TAG = "[SMOKE]"
+SMOKE_LOG_FILE = "smoke.log"
+
+
+def get_smoke_logger(name: str = "smoke") -> logging.Logger:
+    """★第117批 T-117d②（R4-B22）：冒烟 / 合成指纹用例专用日志器。
+
+    背景（烛微 117 §3 实测）：停机窗 pulse.log 出现一行
+        ``[指纹咨询硬闸] 指纹=a.py|m|silent_exception ...``
+    ——那是**合成指纹**（file="a.py"、method="m"）驱动的冒烟产物，却被生产判据
+    当成真实命中（对「INFO>=1」类判据构成**假阳性风险**，本次差点误导结论）。
+
+    规矩：凡用合成指纹 / 假数据驱动的冒烟与单测，一律走本日志器，不得写进 pulse.log。
+
+    三保险：
+      ① 独立文件 ``logs/smoke.log``（与 pulse.log 物理隔离）；
+      ② 每条前缀 ``[SMOKE]``（即便被复制粘贴到别处也一眼可辨）；
+      ③ ``propagate = False``（绝不冒泡到 root 'pulse'，双重不污染）。
+
+    用法（冒烟脚本 / 单测）：
+        ``mod._module_logger = get_smoke_logger("my_smoke_case")``
+    """
+    _lg = logging.getLogger(f"pulse.smoke.{name}")
+    _lg.setLevel(logging.DEBUG)
+    _lg.propagate = False
+    if not any(getattr(_h, "_pulse_smoke", False) for _h in _lg.handlers):
+        try:
+            os.makedirs(_log_dir, exist_ok=True)
+            _h = logging.FileHandler(
+                os.path.join(_log_dir, SMOKE_LOG_FILE), encoding="utf-8")
+            _h.setLevel(logging.DEBUG)
+            _h.setFormatter(logging.Formatter(
+                "%(asctime)s " + SMOKE_TAG + " [%(name)s] %(levelname)s: %(message)s",
+                datefmt="%Y-%m-%d %H:%M:%S"))
+            _h._pulse_smoke = True
+            _lg.addHandler(_h)
+        except Exception as _se:
+            print(f"[logger] smoke 日志句柄初始化失败(降级为纯内存): {type(_se).__name__}: {_se}", file=sys.stderr)
+    return _lg
+
+
 # ========== ★主线第32批 T3（P2-190）：异常/调用位置动态获取 ==========
 _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
 
 
 def exc_location(depth: int = 1) -> str:
     """返回「相对项目根的路径:行号」，用于异常日志（替代硬编码行号）。
```

### `tests/test_t115d_dummy_skip_ask.py`

> 行尾：备份=LF 当前=LF

```diff
--- a/tests/test_t115d_dummy_skip_ask.py
+++ b/tests/test_t115d_dummy_skip_ask.py
@@ -12,12 +12,21 @@
     _src = os.path.join(
         os.path.dirname(__file__), "..", "nucleus", "reasoning", "SafeEvolutionExecutor.py")
     _src = os.path.abspath(_src)
     _spec = importlib.util.spec_from_file_location("SEE_t115d_test", _src)
     _mod = importlib.util.module_from_spec(_spec)
     _spec.loader.exec_module(_mod)
+    # ★第117批 T-117d②（R4-B22 冒烟隔离规矩）：本用例用**合成指纹**
+    #   （file="a.py"、method="m"）驱动断5 硬闸，其 `[指纹咨询硬闸]` INFO 属冒烟产物。
+    #   实测该行曾落进 pulse.log（2026-09-23 21:05:26），对「INFO>=1」类生产判据构成
+    #   假阳性风险。改为走 smoke 日志器（独立 smoke.log + [SMOKE] 前缀 + 不冒泡）。
+    try:
+        from nucleus.logger import get_smoke_logger
+        _mod._module_logger = get_smoke_logger("t115d_dummy_skip_ask")
+    except Exception:
+        pass  # 拿不到 smoke 日志器就保持原样（不因日志设施失败而让用例失败）
     return _mod.SafeEvolutionExecutor
 
 
 def test_dummy_placeholder_no_attributeerror():
     """占位/Dummy 实例调用冷却落盘方法不应再抛 AttributeError。"""
     SEE = _load_see()
```
