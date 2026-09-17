# -*- coding: utf-8 -*-
"""
ContradictionResolver.py —— 矛盾解决器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 矛盾检测后的解决与调和
机制: 基于ContradictionResolver类实现，包含8个核心方法
定位: 推理治理层
"""

from __future__ import annotations
import time

from typing import Any, Optional
from collections.abc import Callable


# 来源声誉基准表（模糊匹配 source_organ，命中越高优先级越高）
_SOURCE_REPUTATION = {
    "self": 0.95,        # 自我架构知识
    "inner_world": 0.90,  # 内在世界推导
    "cortex": 0.85,      # 大脑皮层
    "liver": 0.80,       # 肝脏归纳
    "推导": 0.80,
    "user": 0.70,        # 用户直接陈述
    "search": 0.60,      # 外部检索
    "外部": 0.60,
    "web": 0.60,
    "unknown": 0.50,
}

_STRATEGY_DEFAULT = "time"


class ContradictionResolver:
    """矛盾消解原语（纯静态方法，无状态）。"""

    # ------------------------------------------------------------------
    # 属性归一化
    # ------------------------------------------------------------------
    @staticmethod
    def _timestamp_of(node: Any) -> Optional[float]:
        """取节点时间戳（created_at / updated_at / timestamp / detected_at）。
        支持 datetime 或数值；无法解析返回 None。"""
        if node is None:
            return None
        for key in ("created_at", "updated_at", "timestamp", "detected_at"):
            v = getattr(node, key, None)
            if v is None:
                continue
            # datetime / time 对象
            ts = getattr(v, "timestamp", None)
            if callable(ts):
                try:
                    return float(ts())
                except Exception:
                    pass
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _source_rank(node: Any) -> float:
        """来源信任分值：优先取 source_trust 字段，否则按来源声誉表估计。"""
        if node is None:
            return 0.0
        st = getattr(node, "source_trust", None)
        if st is not None:
            try:
                return float(st)
            except (TypeError, ValueError):
                pass
        src = (str(getattr(node, "source_organ", "")) or "").lower()
        if not src:
            return 0.5
        for key, val in _SOURCE_REPUTATION.items():
            if key in src:
                return val
        return 0.5

    @staticmethod
    def _is_manual(node: Any) -> bool:
        return bool(getattr(node, "manual_authoritative", False))

    # ------------------------------------------------------------------
    # 单对消解
    # ------------------------------------------------------------------
    @staticmethod
    def resolve(node_a: Any, node_b: Any, strategy: str = _STRATEGY_DEFAULT) -> dict:
        """按策略对一对矛盾节点做裁决（包装层：调用 _resolve_inner 并预埋知识质量信号）。"""
        result = ContradictionResolver._resolve_inner(node_a, node_b, strategy)
        try:
            from nucleus.telemetry.phase18_signals import get_phase18_signals
            get_phase18_signals().record_knowledge_quality(
                "contradiction_resolve",
                resolved=bool(result.get("resolved", False)),
                strategy=str(result.get("strategy", strategy)),
                reason=str(result.get("reason", "")),
            )
        except Exception:
            pass
        return result

    @staticmethod
    def _resolve_inner(node_a: Any, node_b: Any, strategy: str = _STRATEGY_DEFAULT) -> dict:
        """按策略对一对矛盾节点做裁决（纯逻辑，无信号采集）。

        Returns:
            {
                "resolved": bool,            # 能否裁决
                "winner":   node | None,     # 胜出方
                "loser":    node | None,     # 落败方
                "strategy": str,
                "reason":   str,
            }
        无法裁决（时间/来源相同、无人工标记、节点缺失等）时 resolved=False。
        """
        empty = {"resolved": False, "winner": None, "loser": None,
                 "strategy": strategy, "reason": "未知"}
        if node_a is None or node_b is None:
            return {**empty, "reason": "节点缺失，无法裁决"}

        strategy = strategy or _STRATEGY_DEFAULT

        if strategy == "manual":
            if ContradictionResolver._is_manual(node_a):
                return {"resolved": True, "winner": node_a, "loser": node_b,
                        "strategy": "manual", "reason": "节点A被人工标记为权威"}
            if ContradictionResolver._is_manual(node_b):
                return {"resolved": True, "winner": node_b, "loser": node_a,
                        "strategy": "manual", "reason": "节点B被人工标记为权威"}
            return {**empty, "reason": "无人工权威标记，暂不裁决（交回人工）"}

        if strategy == "time":
            ta = ContradictionResolver._timestamp_of(node_a)
            tb = ContradictionResolver._timestamp_of(node_b)
            if ta is None or tb is None:
                return {**empty, "reason": "时间戳缺失，无法按时间裁决"}
            if ta == tb:
                return {**empty, "reason": "时间戳相同，无法按时间裁决"}
            winner = node_a if ta > tb else node_b
            loser = node_b if ta > tb else node_a
            return {"resolved": True, "winner": winner, "loser": loser,
                    "strategy": "time", "reason": "时间优先：保留较新陈述"}

        if strategy == "source":
            ra = ContradictionResolver._source_rank(node_a)
            rb = ContradictionResolver._source_rank(node_b)
            if ra == rb:
                return {**empty, "reason": "来源信任相同，无法按来源裁决"}
            winner = node_a if ra > rb else node_b
            loser = node_b if ra > rb else node_a
            return {"resolved": True, "winner": winner, "loser": loser,
                    "strategy": "source", "reason": "来源优先：保留高来源信任"}

        # 未知策略：交回原有信任差逻辑（不在此处理）
        return {**empty, "reason": f"未启用策略'{strategy}'，交回信任差逻辑"}

    # ------------------------------------------------------------------
    # 写入时矛盾预检
    # ------------------------------------------------------------------
    @staticmethod
    def precheck_on_write(new_node: Any, existing_nodes: list,
                          strategy: str = _STRATEGY_DEFAULT) -> dict:
        """新知识落库前，与已有节点比对矛盾，按策略决定写入动作。

        Returns:
            {"action": "add"|"skip", "conflict_with": node_id|None, "reason": str}
            - add：无矛盾，或虽矛盾但新陈述胜出（调用方照常写入）。
            - skip：命中矛盾且旧陈述按策略胜出（调用方应跳过新写入）。
        任一异常按「无矛盾」保守处理（照常写入），保证写入链路不中断。
        """
        result_add = {"action": "add", "conflict_with": None,
                      "reason": "无矛盾，照常写入"}
        try:
            from nucleus.reasoning.ContradictionDetector import ContradictionDetector
            for ex in existing_nodes or []:
                if ex is None or ex is new_node:
                    continue
                if ContradictionDetector.detect(new_node, ex):
                    r = ContradictionResolver.resolve(new_node, ex, strategy)
                    if r["resolved"]:
                        if r["winner"] is new_node:
                            return {"action": "add",
                                    "conflict_with": getattr(ex, "node_id", ""),
                                    "reason": "命中矛盾，但新陈述按策略胜出，覆盖旧陈述"}
                        return {"action": "skip",
                                "conflict_with": getattr(ex, "node_id", ""),
                                "reason": f"命中矛盾，旧陈述按策略胜出（{r['reason']}）"}
                    # 无法裁决（如 manual 无标记）→ 保守保留两者，照常写入
        except Exception:
            pass
        return result_add

    # ------------------------------------------------------------------
    # 跟踪列表批量缩减（驱动 13对→<5对）
    # ------------------------------------------------------------------
    @staticmethod
    def reduce_tracking(tracking: list[dict], node_getter: Callable[[str], Any],
                        strategy: str = _STRATEGY_DEFAULT) -> int:
        """对活跃矛盾跟踪列表应用策略消解，原地标记可消解项，返回已消解数量。

        tracking 项结构（与 PulseInnerWorld._contradiction_tracking 对齐）：
            {"node_a_id", "node_b_id", "resolved": bool, "resolution": str, ...}
        node_getter(node_id) -> PulseNode | None
        """
        resolved_count = 0
        for t in tracking:
            if not isinstance(t, dict) or t.get("resolved"):
                continue
            a = node_getter(t.get("node_a_id")) if node_getter else None
            b = node_getter(t.get("node_b_id")) if node_getter else None
            r = ContradictionResolver.resolve(a, b, strategy)
            if r["resolved"]:
                t["resolved"] = True
                t["resolution"] = f"策略消解({strategy}): {r['reason']}"
                resolved_count += 1
        return resolved_count



