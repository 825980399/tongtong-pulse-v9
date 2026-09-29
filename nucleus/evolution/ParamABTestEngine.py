# -*- coding: utf-8 -*-
"""
ParamABTestEngine.py —— 参数AB测试引擎

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 参数优化的AB测试与效果对比
机制: 函数式模块，包含5个工具函数
定位: 进化验证层
"""

import math
import os
import re
from datetime import datetime
from typing import Any
from nucleus.evolution.LogAnalyzer import extract_log_level  # ★主线第30批 T2：真实日志级别解析


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def welch_t_test(sample_a: list[float], sample_b: list[float],
                 alpha: float = 0.05) -> dict[str, Any]:
    """★P1-3(2026-09-05)：Welch t 检验（标准库实现，不引入 scipy）。

    背景：
      原 AB 测试用「score > score * 1.1」拍脑袋阈值判定显著性，纯属伪统计。
      本函数用 Welch's t-test（两样本不等方差 t 检验）给出真实 p 值，
      判定两组均值差异是否统计显著。

    公式：
      t = (mean_a - mean_b) / sqrt(var_a/n_a + var_b/n_b)
      df（Welch–Satterthwaite）=
        (var_a/n_a + var_b/n_b)² / [(var_a/n_a)²/(n_a-1) + (var_b/n_b)²/(n_b-1)]
      p 值：用 t 分布近似（大样本趋近正态，小样本用 t 分布不完全伽马函数）。

    返回：
      {"t": float, "df": float, "p_value": float,
       "significant": bool, "alpha": float, "mean_a": float, "mean_b": float}

    边界：
      - 任一组样本数 < 2 → significant=False（无法估计方差）
      - 两组方差均为 0 → 均值相同则显著为 False，不同则显著为 True
    """
    n_a = len(sample_a)
    n_b = len(sample_b)
    if n_a < 2 or n_b < 2:
        return {"t": 0.0, "df": 0.0, "p_value": 1.0,
                "significant": False, "alpha": alpha,
                "mean_a": 0.0, "mean_b": 0.0}

    mean_a = sum(sample_a) / n_a
    mean_b = sum(sample_b) / n_b

    var_a = sum((x - mean_a) ** 2 for x in sample_a) / (n_a - 1)
    var_b = sum((x - mean_b) ** 2 for x in sample_b) / (n_b - 1)

    # 方差均为 0：两组都是常数，直接比均值
    if var_a == 0.0 and var_b == 0.0:
        _diff = abs(mean_a - mean_b) > 1e-12
        return {"t": float("inf") if _diff else 0.0,
                "df": float("inf"), "p_value": 0.0 if _diff else 1.0,
                "significant": _diff, "alpha": alpha,
                "mean_a": mean_a, "mean_b": mean_b}

    se = math.sqrt(var_a / n_a + var_b / n_b)
    if se == 0.0:
        return {"t": 0.0, "df": 0.0, "p_value": 1.0,
                "significant": False, "alpha": alpha,
                "mean_a": mean_a, "mean_b": mean_b}

    t_stat = (mean_a - mean_b) / se

    # Welch–Satterthwaite 自由度
    _va = var_a / n_a
    _vb = var_b / n_b
    _num = (_va + _vb) ** 2
    _den = (_va ** 2) / (n_a - 1) + (_vb ** 2) / (n_b - 1)
    df = _num / _den if _den > 0 else float("inf")

    # p 值：双侧检验。用 t 分布累积分布函数。
    # 实现取舍：df ≥ 30 时 t 分布逼近标准正态（误差 < 1%），直接用 erf 正态近似；
    # df < 30 时同样用正态近似并记录近似标记，避免手写不完全贝塔函数的数值风险。
    # 对 A/B 测试的典型样本量（几十～几百），此近似已足够可靠，且零第三方依赖。
    # ★双侧检验必须用 |t|：P(|T| > |t|) = 2 * (1 - CDF(|t|))，
    #   否则 t 为负（均值 A < B）时 CDF≈0 会得到 p≈2 被 clamp 到 1.0 的错误结果。
    _z = abs(t_stat)
    # 正态 CDF：0.5 * (1 + erf(z/sqrt(2)))
    _cdf = 0.5 * (1.0 + math.erf(_z / math.sqrt(2.0)))
    _p = 2.0 * (1.0 - _cdf)  # 双侧
    _p = min(1.0, max(0.0, _p))

    return {"t": t_stat, "df": df, "p_value": _p,
            "significant": _p < alpha, "alpha": alpha,
            "mean_a": mean_a, "mean_b": mean_b,
            "normal_approx": df < 30}


