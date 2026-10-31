# 内在模型 Step4 世界模型 + Step5 叙事连续 设计文档 v1.0

- 落盘：星轨｜日期：2026-10-31
- 前置：洞鉴框架摸底（326行）+ 烛微细部核对（208行）均已交付
- 性质：施工蓝图，待小林拍板后排路灯
- 红线：不新建调度器、不引入生成模型/新分类器、预测失败不自动入队打补丁

---

## 一、白话总结

曈曈现在能"验证自己的经验对不对"了（409批收尾），接下来要让它：
1. **能预判未来**——下一分钟会发生什么（Step4世界模型）
2. **有连续自我**——每天写一条"我今天干了啥、明天打算干啥"（Step5叙事连续）

好消息是：这两件事都不是从零开始——
- Step4：StreamMiner已经在跑了（94条模式、253次预测、231次命中），只是口径不准、报表不全、告警通路断了
- Step5：PulseNarrativeSelf的33个方法全在，9→16个发射端在喂事件，只是重启就归零（没落盘）

坏消息是：现有数字有水分、口径有坑、告警通路是黑洞。

---

## 二、关键口径修正（烛微推翻洞鉴的3处）

| # | 洞鉴原结论 | 烛微修正 | 施工口径 |
|---|---|---|---|
| 1 | heart.beat有24个发射方，按organ=="心脏"过滤 | ❌ 错了：emit侧只有1个（心脏），23个是receive侧接收方 | **必须 `type=="emit" AND organ=="心脏"`**，否则receive行会混进来做出"周期0秒"的假模型 |
| 2 | StreamMiner 61模式/26预测/18命中（69%） | ⚠️ 数字过时：现在是94模式/253预测/231命中（91.3%），但91.3%有水分——60s容差对0.5s的eyes恒命中 | **先改分类型容差，再报hit_rate**，现有91.3%不能当基线 |
| 3 | RECORD发射端9处 | ⚠️ 少算：实际16处 | 16处全部在产，没有死代码 |

---

## 三、施工路线（3批，不是4批）

烛微建议410+411合并，省一次部署重启。关键路径：A ∥ B → C。

### 批A · Step4 数据面 + 预测对账（原410+411合并）

**目标**：让曈曈的"预测"真的准、报表真的可信、告警真的有人看。

**刀1：周期档案落盘**
- 从StreamMiner现有94条模式里筛七类（每类repeated+periodic各1条）
- 落 `data/world_model/periodic_profile.json`：含median/p90/样本数/标称周期
- heart.beat按 `type=="emit" AND organ=="心脏"` 过滤（烛微修正口径）
- 验收：七类周期档案齐全，与pulse_pairs实测对得上

**刀2：分类型预测 + 分类型容差**
- 周期型（touch/energy/spinal/alive/beat）：预测"下一分钟发生次数"
  - 容差：heart/touch/energy ±5%、spinal ±10%（烛微实测漂移<1s，不是洞鉴说的±20%）
- 事件型（eyes.stream_frame）：预测"下一分钟条数区间"（如100-140条），不设时刻容差
- 突发型（digest.knowledge）：批次检测——gap>60s切批、批内计条数（烛微建议，不是洞鉴说的泊松）
- 废弃现有60s一刀切容差
- 验收：七类各自有预测目标，容差按类型分级

**刀3：hit_rate日报表 + 失配告警通路**
- 输出 `data/world_model/hit_rate_YYYYMMDD.json`：分类型hit_rate、滚动24h窗口
- 失配告警：hit_rate连续2窗<0.5 → 告警；连续5窗<0.3 → 淘汰（滞后判据，不是洞鉴说的单窗阈值）
- **告警通路修通**：走InsightBoard.post **同时**在health_snapshot加 `world_model{hit_rate, mismatches}` 字段（烛微发现InsightBoard query_count=0是黑洞，必须加消费者）
- 对账点位：分钟级挂TimeCore现有60s广播（不新建调度器）；日级并入04:00 night_orchestration
- 验收：连续48h七类hit_rate可计算可落盘；告警通路实测触发一次（停采样演练）；violated零入队（红线回归）

