# -*- coding: utf-8 -*-
"""
self_probe.py —— 自我探测

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 框架自我状态探测与健康检查
机制: 基于AutonomousProbeOrchestrator类实现，包含10个核心方法
定位: 自省监测层
"""

from __future__ import annotations
from nucleus._silent_except import silent_exc

import json
import os
import re
import time
from collections import Counter
from typing import Any

from nucleus.const import LogLevel
from nucleus.logger import get_module_logger
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底
from nucleus.data.DataAccessLayer import safe_read_json


_module_logger = get_module_logger("自主探查")


def get_probe_orchestrator() -> AutonomousProbeOrchestrator:
    """模块级单例（与 self_inspector 的 get_xxx 模式一致）"""
    global _probe_orchestrator
    if _probe_orchestrator is None:
        _probe_orchestrator = AutonomousProbeOrchestrator()
    return _probe_orchestrator


def shutdown_probe_orchestrator():
    """复位单例（退出时调用，避免重启复用旧状态）"""
    global _probe_orchestrator
    _probe_orchestrator = None


_probe_orchestrator: AutonomousProbeOrchestrator | None = None


class AutonomousProbeOrchestrator(SilentLogMixin):
    """自主深度探查编排器"""

    def __init__(self, project_root: str | None = None):
        self._project_root = project_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._log_path = os.path.join(self._project_root, "logs", "pulse.log")
        self._patch_history = os.path.join(self._project_root, "data", "patches", "patch_history.json")
        self._report_dir = os.path.join(self._project_root, "data", "probe")
        try:
            os.makedirs(self._report_dir, exist_ok=True)
        except Exception as e:
            silent_exc(e, "self_probe.py:59:probe", level="warning")
        # 探查预算（config 可覆写）
        self._max_targets = 5        # 每轮最多深入的目标数
        self._max_depth = 3          # L1/L2/L3 三层
        self._node_budget = 60       # 单轮总探索节点上限
        self._time_budget = 25.0     # 单轮总时长上限（秒）
        self._diminishing_stop = 2   # 连续 N 个目标无新发现即停
        # 器官扫描缓存（每轮只扫一次，避免 get_method_body 内部重复全量扫描）
        self._organ_cache: dict[str, Any] | None = None
        self._load_config()

    # ========== 配置 ==========
    def _load_config(self):
        try:
            import config
            _cfg = getattr(config, "SELF_PROBE_CONFIG", {})
            self._max_targets = _cfg.get("max_targets", self._max_targets)
            self._max_depth = _cfg.get("max_depth", self._max_depth)
            self._node_budget = _cfg.get("node_budget", self._node_budget)
            self._time_budget = _cfg.get("time_budget_seconds", self._time_budget)
            self._diminishing_stop = _cfg.get("diminishing_stop", self._diminishing_stop)
            self._enabled = _cfg.get("enabled", True)
        except Exception:
            self._enabled = True

    def _get_organs(self) -> dict[str, Any]:
        """获取器官结构（缓存，每轮只扫一次）"""
        if self._organ_cache is None:
            try:
                from nucleus.self_inspector import get_self_inspector
                self._organ_cache = get_self_inspector().scan_all_organs()
            except Exception:
                self._organ_cache = {}
        return self._organ_cache or {}

    # ==================================================================
    # L1 目标选择：聚合多信号源，产出候选目标
    # ==================================================================
    def collect_signals(self) -> list[dict[str, Any]]:
        """从多个信号源聚合可疑目标（查什么）。

        返回 [{target, signal_type, score, evidence, meta}]
        """
        _targets: list[dict[str, Any]] = []
        try:
            _targets.extend(self._signal_from_logs())
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        try:
            _targets.extend(self._signal_from_complexity())
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        try:
            _targets.extend(self._signal_from_patch_failures())
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # 去重 + 按 score 降序
        _seen: dict[str, float] = {}
        _merged: list[dict[str, Any]] = []
        for _t in _targets:
            _key = _t.get("target", "")
            if not _key:
                continue
            if _key in _seen:
                # 合并信号（score 取 max，证据合并）
                for _m in _merged:
                    if _m.get("target") == _key:
                        _m["score"] = max(_m.get("score", 0.0), _t.get("score", 0.0))
                        _ev = _m.setdefault("evidence", [])
                        _ev.extend(_t.get("evidence", []))
                        _m["signal_types"] = list(set(_m.get("signal_types", [])) | set(_t.get("signal_types", [])))
                        break
            else:
                _seen[_key] = 1.0
                _merged.append(_t)
        _merged.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return _merged[: self._max_targets * 3]

    def _signal_from_logs(self) -> list[dict[str, Any]]:
        """信号源1：运行日志 ERROR/WARNING 按模块聚合"""
        _result: list[dict[str, Any]] = []
        if not os.path.exists(self._log_path):
            return _result
        _err_count: Counter[str] = Counter()
        _warn_count: Counter[str] = Counter()
        _err_samples: dict[str, list[str]] = {}
        try:
            with open(self._log_path, encoding="utf-8", errors="ignore") as _f:
                for _line in _f:
                    _upper = _line.upper()
                    if " ERROR " in _upper or _upper.startswith("ERROR "):
                        _mod = self._extract_module(_line)
                        if _mod:
                            _err_count[_mod] += 1
                            _err_samples.setdefault(_mod, []).append(_line.strip()[-200:])
                    elif " WARNING " in _upper or _upper.startswith("WARNING "):
                        _mod = self._extract_module(_line)
                        if _mod:
                            _warn_count[_mod] += 1
        except Exception:
            return _result
        for _mod, _n in _err_count.most_common(8):
            # ERROR 权重 3.0，WARNING 权重 1.0
            _score = 3.0 * _n + 1.0 * _warn_count.get(_mod, 0)
            if _score < 4.0:
                continue
            _result.append({
                "target": _mod,
                "signal_type": "log_error",
                "score": min(100.0, _score * 3.0),
                "evidence": _err_samples.get(_mod, [])[:2],
                "meta": {"error_count": _n, "warning_count": _warn_count.get(_mod, 0)},
                "signal_types": ["log_error"],
            })
        return _result

    def _extract_module(self, line: str) -> str | None:
        """从日志行提取模块名（[模块] 或 [框架]）"""
        _m = re.search(r"\[([^\]]+)\]\s+(?:INFO|WARNING|ERROR|DEBUG)", line)
        if _m:
            _name = _m.group(1).strip()
            if _name and _name not in ("AiBotSDK",):
                return _name
        return None

    def _signal_from_complexity(self) -> list[dict[str, Any]]:
        """信号源2：代码复杂度异常（方法数多 / 超长方法）"""
        _result: list[dict[str, Any]] = []
        _organs = self._get_organs()
        for _name, _info in list(_organs.items())[:80]:
            _methods = _info.get("methods", []) or []
            _n = len(_methods)
            if _n >= 40:
                _result.append({
                    "target": _name,
                    "signal_type": "complexity",
                    "score": min(100.0, 30.0 + _n * 1.2),
                    "evidence": [f"方法数={_n}（≥40 阈值），复杂度偏高"],
                    "meta": {"method_count": _n, "file": _info.get("file_path", "")},
                    "signal_types": ["complexity"],
                })
        # 超长方法信号（文件级）
        for _name, _info in list(_organs.items())[:80]:
            _long = [m for m in (_info.get("methods", []) or []) if (m.get("body_len") or 0) > 1500]
            if _long:
                _result.append({
                    "target": _name,
                    "signal_type": "long_method",
                    "score": min(100.0, 45.0 + len(_long) * 8.0),
                    "evidence": [f"超长方法 {len(_long)} 个（body>1500 字符）"],
                    "meta": {"long_methods": [m.get("name") for m in _long[:5]]},
                    "signal_types": ["long_method"],
                })
        return _result

    def _signal_from_patch_failures(self) -> list[dict[str, Any]]:
        """信号源3：补丁失败历史（文件级失败率）"""
        _result: list[dict[str, Any]] = []
        if not os.path.exists(self._patch_history):
            return _result
        try:
            _hist = safe_read_json(self._patch_history, default={})
        except (ValueError, OSError) as e:
            print(f"[WARNING] self_probe.py:214: {type(e).__name__}: {e}")
            return _result
        _by_file: dict[str, list[dict]] = {}
        for _p in (_hist if isinstance(_hist, list) else []):
            _file = os.path.basename(_p.get("file", ""))
            if _file:
                _by_file.setdefault(_file, []).append(_p)
        for _file, _patches in _by_file.items():
            _fail = sum(1 for _p in _patches if not _p.get("applied"))
            _total = len(_patches)
            if _total >= 2 and _fail >= 2:
                _result.append({
                    "target": _file,
                    "signal_type": "patch_failure",
                    "score": min(100.0, 40.0 + (_fail / _total) * 50.0),
                    "evidence": [f"补丁失败 {_fail}/{_total} 次"],
                    "meta": {"failed": _fail, "total": _total},
                    "signal_types": ["patch_failure"],
                })
        return _result

    # ==================================================================
    # L2 深度深入：对目标做多层展开（查多深）
    # ==================================================================
    def _probe_target(self, target: dict[str, Any], budget: dict[str, Any]) -> dict[str, Any]:
        """对单个目标做三层深入探查。

        budget: {"nodes": int, "start": float} 由编排器统一分配
        """
        _name = target.get("target", "")
        _findings: list[dict[str, Any]] = []
        _root_causes: list[str] = []
        _nodes = 0
        # ---- L1：目标定位与现状快照 ----
        _finding1 = self._probe_l1(_name)
        if _finding1:
            _findings.append(_finding1)
            _nodes += 1
        # ---- L2：方法/依赖深入 ----
        _depth2 = self._probe_l2(_name, budget)
        _findings.extend(_depth2["findings"])
        _nodes += _depth2["nodes"]
        # ---- L3：根因链（跨模块 + 日志交叉验证） ----
        _depth3 = self._probe_l3(_name, _depth2["called_methods"], budget)
        _root_causes.extend(_depth3["root_causes"])
        _nodes += _depth3["nodes"]
        budget["nodes"] -= _nodes
        return {
            "target": _name,
            "signal_type": target.get("signal_type", "unknown"),
            "score": target.get("score", 0.0),
            "findings": _findings,
            "root_causes": _root_causes[:3],
            "evidence": target.get("evidence", []),
            "nodes_used": _nodes,
            "signal_types": target.get("signal_types", []),
        }

    def _probe_l1(self, name: str) -> dict[str, Any] | None:
        """L1：目标定位——结构快照"""
        try:
            _organs = self._get_organs()
            _info = _organs.get(name)
            if not _info:
                return None
            _method_count = _info.get("method_count", 0)
            _file = _info.get("file_path", "")
            return {
                "level": "L1",
                "kind": "structure",
                "detail": f"目标 {name} 结构定位：方法数={_method_count}",
                "meta": {"method_count": _method_count, "file": os.path.basename(_file)},
            }
        except Exception as e:
            silent_exc(e, "self_probe.py:296:_probe_l1", level="warning")
            return None

    def _probe_l2(self, name: str, budget: dict[str, Any]) -> dict[str, Any]:
        """L2：方法体深入——直接解析方法体长度 + 自调用关系（不触发全量扫描）"""
        _findings: list[dict[str, Any]] = []
        _called: list[str] = []
        _nodes = 0
        try:
            _organs = self._get_organs()
            _info = _organs.get(name)
            if not _info:
                return {"findings": [], "nodes": 0, "called_methods": []}
            _file = _info.get("file_path", "")
            _methods = _info.get("methods", []) or []
            for _m in _methods[:20]:
                if budget["nodes"] <= 0:
                    break
                _mname = _m.get("name", "")
                if not _mname or _mname.startswith("__"):
                    continue
                _body = self._read_method_body_light(_file, _m.get("line_number", 0))
                _nodes += 1
                if _body:
                    # 自调用关系：方法体内引用的其他方法名
                    _inner_calls = re.findall(r"\b(self\.([a-zA-Z_]\w*))\s*\(", _body)
                    _called.extend(_c for _, _c in _inner_calls)
                    if len(_body) > 1500:
                        _findings.append({
                            "level": "L2",
                            "kind": "method_complexity",
                            "detail": f"{name}.{_mname} 方法体 {len(_body)} 字符（>1500 超长）",
                            "meta": {"method": _mname, "body_len": len(_body)},
                        })
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return {"findings": _findings, "nodes": _nodes, "called_methods": list(set(_called))[:15]}

    def _read_method_body_light(self, file_path: str, start_line: int) -> str:
        """轻量读取方法体：从 def 行到下一个顶层 def/class（不触发全库扫描）"""
        if not file_path or not os.path.exists(file_path) or start_line <= 0:
            return ""
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as _f:
                _lines = _f.readlines()
            _body: list[str] = []
            _start = start_line - 1
            for _i in range(_start, min(_start + 400, len(_lines))):
                _l = _lines[_i]
                if _i > _start:
                    _stripped = _l.lstrip()
                    _indent = len(_l) - len(_stripped)
                    # 下一个顶层 def/class（缩进≤4）视为方法结束
                    if _indent <= 4 and _stripped.startswith(("def ", "class ")):
                        break
                _body.append(_l)
            return "".join(_body)
        except Exception:
            return ""

    def _probe_l3(self, name: str, called_methods: list[str], budget: dict[str, Any]) -> dict[str, Any]:
        """L3：根因链——把日志信号与调用链交叉验证，形成根因假设"""
        _root_causes: list[str] = []
        _nodes = 0
        # 与运行日志交叉验证：该模块最近是否仍高频报错
        _recent_error = self._count_recent_errors(name)
        if _recent_error > 0:
            _root_causes.append(
                f"{name} 最近日志窗口仍有 {_recent_error} 条 ERROR/WARNING（运行期证据），"
                f"涉及被调用方法: {', '.join(called_methods[:5]) or '无'}"
            )
            _nodes += 1
        elif called_methods:
            _root_causes.append(
                f"{name} 近期日志无新增报错，调用链健康；关联方法 {len(called_methods)} 个，"
                f"提示为历史遗留或低频路径问题"
            )
            _nodes += 1
        return {"root_causes": _root_causes, "nodes": _nodes}

    def _count_recent_errors(self, name: str, window_lines: int = 3000) -> int:
        """统计日志尾部窗口中该模块的 ERROR/WARNING 条数"""
        if not os.path.exists(self._log_path):
            return 0
        try:
            with open(self._log_path, encoding="utf-8", errors="ignore") as _f:
                _lines = _f.readlines()[-window_lines:]
            _n = 0
            for _l in _lines:
                _upper = _l.upper()
                if (" ERROR " in _upper or " WARNING " in _upper) and f"[{name}]" in _l:
                    _n += 1
            return _n
        except Exception:
            return 0

    # ==================================================================
    # L3 停止决策（编排层）：收益递减 / 预算上限
    # ==================================================================
    def _should_stop(self, budget: dict[str, Any], no_new_streak: int) -> bool:
        """何时停：预算耗尽 / 时间超限 / 连续无新发现"""
        if budget["nodes"] <= 0:
            return True
        if time.time() - budget["start"] > self._time_budget:
            return True
        # 收益递减：连续目标无任何根因/发现即停
        return no_new_streak >= self._diminishing_stop

    # ==================================================================
    # 主入口：执行一轮自主探查
    # ==================================================================
    def run_probe_cycle(self, force: bool = False) -> dict[str, Any]:
        """执行一轮完整自主探查。

        Args:
            force: 忽略 enabled 开关强制运行（供手动/启动诊断调用）
        """
        if not force and not getattr(self, "_enabled", True):
            return {"status": "disabled", "targets": 0, "findings": 0}
        _start = time.time()
        self._organ_cache = None  # 每轮重建缓存
        _budget = {"nodes": self._node_budget, "start": _start}
        _targets = self.collect_signals()
        _report: dict[str, Any] = {
            "status": "completed",
            "run_id": f"probe_{int(_start)}",
            "ts": _start,
            "targets_evaluated": len(_targets),
            "probed": [],
            "no_new_streak": 0,
            "stopped_reason": "",
        }
        _total_findings = 0
        _no_new = 0
        for _t in _targets[: self._max_targets]:
            if self._should_stop(_budget, _no_new):
                _report["stopped_reason"] = "预算耗尽" if _budget["nodes"] <= 0 else (
                    "时间超限" if time.time() - _start > self._time_budget else "收益递减"
                )
                break
            try:
                _probed = self._probe_target(_t, _budget)
            except Exception as _e:
                _module_logger.debug(f"探查目标异常 {_t.get('target','')}: {_e}")
                continue
            _probed["findings"] = _probed.get("findings", []) or []
            _probed["root_causes"] = _probed.get("root_causes", []) or []
            _report["probed"].append(_probed)
            _total_findings += len(_probed["findings"]) + len(_probed["root_causes"])
            if not _probed["findings"] and not _probed["root_causes"]:
                _no_new += 1
            else:
                _no_new = 0
        _report["findings_total"] = _total_findings
        _report["duration_ms"] = round((time.time() - _start) * 1000, 1)
        if not _report.get("stopped_reason"):
            _report["stopped_reason"] = "目标完成"
        # 落盘 + 发布
        self._save_report(_report)
        self._publish_report(_report)
        _module_logger.info(
            f"自主深度探查完成: 评估{_report['targets_evaluated']}目标/深入{len(_report['probed'])}个, "
            f"发现{_total_findings}项, 耗时{_report['duration_ms']}ms ({_report['stopped_reason']})"
        )
        return _report

    def _save_report(self, report: dict[str, Any]):
        """探查报告落盘（后台日志可追溯）"""
        try:
            _path = os.path.join(self._report_dir, f"probe_{report['run_id']}.json")
            with open(_path, "w", encoding="utf-8") as _f:
                json.dump(report, _f, ensure_ascii=False, indent=2)
        except Exception as _e:
            _module_logger.debug(f"探查报告落盘失败: {_e}")

    def _publish_report(self, report: dict[str, Any]):
        """发布到 InsightBoard（供进化仪表盘/决策消费）"""
        try:
            from nucleus.InsightBoard import get_insight_board
            _board = get_insight_board()
            _content = (
                f"自主深度探查：评估{report['targets_evaluated']}目标，深入{len(report['probed'])}个，"
                f"发现{report['findings_total']}项；"
                f"Top根因: " + self._summarize_root_causes(report)
            )
            _board.post(
                insight_type="autonomous_probe",
                content=_content,
                source_loop="自主探查闭环",
                related_dimension="自主探索/代码健康",
                confidence=0.85,
                keywords=["自主探查", "深度探查", "根因分析", "代码健康"],
            )
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    def _summarize_root_causes(self, report: dict[str, Any]) -> str:
        _parts = []
        for _p in report.get("probed", [])[:3]:
            for _rc in _p.get("root_causes", [])[:1]:
                _parts.append(f"{_p.get('target','')}: {_rc[:80]}")
        return "; ".join(_parts) if _parts else "无显著根因"


if __name__ == "__main__":
    _orch = AutonomousProbeOrchestrator()
    _r = _orch.run_probe_cycle(force=True)
    print(json.dumps(_r, ensure_ascii=False, indent=2)[:3000])
