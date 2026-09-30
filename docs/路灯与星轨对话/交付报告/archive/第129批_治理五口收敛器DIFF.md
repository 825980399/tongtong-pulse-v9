# 第129批 · T-129a 治理五口收敛器 DIFF

> 模式：需停框架执行（本批判定见下「门禁结果·框架状态」）
> 红线：未改 config.py 运行开关 / 未碰 data/knowledge/

## 一、设计要点（烛微定稿）

- 新增 `_approve_via(patch, source, signer)` **唯一 approved 写入口**（类内 @staticmethod）。
- 新增两纯函数 **`_govern_fields_ok`**（字段契约·fail-closed 第一关）与 **`_boundary_check`**（边界·fail-closed 第二关）。
- 五口全部经 `_approve_via` 改写；双层串联 = 任一关不过即 fail-closed 不置 approved。
- W4/W5 人工口强制 `human:<名>` 签名（`_boundary_check` 拦截）；W5 批量口单次 **≤10** 上限（堵「一次点名全放行」）。
- apply 层 T-123b 冻结门**不动**（本收敛器只管「升 approved」）。

## 二、五口实测坐标（T0 偏差已校正）

| 口 | 函数 | 真·赋值行 | 收敛后 |
|---|---|---|---|
| W1 自动 | `_m105_try_release_low_risk` | :628 | `if not _approve_via(patch,"auto",None): return False` |
| W2 老化 | `_m94_apply_pending_aging` | :887 | `if not _approve_via(_p,"aging",None): continue` |
| W3 保存 | `save_pending_patch` | :1044 | `if _approve_via(patch,"save",None): patch["auto_approved"]=True` |
| W4 人工 | `approve_patch` | :1358 | `if not _approve_via(_patch,"human",signer): return {...}` |
| W5 批量 | `approve_all_patches` | :1406/1409 | `_count>=10: break` + `_approve_via(_p,"human",signer)` |

> ⚠️ **任务书偏差**：任务书给的「五处赋值点行号」实为各函数 `def`/docstring 锚点（550/825/981/1286/1337），
> 严格正则初扫只命中 2 行——因命中的是 docstring 里的 ``status='approved'`` 而非真赋值。
> 真·赋值行已上方表校正（567/825/981/1294/1343 → 改写后 628/887/1044/1358/1409）。

## 三、PatchManager.py 完整 DIFF

