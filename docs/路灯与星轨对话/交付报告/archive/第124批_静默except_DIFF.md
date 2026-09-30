commit 021d984d93fa404532477ce569e79062432da7fb
Author: Administrator <825980399@qq.com>
Date:   Fri Sep 25 13:56:29 2026 +0800

    第124批 R5落码+静默except首批7处+git纪律 本批交付（T-124a/b/c）

diff --git a/main.py b/main.py
index 842c998..f4eb72a 100644
--- a/main.py
+++ b/main.py
@@ -1189,7 +1189,8 @@ class PulseFramework:
             if name in _factories:
                 try:
                     return _factories[name]()
-                except Exception:
+                except Exception as e:
+                    silent_exc(e, "main.py:1192 _resolve_component", level="warning")
                     return None
             return None
 
@@ -1311,8 +1312,8 @@ class PulseFramework:
                     get_module_logger("main").warning(
                         "[进化渠道] 注入肺实例失败(降级本地账本): %s: %s",
                         type(_e97a).__name__, _e97a)
-                except Exception:
-                    pass
+                except Exception as e:
+                    silent_exc(e, "main.py:1314 注入肺实例降级", level="warning")
         self.liver = self._create_organ(PulseLiver, "肝",
                                         node_pool=self.node_pool,
                                         knowledge_tree=self.knowledge_tree,
@@ -2245,7 +2246,7 @@ class PulseFramework:
                                 .get("discover_max_issues", 60))
                         except Exception as e:
                             _discover_max = 60
-                            logging.warning(f"进化发现上限回退失败(沿用60): {type(e).__name__}: {e}")
+                            logging.getLogger("pulse").warning(f"进化发现上限回退失败(沿用60): {type(e).__name__}: {e}")
                         _raw = self.evolution_loop.discover_all_issues(
                             log_file="logs/pulse.log",
                             max_issues=_discover_max,
@@ -2302,7 +2303,7 @@ class PulseFramework:
                                     _inspector = get_self_inspector()
                                 except Exception as e:
                                     _inspector = None
-                                    logging.warning(f"代码审查器初始化失败(跳过): {type(e).__name__}: {e}")
+                                    logging.getLogger("pulse").warning(f"代码审查器初始化失败(跳过): {type(e).__name__}: {e}")
                                 _executor = get_safe_evolution_executor()
                                 _result = _executor.repair_with_distillation(
                                     _issues, self_inspector=_inspector)
@@ -3502,7 +3503,7 @@ def main():
             f"曈曈已成功启动\n{_online_organs}/{_total_organs}个器官在线（实时扫描）\n知识节点: {framework.node_pool.count()}个"
         )
     except Exception as e:
-        logging.warning(f"[企业微信] 桥接器启动失败（不影响框架运行）: {e}")
+        logging.getLogger("pulse").warning(f"[企业微信] 桥接器启动失败（不影响框架运行）: {e}")
         framework.wecom_bridge = None
 
     # ★v23.0新增：自我验证
@@ -3615,7 +3616,7 @@ def main():
         health_ui.set_node_pool(framework.node_pool)
         health_ui.start()
     except Exception as e:
-        logging.warning(f"[框架] 人体UI启动失败 (端口5051): {e}")
+        logging.getLogger("pulse").warning(f"[框架] 人体UI启动失败 (端口5051): {e}")
     
     # 启动Web对话窗口（独立Web服务）
     web_chat = None
@@ -3624,7 +3625,7 @@ def main():
         web_chat = WebChatServer(port=5052)
         web_chat.start(info_field=framework.info_field, pulse_core=framework.pulse_core)
     except Exception as e:
-        logging.warning(f"[框架] Web对话窗口启动失败 (端口5052): {e}")
+        logging.getLogger("pulse").warning(f"[框架] Web对话窗口启动失败 (端口5052): {e}")
     
     # 启动功能模块加载器
     framework.function_loader = FunctionLoader(framework)
