# -*- coding: utf-8 -*-
"""189批刀2：内在模型进度仪表盘（观测级 · tools 导出面 · 零停窗 · 只读）。

一页导出 L1-L4 各组件「在册状态」+ 三指标趋势 + 留存率现值 + L3 缺口声明：

  L1 语义缓存器(L2全量在跑) / data_quality_evaluator
  L2 经验检索器(在册)
  L3 external_dependency_observe(仅观测不实装)
  L4 补丁质量评估器 / 模型自更新器(框架状态)

  - 三指标趋势：外部依赖观测三指标（178刀4 基线埋点），取自 snapshot() + 日志末值；
  - 留存率现值：188刀4 训练对物化后实测（动态读 training_pairs_*.jsonl / calls_*.jsonl），
    非旧口径 2.4%；
  - L3 缺口声明：40-43批设计稿 L3 语义决策层待补证。

★不做（任务书明确）：不建真组件、不翻生产开关、不改 L1-L4 代码（只读导出）。
"""
from __future__ import annotations

import glob
import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc


def _proj_root() -> str:
    _here = os.path.abspath(__file__)
    return os.path.dirname(os.path.dirname(_here))


def _cfg_flag(name: str, default: bool = False) -> bool:
    """读取 config 开关（import 安全，失败回默认）。"""
    try:
        import config as _cfg
        return bool(getattr(_cfg, name, default))
    except Exception as _e:
        silent_exc(_e, where="dashboard._cfg_flag:%s" % name)
        return default


def _module_present(dotted: str) -> bool:
    """模块是否可导入（在册判据之一，import 安全）。"""
    try:
        __import__(dotted)
        return True
    except Exception as _e:
        silent_exc(_e, where="dashboard._module_present:%s" % dotted)
        return False


# =============================================================================
# L1-L4 在册状态
# -----------------------------------------------------------------------------
def _layer_l1() -> dict[str, Any]:
    _observe = _cfg_flag("ENABLE_SEMANTIC_CACHE_OBSERVE", True)
    _l2 = _cfg_flag("ENABLE_SEMANTIC_CACHE_L2", False)
    _thr = None
    try:
        import config as _cfg
        _thr = float(getattr(_cfg, "SEMANTIC_CACHE_THRESHOLD", None) or 0.85)
    except Exception as _e:
        silent_exc(_e, where="dashboard._layer_l1.threshold")

    _dq = _cfg_flag("ENABLE_LLM_DATA_QUALITY_EVAL", True)
    return {
        "semantic_cache": {
            "module": "nucleus.llm.semantic_cache",
            "present": _module_present("nucleus.llm.semantic_cache"),
            "observe_on": _observe,
            "l2_on": _l2,
            "threshold": _thr,
            "status": "在册·L1观测级+L2全量返回" if (_observe and _l2)
            else "在册·观测级(部分关)",
        },
        "data_quality_evaluator": {
            "module": "nucleus.llm.data_quality_evaluator",
            "present": _module_present("nucleus.llm.data_quality_evaluator"),
            "enabled": _dq,
            "status": "在册·观测级" if _dq else "在册·关闭",
        },
    }


def _layer_l2() -> dict[str, Any]:
    _ret = _cfg_flag("ENABLE_EXPERIENCE_RETRIEVER_OBSERVE", True)
    return {
        "experience_retriever": {
            "module": "nucleus.mnemosyne.experience_retriever",
            "present": _module_present("nucleus.mnemosyne.experience_retriever"),
            "observe_on": _ret,
            "status": "在册·观测级(不替换既有检索)" if _ret else "在册·关闭",
        },
    }


def _layer_l3() -> dict[str, Any]:
    _observe = _cfg_flag("ENABLE_SEMANTIC_CACHE_OBSERVE", True)
    return {
        "external_dependency_observe": {
            "module": "nucleus.field.external_dependency_observe",
            "present": _module_present("nucleus.field.external_dependency_observe"),
            "observe_only": True,
            "real_implementation": False,
            "reuses_flag": "ENABLE_SEMANTIC_CACHE_OBSERVE",
            "observe_on": _observe,
            "status": "在册·仅观测埋点(复用L1开关·不实装决策器)",
        },
    }


def _layer_l4() -> dict[str, Any]:
    _pq = _cfg_flag("ENABLE_PATCH_QUALITY_EVALUATOR", True)
    _su = _cfg_flag("ENABLE_MODEL_SELF_UPDATER", True)
    return {
        "patch_quality_evaluator": {
            "module": "nucleus.evolution.patch_quality_evaluator",
            "present": _module_present("nucleus.evolution.patch_quality_evaluator"),
            "enabled": _pq,
            "status": "在册·框架观测级(不改进化决策)" if _pq else "在册·关闭",
        },
        "model_self_updater": {
            "module": "nucleus.llm.model_self_updater",
            "present": _module_present("nucleus.llm.model_self_updater"),
            "enabled": _su,
            "status": "在册·仅框架(不实训练)" if _su else "在册·关闭",
        },
    }


