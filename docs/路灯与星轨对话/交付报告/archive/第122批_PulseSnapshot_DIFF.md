# 第122批 T-122b · PulseSnapshot.py 改动 DIFF

- 改前 sha256: `c134b5e690bee93167c1042f3817ad398a8f63580d51b42fddbad53fc05f5573`
- 改后 sha256: `bafe90a69ff06b3b1c4c1aa079f8c1facbc5c118608a462ab0c8e93cb3379447`
- 改动量: +6 行（写白名单 +3 / 读映射 +3），5 处触点中的 T1/T2；T3 required_columns 不加 / T4 schema 版本不 bump / T5 冷层触点不动
- 行尾: CRLF 保全（改后 2999 CRLF / 0 LF-only）

```diff
--- a/PulseSnapshot.py (改前 .bak_batch122)
+++ b/PulseSnapshot.py (改后)
@@ -2493,6 +2493,9 @@
                 _row["source_timestamp"] = float(_d.get("source_timestamp", 0.0) or 0.0)
                 _row["quality_flag"] = str(_d.get("quality_flag", "clean"))
                 _row["quality_reason"] = str(_d.get("quality_reason", ""))
+            # ★第122批 T-122b：C4断5 补 conflict_count/last_conflict_at 两键（Parquet 主存储搬运，与 JSON 侧 T-120e 对齐）
+            _row["conflict_count"] = int(_d.get("conflict_count", 0) or 0)
+            _row["last_conflict_at"] = float(_d.get("last_conflict_at", 0.0) or 0.0)
             _rows.append(_row)
         return _rows
 
@@ -2559,6 +2562,9 @@
             "source_timestamp": float(_row.get("source_timestamp", 0.0) or 0.0),
             "quality_flag": _row.get("quality_flag", "clean") or "clean",
             "quality_reason": _row.get("quality_reason", "") or "",
+            # ★第122批 T-122b：C4断5 读映射补两键（与写白名单对齐）
+            "conflict_count": int(_row.get("conflict_count", 0) or 0),
+            "last_conflict_at": float(_row.get("last_conflict_at", 0.0) or 0.0),
         }
 
     @staticmethod
```
