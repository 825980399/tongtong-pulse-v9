# 任务书：第82批 T-f（⑦ benchmark 硬编码 12295）+ T-g（⑥ FAISS ADD_UNDERFLOW，单列）

> 星轨只读定位，路灯执行。⑦最简单先做；⑥与冷热懒加载相关，单列不与 P0 混。

## ⑦ m72 benchmark 硬编码节点数 12295 → 动态基线+容差

**只读定位**：
- `tools/benchmark_hot_cold_faiss_kal.py:187` `stage_faiss_fix(count=12295, dim=512, ...)`
- 同文件 `:362`（docstring）、`:460`（help 文本）、`:505` `_cnt = ... (12295 if a.real_data else 2000)`
- `tests/test_distributed_e2e_m72.py:396-399` `assert len(nodes) == 12295` 硬断言

**问题**：节点数已从 12295 增长（当前 L1=1375、总节点随进化持续变），硬编码会随数据增长而误判测试失败。

**改法**：
1. benchmark 的 real_data 计数改为**动态读 Parquet 实际节点数**（`_load_real_nodes` 返回值）作为 count 上限，而非写死 12295；保留 `--faiss-count` 参数可覆盖。
2. `test_distributed_e2e_m72.py:399` 硬断言改为 `len(nodes) >= 1000`（或与实际目录一致的下限容差），不断言精确值。
3. 注释里"12295"改为"以 Parquet 实际节点数为准"。

**回归**：`python tools/benchmark_hot_cold_faiss_kal.py --real-data` 能跑通；`tests/test_distributed_e2e_m72.py` 绿。纯测试/benchmark 维护，不碰生产数据。

---

## ⑥ FAISS ADD_UNDERFLOW（faiss_store.py:143）—— 单列，不混 P0

**只读定位**：`nucleus/vector_store/faiss_store.py`
- :114 `vecs = np.array([v for _, v in vectors], dtype=np.float32)`
- :115-116 **空保护已有**（`if vecs.shape[0]==0: return`），所以 17:33 的 `AssertionError` 不是空数组。
- :132 `index.add(vecs)` 仍断言失败。**最可能根因（待路灯最小复现确认）**：
  1. 单条向量时 `np.array` 得到 `(d,)` 1 维而非 `(1,d)`，faiss `add` 要求 2 维 → AssertionError；
  2. 或 vecs 实际维度与 `self._dimension` 不一致；
  3. IVFFlat `:128 index.train(vecs)` 在样本过少时断言。

**改法**：
1. `:114` 后补 `vecs = np.atleast_2d(vecs)`（或 `vecs.reshape(1,-1)` 当 shape 为 1D），保证 2D；
2. 补维度一致性校验：vecs.shape[1] != self._dimension 时 warning 并跳过（不崩）；
3. IVFFlat 分支：样本数 < nlist 时降级 FlatL2；
4. 加最小复现用例：单条向量 add_vectors 不抛 AssertionError。

**回归**：复现 17:33 场景（空/单条/维度不匹配）不再 WARNING 断索引，仍走暴力余弦回退不影响检索正确性。

---

## 交付结论（路灯，2026-09-19）

- 交付报告：`docs/路灯与星轨对话/交付报告/已分析/2026-09-19_主线第82批TeaTfg_cid主线程补漏与基准修复_交付报告.md`
- **状态：⑦ + ⑥ 均完成**。

### ⑦ benchmark 硬编码 12295 → 动态（6 处 + 测试 1 处）

| 位置 | 改动 |
|---|---|
| `:365`（新增） | `_real_node_count(parquet_dir)`：动态读 Parquet 实际节点数，失败返回 0 |
| `:187` / `:191-193` / `:201-202` | `stage_faiss_fix(count=None)`；None 时 `count = _real_node_count() or 2000`；docstring 改"以 Parquet 实际节点数为准" |
| `:362` | docstring："恢复实际节点数" |
| `:460` | `--real-data` help 改"Parquet 实际节点数" |
| `:521-522` | `_cnt = a.faiss_count if a.faiss_count > 0 else ((_real_node_count(a.parquet_dir) or 2000) if a.real_data else 2000)` |
| `tests/test_distributed_e2e_m72.py:399` | `len(nodes) >= 1000`（原 `== 12295`）+ 注释 |

**实测**：动态计数 = **12353**（≠12295，证实已过时）；`--real-data` 跑通（`node_pool_real=OK`）；`--faiss-count 0` 动态分支实测 `count=12353, FlatL2, index_ready=true`；m72 由 `1 failed/49 passed` → **`50 passed`**。

### ⑥ FAISS ADD_UNDERFLOW —— 根因实测为「维度不匹配」

- 排除任务书候选 1（单条向量 1 维：`np.array` 恒 2 维、空保护已存在）与候选 3（IVFFlat 样本<nlist：`nlist=max(1,int(sqrt(n)))` 恒 `n>=nlist`，且该用例走 FlatL2）。
- **真根因**：`stage_faiss(200,64,...)` 用单例 `get_faiss_store()`（`_dimension` 取 `config.VECTOR_DIMENSION=512`）却喂 64 维向量 → `IndexFlatL2(512).add((200,64))` 抛空消息 AssertionError → `_index=None` 且 `_vectors` 未落盘 → `vector_count=0 < 200` → ADD_UNDERFLOW。
- **修复 3 处**（`nucleus/vector_store/faiss_store.py`）：`:115-127` `atleast_2d` + 维度校验（WARNING+跳过索引+**向量落内存**）；`:140-147` IVFFlat 样本<nlist 降级 FlatL2；`:197-205` 增量路径同样校验维度。
- **先红后绿**：改前 `3 failed, 2 passed` → 改后 `5 passed`（新建 `tests/test_faiss_add_underflow_m82.py`）。
- **零回归**：m73 **26 passed**、m69 **34 passed**、m72 **50 passed**。

### 门禁与备份
- ruff F 全项目 `All checks passed!`（另修星轨②测试文件 1 处 F401，详见报告 §八）；E402 零新增；py_compile EXIT 0。
- 备份 `.bak_tea/`（`benchmark_hot_cold_faiss_kal.py.bak`、`test_distributed_e2e_m72.py.bak`、`faiss_store.py.bak` 等 6 个），可回退；`config.py` 本批零写入。

### 遗留（P2，未擅自改）
`stage_faiss` 仍用单例（dim=512）而基准传 dim=64，现优雅降级为暴力回退（不再 ADD_UNDERFLOW），但**测不到 FAISS 索引性能**；建议后续把 `stage_faiss` 改为 `FAISSVectorStore(dimension=dim, ...)`（与 `stage_faiss_fix` 一致）。

