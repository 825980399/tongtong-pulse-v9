# -*- coding: utf-8 -*-
"""通用自主能力基座（Capability Substrate）—— 铁律 304 的代码化落地。

为什么存在
----------
外部 LLM 渠道不稳定、随时会换模型（siliconflow 402 / bailian 400 / zhipu 抖动），
而既有经验机制 `append_reminder` 把经验写进**下次 LLM 的 prompt** —— 换模型、
换 prompt、清上下文，经验即归零（`lesson_cooldown.py` 开头已有实证结论）。

本模块提供**运行在曈曈进程里**的能力容器，与领域无关：
  · Capability = detect / synthesize / verify 三元组（+ 可回滚语义）
  · CapabilityKernel 按 ``domain`` 分派，不绑定补丁闭环
  · ExperienceLedger 是**结构化** append-only 记录，不是自然语言 prompt
  · AutonomyMetrics 按 domain 度量「自给率 / 外部依赖率」

设计边界（务必遵守）
--------------------
1. **内核绝不调用外部 LLM，也不 import 任何外部模型/HTTP 客户端**。
   覆盖不了的对象只返回 ``external_needed=True`` 交给调用方决定。
   这是铁律 304 的可执行判据，单测会 AST 扫描 import 节点守住它。
2. **领域无关**：domain 只是字符串。补丁修复、内在世界推理、
   认知反思、任意子系统都可以注册自己的 Capability，共用同一套台账与指标。
3. **不接生产调用点**（第一期）：默认影子模式，``run()`` 不改动传入对象；
   需要真实改写必须显式 ``enforce=True`` 或打开灰度开关。
4. **日志/留痕不决定返回值**（铁律 40）：所有落盘都走 ``_note`` 且带 silent_exc。
5. **pytest 护栏**：默认生产路径 + ``pytest`` 在 sys.modules ⇒ 不写盘
   （否则一次回归就往生产台账灌假经验）。
"""
from __future__ import annotations

import copy
import json
import os
import sys
import time
from dataclasses import dataclass, field

from nucleus._silent_except import silent_exc

SCHEMA = "autonomy_kernel/474.v1"
DEFAULT_LEDGER_REL = "data/autonomy/experience.jsonl"

_APPLIED = "applied"
_SKIPPED = "skipped"
_FAILED = "failed"
_NEEDS_EXTERNAL = "needs_external"


def _cfg(name: str, default: bool) -> bool:
    """★只有 None 才算缺省（0 / False 是显式关，不能被 ``or default`` 吞掉）。"""
    try:
        import config as _c
        _v = getattr(_c, name, None)
        return default if _v is None else bool(_v)
    except Exception as _e:
        silent_exc(_e, where="autonomy.kernel::_cfg", level="debug")
        return default


@dataclass
class CapabilityContext:
    """领域无关的能力输入。

    ``domain``   决定分派到哪些 Capability（如 code_repair / reasoning / reflection）。
    ``artifact`` 领域对象；对代码修复域是含 original_code/modified_code 的 dict，
                 对推理域可以是题面与推导状态——内核不解释它的结构。
    """

    domain: str
    artifact: dict
    evidence: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


@dataclass
class CapabilityResult:
    capability_id: str
    status: str
    artifact: dict
    changes: list = field(default_factory=list)
    reason: str = ""
    external_used: bool = False


class Capability:
    """一个可自主执行的能力。子类只需实现三件事。

    ``detect``     判别力所在：必须只在**真的有问题**时返回 True
                   （已合格的输入必须 False，否则就是铁律 35 说的自欺）。
    ``synthesize`` 产出变换后的 artifact，不改原对象。
    ``verify``     **独立入口**校验，不得复用 synthesize 的中间结论
                   （铁律 30：拿被测函数输出当基准 = 循环论证）。
    """

    capability_id = ""
    domains: tuple = ()
    error_type = ""

    def detect(self, ctx: CapabilityContext) -> bool:
        return False

    def synthesize(self, ctx: CapabilityContext) -> CapabilityResult:
        raise NotImplementedError

    def verify(self, before: dict, after: dict, ctx: CapabilityContext) -> tuple:
        return False, "verify 未实现"


