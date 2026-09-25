diff --git a/nucleus/mnemosyne/PulseNodePool.py b/nucleus/mnemosyne/PulseNodePool.py
index 20d360e..5248a1d 100644
--- a/nucleus/mnemosyne/PulseNodePool.py
+++ b/nucleus/mnemosyne/PulseNodePool.py
@@ -24,6 +24,8 @@ from nucleus.logger import get_module_logger
 from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底
 from nucleus.mnemosyne.PulseNode import PulseNode
 
+from nucleus.data.DataAccessLayer import safe_write_json  # ★T-125a：原子写复用（原子写）
+
 
 _module_logger = get_module_logger("PulseNodePool")
 
@@ -1735,6 +1737,7 @@ class PulseNodePool(SilentLogMixin):
                                     "value": str(getattr(_node, "value", ""))[:50],
                                     "action": "downgrade_l3",
                                 })
+                                self._l3_fuse_record()  # ★T-125a：接通保险丝记录（N=1 跨重启持久化）
                         except Exception:
                             pass
                     continue
@@ -1806,7 +1809,8 @@ class PulseNodePool(SilentLogMixin):
             "dormant_candidates": _dormant_cands,
             "cleanup_candidates": _cleanup_cands,
             "applied_reinforcements": _applied,
-            "l3_downgraded": 0,            "l3_downgrade_candidates": len(_downgrade_cands),
+            "l3_downgraded": 0,
+            "l3_downgrade_candidates": len(_downgrade_cands),
             "verified_at": _now,
         }
 
@@ -1818,7 +1822,7 @@ class PulseNodePool(SilentLogMixin):
         import os as _os
         import json as _json
         _here = _os.path.dirname(_os.path.abspath(__file__))
-        _root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_here)))
+        _root = _os.path.dirname(_os.path.dirname(_here))  # ★T-125a：修正越界（原三级dirname落到项目根父目录）
         _path = _os.path.join(_root, "data", "l3_downgrade_fuse.json")
         try:
             with open(_path, encoding="utf-8") as _f:
@@ -1836,12 +1840,11 @@ class PulseNodePool(SilentLogMixin):
         return int(_st.get("count", 0)) < self._L3_FUSE_DAILY_MAX
 
     def _l3_fuse_record(self) -> None:
-        """保险丝记一笔（C2 不调用；124 批实际执行降级时调用，跨重启持久化）。"""
+        """保险丝记一笔（125 批接通降级执行点，跨重启持久化，原子写复用 safe_write_json）。"""
         import os as _os
-        import json as _json
         import time as _t
         _here = _os.path.dirname(_os.path.abspath(__file__))
-        _root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_here)))
+        _root = _os.path.dirname(_os.path.dirname(_here))  # ★T-125a：修正越界（原三级dirname落到项目根父目录）
         _path = _os.path.join(_root, "data", "l3_downgrade_fuse.json")
         _today = _t.strftime("%Y-%m-%d")
         _st = self._l3_fuse_state()
@@ -1849,13 +1852,14 @@ class PulseNodePool(SilentLogMixin):
             _st = {"date": _today, "count": 0}
         _st["count"] = int(_st.get("count", 0)) + 1
         try:
-            _d = _os.path.dirname(_path)
-            if _d and not _os.path.isdir(_d):
-                _os.makedirs(_d, exist_ok=True)
-            with open(_path, "w", encoding="utf-8") as _f:
-                _json.dump(_st, _f)
-        except Exception:
-            pass
+            if safe_write_json is not None:
+                safe_write_json(_path, _st)
+            else:
+                _module_logger.warning(
+                    f"[L3保险丝] safe_write_json 不可用，跳过记录: {_path}")
+        except Exception as _e:
+            _module_logger.warning(
+                f"[L3保险丝] 记录失败(已忽略): {type(_e).__name__}: {_e}")
 
     def start_memory_verification_loop(self, interval_hours: float = 6.0,
                                        stale_days: float = 45.0,
