# -*- coding: utf-8 -*-
"""493批刀1：统一 Self-Index JSON Schema（自主语义理解层 · 第一阶段底座）。

本模块只定义「曈曈身体结构」的统一导出数据结构——**纯函数、零行为、不修改任何
现有器官逻辑 / 应答路径**。所有字段由 exporters（493-2/3/4）从运行态 / 代码 / 静态表
**动态读取**，不在此硬编码任何实体信息。

数据层级（参照烛微 493 前置分析 §11.8「Self-Index 把身体变成可检索的索引」）：
  · organs         器官清单（name / 代码路径 / 功能 / 队列深度 / 内存 / 线程）
  · events         事件注册表（事件名 / 发布器官 / 订阅者 / 平均扇出 / 典型延迟）
  · intents        意图→方法映射（意图 / 处理 method / 示例问句 / 命中率 / 本地 or LLM）
  · body_relations 器官连接关系（谁订阅谁 / 谁调用谁）

纪律（任务书 §纪律）：
  1. 只读导出 + 查询，不修改现有器官逻辑、不改变现有应答路径；
  2. 每个 JSON 导出带时间戳（内容 `generated_at` 字段），路径不写死（相对 `data/self_index`）；
  3. 所有字段动态读取，不许硬编码写死。
"""
from __future__ import annotations

import time

#: Self-Index 结构版本（换结构必须升版本，避免历史数据被静默重解释）
SELF_INDEX_VERSION = "493.self_index.v1"

#: 三个导出器产出的 kind 名（对应 data/self_index/{kind}.json）
SELF_INDEX_KIND = ("event_subscribers", "organs_runtime", "intent_map")


def _now_str() -> str:
    """当前本地时间字符串（generated_at 默认时间戳）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def build_self_index(organs, events, intents, body_relations,
                     generated_at: str | None = None) -> dict:
    """打包顶层 Self-Index（493-1 统一结构）。

    Args:
        organs/events/intents/body_relations: 各导出器产出的列表/字典。
        generated_at: 可选时间戳；缺省用当前本地时间。
    """
    return {
        "version": SELF_INDEX_VERSION,
        "generated_at": generated_at or _now_str(),
        "organs": list(organs) if organs is not None else [],
        "events": dict(events) if events is not None else {},
        "intents": list(intents) if intents is not None else [],
        "body_relations": list(body_relations) if body_relations is not None else [],
    }


def empty_self_index(generated_at: str | None = None) -> dict:
    """开关关闭 / 数据缺失时返回的空壳（保证下游可解析，不抛）。"""
    return build_self_index([], {}, [], [], generated_at=generated_at)
