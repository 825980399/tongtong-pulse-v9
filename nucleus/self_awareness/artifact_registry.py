# -*- coding: utf-8 -*-
"""★第158批 上-A T-自我审计-4（P2）：文件产物注册表（写读闭环审计）。

问题
----
「代码写了文件、但没人读」是**二阶断点**——第一阶断链门禁（cw2/broken-chain）
只看**代码符号**的引用，看不见「数据产物」这条链。这类断点更隐蔽：报告照写、
文件照涨，就是没人消费。

定位（与既有件的边界 · 一件勿重做）
----------------------------------
* **PCM**（``ProductionConsumptionMatcher``）负责**扫描**产物路径 + 判 producer/
  consumer——本件**不重复实现扫描**，直接复用其结果；
* 本件负责**声明 + 审计**：把「本仓应当产出哪些文件产物、各自期望有没有消费方」
  写成**注册表**，再与 PCM 实测**对账**，输出三类缺口：

  ==============  ==========================================================
  缺口类型        含义
  ==============  ==========================================================
  ``unregistered`` 实有产物但**未登记**（口径缺失，将来无法判断该不该有人读）
  ``declared_absent`` 登记了但**实际没有产出**（声明与现实脱节）
  ``no_consumer`` 产出且有 producer，但**无 consumer** = ★二阶断点
  ==============  ==========================================================

★口径纪律
----------
注册表里的 ``expect_consumer=False`` 表示**该产物设计上不被消费**（如自观测
快照、探针产物），审计时**不计为断点**——避免把设计性产物误判成缺陷
与 T-审计-2 的「设计性拒绝」同一原则。

只读：本件不创建、不删除、不移动任何产物文件。
"""
from __future__ import annotations

import fnmatch
import io
import json
import os
import sys
import time
from typing import Any

from nucleus._silent_except import silent_exc

__all__ = [
    "ARTIFACT_SPECS",
    "registry_path",
    "load_registry",
    "save_registry",
    "audit",
]

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 产物注册表（声明面）——``expect_consumer=False`` 者设计上不被消费，不计断点。
ARTIFACT_SPECS: list[dict[str, Any]] = [
    {
        "id": "reports",
        "glob": "data/reports/**/*.json",
        "producer_hint": "ReportBus / publishers / DailyScheduler",
        "expect_consumer": True,
        "note": "报告产物（v2 四归位见 T-审计-2 capability_ledger）",
    },
    {
        "id": "patch_history",
        "glob": "data/patches/patch_history.json",
        "producer_hint": "PatchManager / SafeEvolutionExecutor",
        "expect_consumer": True,
        "note": "补丁历史（N-9 判据数据源）",
    },
    {
        "id": "quality_report",
        "glob": "data/evolution/patch_quality_report.json",
        "producer_hint": "patch_quality_evaluator",
        "expect_consumer": True,
        "note": "补丁质量评估报告",
    },
    {
        "id": "probe",
        "glob": "data/probe/*",
        "producer_hint": "探针 / 观测路由",
        "expect_consumer": True,
        "note": "探针产物（第5刀已接 health_ui 只读路由）",
    },
    {
        "id": "self_awareness",
        "glob": "data/self_awareness/**/*.json*",
        "producer_hint": "DailyScheduler / maturity_ledger / rule_engine",
        "expect_consumer": True,
        "note": "自我认知台账与趋势",
    },
    {
        "id": "task_ledger",
        "glob": "data/evolution/task_ledger.json",
        "producer_hint": "SafeEvolutionExecutor.task_ledger",
        "expect_consumer": True,
        "note": "任务态账本（O-A1）",
    },
    {
        "id": "cooldown",
        "glob": "data/evolution/cooldown.json",
        "producer_hint": "SafeEvolutionExecutor 冷却域",
        "expect_consumer": True,
        "note": "冷却账（O-A1 只读核对）",
    },
    {
        "id": "llm_traces",
        "glob": "data/llm_traces/*",
        "producer_hint": "LLM 依赖度埋点",
        "expect_consumer": False,          # ★自观测产物，设计性不被消费
        "note": "LLM 调用轨迹（自观测留存，非断点）",
    },
]


def registry_path() -> str:
    """注册表落盘路径（``data/self_awareness/artifact_registry.json``）。"""
    return os.path.join(_PROJECT_ROOT, "data", "self_awareness", "artifact_registry.json")


def load_registry(path: str | None = None) -> list[dict]:
    """读注册表；不存在则回落到**声明面** :data:`ARTIFACT_SPECS`。"""
    _p = path or registry_path()
    if not os.path.isfile(_p):
        return [dict(_s) for _s in ARTIFACT_SPECS]
    try:
        with io.open(_p, encoding="utf-8") as _f:
            _d = json.load(_f)
        _specs = _d.get("specs") if isinstance(_d, dict) else _d
        if isinstance(_specs, list) and _specs:
            return [_s for _s in _specs if isinstance(_s, dict)]
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="nucleus.self_awareness.artifact_registry::load_registry",
                   level="warning")
    return [dict(_s) for _s in ARTIFACT_SPECS]


def save_registry(specs: list[dict], path: str | None = None) -> str:
    """写注册表（生产路径需授权；测试环境下不写生产 data/）。"""
    _p = path or registry_path()
    try:
        if ("pytest" in sys.modules) or os.environ.get("PYTEST_CURRENT_TEST"):
            sys.stderr.write("[artifact_registry] 测试环境跳过生产写入: %s\n" % _p)
            return _p
    except Exception as _e:
        silent_exc(_e, where="nucleus.self_awareness.artifact_registry::save_registry env")
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with io.open(_p, "w", encoding="utf-8") as _f:
            json.dump({"version": "158A-T4-1", "generated_at": time.time(),
                       "specs": specs}, _f, ensure_ascii=False, indent=2)
    except OSError as _e:
        silent_exc(_e, where="nucleus.self_awareness.artifact_registry::save_registry",
                   level="warning")
    return _p


