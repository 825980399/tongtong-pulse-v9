# gate_health_check.py 首跑复核操作指引 · 第217批

> 适用前提：216 批新建的 `tools/gate_health_check.py`（八项指标巡检）已就位，
> 但截至 216 交付时曈曈仍处于停摆/旧格式窗——首跑复核（M1/M7 转正、R 现值、差集）
> 必须等用户启动一个**曈曈重启窗（≥10min）**后才有真实数据可查。
> 本指引即为此窗口的操作手册。

---

## 0. 为什么需要「重启窗」

`gate_health_check.py` 的 M1/M7 直接读 `data/monitor/pulse_pairs_YYYYMMDD.jsonl` 的**新格式 pair 行**
（含 `seq` / `target_organ` / `amplitude` 三字段，由 215 批 `pulse_tracer` 补字段落地）。

- 停摆/旧格式窗：pair 行全是老格式（缺 `seq`/`target_organ`），M1/M7 退化为 `n/a`（WARN）。
- 重启窗：新格式 pair 行开始累积，M1 才能算「区间密度」、M7 才能算「字段完备率」→ 转正为 `PASS`。

**结论：没重启 = 没数据 = 首跑复核无意义。先起窗，再跑工具。**

---

## 1. 启动曈曈重启窗（≥10min）

- 由用户在运行环境重启曈曈进程（或等待其自然进入新运行窗）。
- 窗口**至少 10 分钟**，让 `pulse_tracer` 落够新格式 pair 行（215 批 cap 修复后按日分文件、
  单文件上限 20000 行；首窗一般远未触顶，M2 大概率仍为 WARN，属预期，勿判回归）。
- 窗口内保持进程在线，不要中途停框架。

---

## 2. 运行工具（首跑复核）

在项目根目录执行（生产路径为工具默认值，无需额外参数）：

```bash
cd D:/xinrenlei/tongtong-pulse-v9
python tools/gate_health_check.py
```

如需指定日期/源或机器可读输出，可用覆写参数（216 批已实现）：

```bash
python tools/gate_health_check.py --date 20261014 --json
# --pairs-file <path>   覆写 pair jsonl
# --log-file <path>     覆写 pulse.log（默认 logs/pulse.log）
# --monitor-dir <path>  覆写 monitor 目录
# --pair-cap <int>      覆写 PAIR 行数上限
# --heartbeat-nominal <float>  覆写心跳标称秒（默认 13.5）
# --json                额外输出 JSON（退出码语义不变）
```

退出码：任一指标 `FAIL` → 退出码非 0；全 `PASS/WARN` → 0。首跑重点关注 stdout 的八行指标表。

---

## 3. 检查 M1_PAIR_COVERAGE 转正（n/a → PASS）

- 旧格式窗输出：`M1_PAIR_COVERAGE  n/a  [WARN]`（seq 缺失，区间密度无法计算）。
- 重启窗期望：`M1_PAIR_COVERAGE  <pct>%  [PASS]`（pct ≥ `COVERAGE_PASS_RATIO=0.95`，
  算法＝`present/(max(seq)-min(seq)+1)`，反映 seq 缺漏程度）。
- **判定**：出现 `PASS` 即证明新格式 pair 行已带连续 `seq` → 窗口生效。仍 `n/a` 说明窗口未起或
  tracer 未落新行，回到第 1 步。

---

## 4. 检查 M7_PAIR_FIELD_COMPLETE 转正（n/a → PASS）

- 旧格式窗输出：`M7_PAIR_FIELD_COMPLETE  n/a  [WARN]`（行无 `target_organ`/`amplitude`）。
- 重启窗期望：`M7_PAIR_FIELD_COMPLETE  <k>/<n>  [PASS]`（k = 含 `{seq,target_organ,amplitude}`
  三字段的新行数，n = 新行总数；填充率=0 时工具判定「闸开无人声明」，属已知 WARN，见附注②）。
- **判定**：出现 `PASS`/`k/n` 即证明三字段已随 tracer 落地。仍 `n/a` 同第 3 步排查。

---

## 5. 计算 R_runtime（distinct{(organ, target_organ)}）

R_runtime 是「运行态真实声明边」的真值，由新格式 pair 行直接投影：

```text
R_runtime = distinct{ (row["organ"], row["target_organ"]) | row ∈ 新格式 pair 行, target_organ 非空 }
```

