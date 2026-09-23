# 第82批 T-e / T-f / T-g 交付页 —— 状态：已落地待终验（需重启后真对话验收）

> 供星轨独立读码复核。本页只列生产证据与实跑输出，不含自述结论。
> 日期：2026-09-19；执行：路灯。交付报告：`交付报告/已分析/2026-09-19_主线第82批TeaTfg_cid主线程补漏与基准修复_交付报告.md`

## 一、备份（.bak_tea/）

`.bak_batch82a/` 已存在（内含 9-12/9-15 旧 .bak）→ 按 T-e 红线"已存在则覆盖为 .bak_tea"改用 `.bak_tea/`：

| 文件 | 大小 |
|---|---|
| organs/motor/PulseController.py.bak | 174393 |
| tools/benchmark_hot_cold_faiss_kal.py.bak | 19777 |
| nucleus/vector_store/faiss_store.py.bak | 18869 |
| tests/test_dialog_cid_sanitize_m82.py.bak | 5515 |
| tests/test_distributed_e2e_m72.py.bak | 24785 |
| tests/test_periodic_scheduler_manual_registry_m82.py.bak | 2498 |

## 二、T-e 生产证据（补主线程 cid）

`organs/motor/PulseController.py:2681-2684`（改后）：

```python
            result = self._search_deep_headless(
                search_topic or reason, reason, max_articles,
                correlation_id=payload.get("search_correlation_id", ""),
            )
```

- 两条进 `_search_deep_headless` 的路径现均带 cid：异步线程路径 `:2538-2540`、**主线程路径 `:2681-2684`**。
- **requests 降级路径（:2689-2769）无需补**：该段只调 `_emit_digest`/`_finish_deep_search`/`_fallback_single_search`；全文件 `_emit_stage_feedback` 仅 5 处调用（917/1066/1098/1117/1136），**全在 `_search_deep_headless` 内**且均已带 `correlation_id=correlation_id`。

## 三、T-f 生产证据（benchmark 动态基线）

- 新增 `_real_node_count(parquet_dir)`（`tools/benchmark_hot_cold_faiss_kal.py:365`）；`stage_faiss_fix(count=None)`（:187）；`_cnt` 动态（:521-522）；docstring/help 3 处改"以 Parquet 实际节点数为准"。
- `tests/test_distributed_e2e_m72.py:399`：`len(nodes) >= 1000`。
- **实测动态计数 = 12353**（≠12295）；`--real-data` 跑通；`--faiss-count 0` 实测 `count=12353, FlatL2, index_ready=true`。

## 四、T-g 生产证据（FAISS ADD_UNDERFLOW）

根因**实测为维度不匹配**（非任务书候选的单条向量/空数组）：`stage_faiss(200,64,...)` 用单例 `get_faiss_store()`（`_dimension` 来自 `config.VECTOR_DIMENSION=512`）喂 64 维向量 → `IndexFlatL2(512).add((200,64))` 抛空消息 AssertionError → `_index=None` 且 `_vectors` 未落盘 → `vector_count=0 < 200` → ADD_UNDERFLOW。

`nucleus/vector_store/faiss_store.py` 3 处：`:115-127`（`atleast_2d` + 维度校验 → WARNING + 跳过索引 + **向量落内存**）、`:140-147`（IVFFlat 样本<nlist 降级 FlatL2）、`:197-205`（增量路径同校验）。

## 五、实跑输出（先红后绿）

```
T-e  改前: 1 failed, 16 passed   →  改后: 17 passed   (test_dialog_cid_sanitize_m82.py)
T-g  改前: 3 failed, 2 passed    →  改后: 5 passed    (test_faiss_add_underflow_m82.py)
m72  T-f 前 1 failed/49 passed   →  后  50 passed
m73 26 passed   m69 34 passed   m82 家族 37 passed
ruff F 全项目: All checks passed!   E402 改动文件: All checks passed!   py_compile EXIT 0
```

## 六、待星轨复核点

1. T-e 主线程路径取值是否与异步路径同口径（`payload.get("search_correlation_id","")`）；
2. requests 降级路径"无需补"的 grep 判断是否成立（`_emit_stage_feedback` 调用点清单）；
3. T-g 维度不匹配时"向量落内存 + 跳过索引"是否满足"仍走暴力余弦回退不影响检索正确性"；
4. **越界改动**：`tests/test_periodic_scheduler_manual_registry_m82.py` 删了未使用的 `import os`（全库 ruff F 唯一残留），如认为应保留请指示回退（`.bak_tea/tests/` 有改动前副本）。

## 七、终极验收（重启后，非 pytest）

- `你好`/`在吗` → 本地问候；`1+1等于几` → 短答案不兜底；`你是谁` → 本地答；
- 教新知识触发搜索 → **终止回退有回应（cid 真回来，主线程路径也须验证）**；
- 重启前请确认三开关：`PARQUET_AS_PRIMARY_STORAGE=True` / `SNAPSHOT_USE_INCREMENTAL_LOG=True` / `SNAPSHOT_HOT_COLD_LOAD=False`。