# ============================================================ 主线第57批 T3（P2-397）：轻量级矛盾解决机制
# 现状：ContradictionDetector 只检测、ContradictionResolver.resolve 只做两两策略裁决，
# 但 40 对未解决矛盾缺少「分类->路由->老化->状态跟踪」工作流，导致只检测不解决。
# 本批在 ContradictionResolver 内新增轻量工作流（不改动检测与核心裁决逻辑，向后兼容）。

# 矛盾解决状态（与 PulseInnerWorld._contradiction_tracking 跟踪项对齐）
CONTRADICTION_STATUS_UNRESOLVED = "unresolved"   # 未解决
CONTRADICTION_STATUS_RESOLVING = "resolving"     # 解决中（已路由人工裁决 / 已降优先级）
CONTRADICTION_STATUS_RESOLVED = "resolved"       # 已解决
CONTRADICTION_STATUS_ARCHIVED = "archived"       # 已归档

# 解决方式分类
CONTRADICTION_RESOLVE_AUTO = "auto"   # 自动可解决（记录并标记待修复）
CONTRADICTION_RESOLVE_HUMAN = "human"  # 需人工裁决（设计/架构冲突）

# 默认老化阈值（天）—— 裁决2 方案A：7天降优先级，30天归档
CONTRADICTION_DEMOTE_DAYS_DEFAULT = 7
CONTRADICTION_ARCHIVE_DAYS_DEFAULT = 30

