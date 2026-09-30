# 第128批 T-128c · tracer 守卫 DIFF

## 改动
`utils/pulse_tracer.py` 在 `new_orphans` 侧裸读 `o['timestamp']` 处补 `isinstance(o, dict)` 守卫，
防止孤儿记录非 dict 时抛 `KeyError`/`TypeError`；非 dict 直接 `continue`，**不影响现有行为**。

## 该文件 diff 片段
```diff
diff --git a/utils/pulse_tracer.py b/utils/pulse_tracer.py
index e51e5f1..a6fdece 100644
--- a/utils/pulse_tracer.py
+++ b/utils/pulse_tracer.py
@@ -11,6 +11,7 @@ import os
 import threading
 import time
 from nucleus.data.DataAccessLayer import safe_read_json, safe_write_json
+from nucleus._silent_except import silent_exc
 
 _MAX_EVENTS = 200
 _MAX_ORPHANS = 100
@@ -166,8 +167,8 @@ def _flush_at_exit():
             return
         _flusher_stop.set()
         flush_to_file(force=True)
-    except Exception:
-        pass
+    except Exception as e:
+        silent_exc(e, "utils/pulse_tracer.py:169:退出终刷异常", level="warning")
 
 
 atexit.register(_flush_at_exit)
@@ -219,6 +220,8 @@ def flush_to_file(force: bool = False):
                 existing_keys.add(key)
         
         for o in new_orphans:
+            if not isinstance(o, dict):
+                continue
             key = f"{o['timestamp']:.3f}_{o['organ']}_{o['event_type']}"
             if key not in existing_keys:
                 existing_orphans.append(o)
```

## 核验
- py_compile 通过
- 静默except CI：PASS（本批新增 1 处 `silent_exc` 在 :169，计入净减16，不属名单外新增）