@@ -3653,7 +3654,7 @@ def main():
         if _hot_reload_organs:
             print(f"[Config] 热重载回调已注册（{len(_hot_reload_organs)}个器官: {', '.join(_hot_reload_organs)}）")
     except Exception as _hre:
-        logging.warning(f"[Config] 热重载回调注册失败: {_hre}")
+        logging.getLogger("pulse").warning(f"[Config] 热重载回调注册失败: {_hre}")
 
     # ========== 假死探测器（P1） ==========
     # 框架启动后若长时间无任何脉冲被实际处理（疑似卡死/死锁），自动 dump 所有线程
@@ -3664,7 +3665,7 @@ def main():
             _crash_fh = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "pulse_crash.log"), "a", encoding="utf-8", errors="replace")  # noqa: SIM115 - 有意持有句柄供 faulthandler 常驻
         except Exception as e:
             _crash_fh = None
-            logging.warning(f"崩溃日志句柄初始化失败(留空): {type(e).__name__}: {e}")
+            logging.getLogger("pulse").warning(f"崩溃日志句柄初始化失败(留空): {type(e).__name__}: {e}")
         # 关键：把原生崩溃（如 PortAudio 的 access violation）堆栈也重定向到日志文件。
         # 否则 faulthandler 只打印到控制台，不会写入 pulse_crash.log（这正是上次日志为空的原因）。
         try:
@@ -3680,7 +3681,7 @@ def main():
     except Exception as e:
         _fh = None
         _crash_fh = None
-        logging.warning(f"日志句柄初始化失败(禁用落盘): {type(e).__name__}: {e}")
+        logging.getLogger("pulse").warning(f"日志句柄初始化失败(禁用落盘): {type(e).__name__}: {e}")
 
     _lv_fw = framework  # 捕获闭包引用
 
@@ -3697,7 +3698,8 @@ def main():
                 _inf = getattr(_lv_fw, "info_field", None)
                 _getter = getattr(_inf, "get_total_handled", None)
                 _handled = _getter() if _getter else None
-            except Exception:
+            except Exception as e:
+                silent_exc(e, "main.py:3700 假死探测取handled", level="warning")
                 continue
             if _handled is None:
                 continue
@@ -3716,14 +3718,14 @@ def main():
                             if os.path.exists(_cp) and os.path.getsize(_cp) > 20 * 1024 * 1024:
                                 try:
                                     _fh.disable()
-                                except Exception:
-                                    pass
+                                except Exception as e:
+                                    silent_exc(e, "main.py:3719 崩溃日志轮转disable", level="warning")
                                 try:
                                     if os.path.exists(_cp + ".1"):
                                         os.remove(_cp + ".1")
                                     os.rename(_cp, _cp + ".1")
-                                except Exception:
-                                    pass
+                                except Exception as e:
+                                    silent_exc(e, "main.py:3725 崩溃日志轮转rename", level="warning")
                                 try:
                                     _crash_fh = open(_cp, "a", encoding="utf-8", errors="replace")
                                     _fh.enable(file=_crash_fh)
@@ -3772,7 +3774,7 @@ def main():
                         if _apply_pending_patches_and_restart(framework):
                             sys.exit(0)
                 except Exception as _apply_check_e:
-                    logging.warning(f"[进化] 应用请求检测异常(忽略): {_apply_check_e}")
+                    logging.getLogger("pulse").warning(f"[进化] 应用请求检测异常(忽略): {_apply_check_e}")
     except KeyboardInterrupt as _se:
         silent_exc(_se, "main.py:3646")
     except Exception as _main_loop_e:
diff --git a/nucleus/_silent_except.py b/nucleus/_silent_except.py
index c9d3433..fa22bbd 100644
--- a/nucleus/_silent_except.py
+++ b/nucleus/_silent_except.py
@@ -16,13 +16,15 @@ except Exception:  # pragma: no cover - 配置缺失时安全降级
     _FEATURE = {}
 
 
