# -*- coding: utf-8 -*-
"""
HealthScore.py —— 健康评分

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 框架整体健康度量化评分
机制: 基于HealthScore类实现，包含10个核心方法
定位: 监控评估层
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

from nucleus.const import LogLevel
from nucleus.evolution.LogAnalyzer import extract_log_level  # ★第29批 T4：日志级别真实标记解析
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底



class HealthScore(SilentLogMixin):
    """多目标健康度评分器。"""

    # 默认维度权重（总和应为 1.0）
    DEFAULT_WEIGHTS = {  # 权重表，只读常量
        "stability": 0.40,   # 稳定性
        "noise": 0.20,       # 日志噪音
        "performance": 0.20, # 性能
        "activity": 0.15,    # 活性
        "knowledge": 0.05,   # 知识演化
    }

    def __init__(self, project_root: str, weights: dict[str, float] | None = None):
        self._project_root = project_root
        self._weights = weights or dict(self.DEFAULT_WEIGHTS)
        # 归一化权重，防止调用方传入的权重和不为 1
        _total = sum(self._weights.values()) or 1.0
        self._weights = {k: v / _total for k, v in self._weights.items()}

    # ========== 入口 ==========

    def score(self, log_file: str | None = None,
              runtime_metrics: dict[str, Any] | None = None,
              system_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        计算综合健康度。
        返回:
            {
                "score": 0-100 合成分,
                "grade": "优秀/良好/一般/较差/危险",
                "dimensions": {维度: {"score": 分, "detail": 明细}},
                "weights": 权重,
                "timestamp": 时间戳,
            }
        """
        _dims: dict[str, dict[str, Any]] = {}

        # 1. 稳定性（日志 ERROR/CRITICAL/Traceback）
        _dims["stability"] = self._score_stability(log_file)

        # 2. 噪音（WARNING 刷屏 + 重复日志）
        _dims["noise"] = self._score_noise(log_file)

        # 3. 性能（CPU/内存/脉冲耗时）
        _dims["performance"] = self._score_performance(runtime_metrics, system_snapshot)

        # 4. 活性（器官在线率 + 心跳 + 脉冲吞吐）
        _dims["activity"] = self._score_activity(system_snapshot, runtime_metrics)

        # 5. 知识演化（L2/L3 增长）
        _dims["knowledge"] = self._score_knowledge(system_snapshot)

        # 合成加权总分
        _total = 0.0
        for _dim, _w in self._weights.items():
            _total += _dims.get(_dim, {}).get("score", 0.0) * _w

        _score = round(max(0.0, min(100.0, _total)), 1)
        return {
            "score": _score,
            "grade": self._grade(_score),
            "dimensions": _dims,
            "weights": dict(self._weights),
            "timestamp": time.time(),
        }

    # ========== 各维度评分 ==========

    def _score_stability(self, log_file: str | None) -> dict[str, Any]:
        """稳定性：ERROR/CRITICAL/Traceback 越少分越高。"""
        _errors, _criticals, _tracebacks, _total = self._scan_log(log_file, "stability")
        # 无日志或日志极少 → 满分（无证据表明不稳定）
        if _total == 0:
            return {"score": 100.0, "detail": {"errors": 0, "criticals": 0, "tracebacks": 0, "total": 0}}
        # 错误密度 = (ERROR + CRITICAL*3 + Traceback*5) / 总行数
        _penalty_density = (_errors + _criticals * 3 + _tracebacks * 5) / max(1, _total)
        # 密度 0 → 100 分，密度 0.1（10% 是错误）→ 0 分，线性衰减
        _score = max(0.0, 100.0 - _penalty_density * 1000.0)
        return {
            "score": round(_score, 1),
            "detail": {
                "errors": _errors,
                "criticals": _criticals,
                "tracebacks": _tracebacks,
                "total_lines": _total,
                "error_density": round(_penalty_density, 4),
            },
        }

    def _score_noise(self, log_file: str | None) -> dict[str, Any]:
        """噪音：WARNING 刷屏 + 重复日志占比。"""
        _warnings, _total, _repeated = self._scan_log(log_file, "noise")
        if _total == 0:
            return {"score": 100.0, "detail": {"warnings": 0, "repeated": 0, "total": 0}}
        # WARNING 密度
        _warn_density = _warnings / max(1, _total)
        # 重复日志密度（估算：同器官 DEBUG 刷屏）
        _repeat_density = _repeated / max(1, _total)
        _penalty = _warn_density * 400.0 + _repeat_density * 300.0
        _score = max(0.0, 100.0 - _penalty)
        return {
            "score": round(_score, 1),
            "detail": {
                "warnings": _warnings,
                "repeated": _repeated,
                "total_lines": _total,
                "warning_density": round(_warn_density, 4),
            },
        }

    def _score_performance(self, runtime_metrics: dict[str, Any] | None,
                           system_snapshot: dict[str, Any] | None) -> dict[str, Any]:
        """性能：CPU/内存占用 + 脉冲平均耗时。"""
        _cpu = 0.0
        _mem = 0.0
        _pulse_avg_ms = 0.0

        if system_snapshot:
            _molecular = system_snapshot.get("molecular", {})
            _cpu = float(_molecular.get("cpu_usage", 0.0) or 0.0)
            _mem = float(_molecular.get("memory_usage", 0.0) or 0.0)
        if runtime_metrics:
            _pulse_avg_ms = float(runtime_metrics.get("pulse_avg_ms", 0.0) or 0.0)

        # CPU > 80% 严重扣分，< 40% 满分；内存同理；脉冲耗时 > 100ms 扣分
        _cpu_score = max(0.0, 100.0 - max(0.0, _cpu - 40.0) * 1.5)
        _mem_score = max(0.0, 100.0 - max(0.0, _mem - 50.0) * 1.2)
        _pulse_score = max(0.0, 100.0 - max(0.0, _pulse_avg_ms - 20.0) * 1.0)
        _score = (_cpu_score * 0.4 + _mem_score * 0.3 + _pulse_score * 0.3)
        return {
            "score": round(_score, 1),
            "detail": {
                "cpu_usage": _cpu,
                "memory_usage": _mem,
                "pulse_avg_ms": _pulse_avg_ms,
            },
        }

    def _score_activity(self, system_snapshot: dict[str, Any] | None,
                        runtime_metrics: dict[str, Any] | None) -> dict[str, Any]:
        """活性：器官在线率 + 心跳 + 脉冲吞吐。"""
        _organs_online = 0
        _organs_total = 0
        _pulse_count = 0
        _heartbeat_status = ""

        if system_snapshot:
            _organs = system_snapshot.get("organs", {})
            _organs_total = int(_organs.get("total", 0) or 0)
            _organs_online = int(_organs.get("online", 0) or 0)
            _hb = system_snapshot.get("heartbeat", {})
            _heartbeat_status = str(_hb.get("status", ""))
        if runtime_metrics:
            _pulse_count = int(runtime_metrics.get("pulse_count", 0) or 0)

        # 器官在线率（无数据视为满分，避免冷启动误判）
        if _organs_total > 0:
            _online_rate = _organs_online / _organs_total
        else:
            _online_rate = 1.0
        _organ_score = _online_rate * 100.0

        # 心跳状态
        _hb_score = 100.0 if "起搏" in _heartbeat_status or not _heartbeat_status else 70.0

        # 脉冲吞吐（有活动即加分，无数据中性）
        _pulse_score = 60.0 if _pulse_count == 0 else min(100.0, 60.0 + _pulse_count * 0.001)

        _score = _organ_score * 0.6 + _hb_score * 0.2 + _pulse_score * 0.2
        return {
            "score": round(_score, 1),
            "detail": {
                "organs_online": _organs_online,
                "organs_total": _organs_total,
                "online_rate": round(_online_rate, 3),
                "pulse_count": _pulse_count,
                "heartbeat_status": _heartbeat_status,
            },
        }

    def _score_knowledge(self, system_snapshot: dict[str, Any] | None) -> dict[str, Any]:
        """知识演化：L2/L3 节点数量与演化活跃度。"""
        _total = 0
        _l2l3 = 0
        _fuse_count = 0
        _compress_count = 0

        if system_snapshot:
            _nodes = system_snapshot.get("nodes", {})
            _total = int(_nodes.get("total", 0) or 0)
            # hot+warm+cold 都是 L2/L3（L1 会被单独标记，但这里用总数近似）
            _l2l3 = int(_nodes.get("hot", 0) or 0) + int(_nodes.get("warm", 0) or 0) + int(_nodes.get("cold", 0) or 0)
            _liver = system_snapshot.get("liver", {})
            _fuse_count = int(_liver.get("fuse_count", 0) or 0)
            _compress_count = int(_liver.get("compress_count", 0) or 0)

        # 知识沉淀度：L2/L3 占比（高说明知识在沉淀，低说明停在 L1 未消化）
        if _total > 0:
            _sediment_rate = _l2l3 / _total
        else:
            _sediment_rate = 0.5  # 无数据中性
        _sediment_score = _sediment_rate * 100.0

        # 演化活跃度：融合+压缩有产出即加分
        _evolve_score = min(100.0, (_fuse_count + _compress_count) * 5.0)

        _score = _sediment_score * 0.7 + _evolve_score * 0.3
        return {
            "score": round(_score, 1),
            "detail": {
                "total_nodes": _total,
                "l2l3_nodes": _l2l3,
                "sediment_rate": round(_sediment_rate, 3),
                "fuse_count": _fuse_count,
                "compress_count": _compress_count,
            },
        }

    # ========== 工具方法 ==========

    def _scan_log(self, log_file: str | None, mode: str) -> tuple[int, int, int]:
        """
        扫描日志，返回 (关键计数, 总行数, 重复计数)。
        mode="stability": (errors, criticals, tracebacks, total) —— 实际返回4元组由调用方解包差异处理
        mode="noise": (warnings, total, repeated)
        为统一，这里按调用方需求返回；用 mode 区分。
        """
        if log_file is None:
            _log_file = os.path.join(self._project_root, "logs", "pulse.log")
        else:
            _log_file = log_file

        if not os.path.exists(_log_file):
            return (0, 0, 0) if mode == "noise" else (0, 0, 0, 0)

        _errors = 0
        _criticals = 0
        _tracebacks = 0
        _warnings = 0
        _total = 0

        # 用于估算重复日志：统计 (器官, 日志内容前40字符) 的出现次数
        _line_signatures: dict[str, int] = {}
        _repeated = 0

        try:
            with open(_log_file, encoding="utf-8", errors="ignore") as _f:
                for _line in _f:
                    _total += 1
                    _upper = _line.upper()
                    # ★主线第29批 T4/P2-176：优先按「真实级别标记」判定。
                    #   原实现用子串匹配，会让消息正文含 "JSONDecodeError" 的 WARNING 行
                    #   同时计入 errors 与 warnings（实测虚高）。无标记行保留原判定。
                    _real_level = extract_log_level(_line)
                    if _real_level:
                        if _real_level == "CRITICAL":
                            _criticals += 1
                        elif _real_level == "ERROR":
                            _errors += 1
                        elif _real_level == "WARNING":
                            _warnings += 1
                        elif _real_level == "TRACEBACK":
                            _tracebacks += 1
                    else:
                        if "CRITICAL" in _upper:
                            _criticals += 1
                        elif "ERROR" in _upper:
                            _errors += 1
                        elif "TRACEBACK" in _upper or 'File "' in _line:
                            _tracebacks += 1
                        if "WARNING" in _upper:
                            _warnings += 1
                    # 估算重复：同一器官 + 同类日志
                    _m = re.match(r'[\d\-:. ]+\[([^\]]+)\]\s+(\w+):\s*(.{0,40})', _line)
                    if _m:
                        _sig = (_m.group(1), _m.group(2), _m.group(3))
                        _line_signatures[_sig] = _line_signatures.get(_sig, 0) + 1
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 重复日志 = 出现超过 3 次的签名数量（近似）
        _repeated = sum(1 for _c in _line_signatures.values() if _c > 3)

        if mode == "noise":
            return (_warnings, _total, _repeated)
        return (_errors, _criticals, _tracebacks, _total)

    def _grade(self, score: float) -> str:
        if score >= 85:
            return "优秀"
        if score >= 70:
            return "良好"
        if score >= 55:
            return "一般"
        if score >= 40:
            return "较差"
        return "危险"


# ========== 便捷函数 ==========

def compute_health_score(project_root: str,
                         log_file: str | None = None,
                         runtime_metrics: dict[str, Any] | None = None,
                         system_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """便捷入口：计算健康度。"""
    return HealthScore(project_root).score(log_file, runtime_metrics, system_snapshot)


if __name__ == "__main__":
    # 自测：用当前项目日志算一次健康度
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _result = compute_health_score(_root)
    print(f"健康度: {_result['score']} 分 ({_result['grade']})")
    for _dim, _info in _result["dimensions"].items():
        print(f"  {_dim:12s}: {_info['score']:5.1f} 分  {_info['detail']}")
