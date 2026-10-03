# 第158批 上-A · T-自我审计-2 收尾交付报告（true_idle 归位细分）

> 任务书：`第158批_上-A_路灯后续任务推进_20261003.md` § 一 ｜ 代码提交：`efb8cbc`
> 性质：细分层收尾（**判据字段与优先级未动**）

## 一、施工

在现有账本上加**二级归位细分层**（`capability_ledger.py`，+55/-2）：

1. `anomalies` 非空且**全部** `suggested_action=="log_only"` → 「存档型-仅记录意图」（子类 `log_only_intent`）
2. `anomalies` 为空 且 `generator ∈ PERIODIC_GENERATORS`（`DailyScheduler.run_once` / `SelfAwarenessEngine.generate_report`）→「观测型-周期画像」（子类 `periodic_profile`）
3. 两者皆非且无消费 → 才记 `true_idle`（真空转）

新增 `subclass_of()` 与 `reconcile_reports` 的 `by_subclass` 统计；**五类守恒不变**（子类只是 `archived` 内的细分层）。

## 二、实测：true_idle 29.7% → 4.6%

| 归位 | 修正前 | 修正后 |
|---|---|---|
| consumed | 147 | 147 |
| archived | 137 | **246**（并入细分） |
| design_declined | 22 | 22 |
| event | 0 | 0 |
| **true_idle** | **129（29.7%）** | **20（4.6%）** |
| 守恒 | 435=435 | 435=435 ✅ |

**子类细分**：consumed 147 / archived 137（路径归档）/ **periodic_profile 106** / design_declined 22 / **log_only_intent 3** / true_idle 20

## 三、★残留 20 份如实上报（未臆造归零）

- **17 份**全部是 `generator=guard_probe, report_type=runtime` —— 门禁探针产物，设计上不追求被消费；
- **3 份** JSON 不可解析。

按裁定第 3 条「若出现无法归类的残留，如实上报样本，不臆造规则」，我**没有**把 `guard_probe` 硬塞进周期画像类。如需进一步归零，请裁定两点：

1. `guard_probe` 是否属「设计性不消费」，可并入观测型？
2. 不可解析的 3 份是否应单列「不可解析」类（不计真空转）？

## 四、门禁

ruff F / cw2 / arity / broken-chain 全 PASS；纯 CRLF（loneLF=0）。

## 五、施工包状态声明行

`158上-A施工包状态：O-A2[✅cf50547] / O-A1[✅dbe7bb4] / T-审计-2[✅a690d76+小修3770f54+收尾efb8cbc・残留20份待裁定] / T-审计-5[✅244b2e1] / T-审计-6[✅70b7abf] / T-登记册并发防护-1[✅74bb8a2] / T-唯一口径件-1[进行中] / T-审计-1[待] / T-审计-4[待] / 77.9s[待] / _create_organ退役[待] / T-进化验证率-1[⏸已改排・与P2-65同期] / P2-65[⏸改排]`

> 纪律：逐刀独立提交、精确暂存（`git commit -F -- <路径>`）、未 push（铁律113）、匿名 `Tongtong Dev`、PII 0、门禁全 PASS。
