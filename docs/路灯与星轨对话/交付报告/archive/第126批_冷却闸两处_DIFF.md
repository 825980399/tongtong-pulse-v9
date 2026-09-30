diff --git a/main.py b/main.py
index f4eb72a..7f7f267 100644
--- a/main.py
+++ b/main.py
@@ -1,4 +1,5 @@
 from nucleus._silent_except import silent_exc  # 主线第78批 T2：静默异常可见化
+from nucleus._warn_throttle import should_warn
 from config import EXTERNAL_CALL_TIMEOUTS  # noqa: F401
 """main —— v9.5 PulseNet 脉冲框架总入口（自进化基座版）
 
@@ -3699,7 +3700,8 @@ def main():
                 _getter = getattr(_inf, "get_total_handled", None)
                 _handled = _getter() if _getter else None
             except Exception as e:
-                silent_exc(e, "main.py:3700 假死探测取handled", level="warning")
+                if should_warn("main:false_death_probe", 300):
+                    silent_exc(e, "main.py:3700 假死探测取handled", level="warning")
                 continue
             if _handled is None:
                 continue
diff --git a/organs/core/PulseMetricsCollector.py b/organs/core/PulseMetricsCollector.py
index 5174bcd..8e2934f 100644
--- a/organs/core/PulseMetricsCollector.py
+++ b/organs/core/PulseMetricsCollector.py
@@ -36,6 +36,7 @@ from nucleus.const import (
 from nucleus.data.DataAccessLayer import safe_write_json
 from nucleus.organ_identity import ORGAN_ALIASES  # ★T-112d：器官名归一化单源真相
 from nucleus._silent_except import silent_exc
+from nucleus._warn_throttle import should_warn
 
 # 尝试读取配置，缺失时使用默认值
 try:
@@ -427,7 +428,8 @@ class PulseMetricsCollector(BasePulseOrgan):
                     elif et.startswith("controller."):
                         controller_stats["operations"] += 1
             except Exception as e:
-                silent_exc(e, "PulseMetricsCollector.py:428 快照统计", level="warning")
+                if should_warn("mc:snapshot_stats", 300):
+                    silent_exc(e, "PulseMetricsCollector.py:428 快照统计", level="warning")
         snapshot["controller"] = controller_stats
 
         # ===== 新增：无头浏览器统计 =====
