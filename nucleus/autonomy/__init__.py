# -*- coding: utf-8 -*-
"""通用自主能力基座（铁律 304）。

领域无关：补丁修复、内在世界推理、认知反思及任意子系统都可注册 Capability，
共用同一套结构化经验台账与自主性指标。内核不调用外部 LLM。
"""
from .kernel import SCHEMA, Capability, CapabilityContext, CapabilityKernel, CapabilityResult
from .operators import (
                     DEFAULT_OPERATORS,
                     DOMAIN_CODE_REPAIR,
                     IndentLevelAlignCapability,
                     SignatureDocstringRestoreCapability,
                     SilentExcBackfillCapability,
                     build_default_kernel,
)

__all__ = [
                     "DEFAULT_OPERATORS",
                     "DOMAIN_CODE_REPAIR",
                     "SCHEMA",
                     "Capability",
                     "CapabilityContext",
                     "CapabilityKernel",
                     "CapabilityResult",
                     "IndentLevelAlignCapability",
                     "SignatureDocstringRestoreCapability",
                     "SilentExcBackfillCapability",
                     "build_default_kernel",
]
