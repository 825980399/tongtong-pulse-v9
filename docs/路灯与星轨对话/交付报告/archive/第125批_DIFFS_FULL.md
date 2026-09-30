diff --git a/nucleus/hardware_probe.py b/nucleus/hardware_probe.py
index d28e88d..1c15c91 100644
--- a/nucleus/hardware_probe.py
+++ b/nucleus/hardware_probe.py
@@ -12,6 +12,7 @@ hardware_probe.py —— 硬件探测器
 """
 
 from __future__ import annotations
+from nucleus._silent_except import silent_exc
 from config import TIMEOUT_CONFIG
 
 import os
@@ -82,7 +83,8 @@ def _has_psutil() -> bool:
     try:
         import psutil  # noqa: F401
         return True
-    except ImportError:
+    except ImportError as e:
+        silent_exc(e, "nucleus/hardware_probe.py:85", level="warning")
         return False
 
 
@@ -91,7 +93,8 @@ def _detect_cores() -> int:
     try:
         import psutil
         return psutil.cpu_count(logical=True) or os.cpu_count() or 1
-    except Exception:
+    except Exception as e:
+        silent_exc(e, "nucleus/hardware_probe.py:94", level="warning")
         return os.cpu_count() or 1
 
 
@@ -101,7 +104,8 @@ def _detect_memory_gb() -> float:
         import psutil
         vm = psutil.virtual_memory()
         return round(vm.total / (1024 ** 3), 1)
-    except Exception:
+    except Exception as e:
+        silent_exc(e, "nucleus/hardware_probe.py:104", level="warning")
         return 0.0
 
 
@@ -198,8 +202,8 @@ def _detect_gpu_deep() -> dict[str, Any]:
             _mem_free = _info.get("memory_free_mb", 0)
             _info["shared_memory_mb"] = 0  # 独显不加载共享显存空想条（稳定优先）
             _info["usable_mb"] = _mem_free  # 实际可用于计算的显存
-    except Exception:
-        pass
+    except Exception as e:
+        silent_exc(e, "nucleus/hardware_probe.py:201", level="warning")
     return _info
 
 
diff --git a/nucleus/llm/ChannelQuotaMonitor.py b/nucleus/llm/ChannelQuotaMonitor.py
index 3287135..d3dc498 100644
--- a/nucleus/llm/ChannelQuotaMonitor.py
+++ b/nucleus/llm/ChannelQuotaMonitor.py
@@ -1,4 +1,5 @@
 # -*- coding: utf-8 -*-
+from nucleus._silent_except import silent_exc
 """ChannelQuotaMonitor.py —— 渠道免费额度监控与自动切换
 
 版本: v10 PulseNet
@@ -83,7 +84,8 @@ class ChannelQuotaMonitor:
         try:
             import config as _c
             return _c
-        except Exception:
+        except Exception as e:
+            silent_exc(e, "nucleus/llm/ChannelQuotaMonitor.py:86", level="warning")
             return None
 
     def enabled(self) -> bool:
@@ -98,7 +100,8 @@ class ChannelQuotaMonitor:
         _c = self._cfg()
         try:
             return float(getattr(_c, "QUOTA_DEGRADE_RATIO", 0.10))
-        except Exception:
+        except Exception as e:
+            silent_exc(e, "nucleus/llm/ChannelQuotaMonitor.py:101", level="warning")
             return 0.10
 
     def pause_ratio(self) -> float:
@@ -247,8 +250,8 @@ class ChannelQuotaMonitor:
                     self._daily_reset[channel_name] = _today
                     self._dirty = True
                     self._last_save = 0.0
-        except Exception:
-            pass
+        except Exception as e:
+            silent_exc(e, "nucleus/llm/ChannelQuotaMonitor.py:250", level="warning")
 
     def tick_daily_reset(self) -> int:
         """★第97批 T-97c：运维/调度主动触发——对所有 active 渠道执行每日重置检查。
@@ -266,8 +269,8 @@ class ChannelQuotaMonitor:
                     self._ensure_daily_reset(_name)
                     if self._daily_reset.get(_name) != _before:
                         _n += 1
-        except Exception:
-            pass
+        except Exception as e:
+            silent_exc(e, "nucleus/llm/ChannelQuotaMonitor.py:269", level="warning")
         return _n
 
     # ------------------------------------------------------------------
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
diff --git a/nucleus/mnemosyne/PulseSnapshot.py b/nucleus/mnemosyne/PulseSnapshot.py
index d039a6b..6687c2c 100644
--- a/nucleus/mnemosyne/PulseSnapshot.py
+++ b/nucleus/mnemosyne/PulseSnapshot.py
@@ -1,4 +1,5 @@
 # -*- coding: utf-8 -*-
+from nucleus._silent_except import silent_exc
 """
 PulseSnapshot.py —— 脉冲快照
 