- 工具 M7 已核字段完备性、M1 已备 seq 区间密度；R 本体即两行集合操作，可借巡检附带出数
  （或在窗口末用 `pulse_pairs_YYYYMMDD.jsonl` 单独 `awk`/`python` 投影）。
- **口径刚性**：必须与 E_proj 同口径才可比——E_proj 是「静态源码派生边」，R_runtime 是
  「运行态真实声明边」，二者差集即「源码发得出、运行态却没声明 target_organ」的边。

---

## 6. 差集导出（E_proj − R_runtime）

名单已见于 `docs/分析报告/图谱边集预名单_20261014.md`（217 批刀1 产出，E_proj 已去 wildcard，92 个边对）。

差集计算（纯集合差，无需新工具）：

```text
待补声明名单 = E_proj − R_runtime
  即：E_proj 中「(pre_organ, post_organ) 边对」在 R_runtime 里找不到对应真实声明的那些
```

- 逐行已附 `via_event_types[]`（连接该边对的事件类型）与 `graph_edge_refs[]`（静态边序号），
  便于回归剔除与定位 emit 调用点。
- 差集送星轨复核：补声明落地（在对应器官 `emit` 调用点加 `target_organ="<订阅器官>"` kwarg）
  **属 218 批**，本批只导出、不落地（自愿声明制下「替器官声明」是语义决策，越权红线）。

---

## 7. 三谜裁决（双路分叉判定）

烛微 217 前置分析给出「三谜」最强候选解释＝**双路分叉**：
器官经 `pulse_core.emit(...)` 显式调用（带 `target_organ`）进 wire 与 tracer；
而 `_send` 兜底构建路（`BasePulseOrgan`）产的脉冲 `target` 恒空——两路流量比同时决定
「填充率 100% / real=44+simplified=21 / 静态零显式声明」三个数字，互不矛盾。

**裁决手法（新窗 pair 行按 `(organ, target 非空与否)` 分路统计，10 分钟内出结论）**：

| 分路 | 含义 | 裁决含义 |
|---|---|---|
| A：`target_organ` 非空行 | emit 显式声明路真实在跑 | 双路分叉坐实（显式路有流量） |
| B：`target_organ` 空行 | `_send` 兜底路（target 恒空） | 兜底路占比 → 决定三个数字配比 |
| A/B 流量比 | 两路相对规模 | 比差大 → 双路分叉确证；比趋零 → 走次候选（tracer 统计体 truthiness 缺陷） |

- 把新窗 pair 行按上表分两路计数，记录 A 行数 / B 行数 / 占比。
- **判据**：B 行存在且 A 行 > 0 → 双路分叉成立（三个「谜」在双路模型下自洽）。
- 若 A 行恒为 0（显式路无流量）→ 退回次候选，单列请星轨裁决，不在本批强行定性。

---

## 附注（217 窗已知项，勿误判回归）

1. **M2_PAIR_SIZE 首窗大概率 WARN**：215 批 cap 修复未随框架重启触发裁剪，旧行 79,494 仍 > 20000
   上限 → 首窗 M2 仍 `WARN` 属预期；待下一窗口裁至 20000 内转 `PASS`，非回归。
2. **M7 填充率=0 的 WARN**：若窗口内 `target_organ` 声明率为 0（闸开但无人声明），M7 报 WARN 属已知，
   不判回归；转正看的是「字段完备率 k/n」能否算出（非 n/a），而非填充率必须 >0。
3. **M5 首跑 total=383 / pair=15**：pair 通道留痕 15 行是旧窗遗存，217 窗复核时 >0 即 WARN 属已知，
   勿慌勿判回归。
4. **M6 心跳 12.72s / 偏移 5.8%**：在 10% 容差内，是负载自适应基线漂移的常态读数，仅供长期对照。
5. **M8 起停计数差**：首跑 `stop=10/start=4` 的不对称未被现规则捕捉（现规则只判「零启动/零装配」）；
   起停计数差 ≥2 → 建议后续工具加 WARN（提需求，不代排）。

---

## 交付物索引

- 本指引：`docs/分析报告/gate_health_check首跑复核操作指引_20261014.md`（217 批刀2）
- E_proj 预名单：`docs/分析报告/图谱边集预名单_20261014.md`（217 批刀1）
- 巡检工具：`tools/gate_health_check.py`（216 批新建，本批不改动）
- 218 批：差集经星轨复核后补声明落地 + real≥50 终验

*路灯 · 217批刀2 · 首跑复核操作指引 · 2026-10-14 · 不commit（由星轨统一commit）*