# =============================================================================
# 三指标趋势（178刀4 外部依赖观测）
# -----------------------------------------------------------------------------
def _read_external_dependency_metrics() -> dict[str, Any]:
    """外部依赖观测三指标（进程内 snapshot + 日志末值趋势）。"""
    _cur: dict[str, Any] | None = None
    try:
        from nucleus.field.external_dependency_observe import snapshot
        _cur = snapshot()
    except Exception as _e:
        silent_exc(_e, where="dashboard.external_dependency.snapshot")
    _last_logged = _read_last_external_dependency_snapshot_from_logs()
    _llm_used_false_ratio = (_cur or {}).get("llm_used_false_ratio", 0.0) if _cur else 0.0
    _ext_dep = round(1.0 - float(_llm_used_false_ratio), 6)
    return {
        "current": _cur,
        "last_logged": _last_logged,
        "external_dependency": _ext_dep,
        "note": (
            "三指标源自 178刀4 外部依赖观测埋点（external_channel_calls / "
            "llm_used_false_ratio / delegate_browser）。当前值为进程内计数；"
            "历史趋势见 logs/ 中 [external_dependency_observe] snapshot 行。"
            "无独立持久化指标文件（勿与 data/metrics/llm_dependency.json 混淆）。"
        ),
    }


def _read_last_external_dependency_snapshot_from_logs() -> dict[str, Any] | None:
    """best-effort：从 logs/ 解析最近一条 [external_dependency_observe] snapshot 行。"""
    _logs = os.path.join(_proj_root(), "logs")
    if not os.path.isdir(_logs):
        return None
    _marker = "[external_dependency_observe] snapshot"
    _last: dict[str, Any] | None = None
    try:
        for _f in glob.glob(os.path.join(_logs, "*.log")):
            try:
                with open(_f, encoding="utf-8", errors="replace") as _fh:
                    _lines = _fh.read().splitlines()[-2000:]
            except OSError as _e:
                silent_exc(_e, where="dashboard.read_logs.open")
                continue
            for _ln in _lines:
                if _marker not in _ln:
                    continue
                _kv = {}
                for _seg in _ln.split():
                    if "=" in _seg:
                        _k, _v = _seg.split("=", 1)
                        try:
                            _kv[_k] = float(_v)
                        except ValueError as _e:
                            silent_exc(_e, where="dashboard.read_logs.parse")

                if _kv:
                    _last = _kv
    except Exception as _e:
        silent_exc(_e, where="dashboard.read_logs")
    return _last


# =============================================================================
# 留存率现值（188刀4 训练对物化实测）
# -----------------------------------------------------------------------------
def _compute_retention() -> dict[str, Any]:
    """动态计算训练对转化率（留存率）：

    numerator   = training_pairs_*.jsonl 行数（188刀4 物化产物）
    denominator = calls_*.jsonl 中 status=success 且 prompt/response 非空 行数
    （即 build_training_pair 可转的调用）
    """
    _base = os.path.join(_proj_root(), "data", "llm_traces")
    _pairs = 0
    for _f in glob.glob(os.path.join(_base, "training_pairs_*.jsonl")):
        try:
            with open(_f, encoding="utf-8", errors="replace") as _fh:
                for _ in _fh:
                    _pairs += 1
        except OSError as _e:
            silent_exc(_e, where="dashboard.retention.os")
    _elig = 0
    for _f in glob.glob(os.path.join(_base, "calls_*.jsonl")):
        try:
            with open(_f, encoding="utf-8", errors="replace") as _fh:
                for _ln in _fh:
                    _ln = _ln.strip()
                    if not _ln:
                        continue
                    try:
                        _r = json.loads(_ln)
                    except Exception as _e:
                        silent_exc(_e, where="dashboard.retention.json")
                        continue
                    if (_r.get("status") == "success"
                            and (_r.get("prompt") or "").strip()
                            and (_r.get("response") or "").strip()):
                        _elig += 1
        except OSError as _e:
            silent_exc(_e, where="dashboard.retention.os")
    _rate = round(_pairs / _elig, 6) if _elig else 0.0
    return {
        "value": _rate,
        "materialized_pairs": _pairs,
        "eligible_calls": _elig,
        "formula": ("training_pairs_*.jsonl 行数 / calls_*.jsonl 中 "
                    "status=success 且 prompt/response 非空 行数"),
        "note": "188刀4 训练对物化后实测；非旧口径 2.4%。",
    }