@@ -266,8 +267,8 @@ class PulseSnapshot:
                 if _n > 0:
                     self._logger.info(
                         f"[P2-88] 已清理临时快照残留 {_n} 个（%TEMP%/snap_t4_*）")
-            except Exception:
-                pass
+            except Exception as e:
+                silent_exc(e, "nucleus/mnemosyne/PulseSnapshot.py:269", level="warning")
 
     def _log(self, level: str, msg: str):
         """统一日志输出"""
@@ -344,8 +345,8 @@ class PulseSnapshot:
                 self._logger.info(
                     f"[DataQualityGuard] 快照保存前健康度: 累计检查{_gs.get('checked', 0)}次, "
                     f"异常{_gs.get('bad', 0)}个")
-        except Exception:
-            pass
+        except Exception as e:
+            silent_exc(e, "nucleus/mnemosyne/PulseSnapshot.py:347", level="warning")
 
         if self.node_pool is None:
             self._log(LogLevel.WARNING, "node_pool 未注入，跳过保存")
@@ -783,8 +784,8 @@ class PulseSnapshot:
                 _stall_to = float(getattr(_c80, "SNAPSHOT_SAVE_TIMEOUT", 1800) or 1800)
                 _full_int = float(getattr(_c80, "SNAPSHOT_FULL_SAVE_INTERVAL", 3600) or 3600)
                 _stall_to = max(_stall_to, 2 * _full_int)
