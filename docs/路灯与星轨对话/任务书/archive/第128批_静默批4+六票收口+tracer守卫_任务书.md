# 第128批 任务书：静默except批4 + 六票账面收口 + tracer守卫

> 派发：星轨　｜　执行：路灯　｜　前置分析：烛微第128批
> 模式：框架已停，改代码后重启生效
> 红线：不改config.py运行开关、不碰data/knowledge/、改前备份

---

## 0. 本批定位

**W6前最后一批代码改动。**
本批三个方向：静默except批4（收口16员）+ 六票账面收口（6×rejected）+ tracer守卫顺带手。

---

## 1. T-128a（P1）：静默except批4（收口16员）

### 背景
批3已改完，AST口径472。本批改16员，期望472→456。

### 范围（16员）
- PulseLiver长尾（:3622，行号已漂）
- InnerWorld
- 其他杂项
- 具体名单见烛微dz128重锚表

### 验收
- AST口径：472→456（减16）
- CI门禁PASS（新增=0）
- py_compile过
- m95单测39 passed + 4 skipped

---

## 2. T-128b（P1）：六票账面收口（6×rejected）

### 背景
冻结的6条LLM补丁，烛微终裁：0放行/6否决。
本批改账面状态，不改代码。

### 6条补丁处置
| 票 | 目标 | 判定 | 处置 |
|---|---|---|---|
| ① | PulseLiver._get_background_tempo | 否决 | frozen→rejected |
| ② | pulse_tracer.flush_to_file | 否决（守卫已由117批覆盖） | frozen→rejected |
| ③ | PulseLiver._m70_get_node | 否决 | frozen→rejected |
| ④ | PulseSystemManager.on_pulse | 否决（含未声明行为删改） | frozen→rejected |
| ⑤ | PulseSystemManager._check_and_repair | 否决 | frozen→rejected |
| ⑥ | PulseLiver._count_nodes | 否决 | frozen→rejected |

### 验收
- pending_patches.json里6条全部status=rejected
- 备注栏写明否决理由

---

## 3. T-128c（P1）：tracer守卫顺带手

### 背景
烛微发现pulse_tracer.py new_orphans侧3行isinstance守卫缺失（:221-222现读裸o['timestamp']）。

### 改动
- 加isinstance守卫
- 防KeyError/TypeError

### 验收
- py_compile过
- 不影响现有行为

---

## 4. T-128d（P2）：git commit第14票

### 背景
本批改动commit入库。

### commit message
`第128批 静默except批4(16员) + 六票账面收口 + tracer守卫 交付（T-128a/b/c）`

---

## 5. 门禁要求

| 门禁 | 标准 |
|---|---|
| ruff F | =0 |
| py_compile | 改动文件全过 |
| m95单测 | 39 passed + 4 skipped |
| 静默except CI | PASS（新增=0） |
| 行尾保全 | .py CRLF不变 |

---

## 6. 交付要求

1. 静默except批4 DIFF
2. 六票收口记录
3. tracer守卫DIFF
4. git commit记录
5. 门禁结果

---

*星轨 · 2026-09-25 · 第128批*
