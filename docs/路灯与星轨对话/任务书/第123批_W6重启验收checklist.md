# 第123批 · W6 重启验收 Checklist

> 用途：装库（face_recognition 1.3.0 + face_recognition_models 0.3.0，T-122a 已完成）后**第一次重启**的逐项验收。
> W6 = 装库后首启，要验证 `人脸识别=有` 翻转（boot#6 是装库前起的，故本次为装库后首次）。
> 模式：框架已停→改代码后重启生效。重启前确认：DAL 已止血、6 补丁已冻结（不会自动应用）、C2 已纯接线（零行为变化）。
> 判据口径：每条 ✅ = 达到"判定标准"；❌ = 不满足，按"失败处置"处理，必要时回退（见末节）。

---

## 〇、重启前确认（先勾这 3 条再起）

- [ ] **DAL 止血已生效**：`pytest tests/test_data_access_layer.py` = 10 passed；文件无 BOM；`safe_read_json` 两守卫（`default={}` / `os.path.exists` 检查）在位。
- [ ] **6 补丁已冻结**：`data/patches/pending_patches.json` 中 6 条 `status=frozen` + `frozen=True` + `auto_released=False`；PatchManager 两处 fail-closed 门禁在位 → 重启 `apply_all_pending(only_approved=True)` 不会应用它们。
- [ ] **C2 纯接线无副作用**：`conflict_count` 现网恒 0 → `l3_downgraded=0`、不降级（T-123d）。

> 重启命令沿用既有启动方式（main.py 入口）。注意 T-122a 已知：1.3.0 wheel `__version__` 误标 1.2.3、0.3.0 模型路径改 `*_location` 函数且无 `PREDICTOR_PATHS`——冒烟核验命令需按 122 批偏差②修正，勿用旧 `PREDICTOR_PATHS` 路径断言。

---

## 一、T+0（启动后立即可查）

### 1. 人脸识别 = 有
- **判定标准**：boot 日志出现 `人脸识别=有`（或等价 capability 标记 `有`），且 face_recognition 可 import、models 路径可用。
- **核验方法**：
  - `grep -rn "人脸识别" logs/ | tail -20`（找本次 boot 段）
  - 进程内核验：`python -c "import face_recognition, face_recognition_models; print(face_recognition.__version__)"`（预期能 import；版本字符串可能误标 1.2.3，属已知，不影响能力）
  - 模型路径用 `*_location()` 函数核验（非 `PREDICTOR_PATHS`）
- **失败处置**：若仍 `无` → 先查 import 报错 / 模型路径；对照 T-119a 装库前置硬化（VC SystemExit 4 处 + 白名单守卫，脏键"用户/访客/小林"禁止入册）。勿在 W6 现场改代码，回退到 boot#6 状态。

### 2. boot 完整率 7/7
- **判定标准**：七个核心器官 boot 全部成功，完整率 = 7/7（无器官 boot 失败）。
- **核验方法**：
  - `grep -rn "boot完整率" logs/ | tail -5` → 期望 `7/7`
  - 或逐器官 boot 成功计数 = 7（对照 R4 B2/B3；122 批 B3 = boot 完整率 7/7/0）
- **失败处置**：若 < 7 → 查失败器官 boot 堆栈；对照 R4 总单 B2/B3。

### 3. Parquet 两键生效
- **判定标准**：冷存 Parquet 列含 `conflict_count` 与 `last_conflict_at` 两键；load 回读该两键非缺失（T-122b）。
- **核验方法**：
  - 重启后触发一次冷存落盘 / 读取，检查 Parquet schema / 回读字段存在性
  - 对照 122 批 `第122批_PulseSnapshot_DIFF.md`（`_nodes_to_parquet_columns` / `_parquet_row_to_dict` 各 +3 行搬 conflict_count / last_conflict_at）
- **失败处置**：若两键缺失 → 查 PulseSnapshot 序列化路径是否启用 Parquet（T-122b T3/T4/T5 不动，仅搬运）；核对 `ENABLE_*PARQUET*` 开关语义（T-122b 统一过）。

---

## 二、T+5min（启动后 5 分钟滑窗）

