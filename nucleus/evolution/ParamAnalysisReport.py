# -*- coding: utf-8 -*-
"""
ParamAnalysisReport.py —— 参数分析报告

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 参数变更影响分析报告生成
机制: 基于ParamAnalyzer类实现，包含10个核心方法
定位: 进化管理层
"""

import json
import os
import time
from collections import defaultdict
from datetime import datetime
from typing import Any
from nucleus.data.DataAccessLayer import safe_read_json


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PATCH_HISTORY_PATH = os.path.join(_PROJECT_ROOT, "data", "param_patch_history.json")
_CONFIG_CHANGES_LOG = os.path.join(_PROJECT_ROOT, "logs", "config_changes.log")
_REPORT_DIR = os.path.join(_PROJECT_ROOT, "logs", "param_reports")


class ParamAnalyzer:
    """参数分析器：分析补丁历史、参数相关性、生成报告"""

    def __init__(self):
        os.makedirs(_REPORT_DIR, exist_ok=True)

    def load_patch_history(self) -> list[dict[str, Any]]:
        """加载补丁历史"""
        try:
            if os.path.exists(_PATCH_HISTORY_PATH):
                data = safe_read_json(_PATCH_HISTORY_PATH, default={})
                return data.get("applied_patches", []) + data.get("pending_patches", [])
        except Exception as e:
            print(f"[WARNING] ParamAnalysisReport.py:33: {type(e).__name__}: {e}")
        return []

    def load_config_changes(self) -> list[dict[str, Any]]:
        """加载配置变更日志"""
        changes = []
        try:
            if os.path.exists(_CONFIG_CHANGES_LOG):
                with open(_CONFIG_CHANGES_LOG, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                changes.append(json.loads(line))
                            except Exception as e:
                                print(f"[WARNING] ParamAnalysisReport.py:48: {type(e).__name__}: {e}")
        except Exception as e:
            print(f"[WARNING] ParamAnalysisReport.py:50: {type(e).__name__}: {e}")
        return changes

    def get_statistics(self) -> dict[str, Any]:
        """获取补丁统计信息"""
        patches = self.load_patch_history()
        changes = self.load_config_changes()

        stats = {
            "total_patches": len(patches),
            "applied": sum(1 for p in patches if p.get("status") == "applied"),
            "rolled_back": sum(1 for p in patches if p.get("status") == "rolled_back"),
            "failed": sum(1 for p in patches if p.get("status") == "failed"),
            "effect_verified": sum(1 for p in patches if p.get("effect_verified")),
            "effect_positive": sum(1 for p in patches
                                    if p.get("effect_verified") and p.get("effect_score", 0) > 0),
            "effect_negative": sum(1 for p in patches
                                    if p.get("effect_verified") and p.get("effect_score", 0) < 0),
            "total_config_changes": len(changes),
            "unique_params": len(set(p.get("param") for p in patches if p.get("param"))),  # noqa: C401
        }

        # 按参数统计
        param_stats = defaultdict(lambda: {"applied": 0, "rolled_back": 0, "avg_effect": 0.0,
                                            "effect_count": 0})
        for p in patches:
            param = p.get("param", "unknown")
            if p.get("status") == "applied":
                param_stats[param]["applied"] += 1
            elif p.get("status") == "rolled_back":
                param_stats[param]["rolled_back"] += 1
            if p.get("effect_verified"):
                param_stats[param]["avg_effect"] += p.get("effect_score", 0)
                param_stats[param]["effect_count"] += 1

        for param in param_stats:
            if param_stats[param]["effect_count"] > 0:
                param_stats[param]["avg_effect"] /= param_stats[param]["effect_count"]

        stats["param_stats"] = dict(param_stats)

        # 最有效参数（按平均效果排序）
        effective_params = sorted(
            [(p, s["avg_effect"]) for p, s in param_stats.items() if s["effect_count"] > 0],
            key=lambda x: x[1], reverse=True
        )
        stats["most_effective"] = effective_params[:10]
        stats["least_effective"] = effective_params[-10:] if len(effective_params) > 10 else effective_params[::-1]

        return stats

    def analyze_correlations(self) -> dict[str, Any]:
        """分析参数相关性"""
        changes = self.load_config_changes()
        if len(changes) < 2:
            return {"correlations": [], "message": "数据不足，无法分析相关性"}

        # 按时间窗口分组（5分钟内的变更视为相关）
        time_windows = []
        current_window = []
        last_time = None

        for change in sorted(changes, key=lambda x: x.get("timestamp", "")):
            try:
                ts = datetime.strptime(change["timestamp"], "%Y-%m-%d %H:%M:%S")
                if last_time and (ts - last_time).total_seconds() > 300:
                    if current_window:
                        time_windows.append(current_window)
                    current_window = []
                current_window.append(change)
                last_time = ts
            except Exception:
                continue

        if current_window:
            time_windows.append(current_window)

        # 统计参数共现频率
        co_occurrence = defaultdict(int)
        param_frequency = defaultdict(int)

        for window in time_windows:
            params_in_window = set(c.get("param") for c in window if c.get("param"))  # noqa: C401
            for param in params_in_window:
                param_frequency[param] += 1
            params_list = sorted(params_in_window)
            for i in range(len(params_list)):
                for j in range(i + 1, len(params_list)):
                    co_occurrence[(params_list[i], params_list[j])] += 1

        # 计算相关性（Jaccard相似度）
        correlations = []
        for (p1, p2), count in co_occurrence.items():
            if param_frequency[p1] > 0 and param_frequency[p2] > 0:
                jaccard = count / (param_frequency[p1] + param_frequency[p2] - count)
                correlations.append({
                    "param1": p1,
                    "param2": p2,
                    "co_occurrence": count,
                    "jaccard_similarity": round(jaccard, 3),
                    "strength": "强" if jaccard > 0.5 else ("中" if jaccard > 0.2 else "弱"),
                })

        correlations.sort(key=lambda x: x["jaccard_similarity"], reverse=True)

        return {
            "correlations": correlations[:20],
            "total_windows": len(time_windows),
            "total_params": len(param_frequency),
        }

    def generate_html_report(self) -> str:
        """生成HTML可视化报告"""
        stats = self.get_statistics()
        correlations = self.analyze_correlations()

        # 预设方案
        presets = ParamPresets()
        preset_list = presets.list_presets()

        report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 参数统计表格
        param_rows = ""
        for param, s in sorted(stats.get("param_stats", {}).items(),
                               key=lambda x: x[1]["applied"], reverse=True):
            effect_color = "#22c55e" if s["avg_effect"] > 0 else ("#ef4444" if s["avg_effect"] < 0 else "#6b7280")
            param_rows += f"""
            <tr>
                <td>{param}</td>
                <td>{s['applied']}</td>
                <td>{s['rolled_back']}</td>
                <td style="color:{effect_color}">{s['avg_effect']:.2f}</td>
                <td>{s['effect_count']}</td>
            </tr>"""

        # 相关性表格
        corr_rows = ""
        for c in correlations.get("correlations", []):
            strength_color = "#22c55e" if c["strength"] == "强" else ("#f59e0b" if c["strength"] == "中" else "#6b7280")
            corr_rows += f"""
            <tr>
                <td>{c['param1']}</td>
                <td>{c['param2']}</td>
                <td>{c['co_occurrence']}</td>
                <td>{c['jaccard_similarity']}</td>
                <td style="color:{strength_color}">{c['strength']}</td>
            </tr>"""

        # 预设方案表格
        preset_rows = ""
        for p in preset_list:
            preset_rows += f"""
            <tr>
                <td>{p['name']}</td>
                <td>{p['description']}</td>
                <td>{len(p.get('params', {}))}个参数</td>
                <td><button onclick="applyPreset('{p['name']}')" style="padding:4px 12px;cursor:pointer;">应用</button></td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>曈曈PulseNet 参数补丁分析报告</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; padding: 20px; background: #f8fafc; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        h1 {{ color: #1e293b; border-bottom: 3px solid #3b82f6; padding-bottom: 10px; }}
        h2 {{ color: #334155; margin-top: 30px; }}
        .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin: 20px 0; }}
        .stat-card {{ background: white; padding: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); text-align: center; }}
        .stat-value {{ font-size: 28px; font-weight: bold; color: #3b82f6; }}
        .stat-label {{ font-size: 12px; color: #64748b; margin-top: 5px; }}
        table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        th {{ background: #3b82f6; color: white; padding: 10px; text-align: left; }}
        td {{ padding: 8px 10px; border-bottom: 1px solid #e2e8f0; }}
        tr:hover {{ background: #f1f5f9; }}
        .chart {{ background: white; padding: 15px; border-radius: 8px; margin: 20px 0; height: 300px; }}
        .timestamp {{ color: #64748b; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>曈曈PulseNet 参数补丁分析报告</h1>
        <p class="timestamp">报告生成时间：{report_time}</p>

        <h2>📊 总体统计</h2>
        <div class="stats-grid">
            <div class="stat-card"><div class="stat-value">{stats['total_patches']}</div><div class="stat-label">总补丁数</div></div>
            <div class="stat-card"><div class="stat-value" style="color:#22c55e">{stats['applied']}</div><div class="stat-label">已应用</div></div>
            <div class="stat-card"><div class="stat-value" style="color:#ef4444">{stats['rolled_back']}</div><div class="stat-label">已回滚</div></div>
            <div class="stat-card"><div class="stat-value" style="color:#f59e0b">{stats['failed']}</div><div class="stat-label">失败</div></div>
            <div class="stat-card"><div class="stat-value">{stats['effect_verified']}</div><div class="stat-label">效果验证</div></div>
            <div class="stat-card"><div class="stat-value" style="color:#22c55e">{stats['effect_positive']}</div><div class="stat-label">正向效果</div></div>
            <div class="stat-card"><div class="stat-value">{stats['unique_params']}</div><div class="stat-label">涉及参数</div></div>
            <div class="stat-card"><div class="stat-value">{stats['total_config_changes']}</div><div class="stat-label">配置变更</div></div>
        </div>

        <h2>📈 效果分布</h2>
        <div id="effectChart" class="chart"></div>

        <h2>🔗 参数相关性分析</h2>
        <p>共分析 {correlations.get('total_windows', 0)} 个时间窗口，{correlations.get('total_params', 0)} 个参数</p>
        <table>
            <tr><th>参数1</th><th>参数2</th><th>共现次数</th><th>Jaccard相似度</th><th>相关性强度</th></tr>
            {corr_rows}
        </table>

        <h2>📋 参数统计明细</h2>
        <table>
            <tr><th>参数名</th><th>应用次数</th><th>回滚次数</th><th>平均效果</th><th>验证次数</th></tr>
            {param_rows}
        </table>

        <h2>🎯 参数预设方案</h2>
        <table>
            <tr><th>方案名</th><th>描述</th><th>参数数量</th><th>操作</th></tr>
            {preset_rows}
        </table>
    </div>

    <script>
    // 效果分布图
    var effectChart = echarts.init(document.getElementById('effectChart'));
    effectChart.setOption({{
        tooltip: {{ trigger: 'axis' }},
        legend: {{ data: ['正向效果', '负向效果', '未验证'] }},
        xAxis: {{ type: 'category', data: ['效果统计'] }},
        yAxis: {{ type: 'value' }},
        series: [
            {{ name: '正向效果', type: 'bar', data: [{stats['effect_positive']}], itemStyle: {{color: '#22c55e'}} }},
            {{ name: '负向效果', type: 'bar', data: [{stats['effect_negative']}], itemStyle: {{color: '#ef4444'}} }},
            {{ name: '未验证', type: 'bar', data: [{stats['applied'] - stats['effect_verified']}], itemStyle: {{color: '#94a3b8'}} }}
        ]
    }});

    function applyPreset(name) {{
        alert('预设方案 "' + name + '" 将在框架重启后生效（需手动写入config_override.json）');
    }}
    </script>
</body>
</html>"""

        # 保存报告
        report_filename = f"param_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        report_path = os.path.join(_REPORT_DIR, report_filename)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(html)

        # 同时保存最新报告
        latest_path = os.path.join(_REPORT_DIR, "latest.html")
        with open(latest_path, "w", encoding="utf-8") as f:
            f.write(html)

        return report_path


class ParamPresets:
    """参数预设方案管理"""

    PRESETS_FILE = os.path.join(_PROJECT_ROOT, "data", "param_presets.json")

    # 内置预设方案
    BUILTIN_PRESETS = {
        "high_performance": {
            "name": "高性能模式",
            "description": "大模型调用多、搜索深度大、推理深度高，适合复杂任务",
            "params": {
                "llm_timeout_seconds": 60,
                "llm_model_downgrade_threshold": 5,
                "search_max_results": 20,
                "search_max_depth": 3,
                "inner_world_max_inference_depth": 5,
                "output_relevance_threshold": 0.45,
                "knowledge_query_max_results": 30,
                "heart_base_interval": 0.8,
                "code_learner_scan_interval": 300,
            },
        },
        "energy_saving": {
            "name": "节能模式",
            "description": "减少大模型调用、降低心跳频率、限制搜索深度，适合长时间运行",
            "params": {
                "llm_timeout_seconds": 30,
                "llm_model_downgrade_threshold": 2,
                "search_max_results": 10,
                "search_max_depth": 1,
                "inner_world_max_inference_depth": 2,
                "heart_base_interval": 2.0,
                "heart_max_interval": 5.0,
                "code_learner_scan_interval": 1800,
                "semantic_api_call_limit_per_hour": 15,
            },
        },
        "learning_mode": {
            "name": "学习模式",
            "description": "增加知识检索、代码学习频率、消化阈值宽松，适合快速学习",
            "params": {
                "knowledge_query_max_results": 40,
                "knowledge_min_similarity": 0.3,
                "code_learner_scan_interval": 120,
                "code_learner_max_methods_per_scan": 50,
                "stomach_min_keywords": 1,
                "stomach_min_content_length": 100,
                "stomach_trust_threshold": 20,
                "subconscious_inspiration_interval": 60,
                "interest_decay_rate": 0.005,
            },
        },
        "stable_mode": {
            "name": "稳定模式",
            "description": "保守参数、减少自动调整、提高验证阈值，适合生产环境",
            "params": {
                "output_relevance_threshold": 0.6,
                "knowledge_min_similarity": 0.5,
                "llm_model_downgrade_threshold": 3,
                "heart_base_interval": 1.0,
                "param_patch_max_per_cycle": 1,
                "param_patch_min_effect_threshold": 0.15,
                "stomach_min_keywords": 3,
                "stomach_min_content_length": 300,
                "risk_perception_threshold": 0.6,
            },
        },
        "balanced_mode": {
            "name": "均衡模式",
            "description": "性能与资源的平衡，适合日常使用",
            "params": {
                "llm_timeout_seconds": 45,
                "search_max_results": 15,
                "search_max_depth": 2,
                "inner_world_max_inference_depth": 3,
                "output_relevance_threshold": 0.5,
                "knowledge_query_max_results": 20,
                "heart_base_interval": 1.0,
                "code_learner_scan_interval": 600,
                "stomach_min_keywords": 2,
            },
        },
    }

    def __init__(self):
        self._ensure_presets_file()

    def _ensure_presets_file(self):
        """确保预设文件存在"""
        if not os.path.exists(self.PRESETS_FILE):
            os.makedirs(os.path.dirname(self.PRESETS_FILE), exist_ok=True)
            with open(self.PRESETS_FILE, "w", encoding="utf-8") as f:
                json.dump({"presets": self.BUILTIN_PRESETS, "custom_presets": {}}, f,
                          ensure_ascii=False, indent=2)

    def list_presets(self) -> list[dict[str, Any]]:
        """列出所有预设方案"""
        try:
            data = safe_read_json(self.PRESETS_FILE, default={})
            presets = []
            for key, preset in data.get("presets", {}).items():
                presets.append({"key": key, **preset})
            for key, preset in data.get("custom_presets", {}).items():
                presets.append({"key": key, **preset, "custom": True})
            return presets
        except Exception as e:
            print(f"[WARNING] ParamAnalysisReport.py:416: {type(e).__name__}: {e}")
            return []

    def apply_preset(self, preset_key: str) -> dict[str, Any]:
        """应用预设方案到config_override.json"""
        try:
            data = safe_read_json(self.PRESETS_FILE, default={})
            preset = data.get("presets", {}).get(preset_key) or \
                     data.get("custom_presets", {}).get(preset_key)
            if not preset:
                return {"success": False, "error": f"预设方案 {preset_key} 不存在"}

            # 写入config_override.json
            override_path = os.path.join(_PROJECT_ROOT, "data", "config_override.json")
            override_data = {}
            if os.path.exists(override_path):
                override_data = safe_read_json(override_path, default={})

            if "RUNTIME_PARAMS" not in override_data:
                override_data["RUNTIME_PARAMS"] = {}

            override_data["RUNTIME_PARAMS"].update(preset["params"])
            override_data["_last_preset_applied"] = preset_key
            override_data["_last_preset_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            with open(override_path, "w", encoding="utf-8") as f:
                json.dump(override_data, f, ensure_ascii=False, indent=2)

            return {
                "success": True,
                "preset": preset_key,
                "params_applied": len(preset["params"]),
                "message": f"预设方案 '{preset['name']}' 已应用，将在5秒内热加载生效",
            }
        except Exception as e:
            print(f"[WARNING] ParamAnalysisReport.py:450: {type(e).__name__}: {e}")
            return {"success": False, "error": str(e)}

    def create_custom_preset(self, name: str, description: str,
                             params: dict[str, Any]) -> dict[str, Any]:
        """创建自定义预设方案"""
        try:
            data = safe_read_json(self.PRESETS_FILE, default={})
            key = f"custom_{int(time.time())}"
            data["custom_presets"][key] = {
                "name": name,
                "description": description,
                "params": params,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(self.PRESETS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return {"success": True, "key": key, "name": name}
        except Exception as e:
            print(f"[WARNING] ParamAnalysisReport.py:468: {type(e).__name__}: {e}")
            return {"success": False, "error": str(e)}

    def get_optimal_preset(self) -> dict[str, Any]:
        """根据历史效果推荐最优预设方案"""
        analyzer = ParamAnalyzer()
        stats = analyzer.get_statistics()

        # 根据历史效果计算各预设的匹配度
        preset_scores = {}
        for key, preset in self.BUILTIN_PRESETS.items():
            score = 0
            for param in preset["params"]:
                param_stat = stats.get("param_stats", {}).get(param, {})
                if param_stat.get("effect_count", 0) > 0:
                    score += param_stat["avg_effect"]
            preset_scores[key] = score / max(len(preset["params"]), 1)

        best = max(preset_scores, key=preset_scores.get)
        return {
            "recommended": best,
            "scores": preset_scores,
            "preset_info": self.BUILTIN_PRESETS[best],
        }


# 便捷函数
def generate_report() -> str:
    """生成参数分析报告"""
    analyzer = ParamAnalyzer()
    return analyzer.generate_html_report()


def analyze_correlations() -> dict[str, Any]:
    """分析参数相关性"""
    analyzer = ParamAnalyzer()
    return analyzer.analyze_correlations()


def apply_preset(preset_key: str) -> dict[str, Any]:
    """应用预设方案"""
    presets = ParamPresets()
    return presets.apply_preset(preset_key)


def list_presets() -> list[dict[str, Any]]:
    """列出所有预设方案"""
    presets = ParamPresets()
    return presets.list_presets()
