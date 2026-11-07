# -*- coding: utf-8 -*-
"""474 批第二期：推理域观测桥（旁路，不碰任何现有生产文件）。

为什么是旁路
------------
`PulseInnerWorld._detect_symbolic_reason` **已经**把每次符号推理的结果写进
`verification_learning_hub.record(...)`，字段含 `local_result.solved`、
`api_better`、`needs_verification`、`task_type`。也就是说「哪类题框架能独立解出、
哪类需依赖大模型」这个统计，曈曈自己早就在采集了，只是**没有接到自主性度量上**。

本模块只读 hub 已有的记录，转换成 `ExperienceLedger` 的结构化经验，
让「推理自给率」第一次成为可观测事实。据此：

  · 不改 `PulseInnerWorld.py` ⇒ 不触发 cw3-A 语义守恒重锚
    （该门禁只 watch `organs/brain/PulseInnerWorld.py` 与 `pulse_inner_world_*`）；
  · 不改 `verification_learning_hub.py` ⇒ 不影响任何既有记录路径；
  · 不调用任何外部模型 ⇒ 符合铁律 304。

这正是「创建能力就是为了整体利用」：同一个 `CapabilityKernel` 既服务
`code_repair` 域，也服务 `reasoning` 域，未来任何子系统都一样。
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

DOMAIN_REASONING = "reasoning"


def classify_hub_entry(entry: dict) -> dict:
    """把 hub 一条记录归一成自主性信号。**纯函数**，不改入参、不外联。

    ★474刀25：hub 是**多器官共用的通用终身学习枢纽**，不是推理专用台账。
    实测（20:29 首拍）114 条记录里 0 条含 ``local_result.solved``，全是
    ``code_review / local_analysis_* / exploration_audit / growth_hypothesis``
    等形态（``local_result`` 装的是 issue_type/file/line/method）。旧判据把它们
    全部当成「一次推理尝试」计入分母，且把 ``needs_verification`` 误当「已用外部模型」
    ⇒ 自给率恒 0.0、外部依赖率 0.9714，是**假数**，直接污染铁律 304 的核心度量。

    现在按**写入契约**识别，不猜：
      * 只有 ``_detect_symbolic_reason`` 写的推理记录才带
        ``local_result.solved`` 或以 ``symbolic`` 开头的 ``task_type``；
      * 其它记录标 ``record_kind=other / decidable=False``，
        **不进自给率分母、不判外部依赖**，但仍写台账（不丢观测）。

    对推理类记录，第二期判据逐字保留：
      * ``solved_locally`` 只认 ``local_result.solved is True``（显式真值）；
      * ``external_used`` = ``api_better`` 为真，或「本地没解出且需要外部验证」。
    """
    _e = entry if isinstance(entry, dict) else {}
    _local = _e.get("local_result")
    _local = _local if isinstance(_local, dict) else {}
    _task_type = str(_e.get("task_type") or "unknown")
    _decidable = ("solved" in _local) or _task_type.startswith("symbolic")
    _solved = _local.get("solved") is True
    _api_better = bool(_e.get("api_better"))
    _needs_verify = bool(_e.get("needs_verification"))
    _external = _api_better or (not _solved and _needs_verify)
    return {
        "entry_id": str(_e.get("id") or ""),
        "task_type": _task_type,
        "organ": str(_e.get("organ") or "unknown"),
        "record_kind": "reasoning" if _decidable else "other",
        "decidable": _decidable,
        "solved_locally": _solved if _decidable else False,
        "external_used": _external if _decidable else False,
        "api_better": _api_better,
    }


class ReasoningObserver:
    """消费 hub 记录 → 结构化经验台账 + 推理自给率。

    ``max_seen`` 是有界的（铁律：状态位反复入队必须配上限），防止长跑内存涨上去。
    """

    def __init__(self, kernel: Any, max_seen: int = 4096) -> None:
        self._kernel = kernel
        self._max_seen = max(16, int(max_seen))
        self._seen: set = set()
        self._by_task: dict = {}
        self._total = 0
        self._local = 0
        self._external = 0
        # ★474刀25：通用 hub 记录数（留痕但**不进**自给率分母）
        self._non_reasoning = 0

    def observe(self, entries: Iterable) -> dict:
        """批量消化。**只读** entries，不修改 hub、不修改任何传入对象。"""
        _new = 0
        for _raw in entries or ():
            if not isinstance(_raw, dict):
                continue
            _sig = classify_hub_entry(_raw)
            _key = _sig["entry_id"] or f"{_sig['task_type']}#{self._total}"
            if _key in self._seen:
                continue
            self._seen.add(_key)
            if len(self._seen) > self._max_seen:
                self._seen.clear()
                self._seen.add(_key)
            # ★474刀25：只有「可判定的推理记录」进指标；其它记录仍留痕，
            #   但绝不假扮成「一次依赖外部的推理尝试」。
            if _sig["decidable"]:
                self._total += 1
                if _sig["solved_locally"]:
                    self._local += 1
                if _sig["external_used"]:
                    self._external += 1
                _t = self._by_task.setdefault(
                    _sig["task_type"], {"total": 0, "local": 0, "external": 0})
                _t["total"] += 1
                _t["local"] += int(_sig["solved_locally"])
                _t["external"] += int(_sig["external_used"])
            else:
                self._non_reasoning += 1
            self._kernel.note_experience({
                "kind": "reasoning_observation",
                "domain": DOMAIN_REASONING,
                "capability_id": "reasoning.symbolic_observe",
                "entry_id": _sig["entry_id"],
                "task_type": _sig["task_type"],
                "organ": _sig["organ"],
                "record_kind": _sig["record_kind"],
                "decidable": _sig["decidable"],
                "solved_locally": _sig["solved_locally"],
                "external_used": _sig["external_used"],
                "api_better": _sig["api_better"],
            }, domain=DOMAIN_REASONING)
            _new += 1
        return {"new": _new, **self.snapshot()}

    def snapshot(self) -> dict:
        # ★474刀25：没有可判定样本时返回 None（「无数据」），绝不返回 0.0
        #   （「完全依赖外部」）—— 后者会把空样本误读成能力为零。
        _n = self._total or 1
        return {
            "schema": "autonomy_reasoning/474.v2",
            "domain": DOMAIN_REASONING,
            "total": self._total,
            "solved_locally": self._local,
            "external": self._external,
            "non_reasoning_total": self._non_reasoning,
            "reasoning_self_sufficiency_rate": (
                round(self._local / _n, 4) if self._total else None),
            "external_dependency_rate": (
                round(self._external / _n, 4) if self._total else None),
            "by_task_type": {k: dict(v) for k, v in self._by_task.items()},
            "kernel": self._kernel.snapshot(),
        }