### 4. ERROR 滑窗平稳
- **判定标准**：启动后 5 分钟内 ERROR 级日志无新增异常堆栈（除已知良性项）。
- **核验方法**：
  - `grep -c " ERROR " logs/<本次boot日志>` 启动段 vs 同窗口 5min 后计数对比
  - 注意 D017 日志轮转自感知已处理 rollover 噪声（初始化日志对 `rollover` 走 INFO，不报外部截断 WARNING）
- **失败处置**：若 ERROR 暴涨 → 提取堆栈，按模块定位；优先排除 DAL 止血回归（safe_read_json/safe_write_json）。

### 5. RSS 增量合理
- **判定标准**：进程常驻 RSS 5 分钟增量在合理范围（无内存泄漏/暴涨；参考历史基线，建议 < 500MB，具体以历次 boot 基线为准）。
- **核验方法**：
  - 启动后 0min / 5min 各采一次 RSS（`psutil` 或任务管理器 / `grep VmRSS /proc/<pid>/status`）
  - 对照 107 批无人值守内存/句柄泄漏治理基线
- **失败处置**：若增量异常 → 查启动期一次性加载是否异常（如快照全量读入、向量重建）。

### 6. presence 事件复现
- **判定标准**：presence / SWITCHED 事件在启动后复现（T-118a face R2 层1 急救：小林→访客）。
- **核验方法**：
  - `grep -rn "SWITCHED\|presence\|current_user=访客" logs/ | tail`
  - 期望出现 `SWITCHED(current_user=访客, conf=0.95)` 形态事件（不依赖装库，presence 触发即 emit）
- **失败处置**：若 absence → 查 PulseSelfAwareness._on_user_presence 是否随 R4 重启生效（需 R4 重启或 presence 触发方显效）。

---

## 三、T+1h（启动后 1 小时）

### 7. 影子行（tracer convoy）归零
- **判定标准**：tracer 影子行 / convoy 计数 1h 内 = 0（R4 B24 顺延验收）。
- **核验方法**：
  - 查 tracer 输出 / 影子行日志计数（对照 117 批 tracer 原子闸 `_claim_flush_gate()` + PulseTracerFlusher daemon）
  - `grep -rn "convoy\|影子行" logs/ | wc -l`（期望 0 或仅历史残留）
- **失败处置**：若 > 0 → 查 flush 是否仍竞争；确认 117 批头判尾更原子占闸生效、atexit 终刷在位。

### 8. face `_fc` 计数正常
- **判定标准**：face 模块 `_fc` 计数（人脸识别相关计数器）正常累加/稳定，无异常归零或暴涨。
- **核验方法**：
  - 查 face 模块 `_fc` 计数日志 / 状态字段，对照 W6 前基线
  - 与指标 1（人脸识别=有）联动：能力翻转后 `_fc` 应进入可用计数路径
- **失败处置**：若异常 → 查 face 模块计数初始化（T-118a 层1 急救默认"访客"）与 `_fc` 写入点。

---

## 四、验收结论勾选

- [ ] T+0 全过（人脸识别=有 / boot 7/7 / Parquet 两键）
- [ ] T+5min 全过（ERROR 滑窗 / RSS 增量 / presence 复现）
- [ ] T+1h 全过（影子行 / _fc 计数）
- [ ] W6 实证通过 → 可推进 124 批（D040 C2 实际降级执行 `_l3_fuse_record()`）

## 五、失败回退（兜底）

- DAL 回归：`copy /Y .bak_batch123\nucleus\data\DataAccessLayer.py nucleus\data\DataAccessLayer.py`
- C2 回归：`copy /Y .bak_batch123\nucleus\mnemosyne\PulseNodePool.py nucleus\mnemosyne\PulseNodePool.py`
- 补丁队列回退：`copy /Y .bak_batch123\data\patches\pending_patches.json data\patches\pending_patches.json`
- 装库回退（仅当人脸识别相关硬故障）：`pip uninstall -y face_recognition face_recognition_models`（回退到 boot#6 装库前状态，即"人脸识别=无"）

> 回退后重启即回到本批止血前可运行态；W6 翻转不强行追求，可留待后续批次在稳定基线再验。
