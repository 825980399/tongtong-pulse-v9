# -*- coding: utf-8 -*-
"""
exploration_audit.py —— 探索审计器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 审计框架自主探索行为的安全性与有效性
机制: 函数式模块，包含9个工具函数
定位: 安全治理层
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from config import TIMEOUT_CONFIG
from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger
from nucleus.probe_types import ProbeIssue

_logger = get_module_logger(__name__)

# 严重级别归一映射：三通道可能产出不同大小写/粒度的级别，统一到 high/medium/low
_SEVERITY_ALIASES = {
    "high": "high", "HIGH": "high", "critical": "high", "error": "high",
    "medium": "medium", "MEDIUM": "medium", "warning": "medium",
    "low": "low", "LOW": "low", "info": "low",
}


def register_default_probes() -> None:
    """★P2 探查器注册表：把三条内置通道预注册为默认探查器（幂等，重复注册自动忽略）。

    保持向后兼容——不注册也等价于原三通道；注册后 run_parallel_audit 从注册表驱动，
    允许第三方模块 register 自定义探查器、unregister 默认探查器。
    """
    try:
        from nucleus.probe_registry import get_probe_registry
    except Exception as e:
        silent_exc(e, "exploration_audit.py:42:register_default_probes", level="warning")
        return
    _reg = get_probe_registry()
    _reg.register("tool_channel", "tool", _tool_channel, priority=10, takes_inspector=True)
    _reg.register("rule_channel", "rule", _rule_channel, priority=20, takes_inspector=True)
    _reg.register("llm_channel", "llm", _llm_channel, priority=30, takes_inspector=True)


def _normalize_issue(raw: Any) -> ProbeIssue | None:
    """归一化层（★P1 契约）：把工具/规则两通道产出的 issue 统一映射到 ProbeIssue。

    修复点:
    - 规则通道用 `description` 描述问题，工具通道用 `message`；统一为
      `message`（缺失时回退到 description），避免聚合层读空描述。
    - severity 做别名归一（HIGH→high、warning→medium 等）。
    - line 强制 int（工具通道可能给出 str）。
    """
    if not isinstance(raw, dict):
        return None

    _file = str(raw.get("file", "") or "")
    _type = str(raw.get("type", "") or "unknown")

    # 行号归一：容忍 str / None，统一为 int（非负）
    _line_raw = raw.get("line", 0)
    try:
        _line = max(0, int(_line_raw))
    except (TypeError, ValueError):
        _line = 0

    # severity 归一
    _sev_raw = str(raw.get("severity", "low") or "low")
    _severity = _SEVERITY_ALIASES.get(_sev_raw, _SEVERITY_ALIASES.get(_sev_raw.lower(), "low"))

    # 描述字段统一：message 优先，description 回退，都无则用 suggestion 兜底
    _message = str(raw.get("message", "") or raw.get("description", "") or raw.get("suggestion", "") or "")

    # 用 dict[str, Any] 中间态收集（避免 TypedDict 动态 key 赋值的 literal-required 报错），
    # 最后显式逐字段构造 ProbeIssue 返回（契约强类型化，字段名静态可查）。
    _issue: dict[str, Any] = {
        "file": _file,
        "line": _line,
        "type": _type,
        "severity": _severity,
        "message": _message,
    }

    # 溯源/扩展字段：仅在原始 dict 中存在时透传，保持契约 total=False 语义
    for _src_key in (
        "tool", "organ", "method", "description", "suggestion",
        "lifecycle", "learned_fix_strategy", "confidence",
    ):
        if _src_key in raw and raw[_src_key] not in (None, ""):
            _issue[_src_key] = raw[_src_key]

    # 规则通道无 tool 字段时，补标记以追溯来源（区分于工具通道）
    if "tool" not in _issue and raw.get("description"):
        _issue["tool"] = "rule"

    # 显式构造契约对象，字段缺失/多余由 mypy 静态拦截
    _result: ProbeIssue = {
        "file": _issue["file"],
        "line": _issue["line"],
        "type": _issue["type"],
        "severity": _issue["severity"],
        "message": _issue["message"],
    }
    for _opt_key in (
        "tool", "organ", "method", "description", "suggestion",
        "lifecycle", "learned_fix_strategy", "confidence",
    ):
        if _opt_key in _issue:
            _result[_opt_key] = _issue[_opt_key]  # type: ignore[typeddict-item]

    return _result


def _tool_channel(self_inspector=None) -> list[dict[str, Any]]:
    """通道1：工具初筛（ruff/bandit/mypy/compileall）。"""
    try:
        if self_inspector is not None and hasattr(self_inspector, 'run_tool_checks'):
            return self_inspector.run_tool_checks()
        return []
    except Exception:
        return []


def _rule_channel(self_inspector=None) -> list[dict[str, Any]]:
    """通道2：工程质量规则检查（self_inspector 12 种检测器）。"""
    try:
        if self_inspector is not None and hasattr(self_inspector, 'detect_code_issues'):
            return self_inspector.detect_code_issues()
        return []
    except Exception:
        return []


def _llm_channel(self_inspector=None) -> dict[str, Any]:
    """通道3：LLM 深度审查（运行时引导的定向审查）。

    P1(2026-09-03)：改为子进程执行，完成后销毁，内存完全释放。
    子进程失败时自动回退到主进程直接调用，保证零回归。
    """
    try:
        from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor
        _executor = SafeEvolutionExecutor()
        # 子进程执行（review模式，超时10分钟）
        _result = _executor.run_in_subprocess(
            issues=[], mode="review", timeout=TIMEOUT_CONFIG['code_review'], max_issues=10
        )
        if _result.get("status") == "success":
            _stats = _result.get("stats", {})
            if isinstance(_stats, dict) and _stats:
                return _stats
        # 子进程失败，回退到主进程直接调用
        return _executor.run_runtime_guided_review(self_inspector)
    except Exception:
        return {"status": "error", "reviewed": 0, "distilled": 0, "report": []}


def aggregate_audit_results(tool_issues: list, rule_issues: list,
                            llm_report: dict, strategy: dict | None = None) -> dict[str, Any]:
    """聚合层：按文件+类型+行号去重，分类汇总，输出统一报告。

    ★P1 契约：先对所有 issue 做归一化（`_normalize_issue`），统一字段名后再去重，
    修复工具通道 `message` / 规则通道 `description` 字段不一致导致的描述丢失。

    ★P3-L1：可选 `strategy` 参数（`probe_strategy.compute_strategy()` 的返回值）。
    传入时对命中重点文件/类型的 issue 追加 `strategy_focus=True` 标记，并按
    「重点 + 严重级别」重排序（重点靠前）；传 None 时行为与改造前完全一致（零回归）。
    注意：只做标记 + 排序，绝不裁剪 issue 列表，全量兜底不漏新问题。
    """
    _merged: dict[str, ProbeIssue] = {}
    for _raw_issue in list(tool_issues) + list(rule_issues):
        _issue = _normalize_issue(_raw_issue)
        if _issue is None:
            continue
        _key = f"{_issue.get('file','')}:{_issue.get('line',0)}:{_issue.get('type','unknown')}"
        if _key not in _merged:
            _merged[_key] = _issue
        else:
            # 合并：保留更严重的 severity；同级别时保留有描述信息的那条
            _sev_order = {"high": 3, "medium": 2, "low": 1}
            _old = _merged[_key]
            _old_sev = _sev_order.get(_old.get("severity", "low"), 1)
            _new_sev = _sev_order.get(_issue.get("severity", "low"), 1)
            if _new_sev > _old_sev or _new_sev == _old_sev and _issue.get("message") and not _old.get("message"):
                _merged[_key] = _issue

    _issues = list(_merged.values())
    _severity_counts = {"high": 0, "medium": 0, "low": 0}
    _type_counts: dict[str, int] = {}
    for _i in _issues:
        _sev = _i.get("severity", "low")
        if _sev in _severity_counts:
            _severity_counts[_sev] += 1
        _t = _i.get("type", "unknown")
        _type_counts[_t] = _type_counts.get(_t, 0) + 1

    # ★P3-L1：策略自反馈——对命中重点文件/类型的 issue 标记 + 排序（不裁剪）
    if isinstance(strategy, dict) and (strategy.get("focus_files") or strategy.get("focus_types")):
        _focus_files = set(strategy.get("focus_files") or set())
        _focus_types = set(strategy.get("focus_types") or set())
        for _i in _issues:
            _is_focus = (_i.get("file", "") in _focus_files) or (_i.get("type", "unknown") in _focus_types)
            if _is_focus:
                _i["strategy_focus"] = True
        # 排序：重点靠前（strategy_focus），同组内 severity 高者靠前；稳定排序不改变同等项相对顺序
        _sev_order = {"high": 0, "medium": 1, "low": 2}
        _issues.sort(key=lambda _x: (
            0 if _x.get("strategy_focus") else 1,
            _sev_order.get(_x.get("severity", "low"), 2),
        ))

    _llm_issues = llm_report.get("report", []) if isinstance(llm_report, dict) else []
    return {
        "total_issues": len(_issues),
        "severity_counts": _severity_counts,
        "type_counts": _type_counts,
        "issues": _issues,
        "llm_review": {
            "reviewed": llm_report.get("reviewed", 0) if isinstance(llm_report, dict) else 0,
            "distilled": llm_report.get("distilled", 0) if isinstance(llm_report, dict) else 0,
            "report": _llm_issues,
        },
    }


def run_parallel_audit(self_inspector=None, strategy: dict | None = None) -> dict[str, Any]:
    """探查编排（★P1: 并行调度 + 回灌链；★P2: 注册表驱动动态探查器）。

    优先从探查器注册表取启用的探查器执行；若注册表不可用或未注册任何探查器，
    回退到内置三通道（工具初筛/规则检查/LLM 深度审查），保证审查能力不中断。

    ★P3-L1: 可选 `strategy` 参数（`probe_strategy.compute_strategy()` 返回值），
    传入后聚合层对命中重点的 issue 标记 + 排序；传 None 时零回归。
    """
    # ★P2: 注册表驱动
    try:
        from nucleus.probe_registry import get_probe_registry
        _reg = get_probe_registry()
        _probes = _reg.list_probes()
        if _probes:
            # 有注册的探查器：并行执行（复用全局并行调度器，失败降级本地线程池）
            _results = _reg.run_all(self_inspector)
            return _aggregate_registry_results(_results, strategy)
    except Exception as e:
        _logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    # 回退：内置三通道并行审查
    try:
        from nucleus.parallel_scheduler import get_parallel_scheduler
        _tasks = [
            (lambda: _tool_channel(self_inspector)),
            (lambda: _rule_channel(self_inspector)),
            (lambda: _llm_channel(self_inspector)),
        ]
        _tool_issues, _rule_issues, _llm_report = get_parallel_scheduler().submit_parallel(_tasks)
    except Exception:
        # 降级：调度器不可用时退回本地线程池，保证审查能力不中断
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="audit") as _pool:
            _f_tool = _pool.submit(_tool_channel, self_inspector)
            _f_rule = _pool.submit(_rule_channel, self_inspector)
            _f_llm = _pool.submit(_llm_channel, self_inspector)
            _tool_issues = _f_tool.result()
            _rule_issues = _f_rule.result()
            _llm_report = _f_llm.result()

    return aggregate_audit_results(_tool_issues, _rule_issues, _llm_report, strategy)


def _aggregate_registry_results(results: list[tuple[str, str, Any]],
                                strategy: dict | None = None) -> dict[str, Any]:
    """★P2: 把注册表执行结果归并到统一报告（区分 issue 列表通道与 llm 报告通道）。"""
    _issue_lists: list[list] = []
    _llm_reports: list[dict] = []
    for _name, _channel, _result in results:
        if _channel == "llm":
            if isinstance(_result, dict):
                _llm_reports.append(_result)
        elif isinstance(_result, list):
            _issue_lists.append(_result)
        elif isinstance(_result, dict):
            # 兼容：部分自定义探查器直接返回 dict 报告
            _issue_lists.append(_result.get("issues", []))
    _all_issues: list = []
    for _lst in _issue_lists:
        _all_issues.extend(_lst)
    # 复用聚合层：规则通道字段已由 aggregate 内的 _normalize_issue 归一
    _merged_report = aggregate_audit_results(_all_issues, [], {"report": []}, strategy)
    # 合并 llm 报告
    _llm_merged: dict[str, Any] = {"reviewed": 0, "distilled": 0, "report": []}
    for _rep in _llm_reports:
        _llm_merged["reviewed"] += int(_rep.get("reviewed", 0) or 0)
        _llm_merged["distilled"] += int(_rep.get("distilled", 0) or 0)
        _llm_merged["report"].extend(_rep.get("report", []) or [])
    _merged_report["llm_review"] = _llm_merged
    return _merged_report


def record_audit_findings_to_hub(report: dict[str, Any], max_entries: int = 15) -> int:
    """★P1: 将三通道审查结果回流到终身学习枢纽，闭合 exploration → learning 链路。

    仅回流高/中危样本（上限 max_entries），避免冲刷枢纽；回流条目随其他对比事件一并
    参与蒸馏，最终经已注册的 applier 回灌到 QICA（与 SafeEvolutionExecutor 共用回灌通道）。
    """
    if not isinstance(report, dict):
        return 0
    try:
        from nucleus.mnemosyne.verification_learning_hub import (
            get_verification_learning_hub,
        )
    except Exception as e:
        silent_exc(e, where="nucleus.exploration_audit::record_audit_findings_to_hub L316")
        return 0
    _hub = get_verification_learning_hub()
    _issues = report.get("issues", []) or []
    if not isinstance(_issues, list):
        _issues = []
    _sev_rank = {"high": 0, "medium": 1, "low": 2}
    _issues = sorted(_issues, key=lambda i: _sev_rank.get(i.get("severity", "low"), 2))
    _recorded = 0
    for _issue in _issues[:max(0, int(max_entries))]:
        if not isinstance(_issue, dict):
            continue
        _sev = _issue.get("severity", "low")
        _conf = {"high": 0.9, "medium": 0.7, "low": 0.5}.get(_sev, 0.5)
        _summary = f"{_issue.get('file', '')}:{_issue.get('line', 0)}:{_issue.get('type', 'unknown')}"
        _message = str(_issue.get("message", ""))[:300]
        _issue_type = str(_issue.get("type", "unknown"))
        _fix_hint = _issue.get("fix", "") or _issue.get("suggestion", "") or _message[:200]
        try:
            _hub.record(
                organ="code_learner",
                task_type="exploration_audit",
                input_summary=_summary,
                local_result={"severity": _sev, "message": _message, "issue_type": _issue_type},
                confidence=_conf,
                relevance_score=0.7,
                needs_verification=True,
                verification_result={
                    "llm_suggestion": f"修复{_issue_type}问题: {_fix_hint}",
                    "issue_type": _issue_type,
                    "severity": _sev,
                    "file": _issue.get("file", ""),
                    "line": _issue.get("line", 0),
                },
                api_better=True,
                lesson=_message,
            )
            _recorded += 1
        except Exception as e:
            silent_exc(e, where="nucleus.exploration_audit::record_audit_findings_to_hub L354")
    return _recorded