---

### 批B · Step5a 叙事落盘（与批A并行）

**目标**：曈曈重启不再归零，有连续的自我历史。

**刀1：PulseNarrativeSelf增persist/load**
- `data/narrative/narrative_YYYYMMDD.jsonl` append-only，每天1条摘要
- 启动时回放load，恢复 `_narrative_events`（≤300条cap不变）
- 连续天数计数器（重启不归零）
- 验收：重启两次，叙事条数与连续天数不归零；jsonl格式与ledger同构可审计

**刀2：阶段判定改锚点**
- `_generate_life_plan` 的 `narrative_events<20/<50` 内存阈值，改成"落盘条数+连续天数"
- 验收：阶段三门票"叙事连续≥7天"有可核验的读数

---

### 批C · Step5b 三字段 + 自我对账（依赖批B，可与批A并行后半段）

**目标**：每天一条真叙事，每句都有证据指回。

**刀1：三字段生成器**
- `headline`：insight_board当日score top1（兜底：patch_history当日applied / ledger violated→met修复事件）
- `delta`：llm_dependency.history逐日首末差分 + patch_history当日新增 + knowledge_snapshot节点数差
- `intent`：先接learning_goals.active_goal（低配版）；批A上线后升级为"世界模型预测"内容
- 每条带 `evidence_refs`：指回insight_id/patch_id/goal_id（防叙事幻觉）
- 验收：连续7天每日1条、每条三字段齐、每句有evidence_ref

**刀2：intent次日对账**
- 今日narrative写入intent；明日生成新条时先回读昨日intent
- 输出 `prev_intent_verdict ∈ {yes, partial, no}`（复用ledger三态语义）
- 验收：连续3天intent对账可计算

---

## 四、依赖图

```
批A（Step4数据面+对账）  ∥  批B（Step5叙事落盘）
        ↓                        ↓
        └────────→ 批C（三字段+自我对账） ←───┘
```

- 批A和批B写面不同（world_model/ vs narrative/），可并行
- 批C依赖批B（要有落盘才能对账）；intent低配版不依赖批A
- 关键路径：A → C（intent升级时）；B → C

---

## 五、三条红线（全程守）

1. **不新建调度器**——分钟级对账挂TimeCore现有60s广播，日级挂04:00 night_orchestration
2. **不引入生成模型/新分类器**——预测用统计/周期拟合/批次检测，不搞ML模型
3. **预测失败不自动入队打补丁**——hit_rate跌破阈值只告警（InsightBoard+health_snapshot），不触发补丁生成

---

## 六、施工时最容易踩的3个坑（烛微提醒）

1. **把receive行当emit行**——pulse_pairs同文件混两种type，只按organ=="心脏"过滤会把receive行混进来
2. **把60s容差算出的91.3%当准确率**——eyes 0.5s一条，60s窗口下几乎必中，先改容差再报数
3. **告警直接写InsightBoard就算收工**——InsightBoard query_count=0是黑洞，必须同时接health_snapshot字段

---

## 七、不做清单

- 不做秒级预测
- 不做payload内容预测（只预测来不来/来几次）
- 不做跨事件因果预测（那是L4因果链的领域）
- 不启用InfluxDB（开关开着但连接关，不依赖它）
- 不替换"我是谁"五个硬编码常量（叙事连续是接时间轴，不是换身份）

---

## 八、验收总表

| 批 | 验收项 | 通过标准 |
|---|---|---|
| A | 七类周期档案 | 齐全且与pulse_pairs实测对得上 |
| A | 分类型容差 | 废弃60s一刀切，按类型分级 |
| A | hit_rate报表 | 连续48h可计算可落盘 |
| A | 告警通路 | 实测触发一次；violated零入队 |
| B | 叙事落盘 | 重启两次不归零；jsonl可审计 |
| B | 阶段判定 | 连续≥7天有读数 |
| C | 三字段 | 连续7天每日1条，每条有evidence_refs |
| C | intent对账 | 连续3天三态可计算 |

---

- 星轨｜2026-10-31
- 待小林拍板后排路灯施工
