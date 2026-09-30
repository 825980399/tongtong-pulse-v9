# 第152批 · DIFFS_FULL（关键差异汇编）

> 本文件汇编 T152 各任务的关键 diff 片段，完整 diff 以 git 提交 `5a3c3da` / `cc50926` 为准。
> 全部改动仅涉及**注释 / docstring / 配置删除 / 文档卡片**，生产代码逻辑 0 改动。

---

## A. T152-1 死配置删除（config.py，9 行）

```diff
-ENABLE_PATCH_ASCII_GUARD = False
-ENABLE_TEST_DIR_CLEANUP = False
-EXPERIENCE_POLLUTION_CLEANUP_INTERVAL = 3600
-COLD_STORAGE_CONSISTENCY_CHECK = False
-COLD_MISSING_FILE_LOG_LEVEL = "WARNING"
-SNAPSHOT_HOT_SAVE_INTERVAL = 300
-SNAPSHOT_WARM_SAVE_INTERVAL = 1800
-SNAPSHOT_COLD_SAVE_INTERVAL = 3600
-DISTRIBUTED_SIMULATION_MODE = False
```

同步删除 `tests/` 下 9 条对应断言（如 `test_*.py` 中对上述常量的 `assertEqual`/存在性判断），并清理遗留死导入 `import config`（ruff F401 修复）。

---

## B. T152-2① 导出白名单收紧（export_public.py）

`PUBLIC_DOCS_ALLOW_DIRS`（frozenset，line 152）移除 `工具类文档` 整目录放行。`docs_allowed()`（line 324）维持 fail-closed：

```python
def docs_allowed(rel: str) -> bool:
    top = rel.split("/", 1)[0]
    return top in PUBLIC_DOCS_ALLOW_DIRS   # 仅白名单精确文件可导出
```

> line 317 注释 `精确文件白名单（含子目录路径，如 "工具类文档/公开说明.md"）` 仅为示例说明，不构成放行。

---

## C. T152-2② 注释/docstring 内部名归一（v2 清洗器，tokenize 安全边界）

仅 COMMENT 与三引号 STRING（docstring）区间替换；双引号普通字符串（身份数据）不动。

**config.py:7711（此前 v1 漏判，v2 修正）**：
```diff
-"""★主线第11批（星轨裁决选3）：外挂网关作为可选渠道源的配置。
+"""★主线第11批（内部协作者裁决选3）：外挂网关作为可选渠道源的配置。
```

**PulseInnerWorld.py:10234（此前 v1 漏判，v2 修正）**：
```diff
-"""★第九批 3.2（星轨 P1-22）：...
+"""★第九批 3.2（内部协作者 P1-22）：...
```

**PulseInnerWorld.py:10298（此前 v1 漏判，v2 修正）**：
```diff
-"""★第九批 3.4（星轨 P2-9）：...
+"""★第九批 3.4（内部协作者 P2-9）：...
```

**批次号归一（T152-2④）示例**：
```diff
-... 相关逻辑见 D167 与 T-86b 的实现
+... 相关逻辑见 Dxxx 与相关任务的实现
```

**身份数据保护验证（必须 0 触碰）**：
```python
# config.py SEED_MEMORIES 中仍为原始双引号值，清洗后不变：
{"value": "小林（<CREATOR>）..."}     # 保留
{"value": "路灯（<SELF_NAME> 协作者）"} # 保留
```

---

## D. T152-3 归属注释（config.py:10368）

```diff
+# [T152-3] _apply_placeholder_render：在配置加载末尾对 display_name / seed.value /
+#           identity_rules 等展示值做内存内渲染；不渲染 keywords/space_path 等功能键
 def _apply_placeholder_render():
```

---

## E. T152-4 运行数据卡片（docs/比赛准备/运行数据卡片_20260927.md）

```diff
-| Python 总代码行数 | **288,911** | ...
+| Python 总代码行数 | **283,145** | 同上「Python 总代码行数」
-| 核心代码行数 | **204,269** | ...
+| 核心代码行数 | **204,068** | `nucleus/` + `organs/` + `tools/` 三目录
-| data/ 目录体积 | **5,436 MB** | ...
+| data/ 目录体积 | **4,501 MB** | 本机运行数据（不进发布包）
```

另删除 5 处内部批次号/票号引用（如 `第1xx批`、`D1xx`、`T-xx` 在卡片叙述中的出现），统一为通用说明。

---

## F. 清洗作用域统计

| 类别 | 文件数 | 说明 |
|------|--------|------|
| 文件级 staging（纯 T152） | 372 | config.py + export_public.py + 108 test + 262 纯清洗 py |
| 块级 hunk-staging（混合文件拣选） | 52 | 55 混合文件中含 T152 清洗块者，排除其他会话逻辑 |
| **合计入批** | **424** | |
| 排除（不动） | 379 | 52 混合残留 + 16 纯其他 py + 8 tools/ci + 188 docs 删除 + 110 未跟踪 |

---

## G. 提交清单

```
5a3c3da  [T152-1]  死配置删除 + 测试断言 + config 署名/占位符注释        (109 files)
cc50926  [T152-2/3/4] 导出白名单 + 注释归一 + 数据卡片 + 块级拣选       (316 files)
```

均未 push。
