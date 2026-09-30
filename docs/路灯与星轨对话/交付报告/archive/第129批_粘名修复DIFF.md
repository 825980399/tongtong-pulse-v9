# 第129批 · T-129b 粘名缺陷甲案修复 DIFF

## 背景
VC 粘名通道（sticky-name）= 影子误报最大结构性污染源。测试脸 A 命中后离场、陌生人 B 入座 →
影子行记 `user=A` 而镜头前不是 A。甲案在两处「离场」分支 emit `USER_LEFT` **之后**重置
`_current_user_name = "访客"`，使后续记录不再携带旧名。

## 红线遵守
- **一切 payload 不动**：`USER_LEFT` 的 `payload.user_name` 在 emit 时仍用真实名（反向验收通过）。
- 仅在 emit 块**之后**清粘名，不影响离场记录的真名。

## 插入点
- PathA（fast_left `return` 前，约 :786→改后 :787）：`self._current_user_name = "访客"`
- PathB（稳定离开 elif 尾，约 :864→改后 :865）：`self._current_user_name = "访客"`

## PulseVisualCortex.py DIFF

```diff
diff --git a/organs/senses/PulseVisualCortex.py b/organs/senses/PulseVisualCortex.py
index a0907a0..75a392a 100644
--- a/organs/senses/PulseVisualCortex.py
+++ b/organs/senses/PulseVisualCortex.py
@@ -783,6 +783,7 @@ class PulseVisualCortex(BasePulseOrgan):
                         priority=7,
                         layer="L1"
                     ))
+                self._current_user_name = "访客"  # ★第129批 T-129b：离场即清粘名，防影子误报
                 return {"status": "fast_left", "frame_seq": frame_seq}
         
         # 常规时序追踪（兜底）：基于滑动窗口的稳定性判断
@@ -862,6 +863,7 @@ class PulseVisualCortex(BasePulseOrgan):
                         priority=7,
                         layer="L1"
                     ))
+                self._current_user_name = "访客"  # ★第129批 T-129b：离场即清粘名，防影子误报
         
         # 异步写入视觉流日志（放入队列，后台线程处理）
         try:

```