-def silent_exc(e: Exception, where: str = "") -> None:
+def silent_exc(e: Exception, where: str = "", level: str = "debug") -> None:
     """记录一处被静默捕获的异常（类型 + 信息 + 位置）。
 
-    where 形如 "main.py:29"，便于回溯。灰度关闭时直接返回（复现原 pass 行为）。
+    where 形如 "main.py:29"，便于回溯。level 控制日志级别（默认 debug，不刷屏）；
+    调用方可传 "warning" 提升可见度。灰度关闭时直接返回（复现原 pass 行为）。
     """
     if not _FEATURE.get("enable_silent_except_logging", True):
         return
-    logging.getLogger("pulse.silent_except").debug(
+    _lvl = level if level in ("debug", "info", "warning", "error", "critical") else "debug"
+    getattr(logging.getLogger("pulse.silent_except"), _lvl)(
         f"[静默异常可见化] {where} {type(e).__name__}: {e}"
     )
diff --git a/nucleus/logger.py b/nucleus/logger.py
index a6a10f9..3b1d4c5 100644
--- a/nucleus/logger.py
+++ b/nucleus/logger.py
@@ -22,6 +22,7 @@ import time
 
 import config
 from nucleus.const import LogLevel
+from nucleus._silent_except import silent_exc
 
 
 # 日志级别字符串 → logging 常量映射
@@ -503,8 +504,8 @@ def _warn_rollover_blocked_cooled(exc: BaseException) -> None:
         try:
             _cooldown = float(getattr(config, "LOG_ROLLOVER_WARN_COOLDOWN_SEC",
                                       _ROLLOVER_WARN_COOLDOWN_SEC))
-        except Exception:
-            pass
+        except Exception as e:
+            silent_exc(e, "logger.py:506 轮转冷却读", level="warning")
         _emit = False
         with _rollover_warn_lock:
             if _now - _last_rollover_warn_ts >= _cooldown:
@@ -643,6 +644,48 @@ def get_module_logger(module_name: str) -> logging.Logger:
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
 
diff --git a/organs/core/PulseMetricsCollector.py b/organs/core/PulseMetricsCollector.py
index f319119..5174bcd 100644
--- a/organs/core/PulseMetricsCollector.py
+++ b/organs/core/PulseMetricsCollector.py
@@ -35,6 +35,7 @@ from nucleus.const import (
 )
 from nucleus.data.DataAccessLayer import safe_write_json
 from nucleus.organ_identity import ORGAN_ALIASES  # ★T-112d：器官名归一化单源真相
+from nucleus._silent_except import silent_exc
 
 # 尝试读取配置，缺失时使用默认值
 try:
@@ -425,8 +426,8 @@ class PulseMetricsCollector(BasePulseOrgan):
                         controller_stats["files_read"] += 1
                     elif et.startswith("controller."):
                         controller_stats["operations"] += 1
-            except Exception:
-                pass
+            except Exception as e:
+                silent_exc(e, "PulseMetricsCollector.py:428 快照统计", level="warning")
         snapshot["controller"] = controller_stats
 
         # ===== 新增：无头浏览器统计 =====
diff --git a/tools/ci/cw2_t2e_ci_gate_silent_except.py b/tools/ci/cw2_t2e_ci_gate_silent_except.py
index 93daa80..4624be8 100644
--- a/tools/ci/cw2_t2e_ci_gate_silent_except.py
+++ b/tools/ci/cw2_t2e_ci_gate_silent_except.py
@@ -21,7 +21,7 @@ ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
 # 视为「已上报」的函数名（含框架自有 _log）
 LOG_FUNCS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal",
              "log", "aibot_log", "log_error", "log_warning", "log_info", "record", "report",
-             "notify", "alert", "emit", "_log", "_log_safe", "_log_msg", "_trace"}
+             "notify", "alert", "emit", "_log", "_log_safe", "_log_msg", "_trace", "silent_exc"}
 
 
 def _log(level, msg):
