# -*- coding: utf-8 -*-
"""385 刀2：DL3 决策层（Step 1 —— 影子账本，只写不判）。

## 为什么叫 `DL3` 而不是 `L3`

InfoField 已有一个 **L3 脉冲层**（L0–L3 队列 / 背压 / 降级）。
此 L3 是**决策层**。撞名会让日志与代码检索持续互误（洞鉴 385 第 6 条明写），
故本层统一用 `DL3` / `dec_` 前缀。

## 本批范围（Step 1）

只有「契约 + 账本 + 三个只读影子调用点」，**不产出可执行指令**：
所有结果 `shadow=True`，`action` 默认 `DEFER`。执行器必须忽略。

## 用法

    from nucleus.decision import shadow
    shadow.observe_patch(patch_id="o3_...", file="a.py", status="pending")
    shadow.stats()      # {"enabled": False, "calls": {...}, "written": {...}, "p99_ms": 0.0}

开关 `config.ENABLE_DEC_SHADOW`（默认关）。
"""
from nucleus.decision.contracts import (  # ★421/R1-B2：__all__ 覆盖再导出，noqa 已失效
    ACTIONS, CONTRACT_SCHEMA, DEGRADE_POLICIES, POLICY_VERSION, REASON_CODES,
    SCOPES, DecideResult, DecisionRequest, make_decision_id, make_request_id,
    shadow_result,
)

__all__ = [
    "ACTIONS", "CONTRACT_SCHEMA", "DEGRADE_POLICIES", "POLICY_VERSION",
    "REASON_CODES", "SCOPES", "DecideResult", "DecisionRequest",
    "make_decision_id", "make_request_id", "shadow_result",
]