# 自动可解决信号：矛盾类型
_AUTO_RESOLVE_TYPES = frozenset({"numeric"})
# 自动可解决信号：证据/类别关键词（配置值不一致、文档与代码不符等）
_AUTO_RESOLVE_KEYWORDS = frozenset({"配置", "文档", "代码", "config", "doc", "注释", "参数"})


def _to_ts(value) -> Optional[float]:
    """将跟踪项中的时间字段（float / datetime / 字符串）归一为时间戳；无法解析返回 None。"""
    if value is None:
        return None
    ts = getattr(value, "timestamp", None)
    if callable(ts):
        try:
            return float(ts())
        except Exception:
            pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_cfg(name: str, default):
    """优雅读取 config 阈值（存在则用之，缺失则回退默认，避免因 config 变更而硬失败）。"""
    try:
        import config  # 延迟导入，避免循环依赖
        return getattr(config, name, default)
    except Exception:
        return default


def classify_contradiction(detail) -> str:
    """将一对矛盾分类为「自动可解决」或「需人工裁决」（轻量、可解释）。

    规则：
      - numeric 类型：数值冲突通常对应配置值不一致 / 文档与代码不符 -> 自动可解决。
      - 其余类型（semantic / path）：设计或架构层面的语义冲突 -> 需人工裁决。
      - 若 detail 显式携带 evidence / category / note / reason 含 配置/文档/代码 关键词，
        即使非 numeric 也判为自动可解决。
    """
    if not isinstance(detail, dict):
        return CONTRADICTION_RESOLVE_HUMAN
    ctype = str(detail.get("type") or "").lower()
    if ctype in _AUTO_RESOLVE_TYPES:
        return CONTRADICTION_RESOLVE_AUTO
    _evidence = " ".join(str(detail.get(k, "")) for k in ("evidence", "category", "note", "reason"))
    if any(kw in _evidence for kw in _AUTO_RESOLVE_KEYWORDS):
        return CONTRADICTION_RESOLVE_AUTO
    return CONTRADICTION_RESOLVE_HUMAN


def auto_resolve(detail) -> dict:
    """对自动可解决矛盾生成解决记录（不实际修改知识库，仅标记待修复）。"""
    return {
        "resolved": True,
        "resolution": "auto",
        "status": CONTRADICTION_STATUS_RESOLVED,
        "action": "pending_fix",  # 记录并标记待修复，留待专项批次统一处理
        "reason": "自动可解决({}): 记录并标记待修复".format(detail.get("type", "unknown") if isinstance(detail, dict) else "unknown"),
    }


