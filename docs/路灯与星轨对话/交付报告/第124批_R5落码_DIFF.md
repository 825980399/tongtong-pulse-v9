commit 021d984d93fa404532477ce569e79062432da7fb
Author: Administrator <825980399@qq.com>
Date:   Fri Sep 25 13:56:29 2026 +0800

    第124批 R5落码+静默except首批7处+git纪律 本批交付（T-124a/b/c）

diff --git a/organs/senses/PulseVisualCortex.py b/organs/senses/PulseVisualCortex.py
index a601570..a0907a0 100644
--- a/organs/senses/PulseVisualCortex.py
+++ b/organs/senses/PulseVisualCortex.py
@@ -30,6 +30,7 @@ sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspa
 import os
 import sys
 import threading
+import logging
 import time
 from typing import Any
 
@@ -46,7 +47,25 @@ from nucleus.const import (
     PersonaEvent,
     VisualEvent,
 )
-from nucleus.data.DataAccessLayer import safe_read_json
+from nucleus.data.DataAccessLayer import safe_read_json, safe_write_json  # ★R5 加 safe_write_json
+_DIRTY_FACE_KEYS = ("用户", "访客", "小林")  # ★R5-1 脏键唯一真相源（:362 守卫/load 过滤/save 过滤三处共用）
+
+
+_TOL_BAD = object()   # ★R5 哨兵：区分"合法 null"与"非法值"，避免引入 except→return None 静默处
+
+
+def _tol_or_none(v):
+    """R5: tolerance_override 只允许收紧（0.3~0.6）。非数字/越界 → _TOL_BAD（调用方丢整条）。"""
+    if v is None:
+        return None
+    try:
+        f = float(v)
+    except (TypeError, ValueError):
+        logging.getLogger("pulse").debug(f"[R5] tolerance_override 非数字/越界: {v!r} → _TOL_BAD")
+        return _TOL_BAD
+    return f if 0.3 <= f <= 0.6 else _TOL_BAD
+
+
 
 
 class PulseVisualCortex(BasePulseOrgan):
@@ -67,6 +86,10 @@ class PulseVisualCortex(BasePulseOrgan):
             self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
 
 
+    _FACE_ROSTER_PATH = os.path.join(  # ★R5-2 册路径（同族写法见 :232 视觉流日志）
+        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
+        'data', 'identity', 'face_roster.json')
+
     def __init__(self, organ_name: str = "视觉皮层"):
         super().__init__(organ_name)
         
@@ -86,7 +109,7 @@ class PulseVisualCortex(BasePulseOrgan):
         self._global_lock = threading.Lock()
         
         self.is_running = False
-        self._current_user_name = "小林"  # 当前检测到的用户
+        self._current_user_name = "访客"  # 当前检测到的用户（T-118a：未知默认访客）
         # 摄像头监测线程
         self._camera_thread = None
         self._camera_running = False
@@ -119,10 +142,73 @@ class PulseVisualCortex(BasePulseOrgan):
         self._last_processed_seq = 0  # 最后处理的帧序号，用于丢弃过期帧
         self._gpu_available = None  # GPU是否可用（None=未收到能力更新）
 
+        self._roster_meta: dict[str, dict] = {}   # ★R5-3 册元数据 name→{enrolled_at,source,hits,tolerance_override}
+        self._roster_hits: dict[str, int] = {}    # ★R5-3b hits 预留（本批无写入点，恒取落盘值）
+
         # ===== 新增: 视觉身份记忆 =====
         self._known_face_encodings: dict[str, Any] = {}  # user_name → face_encoding
         self._pending_face_encoding = None  # 刚检测到但尚未识别的人脸编码
         self._pending_face_time = 0.0       # 待绑定人脸编码的检测时间
