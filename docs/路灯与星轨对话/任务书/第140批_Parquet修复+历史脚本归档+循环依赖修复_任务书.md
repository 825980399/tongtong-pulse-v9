# 第140批 路灯任务书

> 生成：星轨 · 2026-09-27
> 上一批：第139批（退出bug修复 + IW第二刀拆分，破2万行）

---

## 任务列表

### T-140a 修复Parquet快照保存bug（P2）
**问题**：启动时报错`type object 'Table' has no attribute 'from_pylist'`，Parquet保存失败
**原因**：pyarrow版本兼容问题，API变了

**施工内容**：
1. 找到PulseSnapshot.py里用`Table.from_pylist`的地方
2. 改成兼容当前pyarrow版本的写法
3. 测试：重启后Parquet能正常保存

---

### T-140b 归档tools/历史一次性脚本（P3）
**目标**：把烛微盘点出来的31个历史一次性脚本归档，不删，移到tools/archive/目录

**施工内容**：
1. 把这31个一次性脚本（都是历史批次的验收/修数脚本）移到tools/archive/
2. 保留4个特殊的（setup_local_databases、check_encoding_hygiene等）
3. 测试：CI门禁还能正常跑

---

### T-140c 修复三个循环依赖（P1，18行代码超简单）
**目标**：把三个循环依赖的红账清零

**施工内容**：
1. 环3：把SafeEvolutionExecutor.py里那一行模块级import挪到函数里，3行代码
2. 环1、环2：self_inspector的C1检测器加边层级分账，只报模块级的环，函数级的记观察账，15行代码
3. 测试：C1检测器现在报0个红环

---

### T-140d 补全requirements.txt漏的依赖（P2）
**问题**：requirements.txt漏了两个核心依赖，换机器装会报错

**施工内容**：
1. requirements.txt加上`cryptography>=41.0.0`（人脸加密用）
2. 加上`faiss-cpu>=1.7.4`（向量检索用）
3. 测试：pip install -r requirements.txt能正常装

---

## 验收标准

1. Parquet快照能正常保存，不再报错
2. tools目录清爽了，31个历史脚本归档
3. 三个循环依赖红账清零
4. requirements.txt补全，依赖声明完整
5. 三道门禁全过：ruff F=0 / py_compile / pytest核心测试

---

*星轨 · 第140批任务书 · 2026-09-27*
