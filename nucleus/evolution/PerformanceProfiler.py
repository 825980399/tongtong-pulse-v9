# -*- coding: utf-8 -*-
"""
PerformanceProfiler.py —— 性能分析器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 代码性能剖析与瓶颈定位
机制: 基于PerformanceProfiler类实现，包含8个核心方法
定位: 进化监测层
"""

from __future__ import annotations

import cProfile
import io
import pstats
import time
from collections import Counter
from collections.abc import Callable
from typing import Any


class PerformanceProfiler:
    """性能热点定位与数据分布统计工具。"""

    def __init__(self, project_root: str = "."):
        self._project_root = project_root

    # ========== 1. cProfile 函数级热点定位 ==========

    def profile_function(self, func: Callable, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """对函数执行 cProfile，返回函数级耗时统计。

        返回:
            {"total_time": float, "call_count": int, "top_functions": [...], "result": Any}
        """
        _profiler = cProfile.Profile()
        _start = time.perf_counter()
        _profiler.enable()
        try:
            _result = func(*args, **kwargs)
        finally:
            _profiler.disable()
        _total = time.perf_counter() - _start

        _stream = io.StringIO()
        _stats = pstats.Stats(_profiler, stream=_stream).sort_stats("cumulative")
        _stats.print_stats(20)

        # 解析 top 函数
        _top = self._parse_pstats(_stream.getvalue())

        return {
            "total_time": round(_total, 4),
            "call_count": _stats.total_calls,
            "top_functions": _top[:10],
            "result": str(_result)[:200] if _result is not None else None,
        }

    def profile_code(self, code_str: str, globals_dict: dict[str, Any] | None = None) -> dict[str, Any]:
        """对代码片段执行 cProfile。"""
        _profiler = cProfile.Profile()
        _start = time.perf_counter()
        _profiler.enable()
        try:
            exec(code_str, globals_dict or {})  # 性能分析工具需要执行代码片段  # noqa: S102
        finally:
            _profiler.disable()
        _total = time.perf_counter() - _start

        _stream = io.StringIO()
        _stats = pstats.Stats(_profiler, stream=_stream).sort_stats("cumulative")
        _stats.print_stats(20)
        _top = self._parse_pstats(_stream.getvalue())

        return {
            "total_time": round(_total, 4),
            "call_count": _stats.total_calls,
            "top_functions": _top[:10],
        }

    @staticmethod
    def _parse_pstats(output: str) -> list[dict[str, Any]]:
        """解析 pstats 输出为结构化函数列表。"""
        _funcs: list[dict[str, Any]] = []
        for _line in output.splitlines():
            _parts = _line.split()
            if len(_parts) >= 5 and _parts[0].replace('.', '').isdigit():
                try:
                    _ncalls = int(_parts[0].split('/')[0])
                    _tottime = float(_parts[1])
                    _cumtime = float(_parts[3])
                    _func = ' '.join(_parts[4:])
                    _funcs.append({
                        "function": _func[:80],
                        "ncalls": _ncalls,
                        "tottime": round(_tottime, 4),
                        "cumtime": round(_cumtime, 4),
                    })
                except (ValueError, IndexError):
                    continue
        return _funcs

    # ========== 2. 数据分布统计 ==========

    @staticmethod
    def analyze_field_distribution(nodes: list[dict[str, Any]] | list[Any],
                                   field: str,
                                   max_samples: int = 10000) -> dict[str, Any]:
        """统计列表中指定字段的类型/长度分布。

        用于判断字段是否可优化存储格式（如 value 100% 为 str → 原生列存储）。

        返回:
            {"total": int, "type_distribution": {...}, "length_distribution": {...},
             "sample_values": [...]}
        """
        _total = min(len(nodes), max_samples)
        _type_counter: Counter[str] = Counter()
        _len_counter: Counter[str] = Counter()
        _samples: list[str] = []

        for i in range(_total):
            _node = nodes[i]
            _val = _node.get(field) if isinstance(_node, dict) else getattr(_node, field, None)
            _type = type(_val).__name__
            _type_counter[_type] += 1

            if isinstance(_val, str):
                _l = len(_val)
                if _l < 50:
                    _len_counter["<50"] += 1
                elif _l < 200:
                    _len_counter["50-200"] += 1
                elif _l < 500:
                    _len_counter["200-500"] += 1
                else:
                    _len_counter[">500"] += 1
            elif isinstance(_val, (list, dict)):
                _len_counter[f"complex({len(_val)})"] += 1
            else:
                _len_counter[str(_val)[:20]] += 1

            if len(_samples) < 3 and _val is not None:
                _samples.append(str(_val)[:60])

        return {
            "field": field,
            "total": _total,
            "type_distribution": dict(_type_counter.most_common()),
            "length_distribution": dict(_len_counter.most_common()),
            "sample_values": _samples,
            "all_same_type": len(_type_counter) == 1,
            "dominant_type": _type_counter.most_common(1)[0] if _type_counter else None,
        }

    # ========== 3. 日志慢操作提取 ==========

    @staticmethod
    def find_slow_operations(log_file: str, threshold_ms: int = 1000) -> list[dict[str, Any]]:
        """从日志中提取超过阈值的慢操作。

        返回:
            [{"time": str, "module": str, "operation": str, "duration_ms": float}, ...]
        """
        import os
        import re
        _ops: list[dict[str, Any]] = []
        if not os.path.exists(log_file):
            return _ops

        try:
            with open(log_file, encoding='utf-8', errors='ignore') as _f:
                _content = _f.read()
        except Exception:
            return _ops

        # 匹配 "耗时 Xms" 或 "X.XXs" 模式
        for _m in re.finditer(
            r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}).*?\[(\w+)\].*?(?:耗时|花费|用了)\s*([\d.]+)\s*(ms|s)',
            _content
        ):
            _time = _m.group(1)
            _module = _m.group(2)
            _val = float(_m.group(3))
            _unit = _m.group(4)
            _ms = _val if _unit == 'ms' else _val * 1000
            if _ms >= threshold_ms:
                _ops.append({
                    "time": _time,
                    "module": _module,
                    "duration_ms": round(_ms, 1),
                    "operation": _m.group(0)[-80:],
                })

        return _ops


# ========== 便捷函数 ==========

def get_performance_profiler(project_root: str = ".") -> PerformanceProfiler:
    """获取 PerformanceProfiler 实例。"""
    return PerformanceProfiler(project_root)


if __name__ == "__main__":
    # 自测：数据分布统计
    _profiler = PerformanceProfiler()
    _test_nodes = [
        {"value": f"test{i}" * 10, "keywords": [f"kw{i}"]}
        for i in range(100)
    ]
    _dist = _profiler.analyze_field_distribution(_test_nodes, "value")
    print(f"字段分布: {_dist['type_distribution']}")
    print(f"长度分布: {_dist['length_distribution']}")
    print(f"全同类型: {_dist['all_same_type']}")

    # 自测：cProfile
    def _slow():
        return sum(i * i for i in range(100000))
    _prof = _profiler.profile_function(_slow)
    print(f"cProfile: {_prof['total_time']}s, {_prof['call_count']} calls")
    print(f"top1: {_prof['top_functions'][0] if _prof['top_functions'] else 'none'}")