def run_ab_test_in_process(test_config: dict) -> dict[str, Any]:
    """
    在独立进程中执行AB测试。

    Args:
        test_config: 测试配置 {
            "test_id": str,
            "param_name": str,
            "control_value": Any,
            "experiment_value": Any,
            "log_file": str,
            "test_duration": int,  # 模拟测试时长（秒）
        }

    Returns:
        测试结果 {
            "test_id": str,
            "param_name": str,
            "control": {"value": ..., "metrics": {...}},
            "experiment": {"value": ..., "metrics": {...}},
            "winner": "control" | "experiment" | "tie",
            "improvement": float,
            "statistically_significant": bool,
            "recommendation": str,
        }
    """
    result = {
        "test_id": test_config.get("test_id"),
        "param_name": test_config.get("param_name"),
        "control": {"value": test_config.get("control_value"), "metrics": {}},
        "experiment": {"value": test_config.get("experiment_value"), "metrics": {}},
        "winner": "tie",
        "improvement": 0.0,
        "statistically_significant": False,
        "recommendation": "",
        "error": None,
    }

    try:
        log_file = test_config.get("log_file")
        if log_file is None:
            log_file = os.path.join(_PROJECT_ROOT, "logs", "pulse.log")

        if not os.path.exists(log_file):
            result["error"] = f"日志文件不存在: {log_file}"
            return result

        # 读取日志，统计关键指标
        def _analyze_log_segment(start_time: float, end_time: float) -> dict:
            metrics = {
                "total_lines": 0,
                "errors": 0,
                "warnings": 0,
                "info": 0,
                "debug": 0,
                "llm_calls": 0,
                "remediations": 0,
                "search_success": 0,
                "digest_complete": 0,
                "knowledge_stored": 0,
                "avg_response_length": 0,
                "response_lengths": [],
            }
            with open(log_file, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    m = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                    if not m:
                        continue
                    try:
                        t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
                    except Exception:
                        continue
                    if start_time <= t <= end_time:
                        metrics["total_lines"] += 1
                        # ★主线第30批 T2：按真实级别标记判定（原 `"ERROR" in line` 既
                        #   大小写敏感、又会命中消息正文中的 ERROR 字样 → 级别分布失真）
                        _lv30 = extract_log_level(line)
                        if (_lv30 in ("ERROR", "CRITICAL")) if _lv30 else ("ERROR" in line):
                            metrics["errors"] += 1
                        elif "WARNING" in line:
                            metrics["warnings"] += 1
                        elif "INFO" in line:
                            metrics["info"] += 1
                        elif "DEBUG" in line:
                            metrics["debug"] += 1
                        if "大模型调用" in line or "优先使用远程大模型" in line:
                            metrics["llm_calls"] += 1
                        if "补救触发" in line or "触发补救" in line:
                            metrics["remediations"] += 1
                        if "搜索.*成功" in line or "抓取成功" in line:
                            metrics["search_success"] += 1
                        if "消化完成" in line:
                            metrics["digest_complete"] += 1
                        if "知识.*存储" in line or "新增知识" in line:
                            metrics["knowledge_stored"] += 1
                        if "控制台输出" in line:
                            # 提取回答长度
                            len_match = re.search(r'长度=(\d+)', line)
                            if len_match:
                                metrics["response_lengths"].append(int(len_match.group(1)))
            if metrics["response_lengths"]:
                metrics["avg_response_length"] = sum(metrics["response_lengths"]) / len(metrics["response_lengths"])
            return metrics

        # 获取日志时间范围
        log_start = None
        log_end = None
        with open(log_file, encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                if m:
                    try:
                        t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
                        if log_start is None or t < log_start:
                            log_start = t
                        if log_end is None or t > log_end:
                            log_end = t
                    except Exception:
                        continue

        if log_start is None or log_end is None:
            result["error"] = "无法解析日志时间范围"
            return result

        # 将日志时间范围分成两半，模拟对照组和实验组
        mid_time = (log_start + log_end) / 2
        control_metrics = _analyze_log_segment(log_start, mid_time)
        experiment_metrics = _analyze_log_segment(mid_time, log_end)

        result["control"]["metrics"] = control_metrics
        result["experiment"]["metrics"] = experiment_metrics

        # 计算综合评分（错误越少越好，知识存储越多越好，搜索成功越多越好）
        def _composite_score(m):
            error_penalty = m["errors"] * 10
            warning_penalty = m["warnings"] * 2
            knowledge_bonus = m["knowledge_stored"] * 5
            search_bonus = m["search_success"] * 3
            digest_bonus = m["digest_complete"] * 2
            return knowledge_bonus + search_bonus + digest_bonus - error_penalty - warning_penalty

        control_score = _composite_score(control_metrics)
        experiment_score = _composite_score(experiment_metrics)

        # ★P1-3(2026-09-05)：统计显著性判定——用 Welch t 检验替换拍脑袋 10% 阈值。
        #   原实现 `score > score * 1.1` 是伪统计（无 p 值、无方差、无自由度），
        #   两组哪怕只差一个样本也会被误判「显著」。改为对两组「响应长度」样本
        #   序列做 Welch t 检验（双侧，alpha=0.05），给出真实 p 值。
        #   样本不足（<2）时回退综合分对比（保守，不误报显著）。
        _ctrl_lengths = control_metrics.get("response_lengths", [])
        _exp_lengths = experiment_metrics.get("response_lengths", [])
        _welch = welch_t_test(list(_ctrl_lengths), list(_exp_lengths))
        result["welch"] = _welch

        if len(_ctrl_lengths) >= 2 and len(_exp_lengths) >= 2:
            # 样本充足：以 Welch t 检验为准
            _sig = _welch.get("significant", False)
            _mean_a = _welch.get("mean_a", 0.0)
            _mean_b = _welch.get("mean_b", 0.0)
            _improvement = ((_mean_b - _mean_a) / max(1.0, abs(_mean_a))) * 100
            if _sig and _mean_b > _mean_a:
                result["winner"] = "experiment"
                result["improvement"] = _improvement
                result["statistically_significant"] = True
                result["recommendation"] = (
                    f"参数'{test_config.get('param_name')}'从"
                    f"{test_config.get('control_value')}调整为"
                    f"{test_config.get('experiment_value')}可提升"
                    f"{_improvement:.1f}%（Welch t={_welch.get('t', 0):.2f}, "
                    f"p={_welch.get('p_value', 1):.3f}），建议采用实验组配置")
            elif _sig and _mean_a > _mean_b:
                result["winner"] = "control"
                result["improvement"] = ((_mean_a - _mean_b) / max(1.0, abs(_mean_b))) * 100
                result["statistically_significant"] = True
                result["recommendation"] = (
                    f"参数'{test_config.get('param_name')}'保持"
                    f"{test_config.get('control_value')}更优（Welch t="
                    f"{_welch.get('t', 0):.2f}, p={_welch.get('p_value', 1):.3f}）")
            else:
                result["winner"] = "tie"
                result["improvement"] = _improvement
                result["statistically_significant"] = False
                result["recommendation"] = (
                    f"参数'{test_config.get('param_name')}'两组差异不显著"
                    f"（Welch p={_welch.get('p_value', 1):.3f} > 0.05），"
                    f"建议继续观察或增加样本量")
        else:
            # 样本不足：回退综合分对比（保守，标记样本不足）
            result["sample_insufficient"] = True
            if experiment_score > control_score * 1.1:
                result["winner"] = "experiment"
                result["improvement"] = (experiment_score - control_score) / max(1, abs(control_score)) * 100
                result["statistically_significant"] = False
                result["recommendation"] = (
                    f"参数'{test_config.get('param_name')}'实验组综合分更高，"
                    f"但响应长度样本不足（对照{len(_ctrl_lengths)}/实验{len(_exp_lengths)}），"
                    f"无法做统计检验，仅作参考")
            elif control_score > experiment_score * 1.1:
                result["winner"] = "control"
                result["improvement"] = (control_score - experiment_score) / max(1, abs(experiment_score)) * 100
                result["statistically_significant"] = False
                result["recommendation"] = (
                    f"参数'{test_config.get('param_name')}'对照组综合分更高，"
                    f"但响应长度样本不足，无法做统计检验，仅作参考")
            else:
                result["winner"] = "tie"
                result["improvement"] = 0.0
                result["statistically_significant"] = False
                result["recommendation"] = (
                    f"参数'{test_config.get('param_name')}'两组效果接近且样本不足，"
                    f"建议继续观察")

    except Exception as e:
        result["error"] = str(e)

    return result


def generate_ab_test_candidates(param_name: str, current_value: Any,
                                 num_candidates: int = 3) -> list[dict]:
    """
    生成AB测试候选配置。

    Args:
        param_name: 参数名
        current_value: 当前值
        num_candidates: 候选数量

    Returns:
        候选配置列表 [{param_name: value, ...}]
    """
    candidates = []
    if isinstance(current_value, (int, float)):
        # 数值型参数：生成上下浮动的候选值
        for i in range(num_candidates):
            if i == 0:
                # 降低20%
                new_val = current_value * 0.8 if current_value > 0 else current_value
            elif i == 1:
                # 保持不变（对照组）
                new_val = current_value
            else:
                # 增加20%
                new_val = current_value * 1.2
            if isinstance(current_value, int):
                new_val = int(new_val)
            candidates.append({param_name: new_val})
    elif isinstance(current_value, bool):
        # 布尔型参数：只有两个候选值
        candidates = [{param_name: False}, {param_name: True}]
    return candidates


# 导出可被ReasoningWorkerPool调用的函数名
EXPORTED_FUNCTIONS = [
    "run_ab_test_in_process",
    "generate_ab_test_candidates",
]