+        # ===== ★R5-4 人脸册加载（失败=空册，WARN 不抛，绝不影响启动）=====
+        _t_r5 = time.time()
+        _np = None                                        # VC 现无 numpy 模块级 import（仅 :515/:562 函数内）
+        try:
+            import numpy as _np
+        except ImportError:                               # 缺 numpy 走纯 list 兜底，不新增静默处
+            _np = None
+            self._log(LogLevel.DEBUG, "[R5] 未安装 numpy，人脸编码落盘/加载走纯 list 兜底")
+        try:
+            _raw = safe_read_json(self._face_roster_path(), default={})
+            if not isinstance(_raw, dict):
+                _raw = {}
+            if not _raw:
+                self._log(LogLevel.INFO, "[R5] 人脸册不存在/为空 → 空册启动（首次运行属正常）")
+            elif _raw.get("version") != 1:
+                raise ValueError("未知册版本 %r（不加载、不回写）" % (_raw.get("version"),))
+            else:
+                _faces = _raw.get("faces") or {}
+                _loaded = _dropped = 0
+                for _name, _item in _faces.items():
+                    if not isinstance(_name, str) or not _name.strip() or len(_name) > 32:
+                        _dropped += 1
+                        continue
+                    if _name in _DIRTY_FACE_KEYS:            # ★与 :362 同一判据（常量同源）
+                        _dropped += 1
+                        continue
+                    _enc = (_item or {}).get("encoding128")
+                    if not isinstance(_enc, list) or len(_enc) != 128:
+                        _dropped += 1
+                        continue
+                    try:
+                        _vec = _np.asarray(_enc, dtype=_np.float64)
+                    except (ImportError, TypeError, ValueError):
+                        try:
+                            _vec = [float(x) for x in _enc]
+                        except (TypeError, ValueError):
+                            _dropped += 1
+                            continue
+                    _it = _item or {}
+                    self._known_face_encodings[_name] = _vec
+                    self._roster_meta[_name] = {
+                        "enrolled_at": float(_it.get("enrolled_at") or 0.0),
+                        "source": _it.get("source") if _it.get("source") in ("auto", "manual") else "auto",
+                        "hits": int(_it.get("hits") or 0),
+                    }
+                    _tol = _tol_or_none(_it.get("tolerance_override"))
+                    # 非法/试图放宽 → 只把该字段降为 null，**不丢整条**（沿用 121 期 schema 语义）
+                    self._roster_meta[_name]["tolerance_override"] = None if _tol is _TOL_BAD else _tol
+                    self._roster_hits[_name] = int(_it.get("hits") or 0)
+                    _loaded += 1
+                self._log(LogLevel.INFO, "[R5] 人脸册加载: %d 条(丢弃 %d) 读 %.3fs"
+                          % (_loaded, _dropped, time.time() - _t_r5))
+        except (Exception, SystemExit) as _e_r5:             # 口径同 :547/:567（T-119a）
+            self._known_face_encodings = {}
+            self._roster_meta = {}
+            self._roster_hits = {}
+            self._log(LogLevel.WARNING,
+                      "[R5] 人脸册加载失败，按空册启动（不影响其它功能）: %s: %s"
+                      % (type(_e_r5).__name__, _e_r5))
+
 
         self._mp_face_detection = None        
         # ★7-5修复(2026-09-05)：以下默认值**必须先于**下方配置加载执行。
@@ -174,7 +260,7 @@ class PulseVisualCortex(BasePulseOrgan):
         try:
             import face_recognition  # noqa: F401
             self._has_face_recognition = True
-        except ImportError:
+        except (ImportError, SystemExit):  # ★T-119a 装库前置硬化：models 缺失 api.py quit() 抛 SystemExit
             self._has_face_recognition = False
             self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
         try:
@@ -359,18 +445,24 @@ class PulseVisualCortex(BasePulseOrgan):
         这样曈曈在与人对话时自然学习对方的长相。
         """
         user_name = payload.get("user_name", "")
-        if not user_name or user_name == "用户":
-            return {"status": "skipped", "reason": "无有效用户名"}
+        if not user_name or user_name in ("用户", "访客", "小林"):  # ★T-119a 绑定白名单守卫：脏键禁止入册
+            return {"status": "skipped", "reason": "脏键(用户/访客/小林)禁止入册"}
         
         # 如果有待绑定的人脸编码且距今30秒内，绑定到当前用户名
         if (self._pending_face_encoding is not None 
             and time.time() - self._pending_face_time < 30):
             self._known_face_encodings[user_name] = self._pending_face_encoding
             self._current_user_name = user_name
+            self._roster_meta[user_name] = {"enrolled_at": time.time(), "source": "auto",
+                                            "hits": 0, "tolerance_override": None}  # ★R5-5 元数据与册同点写
+            self._roster_hits[user_name] = 0                                        # ★R5-5b
+
             self._log(LogLevel.INFO, 
                      f"视觉身份绑定: 将当前人脸与'{user_name}'关联")
             self._pending_face_encoding = None
             self._pending_face_time = 0.0
+            self._save_face_roster()   # ★R5-6 一次绑定=一次落盘（失败只 WARN，不改绑定结果）
+
             return {"status": "bound", "user_name": user_name}
         
         return {"status": "acknowledged", "user_name": user_name}
@@ -544,7 +636,7 @@ class PulseVisualCortex(BasePulseOrgan):
             _conf = max(0.0, 1.0 - best_distance / 0.6)
             return (best_match, _conf)
             
-        except Exception as _e:
+        except (Exception, SystemExit) as _e:  # ★T-119a 硬化：SystemExit 非 Exception 子类
             # ★T-113c：识别失败计数 + 日志，防止"8天0成功"无感知
             _fc = getattr(self, "_face_recognize_fail_count", 0) + 1
             self._face_recognize_fail_count = _fc
@@ -564,7 +656,7 @@ class PulseVisualCortex(BasePulseOrgan):
             face_encodings = face_recognition.face_encodings(rgb_frame)
             if face_encodings:
                 return face_encodings[0]
-        except Exception as e:
+        except (Exception, SystemExit) as e:  # ★T-119a 硬化：SystemExit 非 Exception 子类
             self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
         return None    
 
@@ -870,6 +962,83 @@ class PulseVisualCortex(BasePulseOrgan):
         return self.get_stats()    
     # ========== 统计信息 ==========
     
+    # ========== ★R5-7 册路径 / 落盘（原子写；失败只 WARN）==========
+    def _face_roster_path(self) -> str:
+        """册路径（单独函数=测试可用 TONGTONG_FACE_ROSTER 覆盖，防污染生产生物特征册）。"""
+        return os.environ.get("TONGTONG_FACE_ROSTER", self._FACE_ROSTER_PATH)
+
+    def _save_face_roster(self) -> None:
+        """人脸册落盘。快照遍历（:368 可能并发改写）；backup=False 免生 .bak 明文副本。"""
+        try:
+            _faces = {}
+            for _name, _enc in dict(self._known_face_encodings).items():
+                if _name in _DIRTY_FACE_KEYS:               # ★save 侧同判据复拦
+                    continue
+                _meta = self._roster_meta.get(_name, {})
+                _faces[_name] = {
+                    "encoding128": _enc.tolist() if hasattr(_enc, "tolist") else [float(x) for x in _enc],
+                    "enrolled_at": float(_meta.get("enrolled_at") or time.time()),
+                    "source": _meta.get("source", "auto"),
+                    # hits 为预留字段：现网无"识别命中"计数点（_recognize_face 只 return 名字），故恒 0
+                    "hits": int(self._roster_hits.get(_name, 0)),
+                    "tolerance_override": _meta.get("tolerance_override"),
+                }
+            if not safe_write_json(self._face_roster_path(),
+                                   {"version": 1, "updated_at": time.time(), "faces": _faces},
+                                   backup=False):
+                self._log(LogLevel.WARNING, "[R5] 人脸册落盘返回 False（内存册仍有效）")
+        except (Exception, SystemExit) as _e:
+            self._log(LogLevel.WARNING,
+                      "[R5] 人脸册落盘异常（内存册仍有效）: %s: %s" % (type(_e).__name__, _e))
+
+    # ========== ★R5-8 隐私接口：只回元数据，绝不回显 encoding ==========
+    def _list_faces(self) -> list:
+        """人脸册清单（元数据 only，按 enrolled_at 升序）。无 encoding 任何形式。"""
+        _out = []
+        for _name in dict(self._known_face_encodings).keys():      # 快照遍历
+            _m = self._roster_meta.get(_name, {})
+            _ea = float(_m.get("enrolled_at") or 0.0)
+            _out.append({
+                "name": _name,
+                "enrolled_at": _ea,
+                "enrolled_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_ea)) if _ea else "unknown",
+                "source": _m.get("source", "auto"),
+                "hits": int(self._roster_hits.get(_name, 0)),
+                "tolerance_override": _m.get("tolerance_override"),
+            })
+        _out.sort(key=lambda d: d["enrolled_at"])
+        return _out
+
+    def _forget_face(self, user_name: str) -> dict:
+        """删除指定人脸：内存册 + 元数据 + 待绑定 + 磁盘册四处一致；不回显 encoding。"""
+        if not isinstance(user_name, str) or not user_name.strip():
+            return {"status": "rejected", "reason": "空姓名"}
+        _existed_mem = user_name in self._known_face_encodings
+        self._known_face_encodings.pop(user_name, None)
+        self._roster_meta.pop(user_name, None)
+        self._roster_hits.pop(user_name, None)
+        # ★保守侧：现网 _pending_face_encoding 不记姓名（:124/:539 无 name 字段），无法判定待绑脸是否就是被删者
+        #   → 无条件清待绑定；代价=可能多废一次绑定窗（票面登记取舍）
+        self._pending_face_encoding = None
+        self._pending_face_time = 0.0
+        if user_name == self._current_user_name:
+            self._current_user_name = "访客"   # ★T-118a 同族回落（回落"用户"亦不阻断下次绑定，见票 §D3）
+        try:
+            _raw = safe_read_json(self._face_roster_path(), default={})
+            _faces = (_raw or {}).get("faces") or {}
+            _existed_disk = _faces.pop(user_name, None) is not None
+            if _existed_disk or _existed_mem:
+                if not safe_write_json(self._face_roster_path(),
+                                       {"version": 1, "updated_at": time.time(), "faces": _faces},
+                                       backup=False):
+                    self._log(LogLevel.WARNING,
+                              "[R5] 遗忘落盘失败：'%s' 内存已删，磁盘册可能残留 → 需人工删文件" % user_name)
+            return {"status": "forgotten", "name": user_name, "was_in_memory": _existed_mem}
+        except (Exception, SystemExit) as _e:
+            self._log(LogLevel.WARNING,
+                      "[R5] 遗忘落盘异常（内存已删）: %s: %s" % (type(_e).__name__, _e))
+            return {"status": "forgotten_partial", "name": user_name, "was_in_memory": _existed_mem}
+
     def get_stats(self) -> dict[str, Any]:
         with self._lock:
             return {
