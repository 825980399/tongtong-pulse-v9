# 第130批 T-130b · W7三件式独窗 DIFF

> 提交：`059520b`（第130批 T-130b W7三件式独窗交付，author=Tongtong Dev）
> 基准：`fda267d` → `059520b`

## 改动坐标（T0 实测核验）
- `config.py:1548` `ENABLE_FACE_WELCOME_DIRECT = False → True`（SHADOW 保持 `True`，只记不跳）
- `nucleus/mnemosyne/PulseNodePool.py:1966` 记忆验证闭环日志补 `L3候选/降级` 计数
- `nucleus/mnemosyne/PulseNodePool.py:1789` 与 `:1967` 两处裸 `except:continue` 转 `_module_logger.warning`

## ⚠️ T0 偏差修正（任务书字段名不准）
任务书要求 f-string 追加 `{l3_downgrade_candidates}` / `{l3_downgraded}`，但这两个**裸名在 `:1966` 所在作用域（`start_memory_verification_loop._loop`）并不存在**——它们是 `run_memory_verification` 内的局部变量，未传入 `_loop`。
正确引用为 `_report.get('l3_downgrade_candidates', 0)` 与 `_report.get('l3_downgraded', 0)`（`_report` 即 `run_memory_verification` 的返回 dict，键名一致）。已按此修正，规避运行时 `NameError`。

## Unified Diff
```diff
diff --git a/config.py b/config.py
index d9da183..d34cb48 100644
--- a/config.py
+++ b/config.py
@@ -1545,7 +1545,7 @@ DIALOG_TIMEOUT_FALLBACK_SEC = 60              # 大脑皮层看门狗：对话
 
 # ★第109批 T-109b：face_welcome 快赢开关（方案A：人脸识别后直接欢迎，跳过"你是谁"推理请求，省 1 次 LLM 调用）
 #   False（默认）= 保持原行为（发射 InferenceEvent.REQUEST 融入自我画像）；True = 跳过推理请求，仅打印欢迎 + L1 欢迎脉冲。
-ENABLE_FACE_WELCOME_DIRECT = False
+ENABLE_FACE_WELCOME_DIRECT = True
 # ★第115批 T-115e：face_welcome 影子半态开关（默认开=只记日志不真跳，观察 1 天后由星轨翻 False 真生效）
 FACE_WELCOME_SHADOW = True
 
diff --git a/nucleus/mnemosyne/PulseNodePool.py b/nucleus/mnemosyne/PulseNodePool.py
index e81b3cb..5923210 100644
--- a/nucleus/mnemosyne/PulseNodePool.py
+++ b/nucleus/mnemosyne/PulseNodePool.py
@@ -1786,7 +1786,8 @@ class PulseNodePool(SilentLogMixin):
                         "value": _val,
                         "action": "purge",
                     })
-            except Exception:
+            except Exception as e:
+                _module_logger.warning(f"记忆验证·节点遍历异常(已跳过该节点): {e}", exc_info=True)
                 continue
 
         # ========== ★T-127b（D040 W7-B·断4门修·动作段）：执行 L3→L2 降级 ==========
@@ -1963,8 +1964,9 @@ class PulseNodePool(SilentLogMixin):
                         _log(f"记忆验证闭环: "
                              f"评估{_report.get('total_evaluated', 0)}节点, "
                              f"强化{_reinforce_n}, 沉睡{len(_report.get('dormant_candidates', []))}, "
-                             f"清理候选{_stale_n}, 实际淘汰{_purged}")
-                except Exception:
+                             f"清理候选{_stale_n}, 实际淘汰{_purged}, L3候选{_report.get('l3_downgrade_candidates', 0)}, 降级{_report.get('l3_downgraded', 0)}")
+                except Exception as e:
+                    _module_logger.warning(f"记忆验证闭环·异常(已跳过本轮): {e}", exc_info=True)
                     continue
 
         _thr = _th.Thread(target=_loop, name="MemoryVerifyLoop", daemon=True)

```

## 门禁核验（本提交）
- ruff F = 0（全仓）✅
- py_compile：COMPILE_OK ✅
- 静默except CI：PASS（新增=0）✅
- m95 单测：39 passed / 4 skipped ✅
- 行尾：无 CRCRLF ✅
