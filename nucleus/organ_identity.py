# -*- coding: utf-8 -*-
"""nucleus/organ_identity.py —— 器官名归一化（★相关任务）。

把来自不同命名空间的器官名统一映射到规范 key：
- emit 时的 source_organ（可能带后缀变体，如 "Web对话-人脸监听"）
- 子监听器注册名（如 "企业微信桥接器-回复监听"）
- 沉默告警豁免表裸名（如 "Web对话" / "企业微信" / "TimeCore"）

规范 key 是单源真相（ORGAN_ALIASES）。PulseMetricsCollector._LINK_ALIASES 已
被提升为此表并扩充（覆盖豁免表命名空间）；消费方（PulseBloodVessel / PulseSystemManager）
比对时统一走 resolve_organ_key，避免「第 5 个裸名出现必再犯」的对不齐问题。

设计：
- resolve_organ_key 为纯函数（无副作用、不抛异常），易于单测；
- 大小写/空白不敏感；命中别名表即返回规范 key；
- 未登记名返回小写原文，保证调用方可区分「已知豁免」与「未知器官」，且大小写不敏感比对。
"""
from typing import Dict, FrozenSet, Tuple

#: 规范 key -> 别名元组（单源真相，全部按小写匹配）。
#: 新增/重命名器官时，在此处同时登记规范 key 与所有变体即可，消费方无需改动。
ORGAN_ALIASES: Dict[str, Tuple[str, ...]] = {
    "对话模块": (
        "对话模块", "web对话", "网页对话", "chat_service",
        "对话服务", "企业微信", "企微", "wecom", "微信",
        "mouth", "嘴巴", "Web对话", "Web对话-人脸监听",
        "企业微信桥接器-回复监听",
    ),
    "视觉皮层": ("视觉皮层", "visualcortex", "visual_cortex", "眼睛", "eyes", "eye"),
    "嘴巴": ("嘴巴", "mouth"),
    "眼睛": ("眼睛", "eyes", "eye"),
    "时间中枢": ("TimeCore", "时间中枢", "timecore"),
    "FunctionLoader": ("FunctionLoader", "functionloader"),
    "framework_self_modify_gate": ("framework_self_modify_gate",),
}

#: 小写别名 -> 规范 key（模块加载时构建一次）
_ALIAS_INDEX: Dict[str, str] = {}
for _key, _aliases in ORGAN_ALIASES.items():
    _ALIAS_INDEX[_key.lower()] = _key
    for _a in _aliases:
        _ALIAS_INDEX[str(_a).lower()] = _key


def resolve_organ_key(name: str) -> str:
    """把任意器官名/别名归一为规范 key；未知则返回小写原文（纯函数，不抛异常）。"""
    if not name:
        return ""
    _n = str(name).strip().lower()
    return _ALIAS_INDEX.get(_n, _n)


def resolve_organ_keys(names) -> FrozenSet[str]:
    """批量归一（用于把豁免表转成规范 key 集合）。"""
    return frozenset(resolve_organ_key(_n) for _n in (names or ()))