# =============================================================================
# L3 缺口声明
# -----------------------------------------------------------------------------
def _l3_gap() -> dict[str, Any]:
    return {
        "declared": True,
        "statement": (
            "L3 语义决策层（外部依赖度→复用/退避/委托浏览器 决策器）在 40-43批设计稿中"
            "被设计为「0 / 未实装」；当前仅 external_dependency_observe 做了「只观测」埋点"
            "（不实装决策器）。L3 语义待补证。"
        ),
        "refs": [
            "docs/设计文档/内在模型L3调用精简器设计_v1.0.md",
            "docs/设计文档/178批_刀4_外部依赖度与L3设计稿_20261010.md",
        ],
    }


# =============================================================================
# 汇总导出
# -----------------------------------------------------------------------------
def compute_dashboard() -> dict[str, Any]:
    """汇总内在模型进度仪表盘（纯只读）。"""
    return {
        "version": "v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "layers": {
            "L1": _layer_l1(),
            "L2": _layer_l2(),
            "L3": _layer_l3(),
            "L4": _layer_l4(),
        },
        "three_metrics": _read_external_dependency_metrics(),
        "retention_rate": _compute_retention(),
        "l3_gap": _l3_gap(),
        "meta": {
            "read_only": True,
            "production_switch_flipped": False,
            "no_new_components": True,
        },
    }


def export_internal_model_dashboard(as_json: bool = False) -> Any:
    """导出内在模型进度仪表盘。

    Args:
        as_json: True 返回 JSON 字符串。
    不落盘、不改状态。
    """
    _payload = compute_dashboard()
    if as_json:
        return json.dumps(_payload, ensure_ascii=False, indent=2)
    return _payload


def render_dashboard_md(payload: dict[str, Any] | None = None) -> str:
    """渲染一页 Markdown 仪表盘。"""
    if payload is None:
        payload = compute_dashboard()
    _lines: list[str] = []
    _lines.append("# 曈曈 PulseNet · 内在模型进度仪表盘")
    _lines.append("")
    _lines.append("- 生成时间: %s" % payload.get("generated_at", ""))
    _lines.append("- 只读导出 · 未翻生产开关 · 未新建组件")
    _lines.append("")

    _labels = {
        "L1": "L1 语义缓存 / 数据质量",
        "L2": "L2 经验检索",
        "L3": "L3 外部依赖观测（仅观测不实装）",
        "L4": "L4 补丁质量 / 模型自更新",
    }
    for _lv, _label in _labels.items():
        _lines.append("## %s" % _label)
        for _name, _c in payload["layers"].get(_lv, {}).items():
            _parts = [_name, "(", _c.get("module", ""), "): ", _c.get("status", "")]
            _lines.append("- " + "".join(str(p) for p in _parts))
            for _k in ("observe_on", "l2_on", "enabled", "threshold",
                       "observe_only", "real_implementation", "reuses_flag"):
                if _k in _c:
                    _lines.append("  - %s = %s" % (_k, _c[_k]))
        _lines.append("")

    _tm = payload.get("three_metrics", {})
    _lines.append("## 三指标趋势（178刀4 外部依赖观测）")
    _lines.append("- external_dependency(外部依赖度) = %s" % _tm.get("external_dependency"))
    _cur = _tm.get("current")
    if _cur:
        _lines.append("- 当前进程计数: %s" % json.dumps(_cur, ensure_ascii=False))
    _ll = _tm.get("last_logged")
    if _ll:
        _lines.append("- 日志末值: %s" % json.dumps(_ll, ensure_ascii=False))
    _lines.append("- %s" % _tm.get("note", ""))
    _lines.append("")

    _rr = payload.get("retention_rate", {})
    _lines.append("## 留存率现值（188刀4 训练对物化实测）")
    _lines.append("- 值 = %.4f%% （materialized=%s / eligible=%s）"
                  % (_rr.get("value", 0.0) * 100.0,
                     _rr.get("materialized_pairs", 0), _rr.get("eligible_calls", 0)))
    _lines.append("- 公式: %s" % _rr.get("formula", ""))
    _lines.append("- %s" % _rr.get("note", ""))
    _lines.append("")

    _gap = payload.get("l3_gap", {})
    _lines.append("## L3 缺口声明")
    _lines.append("- %s" % _gap.get("statement", ""))
    for _ref in _gap.get("refs", []):
        _lines.append("- 参考: %s" % _ref)
    _lines.append("")
    return "\n".join(_lines)


def export_internal_model_dashboard_md() -> str:
    """导出内在模型进度仪表盘 Markdown。"""
    return render_dashboard_md(compute_dashboard())


if __name__ == "__main__":
    print(export_internal_model_dashboard_md())