class CapabilityKernel:
    """领域无关的能力注册 / 分派 / 度量内核。"""

    def __init__(self, ledger_path: str | None = None,
                 enforce: bool | None = None,
                 ledger_enabled: bool | None = None) -> None:
        self._caps: list = []
        self._enforce = (_cfg("ENABLE_M474_AUTONOMY_KERNEL", False)
                         if enforce is None else bool(enforce))
        self._ledger_enabled = (_cfg("ENABLE_M474_AUTONOMY_LEDGER", True)
                                if ledger_enabled is None else bool(ledger_enabled))
        self._default_path = ledger_path is None
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self._ledger_path = ledger_path or os.path.join(_root, *DEFAULT_LEDGER_REL.split("/"))
        self._stats: dict = {"runs": 0, "applied": 0, "failed": 0,
                             "external": 0, "by_domain": {}}

    def register(self, cap: Capability) -> bool:
        if not isinstance(cap, Capability) or not cap.capability_id:
            return False
        if any(x.capability_id == cap.capability_id for x in self._caps):
            return False
        self._caps.append(cap)
        return True

    @property
    def capabilities(self) -> tuple:
        return tuple(self._caps)

    def _bucket(self, domain: str) -> dict:
        return self._stats["by_domain"].setdefault(
            domain, {"runs": 0, "applied": 0, "failed": 0, "external": 0})

    def run(self, ctx: CapabilityContext, enforce: bool | None = None) -> dict:
        """跑一遍能力链。**影子模式下绝不修改 ctx.artifact**。

        Returns: status / artifact / records / external_needed / metrics。
        没有任何 Capability 覆盖该 domain ⇒ ``external_needed=True``，
        由调用方决定是否求助外部；内核自己不做任何外部调用。
        """
        _enforce = self._enforce if enforce is None else bool(enforce)
        before = copy.deepcopy(ctx.artifact)
        work = copy.deepcopy(ctx.artifact)
        records: list = []
        bucket = self._bucket(ctx.domain)
        self._stats["runs"] += 1
        bucket["runs"] += 1

        matched = [c for c in self._caps if ctx.domain in c.domains]
        if not matched:
            self._stats["external"] += 1
            bucket["external"] += 1
            self._note({"kind": "needs_external", "domain": ctx.domain,
                        "reason": "no_capability_registered"})
            return {"status": _NEEDS_EXTERNAL, "artifact": before,
                    "records": records, "external_needed": True,
                    "metrics": self.snapshot()}

        for _cap in matched:
            _sub = CapabilityContext(ctx.domain, work,
                                     dict(ctx.evidence), dict(ctx.metadata))
            try:
                _hit = bool(_cap.detect(_sub))
            except Exception as _e:
                silent_exc(_e, where="autonomy.kernel.detect", level="debug")
                _hit = False
            if not _hit:
                records.append({"capability_id": _cap.capability_id,
                                "status": _SKIPPED, "reason": "not_detected"})
                continue
            try:
                _res = _cap.synthesize(_sub)
                _ok, _why = _cap.verify(work, _res.artifact, _sub)
            except Exception as _e:
                silent_exc(_e, where="autonomy.kernel.synthesize", level="debug")
                _ok, _why, _res = False, f"{type(_e).__name__}: {_e}", None
            if not _ok or _res is None:
                self._stats["failed"] += 1
                bucket["failed"] += 1
                records.append({"capability_id": _cap.capability_id,
                                "status": _FAILED, "reason": str(_why)[:200]})
                self._note({"kind": "failed", "capability_id": _cap.capability_id,
                            "error_type": _cap.error_type, "domain": ctx.domain,
                            "reason": str(_why)[:200]})
                continue
            if _enforce:
                work = copy.deepcopy(_res.artifact)
            self._stats["applied"] += 1
            bucket["applied"] += 1
            records.append({"capability_id": _cap.capability_id,
                            "status": _APPLIED if _enforce else _SKIPPED,
                            "shadow": not _enforce,
                            "changes": list(_res.changes)[:20],
                            "reason": _res.reason[:200]})
            self._note({"kind": "applied", "capability_id": _cap.capability_id,
                        "error_type": _cap.error_type, "domain": ctx.domain,
                        "shadow": not _enforce, "external_used": False,
                        "changes": list(_res.changes)[:20]})

        return {"status": _APPLIED if (self._stats["applied"] and _enforce) else _SKIPPED,
                "artifact": work if _enforce else before,
                "records": records, "external_needed": False,
                "metrics": self.snapshot()}

    def snapshot(self) -> dict:
        """按 domain 分桶的自主性指标 —— 供任意子系统读取。"""
        _out: dict = {"schema": SCHEMA, "runs": self._stats["runs"],
                      "applied": self._stats["applied"],
                      "failed": self._stats["failed"],
                      "by_domain": {}}
        for _d, _s in self._stats["by_domain"].items():
            _n = _s["runs"] or 1
            _out["by_domain"][_d] = {
                "runs": _s["runs"], "applied": _s["applied"],
                "failed": _s["failed"],
                "external_dependency_rate": round(_s["external"] / _n, 4),
                "self_sufficiency_rate": round(1.0 - _s["external"] / _n, 4),
            }
        return _out

    def note_experience(self, rec: dict, domain: str | None = None) -> dict:
        """对外统一的**结构化经验**写入口，任意 domain 复用（474 批第二期）。

        与 ``run()`` 的区别：这里消费的是**已经在别处发生过的观测**
        （如验证学习枢纽的推理对错记录），不执行任何能力、不生成任何对象。
        这样补丁修复、内在世界推理、认知反思可以共用同一套台账与指标。

        ★留痕绝不改变传入对象；★不算一次"能力执行"，但算一次观测（影响自给率分母）。
        """
        _d = str(domain or rec.get("domain") or "unspecified")
        _r = dict(rec)
        _r.setdefault("domain", _d)
        _bucket = self._bucket(_d)
        self._stats["runs"] += 1
        _bucket["runs"] += 1
        if bool(_r.get("external_used")):
            self._stats["external"] += 1
            _bucket["external"] += 1
        self._note(_r)
        return self.snapshot()

    def _note(self, rec: dict) -> None:
        """结构化经验台账（append-only）。★留痕绝不改变任何返回值。"""
        if not self._ledger_enabled:
            return
        if "pytest" in sys.modules and self._default_path:
            return
        try:
            _dir = os.path.dirname(os.path.abspath(self._ledger_path))
            if _dir:
                os.makedirs(_dir, exist_ok=True)
            _rec = dict(rec)
            _rec.update({"schema": SCHEMA, "at": time.time()})
            with open(self._ledger_path, "a", encoding="utf-8") as _f:
                _f.write(json.dumps(_rec, ensure_ascii=False) + "\n")
        except Exception as _e:
            silent_exc(_e, where="autonomy.kernel._note", level="debug")