# ------------------------------------------------------------------ 审计
def _match_files(root: str, pattern: str, limit: int = 5000) -> list[str]:
    """按 glob 收集文件（限上限，避免极端情况扫爆）。

    ★``**`` 表示「递归任意层」——拼接目录前缀时**到此为止**（``data/reports/**``
    不能当目录名，否则 isdir 恒假、整类误报 0 文件）。
    """
    _out: list[str] = []
    _parts = str(pattern).split("/")
    _base = root
    for _p in _parts[:-1]:
        if _p == "**":
            break
        _base = os.path.join(_base, _p)
    if not os.path.isdir(_base):
        return []
    _name_pat = _parts[-1]
    try:
        for _dp, _dns, _fns in os.walk(_base):
            _dns[:] = [d for d in _dns if not d.startswith(".bak")
                       and d not in ("__pycache__", ".git")]
            for _fn in _fns:
                if fnmatch.fnmatch(_fn, _name_pat):
                    _out.append(os.path.join(_dp, _fn))
                    if len(_out) >= limit:
                        return _out
    except OSError as _e:
        silent_exc(_e, where="nucleus.self_awareness.artifact_registry::_match_files",
                   level="debug")
    return _out


def _pcm_consumer_index() -> dict[str, int]:
    """复用 PCM：取「有 consumer 的产物」数量索引（不重复实现扫描）。"""
    _idx: dict[str, int] = {}
    try:
        from nucleus.self_awareness.ProductionConsumptionMatcher import (
            ProductionConsumptionMatcher,
        )
        _res = ProductionConsumptionMatcher().scan()
    except Exception as _e:
        silent_exc(_e, where="nucleus.self_awareness.artifact_registry::_pcm_consumer_index",
                   level="warning")
        return _idx
    for _k, _v in (_res or {}).items():
        if not isinstance(_v, dict):
            continue
        for _path, _entry in _v.items():
            if isinstance(_entry, dict):
                _c = _entry.get("consumers") or []
                if _c and _c != []:
                    _idx[str(_path)] = len(_c)
    return _idx


def audit(root: str | None = None, use_pcm: bool = True) -> dict[str, Any]:
    """写读闭环审计：注册表声明 vs 实际产物。

    Args:
        root: 仓库根（测试可注入）。
        use_pcm: 是否复用 PCM 判消费方（关掉则只对账「有无产物」）。

    Returns:
        ``{total_specs, by_id, unregistered, declared_absent, no_consumer,
        designed_unconsumed, source, ran_at}``
    """
    _root = root or _PROJECT_ROOT
    _specs = load_registry()
    _consumers = _pcm_consumer_index() if use_pcm else {}

    _by_id: dict[str, Any] = {}
    _unregistered: list[str] = []
    _absent: list[str] = []
    _no_consumer: list[dict] = []
    _designed: list[dict] = []

    _matched_paths: set[str] = set()
    for _s in _specs:
        _sid = str(_s.get("id"))
        _files = _match_files(_root, str(_s.get("glob") or ""))
        for _f in _files:
            _matched_paths.add(os.path.relpath(_f, _root).replace("\\", "/"))
        _entry = {
            "id": _sid,
            "glob": _s.get("glob"),
            "producer_hint": _s.get("producer_hint"),
            "expect_consumer": bool(_s.get("expect_consumer", True)),
            "files": len(_files),
            "with_consumer": 0,
            "note": _s.get("note", ""),
        }
        if not _files:
            _absent.append(_sid)
        if use_pcm and _files:
            for _f in _files:
                _rel = os.path.relpath(_f, _root).replace("\\", "/")
                # PCM 的键可能是绝对路径或相对路径，两种都试
                _hit = _consumers.get(_rel) or _consumers.get(_f.replace("\\", "/"))
                if _hit:
                    _entry["with_consumer"] += 1
            if _entry["with_consumer"] < _entry["files"]:
                _rec = {"id": _sid, "files": _entry["files"],
                        "with_consumer": _entry["with_consumer"]}
                if _entry["expect_consumer"]:
                    _no_consumer.append(_rec)          # ★二阶断点
                else:
                    _designed.append(_rec)             # 设计性不消费，不计断点
        _by_id[_sid] = _entry

    # 未登记产物：data/ 下实际存在但没被任何 spec 覆盖的 .json/.jsonl（限深度）
    _unregistered = _scan_unregistered(_root, _matched_paths)

    return {
        "source": "artifact_registry+PBM" if use_pcm else "artifact_registry(only)",
        "total_specs": len(_specs),
        "by_id": _by_id,
        "unregistered": _unregistered,
        "declared_absent": _absent,
        "no_consumer": _no_consumer,
        "designed_unconsumed": _designed,
        "ran_at": time.time(),
    }


def _scan_unregistered(root: str, matched: set[str], limit: int = 200) -> list[str]:
    """扫 ``data/`` 下未被任何 spec 覆盖的产物文件（限量，防爆）。"""
    _out: list[str] = []
    _base = os.path.join(root, "data")
    if not os.path.isdir(_base):
        return _out
    for _dp, _dns, _fns in os.walk(_base):
        _dns[:] = [d for d in _dns if not d.startswith(".bak") and d != "__pycache__"]
        for _fn in _fns:
            if not _fn.endswith((".json", ".jsonl")):
                continue
            _rel = os.path.relpath(os.path.join(_dp, _fn), root).replace("\\", "/")
            if _rel in matched:
                continue
            _out.append(_rel)
            if len(_out) >= limit:
                return sorted(_out)
    return sorted(_out)