-            except Exception:
-                pass
+            except Exception as e:
+                silent_exc(e, "nucleus/mnemosyne/PulseSnapshot.py:786", level="warning")
             _stuck = time.time() - getattr(self, "_m67_save_start_time", 0)
             if _stuck > _stall_to:
                 self._log(LogLevel.ERROR,
@@ -891,8 +892,8 @@ class PulseSnapshot:
                     _failed = f"{tmp_path}.failed_{time.strftime('%Y%m%d_%H%M%S')}"
                     os.rename(tmp_path, _failed)
                     self._log(LogLevel.ERROR, f"[第80批 T4] 快照写入失败，保留临时副本供恢复: {_failed}")
-            except Exception:
-                pass
+            except Exception as e:
+                silent_exc(e, "nucleus/mnemosyne/PulseSnapshot.py:894", level="warning")
             raise
         self._cleanup_old_backups()
 
@@ -1588,8 +1589,8 @@ class PulseSnapshot:
                     _failed = f"{tmp_path}.failed_{time.strftime('%Y%m%d_%H%M%S')}"
                     os.rename(tmp_path, _failed)
                     self._log(LogLevel.ERROR, f"[第80批 T4] 快照写入失败，保留临时副本供恢复: {_failed}")
-            except Exception:
-                pass
+            except Exception as e:
+                silent_exc(e, "nucleus/mnemosyne/PulseSnapshot.py:1591", level="warning")
             raise
         
         # ===== 清理旧备份（仅 rotate=True） =====
@@ -1722,8 +1723,8 @@ class PulseSnapshot:
                         _n.linked_nodes = []
                     try:
                         self._m70_lazy_ids.add(getattr(_n, "node_id", ""))
-                    except Exception:
-                        pass
+                    except Exception as e:
+                        silent_exc(e, "nucleus/mnemosyne/PulseSnapshot.py:1725", level="warning")
                     self._m70_lazy_node_map[getattr(_n, "node_id", "")] = _n
                     self._m70_hot_load_stats["lazy"] += 1
                 except Exception:
diff --git a/nucleus/reasoning/PatchManager.py b/nucleus/reasoning/PatchManager.py
index 13feb39..8259f34 100644
--- a/nucleus/reasoning/PatchManager.py
+++ b/nucleus/reasoning/PatchManager.py
@@ -1,4 +1,5 @@
 # -*- coding: utf-8 -*-
+from nucleus._silent_except import silent_exc
 """
 PatchManager.py —— 补丁管理器
 
@@ -259,8 +260,8 @@ def _m92_base_indent(code: str) -> int:
         _fn = getattr(_SEE, "_m91_base_indent", None)
         if callable(_fn):
             return int(_fn(str(code)))
-    except Exception:
-        pass
+    except Exception as e:
+        silent_exc(e, "nucleus/reasoning/PatchManager.py:262", level="warning")
     for _ln in str(code).replace("\r\n", "\n").split("\n"):
         _s = _ln.strip()
         if not _s or _s.startswith("#"):
@@ -1941,7 +1942,8 @@ class PatchManager:
                     "complete": False,
                     "reason": f"与原文相似度过低({_ratio:.2f} < {_min_sim:g})，疑似严重残缺/截断",
                 }
-        except Exception:
+        except Exception as e:
+            silent_exc(e, "nucleus/reasoning/PatchManager.py:1944", level="warning")
             # 相似度计算失败不阻断验证（保守放行，交给后续语法/导入关）
             pass
 
@@ -2480,8 +2482,8 @@ class PatchManager:
             import config as _cfg_mod2
             _timeout = int(getattr(_cfg_mod2, 'EVOLUTION_CONFIG', {}).get(
                 "regression_timeout", _timeout) or _timeout)
-        except Exception:
-            pass
+        except Exception as e:
+            silent_exc(e, "nucleus/reasoning/PatchManager.py:2483", level="warning")
         _passed = 0
         _failed = 0
         _missing = []
@@ -2581,7 +2583,8 @@ class PatchManager:
                     )
                     # 阈值从 config.EVOLUTION_EFFECT_VERIFY_CONFIG 读（低风险40/高风险60）
                     _min_trust = _EV.trust_threshold_for(patch)
-                except Exception:
+                except Exception as e:
+                    silent_exc(e, "nucleus/reasoning/PatchManager.py:2584", level="warning")
                     pass  # 判定失败则维持原门槛，绝不放宽
             if _trust < _min_trust:
                 return {"safe": False, "reason": f"信任分数不足({_trust}<{_min_trust})"}
diff --git a/organs/body/PulseLiver.py b/organs/body/PulseLiver.py
index 6d33e80..3232402 100644
--- a/organs/body/PulseLiver.py
+++ b/organs/body/PulseLiver.py
@@ -2940,6 +2940,24 @@ class PulseLiver(BasePulseOrgan):
             l3_node.trust_score = min(100.0, avg_trust + 10.0)
             l3_node.state = "locked"
 
+            # ★T-125b(D040 A')：融合点记源——记录本 L3 由哪些 L2 来源节点融合而来
+            l3_node.evidence_chain = [
+                {"node_id": getattr(n, "node_id", None), "role": "source"}
+                for n in quality_nodes
+            ]
+            # 反向登记到源桶（l2_id -> set(l3_id)），供后续矛盾归属反查（同会话内即时可见）
+            _bucket = getattr(self, "_l3_src_bucket", None)
+            if _bucket is None:
+                _bucket = {}
+                self._l3_src_bucket = _bucket
+                self._l3_src_bucket_ts = 0.0
+            _l3_id = l3_node.node_id
+            for n in quality_nodes:
+                _nid = getattr(n, "node_id", None)
+                if _nid is None:
+                    continue
+                _bucket.setdefault(_nid, set()).add(_l3_id)
+
             if self.frequency_codec:
                 self.frequency_codec.encode_node(l3_node)
             if self.node_pool:
@@ -3066,6 +3084,40 @@ class PulseLiver(BasePulseOrgan):
         ("正确", "错误"), ("真", "假"), ("有", "没有"),
     )
 
+    # ========== ★T-125b(D040 A')：L3 源反查桶（l2_id -> set(l3_id)） ==========
+    _L3_SRC_BUCKET_TTL = 600.0      # 反查桶惰性重建 TTL（秒）
+    _L3_SRC_BUCKET_CAP = 200_000     # 内存上界（熔断阈值）
+
+    def _l3_refresh_src_bucket(self) -> dict:
+        """惰性重建 L3 源反查桶：TTL 600s 复用；超 200k 条目熔断停止新增。
+        桶内容 = {l2_node_id: set(l3_node_id)}，由已落盘 L3 的 evidence_chain(role=source) 反建，
+        覆盖重启后内存桶丢失的场景；同一会话内由融合点增量登记补齐。
+        """
+        _bucket = getattr(self, "_l3_src_bucket", None)
+        _ts = getattr(self, "_l3_src_bucket_ts", 0.0)
+        _now = time.time()
+        if _bucket is not None and (_now - _ts) < self._L3_SRC_BUCKET_TTL:
+            return _bucket
+        _new = {}
+        if self.node_pool and self._kal:
+            try:
+                _l3s = self._kal.query_nodes(evol_level=PulseNode.EVOL_L3, limit=2000)
+                for _l3 in _l3s:
+                    _srcs = [e.get("node_id") for e in getattr(_l3, "evidence_chain", [])
+                             if e.get("role") == "source" and e.get("node_id")]
+                    for _sid in _srcs:
+                        if len(_new) >= self._L3_SRC_BUCKET_CAP:
+                            break
+                        _new.setdefault(_sid, set()).add(_l3.node_id)
+                    if len(_new) >= self._L3_SRC_BUCKET_CAP:
+                        break
+            except Exception as _e:
+                self._log(LogLevel.WARNING,
+                         f"T-125b 反查桶重建跳过(已忽略): {type(_e).__name__}: {_e}")
+        self._l3_src_bucket = _new
+        self._l3_src_bucket_ts = _now
+        return _new
+
     # [批次4·深度体检][PERF-5] 统一 kw_a 预处理 + 引用类常量
     def _detect_contradictions(self, prefix: str, nodes: list[PulseNode]):
         """检测L2节点中是否存在矛盾节点对（含跨路径检测）"""
@@ -3134,6 +3186,33 @@ class PulseLiver(BasePulseOrgan):
                     # 此前 conflict_count 恒为 0，规则5「三次推导冲突可降级」形同虚设。
                     node_a.conflict_count = getattr(node_a, 'conflict_count', 0) + 1
                     node_b.conflict_count = getattr(node_b, 'conflict_count', 0) + 1
+                    # ★T-125b(D040 A')：矛盾归属——反查桶求记源 L3（只计记源 L3），
+                    #   成员存活校验（97%死引用条款）+ 同对去重 + 1h 冷却闸标记（last_conflict_at）。
+                    #   本批只落观测（last_conflict_at 时间戳），冲突计数执行段归 W7-B（l3_downgraded 恒 0 诚实）。
+                    try:
+                        _bucket = self._l3_refresh_src_bucket()
+                        _a_src = _bucket.get(node_a.node_id)
+                        _b_src = _bucket.get(node_b.node_id)
+                        _src_l3 = (_a_src & _b_src) if (_a_src and _b_src) else set()
+                        for _l3_id in _src_l3:
+                            _l3 = self._kal.get_node(_l3_id) if self._kal else None
+                            if _l3 is None or getattr(_l3, "evol_level", "") != PulseNode.EVOL_L3:
+                                continue  # 97%死引用条款：池中已不存在的 L3 不计数
+                            _pair_key = "%s|%s" % (min(node_a.node_id, node_b.node_id),
+                                                   max(node_a.node_id, node_b.node_id))
+                            _seen = getattr(self, "_conflict_pair_seen", None)
+                            if _seen is None:
+                                _seen = set()
+                                self._conflict_pair_seen = _seen
+                            if _pair_key in _seen:
+                                continue
+                            _seen.add(_pair_key)
+                            _now = time.time()
+                            if (_now - getattr(_l3, "last_conflict_at", 0.0)) >= 3600.0:
+                                _l3.last_conflict_at = _now  # 冷却闸标记；W7-B 在此增 conflict_count
+                    except Exception as _e:
+                        self._log(LogLevel.WARNING,
+                                 f"T-125b 矛盾归属跳过(已忽略): {type(_e).__name__}: {_e}")
                     contradiction_pairs.append({
                         "node_a_id": node_a.node_id,
                         "node_b_id": node_b.node_id,
diff --git a/organs/body/PulseLung.py b/organs/body/PulseLung.py
index 594918a..a56e624 100644
--- a/organs/body/PulseLung.py
+++ b/organs/body/PulseLung.py
@@ -1,6 +1,7 @@
 import logging
 _module_logger = logging.getLogger(__name__)
 # -*- coding: utf-8 -*-
+from nucleus._silent_except import silent_exc
 """
 PulseLung —— 脉冲驱动肺 · 模型调用器官
 
@@ -48,7 +49,8 @@ class PulseLung(BasePulseOrgan):
             import config as _cfg
             _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
             self.ollama_timeout = _rp.get("llm_timeout_seconds", 30)
-        except Exception:
+        except Exception as e:
+            silent_exc(e, "organs/body/PulseLung.py:51", level="warning")
             self.ollama_timeout = 30
 
         # 统计
@@ -64,7 +66,8 @@ class PulseLung(BasePulseOrgan):
             _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
             self._model_downgrade_threshold = _rp.get("llm_model_downgrade_threshold", 0.4)
             self._model_recover_threshold = _rp.get("llm_model_recover_threshold", 0.7)
-        except Exception:
+        except Exception as e:
+            silent_exc(e, "organs/body/PulseLung.py:67", level="warning")
             self._model_downgrade_threshold = 0.4
             self._model_recover_threshold = 0.7
         self._local_fallback = ""  # 远程调用失败时的本地回退模型
diff --git a/organs/body/PulseStomach.py b/organs/body/PulseStomach.py
index 73e7fc0..01a21f7 100644
--- a/organs/body/PulseStomach.py
+++ b/organs/body/PulseStomach.py
@@ -1,4 +1,5 @@
 # -*- coding: utf-8 -*-
+from nucleus._silent_except import silent_exc
 """
 PulseStomach —— 脉冲驱动胃 · 知识消化器官
 
@@ -792,8 +793,8 @@ class PulseStomach(BasePulseOrgan):
                                     if isinstance(_v68, (list, dict)):
                                         try:
                                             _data68[_k68] = _json105.dumps(_v68, ensure_ascii=False)
-                                        except Exception:
-                                            pass
+                                        except Exception as e:
+                                            silent_exc(e, "organs/body/PulseStomach.py:795", level="warning")
                                 _parsed = _data68
                                 self._log(LogLevel.INFO,
                                           f"代码分析JSON经策略5(容错解析:{_st68})成功: "
@@ -2049,8 +2050,8 @@ class PulseStomach(BasePulseOrgan):
                 "content_len": len(str(content or "")),
                 "reason": str(reason)[:80],
             })
-        except Exception:
-            pass
+        except Exception as e:
+            silent_exc(e, "organs/body/PulseStomach.py:2052", level="warning")
 
     def _verify_digestion_quality(self, content: str, keywords: list,
                                   space_path: str, source_organ: str,
@@ -2253,8 +2254,8 @@ class PulseStomach(BasePulseOrgan):
             _s = _t.get_stats() if hasattr(_t, "get_stats") else None
             if isinstance(_s, dict):
                 _out["tracker"] = _s
-        except Exception:
-            pass
+        except Exception as e:
+            silent_exc(e, "organs/body/PulseStomach.py:2256", level="warning")
         return _out
 
     @staticmethod
diff --git a/organs/motor/PulseController.py b/organs/motor/PulseController.py
index 91e4267..3d121c7 100644
--- a/organs/motor/PulseController.py
+++ b/organs/motor/PulseController.py
@@ -1,4 +1,5 @@
 # -*- coding: utf-8 -*-
+from nucleus._silent_except import silent_exc
 """
 PulseController —— 控制器器官 · 网页深度搜索与本地文件/应用操纵
 
@@ -287,8 +288,8 @@ class PulseController(BasePulseOrgan):
             if loop.is_running():
                 self._log(LogLevel.DEBUG, "异步循环运行中，跳过无头浏览器启动")
                 return False
-        except RuntimeError:
-            pass
+        except RuntimeError as e:
+            silent_exc(e, "organs/motor/PulseController.py:290", level="warning")
         except Exception as e:
             self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
         if not PLAYWRIGHT_AVAILABLE:
@@ -376,8 +377,8 @@ class PulseController(BasePulseOrgan):
                 # networkidle 超时说明页面有持续请求（如广告），降级到 domcontentloaded
                 try:
                     self._headless_page.wait_for_load_state('domcontentloaded', timeout=5000)
-                except PlaywrightTimeout:  # type: ignore[possibly-unbound]
-                    pass
+                except PlaywrightTimeout as e:  # type: ignore[possibly-unbound]
+                    silent_exc(e, "organs/motor/PulseController.py:379", level="warning")
             self._headless_last_used = time.time()
 
             human_cfg = self._get_headless_config("human_behavior", {})