def apply_aging(tracking, now=None, demote_days=None, archive_days=None) -> int:
    """对矛盾跟踪列表应用老化策略（原地更新 status / priority）。

    规则（裁决2 方案A）：
      - 未解决且 age >= archive_days(30)：status=archived
      - 未解决且 age >= demote_days(7)：status=resolving（降优先级），priority=low
    已 resolved / archived 的条目不重复处理。返回状态变更条目数。
    """
    if demote_days is None:
        demote_days = _read_cfg("CONTRADICTION_DEMOTE_DAYS", CONTRADICTION_DEMOTE_DAYS_DEFAULT)
    if archive_days is None:
        archive_days = _read_cfg("CONTRADICTION_ARCHIVE_DAYS", CONTRADICTION_ARCHIVE_DAYS_DEFAULT)
    if now is None:
        now = time.time()
    changed = 0
    for t in tracking or []:
        if not isinstance(t, dict):
            continue
        _st = t.get("status")
        if _st in (CONTRADICTION_STATUS_RESOLVED, CONTRADICTION_STATUS_ARCHIVED):
            continue
        _ts = _to_ts(t.get("detected_at") or t.get("created_at") or t.get("first_seen"))
        if _ts is None:
            continue
        _age = (now - _ts) / 86400.0
        _mutated = False
        if _age >= archive_days:
            if _st != CONTRADICTION_STATUS_ARCHIVED:
                t["status"] = CONTRADICTION_STATUS_ARCHIVED
                _mutated = True
        elif _age >= demote_days:
            if _st != CONTRADICTION_STATUS_RESOLVING:
                t["status"] = CONTRADICTION_STATUS_RESOLVING
                _mutated = True
            if t.get("priority") != "low":
                t["priority"] = "low"
                _mutated = True
        if _mutated:
            changed += 1
    return changed


def resolve_batch(tracking, node_getter=None, now=None,
                  demote_days=None, archive_days=None, logger=None) -> dict:
    """轻量级矛盾解决批处理：分类 -> 自动/人工路由 -> 老化 -> 状态跟踪。

    对每条未解决矛盾：
      - classify=auto：调用 auto_resolve，标记 status=resolved、action=pending_fix。
      - classify=human：标记 status=resolving，追加 adjudication 记录并输出日志。
    最后统一应用老化策略。不修改知识库本身。
    返回 {"auto_resolved", "human_routed", "aged", "adjudications"}。
    """
    _auto_n = _human_n = 0
    _adjudications = []
    for t in tracking or []:
        if not isinstance(t, dict):
            continue
        if t.get("status") == CONTRADICTION_STATUS_RESOLVED:
            continue
        _detail = t.get("detail") or t
        _kind = classify_contradiction(_detail)
        if _kind == CONTRADICTION_RESOLVE_AUTO:
            t.update(auto_resolve(_detail))
            t["status"] = CONTRADICTION_STATUS_RESOLVED
            _auto_n += 1
        else:
            t["status"] = CONTRADICTION_STATUS_RESOLVING
            t["resolved"] = False
            _item = {
                "node_a_id": t.get("node_a_id"),
                "node_b_id": t.get("node_b_id"),
                "type": _detail.get("type") if isinstance(_detail, dict) else None,
                "reason": "复杂矛盾（设计/架构），需人工裁决",
            }
            _adjudications.append(_item)
            if logger is not None:
                try:
                    logger("矛盾待裁决: " + str(_item))
                except Exception:
                    pass
            _human_n += 1
    _aged = apply_aging(tracking, now=now, demote_days=demote_days, archive_days=archive_days)
    return {"auto_resolved": _auto_n, "human_routed": _human_n,
            "aged": _aged, "adjudications": _adjudications}

def resolve_contradiction(node_a: Any, node_b: Any,
                          strategy: str = _STRATEGY_DEFAULT) -> dict:
    """模块级便捷入口。"""
    return ContradictionResolver.resolve(node_a, node_b, strategy)