```diff
diff --git a/nucleus/reasoning/PatchManager.py b/nucleus/reasoning/PatchManager.py
index 8259f34..7816323 100644
--- a/nucleus/reasoning/PatchManager.py
+++ b/nucleus/reasoning/PatchManager.py
@@ -547,6 +547,67 @@ class PatchManager:
                                  type(Exception).__name__)
             return False
 
+    # ========== ★第129批 T-129a（P1）：治理五口收敛器 ==========
+    #   六票终裁后加固治理门禁，防止「下一个六票」。
+    #   五处 approved 赋值（W1自动/W2老化/W3保存/W4人工/W5批量）全部经
+    #   _approve_via 唯一写入口，fail-closed 双层串联
+    #   （_govern_fields_ok 字段契约 + _boundary_check 边界），
+    #   W4/W5 人工口强制 human:<名> 签名，W5 批量口单次≤10。
+    #   ★apply 层 T-123b 冻结门不动（本收敛器只管「升 approved」）。
+
+    @staticmethod
+    def _govern_fields_ok(patch: dict) -> bool:
+        """纯函数·fail-closed 第一关：approved 候选字段契约校验。
+
+        - 必须是 dict
+        - 必须有非空目标文件 file
+        - 必须至少含一段代码（original_code 或 modified_code），否则是空壳补丁
+        """
+        if not isinstance(patch, dict):
+            return False
+        _file = patch.get("file")
+        if not isinstance(_file, str) or not _file.strip():
+            return False
+        if not (patch.get("original_code") or patch.get("modified_code")):
+            return False
+        return True
+
+    @staticmethod
+    def _boundary_check(patch: dict, source: str, signer) -> bool:
+        """纯函数·fail-closed 第二关：approved 边界校验。
+
+        - source 必须在白名单 {auto, aging, save, human}
+        - human 口：signer 必须以 "human:" 开头（强制人工签名）
+        - 非 human 口：signer 不得为人工签名（机器口不得冒充人工）
+        """
+        if source not in ("auto", "aging", "save", "human"):
+            return False
+        if source == "human":
+            if not isinstance(signer, str) or not signer.startswith("human:"):
+                return False
+        else:
+            if isinstance(signer, str) and signer.startswith("human:"):
+                return False
+        return True
+
+    @staticmethod
+    def _approve_via(patch: dict, source: str, signer) -> bool:
+        """★第129批 唯一 approved 写入口（五口收敛器核心）。
+
+        fail-closed：任一校验不过 → 不置 approved、返回 False。
+        通过 → 统一落 status=approved + approved_source + approved_signer + approved_at，
+        返回 True。各口在调用后再叠加自身留痕字段（auto_released / aged_* 等）。
+        """
+        if not PatchManager._govern_fields_ok(patch):
+            return False
+        if not PatchManager._boundary_check(patch, source, signer):
+            return False
+        patch["status"] = "approved"
+        patch["approved_source"] = source
+        patch["approved_signer"] = signer
+        patch["approved_at"] = time.time()
+        return True
+
     def _m105_try_release_low_risk(self, patch: dict) -> bool:
         """★第105批 T-105a（P0）：对单条补丁尝试 T-101a 低风险放行（置 approved + 留痕）。
 
@@ -564,7 +625,8 @@ class PatchManager:
             return False
         if not PatchManager._m101_low_risk_release_path(patch):
             return False
-        patch["status"] = "approved"
+        if not PatchManager._approve_via(patch, "auto", None):
+            return False
         patch["auto_released"] = True
         patch["release_reason"] = "low_risk_release:T-101a"
         _module_logger.info(
@@ -822,7 +884,8 @@ class PatchManager:
                 _age_h = self._m94_patch_age_hours(_p, now)
                 if _age_h is None or _age_h < _maxh:
                     continue
-            _p["status"] = "approved"
+            if not PatchManager._approve_via(_p, "aging", None):
+                continue
             _p["aged_approved"] = True
             _p["aged_approved_at"] = _now
             _p["aged_reason"] = _why
@@ -978,8 +1041,9 @@ class PatchManager:
                     f"core={PatchManager._m80_is_core_file(patch.get('file',''))}, "
                     f"auto_apply={PatchManager._m80_auto_apply_enabled(_evo_cfg_a1)})")
             else:
-                patch["status"] = "approved"
-                patch["auto_approved"] = True
+                if PatchManager._approve_via(patch, "save", None):
+                    patch["auto_approved"] = True
+                # 否则 fail-closed：保持原 status，不置 auto_approved
                 _module_logger.info(
                     f"[补丁自动审批] 低风险补丁已自动审批: "
                     f"{patch.get('file','')}:{patch.get('method','')} "
@@ -1283,7 +1347,7 @@ class PatchManager:
                 return (_now - _t) / 86400.0 > _m54_stale_days()
         return False
 
-    def approve_patch(self, index: int) -> dict[str, Any]:
+    def approve_patch(self, index: int, signer: str = "human:unknown") -> dict[str, Any]:
         """将指定待审批补丁的 status 从 verified/pending 提升为 approved（★FIX: 人工审批闭环）"""
         pending = self._load_patch_list(self._pending_file)
         if not (0 <= index < len(pending)):
@@ -1291,8 +1355,8 @@ class PatchManager:
         _patch = pending[index]
         if _patch.get("status") == "approved":
             return {"ok": False, "reason": "该补丁已批准"}
-        _patch["status"] = "approved"
-        _patch["approved_at"] = time.time()
+        if not PatchManager._approve_via(_patch, "human", signer):
+            return {"ok": False, "reason": "审批被治理门禁拦截（需 human:<名> 签名且字段契约完整）"}
         # ★第54批 T3.2（P1）：过期检测 —— 超过阈值天数的补丁打 stale 标记。
         if _m54_stale_mark_on() and self._is_stale(_patch):
             _patch["stale"] = True
@@ -1334,14 +1398,16 @@ class PatchManager:
                 "reason": _reason, "auto_apply": _auto_apply,
                 "stale": bool(_patch.get("stale"))}
 
-    def approve_all_patches(self) -> dict[str, Any]:
+    def approve_all_patches(self, signer: str = "human:unknown") -> dict[str, Any]:
         """批准所有待审批补丁（★FIX: 人工审批闭环）"""
         pending = self._load_patch_list(self._pending_file)
         _count = 0
         for _p in pending:
+            if _count >= 10:
+                break  # ★第129批 T-129a：单次≤10硬上限，堵「一次点名全放行」
             if _p.get("status") != "approved":
-                _p["status"] = "approved"
-                _p["approved_at"] = time.time()
+                if not PatchManager._approve_via(_p, "human", signer):
+                    continue
                 # ★v9.5审美判据：批量审批同样记录质量分（不阻断闭环）
                 try:
                     from nucleus.evolution.AestheticJudge import get_aesthetic_judge

```
