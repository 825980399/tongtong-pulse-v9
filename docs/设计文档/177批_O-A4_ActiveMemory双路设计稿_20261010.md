# 177批 · O-A4 设计稿（Active Memory 双路）

> 状态：设计稿（不实装）｜ 来源：星轨 177批 任务书 刀9 ｜ 关联票：O-A4

## 一、目标
Active Memory 走双路：**确定性前置（免 LLM 凭证）** + **受限子代理（复用 evolution_worker + O-B1 策略面）**，按三态切换。

## 二、双路三态
| 态 | 触发 | 实现 |
|---|---|---|
| DETERMINISTIC | 命中确定性规则 | 免 LLM，直接凭证；落 `matched_rule_id` |
| SUBAGENT | 需推理但受限 | 复用 evolution_worker + O-B1 策略面 |
| NONE | 无匹配且无授权 | 不动作，留痕待裁决 |

## 三、确定性前置凭证设计
- 规则命中即返回 `matched_rule_id`，**不做 LLM 调用**（省成本、降延迟）。
- 规则表与 `tools/export_public.py` 白名单同面维护（声明式）。

## 四、受限子代理复用
- SUBAGENT 态复用 evolution_worker（见 O-B1+B6 隔离面），受 O-B1 三层配置面约束。
- 不开放消息工具、深度上限、廉价模型（沿用 O-B1+B6 隔离补全）。

## 五、停窗预算
纯设计，**0 停窗**；不改动 Active Memory 现有读写路径。

## 六、依赖
- 依赖 O-B1 键名 + evolution_worker 隔离面先落地。

## 七、不实装项
- 不实装规则匹配引擎改动。
- 不建新 config 键（仅设计）。
- 不接 LLM 调用（确定性路免 LLM 即本稿核心）。
