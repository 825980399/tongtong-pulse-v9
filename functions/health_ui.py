"""health_ui —— 曈曈人体UI · 独立Web监控面板（v2.1 知识图谱文本化版）

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

from nucleus.data.DataAccessLayer import safe_read_json
from nucleus._silent_except import silent_exc

# 功能模块元数据声明
FUNCTION_META = {
    "name": "人体UI监控面板",
    "class_name": "HealthUIServer",
    "always_on": True,
    "thread_mode": "background",
}
# 全局节点池引用（由 main.py 注入）
_node_pool = None

# HTML模板（内嵌，无需外部文件）
HEALTH_UI_HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>曈曈 v9.5 · 人体UI</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #0a0a0f; color: #d0d0d0; font-family: 'Microsoft YaHei', sans-serif; padding: 0; }
/* 导航栏 */
.nav { background: #0d0d1a; border-bottom: 1px solid #1a1a2e; padding: 10px 20px; display: flex; gap: 15px; align-items: center; position: sticky; top: 0; z-index: 20; }
.nav h1 { font-size: 1.2em; color: #00d4ff; margin-right: auto; }
.nav-btn { padding: 8px 18px; border-radius: 20px; border: 1px solid #2a2a3a; background: transparent; color: #888; cursor: pointer; font-size: 0.85em; transition: all 0.2s; }
.nav-btn:hover { border-color: #00d4ff; color: #00d4ff; }
.nav-btn.active { background: #00d4ff; color: #0a0a0f; border-color: #00d4ff; font-weight: bold; }
.nav .update { color: #555; font-size: 0.75em; margin-left: 10px; }
/* 面板容器 */
.panel { display: none; padding: 15px; }
.panel.active { display: block; }
/* 卡片布局 */
.row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; margin-bottom: 10px; }
@media (max-width: 800px) { .row { grid-template-columns: 1fr; } }
.card { background: #0d0d1a; border-radius: 10px; padding: 14px; border: 1px solid #1a1a2e; margin-bottom: 10px; }
.card-title { font-size: 0.9em; color: #777; margin-bottom: 8px; }
.metric { display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px solid #151525; font-size: 0.85em; }
.metric:last-child { border-bottom: none; }
.metric-label { color: #666; }
.metric-value { color: #d0d0d0; font-weight: bold; }
.good { color: #00ff88; }
.warn { color: #ffaa00; }
.bad { color: #ff4444; }
.big-num { text-align: center; font-size: 2.5em; font-weight: bold; color: #ff4477; padding: 10px 0; }
.big-num .unit { font-size: 0.4em; color: #555; }
.footer { text-align: center; padding: 15px; color: #333; font-size: 0.75em; }
.status-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin: 1px; }
.green { background: #00ff88; }
.red { background: #ff4444; }
.yellow { background: #ffaa00; }
.gray { background: #444; }
.link-row { display: flex; gap: 8px; flex-wrap: wrap; }
.link-tag { padding: 4px 10px; border-radius: 12px; font-size: 0.75em; border: 1px solid; }
.log-scroll { max-height: 180px; overflow-y: auto; font-family: Consolas, monospace; font-size: 0.75em; line-height: 1.4; }
.log-scroll::-webkit-scrollbar { width: 4px; }
.log-scroll::-webkit-scrollbar-track { background: #0a0a12; }
.log-scroll::-webkit-scrollbar-thumb { background: #1a1a2e; }
/* 进化仪表盘专用 */
.score-circle { width: 120px; height: 120px; border-radius: 50%; border: 8px solid #2a2a3a; display: flex; align-items: center; justify-content: center; margin: 20px auto; font-size: 36px; font-weight: bold; }
.timeline { max-height: 400px; overflow-y: auto; }
.timeline-item { padding: 10px; border-left: 3px solid #00d4ff; margin: 10px 0; background: #14141f; border-radius: 0 8px 8px 0; }
.timeline-item .time { font-size: 12px; color: #555; }
.patch-item { padding: 12px; margin: 8px 0; background: #14141f; border-radius: 8px; border: 1px solid #2a2a3a; }
.module-bar { margin: 10px 0; }
.module-bar .label { display: flex; justify-content: space-between; margin-bottom: 4px; font-size: 13px; }
.module-bar .bar { height: 8px; border-radius: 4px; background: #2a2a3a; }
.module-bar .fill { height: 100%; border-radius: 4px; transition: width 0.5s; }

/* ★ 知识图谱树状文本列表样式 */
.tree-container { max-height: 80vh; overflow-y: auto; padding: 10px; font-family: 'Consolas', 'Microsoft YaHei', monospace; }
.tree-node { padding: 4px 0; border-bottom: 1px solid #151525; cursor: default; }
.tree-node .lvl { display: inline-block; width: 28px; text-align: center; border-radius: 4px; font-size: 0.7em; font-weight: bold; margin-right: 8px; }
.lvl-L4 { background: #ff4477; color: #fff; }
.lvl-L3 { background: #ffaa00; color: #000; }
.lvl-L2 { background: #00d4ff; color: #000; }
.lvl-L1 { background: #555; color: #aaa; }
.tree-path { color: #555; font-size: 0.75em; }
.tree-label { color: #e0e0e0; font-weight: bold; margin-left: 10px; }
.tree-trust { color: #ffaa00; font-size: 0.8em; margin-left: 8px; }
.tree-value { color: #888; font-size: 0.75em; margin-left: 8px; word-break: break-all; }
.tree-indent { margin-left: 20px; border-left: 1px solid #222; padding-left: 10px; }
.tree-group { margin-bottom: 15px; }
.tree-group-title { color: #00d4ff; font-size: 0.9em; font-weight: bold; padding: 5px 0; border-bottom: 1px solid #1a1a2e; margin-bottom: 5px; cursor: pointer; }
.tree-group-title:hover { color: #fff; }
.path-tag { display: inline-block; background: #1a1a2e; color: #aaa; padding: 1px 6px; border-radius: 3px; font-size: 0.7em; margin-right: 4px; }
.search-box { margin-bottom: 10px; display: flex; gap: 8px; }
.search-box input { flex: 1; background: #0d0d1a; border: 1px solid #1a1a2e; color: #e0e0e0; padding: 6px 10px; border-radius: 6px; font-family: inherit; }
.search-box button { background: #00d4ff; color: #0a0a0f; border: none; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-weight: bold; }
.search-box button:hover { background: #00b8e6; }
</style>
</head>
<body>

<!-- 顶部导航 -->
<div class="nav">
    <h1>曈曈 v9.5 · 人体UI</h1>
    <button class="nav-btn active" onclick="switchPanel('monitor', this)">📊 监控总览</button>
    <button class="nav-btn" onclick="switchPanel('evolution', this)">🧬 进化仪表盘</button>
    <button class="nav-btn" onclick="switchPanel('knowledge', this)">🧠 知识图谱</button>
    <button class="nav-btn" onclick="switchPanel('params', this)">⚙️ 参数进化</button>
    <span class="update">刷新：<span id="updateTime">--</span></span>
</div>

<!-- ==================== 面板1：监控总览 ==================== -->
<div id="panel-monitor" class="panel active">
    <!-- ★第十一批 任务1：LLM 依赖度卡片（自包含的独立取数块，不干扰既有刷新逻辑） -->
    <div class="row">
        <div class="card">
            <div class="card-title">🧠 LLM 依赖度</div>
            <div class="big-num"><span id="llmdepRatio">--</span><span class="unit"> 依赖度</span></div>
            <div class="metric"><span class="metric-label">自持力</span><span class="metric-value" id="llmdepSelf">--</span></div>
            <div class="metric"><span class="metric-label">LLM / 本地</span><span class="metric-value" id="llmdepSplit">--</span></div>
            <div class="metric"><span class="metric-label">搜索 / 消化</span><span class="metric-value" id="llmdepOther">--</span></div>
            <div class="metric"><span class="metric-label">回答请求</span><span class="metric-value" id="llmdepTotal">--</span></div>
            <!-- ★第94批 T-94c 方案A：全栈大模型占比（分母含搜索+消化） -->
            <div class="metric"><span class="metric-label">全栈大模型占比</span><span class="metric-value" id="llmdepOverall">--</span></div>
        </div>
    </div>
    <script>
    function loadLLMDep() {
        fetch('/llmdep/data').then(function (r) { return r.json(); }).then(function (d) {
            var dv = d.derived || {};
            var set = function (id, v) { var el = document.getElementById(id); if (el) { el.textContent = v; } };
            set('llmdepRatio', (dv.llm_dependency_ratio || 0).toFixed(3));
            set('llmdepSelf', (dv.self_sufficiency_score || 0).toFixed(3));
            set('llmdepSplit', (dv.llm_call_total || 0) + ' / ' + (dv.local_inference_total || 0));
            set('llmdepOther', (dv.search_total || 0) + ' / ' + (dv.digestion_total || 0));
            // ★第95批 T-95c：旧字段 total_requests 已彻底移除，只读 answer_requests
            set('llmdepTotal', (dv.answer_requests || 0));
            set('llmdepOverall', (dv.overall_llm_share || 0).toFixed(4));
        }).catch(function () { });
    }
    setInterval(loadLLMDep, 10000);
    loadLLMDep();
    </script>
    <!-- 第一行：核心实时数据 -->
    <div class="row">
        <div class="card">
            <div class="card-title">❤️ 动态心率</div>
            <div class="big-num"><span id="heartRate">--</span><span class="unit"> 秒/次</span></div>
            <div class="metric"><span class="metric-label">心跳次数</span><span class="metric-value" id="heartBeatCount">--</span></div>
            <div class="metric"><span class="metric-label">状态</span><span class="metric-value" id="heartStatus">--</span></div>
        </div>
        <div class="card">
            <div class="card-title">👤 当前用户</div>
            <div style="text-align:center;font-size:1.3em;font-weight:bold;color:#00d4ff;padding:10px;" id="userName">--</div>
            <div style="text-align:center;color:#666;font-size:0.8em;" id="userRelation">--</div>
            <div class="metric"><span class="metric-label">亲密度</span><span class="metric-value" id="userCloseness">--</span></div>
            <div class="metric"><span class="metric-label">信任度</span><span class="metric-value" id="userTrust">--</span></div>
        </div>
        <div class="card">
            <div class="card-title">🔗 核心链路</div>
            <div class="link-row" id="linkPanel">加载中...</div>
        </div>
    </div>
    <!-- 情绪状态行 -->
    <div class="row">
        <div class="card">
            <div class="card-title">💓 生命状态</div>
            <div style="text-align:center;font-size:1.5em;padding:10px;color:#00d4ff;" id="lifeState">--</div>
            <div class="metric"><span class="metric-label">状态描述</span><span class="metric-value" id="lifeStateDesc" style="font-size:0.8em;">--</span></div>
            <div class="metric"><span class="metric-label">探索间隔</span><span class="metric-value" id="lifeExploreInt">--</span></div>
            <div class="metric"><span class="metric-label">梦境间隔</span><span class="metric-value" id="lifeDreamInt">--</span></div>
        </div>
        <div class="card">
            <div class="card-title">🧪 当前情绪</div>
            <div style="text-align:center;font-size:2em;padding:10px;" id="emotionCurrent">--</div>
            <div class="metric"><span class="metric-label">强度</span><span class="metric-value" id="emotionIntensity">--</span></div>
        </div>
        <div class="card">
            <div class="card-title">📈 情绪趋势</div>
            <div style="text-align:center;font-size:1.2em;padding:8px;color:#888;" id="emotionTrend">--</div>
        </div>
    </div>
    <!-- 系统状态 -->
    <div class="row">
        <div class="card">
            <div class="card-title">🫀 器官状态</div>
            <div style="min-height:20px;" id="organStatusBar">加载中...</div>
            <div class="metric"><span class="metric-label">总计</span><span class="metric-value" id="organTotal">--</span></div>
            <div class="metric"><span class="metric-label">在线</span><span class="metric-value good" id="organRunning">--</span></div>
            <div class="metric"><span class="metric-label">熔断</span><span class="metric-value bad" id="organFused">--</span></div>
        </div>
        <div class="card">
            <div class="card-title">📡 脉冲统计</div>
            <div class="metric"><span class="metric-label">发射</span><span class="metric-value" id="pulseEmitted">--</span></div>
            <div class="metric"><span class="metric-label">匹配</span><span class="metric-value good" id="pulseMatched">--</span></div>
            <div class="metric"><span class="metric-label">未匹配</span><span class="metric-value warn" id="pulseUnmatched">--</span></div>
            <div class="metric"><span class="metric-label">发布</span><span class="metric-value" id="fieldPublished">--</span></div>
        </div>
        <div class="card">
            <div class="card-title">🧠 知识演化</div>
            <div class="metric"><span class="metric-label">总节点</span><span class="metric-value" id="nodesTotal">--</span></div>
            <div class="metric"><span class="metric-label">🔒 L4 本能</span><span class="metric-value good" id="nodesInstinct">--</span></div>
            <div class="metric"><span class="metric-label">🟢 L3 智慧</span><span class="metric-value good" id="nodesHot">--</span></div>
            <div class="metric"><span class="metric-label">🟡 L2 认知</span><span class="metric-value" id="nodesWarm">--</span></div>
            <div class="metric"><span class="metric-label">⚪ L1 感知</span><span class="metric-value" id="nodesCold">--</span></div>
        </div>
    </div>
    <!-- ★ 运行时指标（真实框架运行状态） -->
    <div class="row">
        <div class="card">
            <div class="card-title">⚡ 运行时指标</div>
            <div class="metric"><span class="metric-label">脉冲处理</span><span class="metric-value" id="rtPulseCount">--</span></div>
            <div class="metric"><span class="metric-label">平均耗时</span><span class="metric-value" id="rtPulseAvg">--</span></div>
            <div class="metric"><span class="metric-label">峰值耗时</span><span class="metric-value" id="rtPulseMax">--</span></div>
            <div class="metric"><span class="metric-label">锁等待(均)</span><span class="metric-value warn" id="rtLockWait">--</span></div>
        </div>
        <div class="card">
            <div class="card-title">🧵 资源状态</div>
            <div class="metric"><span class="metric-label">活跃线程</span><span class="metric-value" id="rtThreads">--</span></div>
            <div class="metric"><span class="metric-label">队列峰值</span><span class="metric-value" id="rtQueueDepth">--</span></div>
            <div class="metric"><span class="metric-label">重入触发</span><span class="metric-value warn" id="rtReentry">--</span></div>
            <div class="metric"><span class="metric-label">运行异常</span><span class="metric-value bad" id="rtErrors">--</span></div>
        </div>
        <div class="card">
            <div class="card-title">🚨 最近异常</div>
            <div class="log-scroll" id="rtErrorSnapshots" style="max-height:120px;">暂无异常</div>
        </div>
    </div>
    <!-- 日志区域（省略部分，与原版相同，使用原有结构即可，下同） -->
    <div class="row">
        <div class="card" style="grid-column: span 2;">
            <div class="card-title">👁️ 视觉流实时数据</div>
            <div class="log-scroll" id="visionLog">加载中...</div>
        </div>
        <div class="card">
            <div class="card-title">📷 眼睛推流</div>
            <div class="log-scroll" id="eyeLog">加载中...</div>
        </div>
    </div>
    <div class="row">
        <div class="card"><div class="card-title">👂 耳朵监听</div><div class="log-scroll" id="earLog">加载中...</div></div>
        <div class="card"><div class="card-title">📡 脉冲流转</div><div class="log-scroll" id="traceEvents">加载中...</div></div>
        <div class="card"><div class="card-title">🔊 嘴巴输出</div><div class="log-scroll" id="mouthLog">加载中...</div></div>
    </div>
    <div class="row">
        <div class="card"><div class="card-title">🖥️ 控制器</div>
            <div class="metric"><span class="metric-label">操作次数</span><span class="metric-value" id="ctrlOps">--</span></div>
            <div class="metric"><span class="metric-label">读文件</span><span class="metric-value" id="ctrlFiles">--</span></div>
            <div class="metric"><span class="metric-label">打开网页</span><span class="metric-value" id="ctrlUrls">--</span></div>
        </div>
        <div class="card"><div class="card-title">🔎 无头浏览器</div>
            <div class="metric"><span class="metric-label">累计搜索</span><span class="metric-value" id="headlessTotal">--</span></div>
            <div class="metric"><span class="metric-label">成功搜索</span><span class="metric-value good" id="headlessSuccess">--</span></div>
            <div class="metric"><span class="metric-label">精读文章</span><span class="metric-value" id="headlessArticles">--</span></div>
            <div class="metric"><span class="metric-label">消化字符</span><span class="metric-value" id="headlessChars">--</span></div>
            <div style="text-align:center;font-size:0.75em;color:#666;margin-top:5px;" id="headlessLastTopic">--</div>
        </div>
        <div class="card"><div class="card-title">💬 社会情感</div><div class="metric" id="socialEmotionList"><span style="color:#666;">等待数据...</span></div></div>
        <div class="card"><div class="card-title">🎯 兴趣方向</div><div class="metric" id="interestList"><span style="color:#666;">等待数据...</span></div></div>
    </div>
    <div class="row">
        <div class="card"><div class="card-title">🦵 双腿学习</div><div class="log-scroll" id="legsLog" style="max-height:150px;">加载中...</div></div>
    </div>
    <div class="row">
        <div class="card"><div class="card-title">🌙 梦境推演</div><div style="text-align:center;font-size:1.5em;padding:8px;color:#888;" id="dreamCount">--</div><div style="text-align:center;font-size:0.8em;color:#666;" id="dreamTopic">--</div></div>
        <div class="card"><div class="card-title">🪞 自我反思</div><div style="text-align:center;font-size:1.5em;padding:8px;color:#888;" id="reflectionCount">--</div><div style="text-align:center;font-size:0.8em;color:#666;" id="reflectionDomain">--</div></div>
        <div class="card"><div class="card-title">🧬 知识压缩</div>
            <div class="metric"><span class="metric-label">L1→L2次数</span><span class="metric-value" id="liverCompress">--</span></div>
            <div class="metric"><span class="metric-label">L2→L3次数</span><span class="metric-value" id="liverFuse">--</span></div>
            <div class="metric"><span class="metric-label">本能升级</span><span class="metric-value" id="instinctUpgrade">--</span></div>
        </div>
    </div>
    <div class="card"><div class="card-title">🚨 异常脉冲记录</div><div class="log-scroll" id="orphanEvents" style="max-height:150px;">加载中...</div></div>
</div>

<!-- ==================== 面板2：进化仪表盘 ==================== -->
<div id="panel-evolution" class="panel">
    <div class="row">
        <div class="card">
            <h2>综合健康评分</h2>
            <div class="score-circle" id="evo-score">--</div>
            <div style="text-align:center;color:#888;" id="evo-score-label">加载中...</div>
        </div>
        <div class="card">
            <h2>模块健康</h2>
            <div id="evo-modules">加载中...</div>
        </div>
    </div>
    <div class="row">
        <div class="card">
            <h2>进化时间线</h2>
            <div class="timeline" id="evo-timeline">加载中...</div>
        </div>
        <div class="card">
            <h2>待处理建议</h2>
            <div id="evo-patches">加载中...</div>
        </div>
    </div>
</div>

<!-- ==================== 面板3：知识图谱（文本树状结构） ==================== -->
<div id="panel-knowledge" class="panel">
    <div class="card" style="margin-bottom:5px;">
        <div class="search-box">
            <input type="text" id="kg-search" placeholder="搜索节点（关键词/路径/层级）..." oninput="filterKG()">
            <button onclick="refreshKG()">🔄 刷新</button>
        </div>
        <div style="font-size:0.75em;color:#666;">总节点：<span id="kg-total-nodes">0</span> · 连线：<span id="kg-total-links">0</span> · 最后更新：<span id="kg-update-time">--</span></div>
    </div>
    <div class="card" style="padding:8px;">
        <div class="tree-container" id="kg-tree-container">
            加载中...
        </div>
    </div>

<!-- ==================== 面板4：参数进化 ==================== -->
<div id="panel-params" class="panel">
    <div class="row">
        <div class="card">
            <div class="card-title">📊 参数补丁统计</div>
            <div class="big-num"><span id="paramTotal">--</span><span class="unit"> 个补丁</span></div>
            <div class="metric"><span class="metric-label">已应用</span><span class="metric-value good" id="paramApplied">--</span></div>
            <div class="metric"><span class="metric-label">已回滚</span><span class="metric-value bad" id="paramRolledBack">--</span></div>
            <div class="metric"><span class="metric-label">效果验证</span><span class="metric-value" id="paramVerified">--</span></div>
            <div class="metric"><span class="metric-label">正向效果</span><span class="metric-value good" id="paramPositive">--</span></div>
        </div>
        <div class="card">
            <div class="card-title">🔧 当前预设方案</div>
            <div style="text-align:center;font-size:1.3em;font-weight:bold;color:#00d4ff;padding:10px;" id="currentPreset">--</div>
            <div style="text-align:center;color:#666;font-size:0.8em;" id="presetDesc">--</div>
            <div class="metric"><span class="metric-label">应用时间</span><span class="metric-value" id="presetTime">--</span></div>
            <div style="margin-top:10px;">
                <select id="presetSelect" style="width:100%;background:#0d0d1a;color:#e0e0e0;border:1px solid #1a1a2e;padding:6px;border-radius:6px;">
                    <option value="">选择预设方案...</option>
                </select>
                <button onclick="applyPreset()" style="width:100%;margin-top:8px;background:#00d4ff;color:#0a0a0f;border:none;padding:8px;border-radius:6px;cursor:pointer;font-weight:bold;">应用预设</button>
            </div>
        </div>
        <div class="card">
            <div class="card-title">📈 参数热加载</div>
            <div class="metric"><span class="metric-label">RUNTIME_PARAMS</span><span class="metric-value" id="runtimeParamsCount">--</span></div>
            <div class="metric"><span class="metric-label">已接入器官</span><span class="metric-value good" id="organsWithRefresh">--</span></div>
            <div class="metric"><span class="metric-label">配置变更次数</span><span class="metric-value" id="configChanges">--</span></div>
            <div class="metric"><span class="metric-label">最后热加载</span><span class="metric-value" id="lastHotReload">--</span></div>
            <div style="text-align:center;margin-top:8px;">
                <button onclick="refreshParams()" style="background:#00d4ff;color:#0a0a0f;border:none;padding:6px 16px;border-radius:6px;cursor:pointer;">🔄 刷新参数</button>
            </div>
        </div>
    </div>
    <div class="row">
        <div class="card" style="grid-column: span 2;">
            <div class="card-title">📋 参数补丁列表</div>
            <div class="log-scroll" id="paramPatchList" style="max-height:300px;">加载中...</div>
        </div>
        <div class="card">
            <div class="card-title">🔗 参数相关性 Top10</div>
            <div class="log-scroll" id="paramCorrelations" style="max-height:300px;">加载中...</div>
        </div>
    </div>
    <div class="row">
        <div class="card" style="grid-column: span 3;">
            <div class="card-title">📝 参数变更历史</div>
            <div class="log-scroll" id="paramChangeHistory" style="max-height:200px;">加载中...</div>
        </div>
    </div>
</div>

<div class="footer">曈曈 v9.5 PulseNet · 数据实时刷新</div>

<script>
// ========== 面板切换 ==========
function switchPanel(name, btn) {
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('panel-' + name).classList.add('active');
    if (btn) btn.classList.add('active');
    if (name === 'knowledge') { loadKGData(); }
    if (name === 'evolution') { loadEvoData(); }
    if (name === 'params') { loadParamsData(); }
}

// ========== 监控总览数据（原有逻辑） ==========
var DATA_PATH = '/data/monitor/health_snapshot.json';
function fetchData() {
    fetch(DATA_PATH).then(function(r) { return r.json(); }).then(function(d) {
        document.getElementById('updateTime').textContent = new Date().toLocaleTimeString();
        var o = d.organs || {}, p = d.pulse || {}, f = d.info_field || {}, n = d.nodes || {}, e = d.knowledge_evolution || {};
        var el;
        el = document.getElementById('organTotal'); if (el) el.textContent = o.total || 0;
        el = document.getElementById('organRunning'); if (el) el.textContent = o.running || 0;
        el = document.getElementById('organFused'); if (el) el.textContent = o.fused || 0;
        var bar = '';
        for (var i=0; i<(o.running||0); i++) bar += '<span class="status-dot green"></span>';
        for (var i=0; i<(o.fused||0); i++) bar += '<span class="status-dot red"></span>';
        el = document.getElementById('organStatusBar'); if (el) el.innerHTML = bar || '无数据';
        var emitted = p.emitted || 0, matched = f.matched || 0;
        el = document.getElementById('pulseEmitted'); if (el) el.textContent = emitted;
        el = document.getElementById('pulseMatched'); if (el) el.textContent = matched;
        el = document.getElementById('pulseUnmatched'); if (el) el.textContent = emitted - matched;
        el = document.getElementById('fieldPublished'); if (el) el.textContent = f.published || 0;
        el = document.getElementById('nodesTotal'); if (el) el.textContent = n.total || 0;
        el = document.getElementById('nodesInstinct'); if (el) el.textContent = (e.instinct_count || 0);
        el = document.getElementById('nodesHot'); if (el) el.textContent = (e.l3_pure || e.l3_count || 0);
        el = document.getElementById('nodesWarm'); if (el) el.textContent = (e.l2_count || 0);
        el = document.getElementById('nodesCold'); if (el) el.textContent = (e.l1_count || 0);
        var hb = d.heartbeat || {};
        if (hb.interval) {
            el = document.getElementById('heartRate'); if (el) el.textContent = hb.interval.toFixed(1);
            el = document.getElementById('heartBeatCount'); if (el) el.textContent = hb.beat_count || 0;
            el = document.getElementById('heartStatus'); if (el) el.textContent = hb.status || '';
        }
        var u = d.current_user || '访客';
        el = document.getElementById('userName'); if (el) el.textContent = u;
        if (u === '小林') {
            el = document.getElementById('userRelation'); if (el) el.textContent = '创造者·父亲';
            el = document.getElementById('userCloseness'); if (el) el.textContent = '1.00';
            el = document.getElementById('userTrust'); if (el) el.textContent = '1.00';
        } else if (u === '访客') {
            el = document.getElementById('userRelation'); if (el) el.textContent = '陌生人';
            el = document.getElementById('userCloseness'); if (el) el.textContent = '0.05';
            el = document.getElementById('userTrust'); if (el) el.textContent = '0.10';
        } else {
            el = document.getElementById('userRelation'); if (el) el.textContent = '已识别';
            el = document.getElementById('userCloseness'); if (el) el.textContent = '--';
            el = document.getElementById('userTrust'); if (el) el.textContent = '--';
        }
        var ctrl = d.controller || {};
        el = document.getElementById('ctrlOps'); if (el) el.textContent = (ctrl.operations || 0) + ' 次';
        el = document.getElementById('ctrlFiles'); if (el) el.textContent = (ctrl.files_read || 0) + ' 次';
        el = document.getElementById('ctrlUrls'); if (el) el.textContent = (ctrl.urls_opened || 0) + ' 次';
        var hb2 = d.headless_browser || {};
        el = document.getElementById('headlessTotal'); if (el) el.textContent = (hb2.total_searches || 0) + ' 次';
        el = document.getElementById('headlessSuccess'); if (el) el.textContent = (hb2.successful_searches || 0) + ' 次';
        el = document.getElementById('headlessArticles'); if (el) el.textContent = (hb2.total_articles || 0) + ' 篇';
        el = document.getElementById('headlessChars'); if (el) el.textContent = ((hb2.total_chars || 0) / 1000).toFixed(1) + 'k 字符';
        el = document.getElementById('headlessLastTopic'); if (el) el.textContent = hb2.last_search_topic ? '最近: ' + hb2.last_search_topic : '等待首次搜索';
        var social = d.social_emotions || {};
        var socialEl = document.getElementById('socialEmotionList');
        if (socialEl) {
            var keys = Object.keys(social);
            if (keys.length > 0) {
                var html = '';
                for (var k=0; k<keys.length; k++) {
                    var val = social[keys[k]];
                    html += '<div class="metric"><span class="metric-label">'+keys[k]+'</span><span class="metric-value" style="color:'+(val>0.3?'#ffaa00':'#888')+';">'+(val*100).toFixed(0)+'%</span></div>';
                }
                socialEl.innerHTML = html;
            } else { socialEl.innerHTML = '<span style="color:#666;">暂无</span>'; }
        }
        var lifeState = d.life_state || {};
        el = document.getElementById('lifeState'); if (el) el.textContent = lifeState.state || '--';
        el = document.getElementById('lifeStateDesc'); if (el) el.textContent = lifeState.previous ? '从 ' + lifeState.previous + ' 切换' : '';
        el = document.getElementById('lifeExploreInt'); if (el) el.textContent = (lifeState.explore_interval || 0) + 's';
        el = document.getElementById('lifeDreamInt'); if (el) el.textContent = (lifeState.dream_interval || 0) + 's';
        var emotionState = d.emotion_state || {};
        el = document.getElementById('emotionIntensity'); if (el) el.textContent = (emotionState.intensity || 0).toFixed(2);
        var trend = emotionState.trend || {};
        el = document.getElementById('emotionTrend');
        if (el) {
            if (trend.description) { el.textContent = trend.description; el.style.color = trend.direction === 'falling' ? '#00ff88' : (trend.direction === 'rising' ? '#ffaa00' : '#888'); }
            else { el.textContent = '数据收集中...'; }
        }
        var interests = d.top_interests || [];
        var interestEl = document.getElementById('interestList');
        if (interestEl) {
            if (interests.length > 0) {
                var html = '';
                for (var i=0; i<interests.length; i++) { html += '<div class="metric"><span class="metric-label">'+interests[i].dim+'</span><span class="metric-value">'+interests[i].val.toFixed(2)+'</span></div>'; }
                interestEl.innerHTML = html;
            } else { interestEl.innerHTML = '<span style="color:#666;">暂无</span>'; }
        }
        var liver = d.liver || {};
        el = document.getElementById('liverCompress'); if (el) el.textContent = (liver.compress_count || 0) + ' 次';
        el = document.getElementById('liverFuse'); if (el) el.textContent = (liver.fuse_count || 0) + ' 次';
        el = document.getElementById('instinctUpgrade'); if (el) el.textContent = (liver.instinct_upgrade || 0) + ' 次';
        var ls = d.link_status;
        el = document.getElementById('linkPanel');
        if (el && ls) {
            var h = '', colors = {'connected':'#00ff88','mismatch':'#ffaa00','orphan':'#ff4444'};
            var labels = {'connected':'畅通','mismatch':'错配','orphan':'断裂'};
            var keys = Object.keys(ls);
            for (var k=0; k<keys.length; k++) {
                var link = ls[keys[k]], c = colors[link.status] || '#666', lb = labels[link.status] || '未知';
                h += '<span class="link-tag" style="border-color:'+c+';color:'+c+';">'+link.emitter+'→'+link.receiver+' '+lb+'</span>';
            }
            el.innerHTML = h;
        }
        var dream = d.dream || {};
        el = document.getElementById('dreamCount'); if (el) el.textContent = '💭 ' + (dream.count || 0) + ' 次';
        el = document.getElementById('dreamTopic'); if (el) el.textContent = dream.last_topic || '等待首次梦境';
        var reflection = d.reflection || {};
        el = document.getElementById('reflectionCount'); if (el) el.textContent = '🔍 ' + (reflection.count || 0) + ' 次';
        el = document.getElementById('reflectionDomain'); if (el) el.textContent = reflection.last_domain || '等待首次反思';
    }).catch(function(){});
}

// ========== 进化仪表盘 ==========
function loadEvoData() {
    fetch('/evolution/data').then(r=>r.json()).then(d=>{
        var s = d.score || 85;
        var circle = document.getElementById('evo-score');
        circle.textContent = s;
        circle.style.borderColor = s>=90?'#4CAF50':s>=75?'#8BC34A':s>=60?'#FF9800':'#f44336';
        circle.style.color = circle.style.borderColor;
        document.getElementById('evo-score-label').textContent = s>=90?'优秀':s>=75?'良好':s>=60?'一般':'需要关注';
        var modDiv = document.getElementById('evo-modules');
        modDiv.innerHTML = (d.modules||[]).map(m=>'<div class="module-bar"><div class="label"><span>'+escapeHtml(m.name)+'</span><span>'+m.score+'/100</span></div><div class="bar"><div class="fill" style="width:'+m.score+'%;background:'+(m.score>=80?'#4CAF50':m.score>=60?'#FF9800':'#f44336')+'"></div></div></div>').join('');
        var tl = document.getElementById('evo-timeline');
        tl.innerHTML = (d.timeline||[]).length===0?'<p style="color:#666;">暂无进化事件</p>':d.timeline.map(t=>'<div class="timeline-item"><div class="time">'+escapeHtml(t.time)+'</div><div class="content">'+escapeHtml(t.event)+'</div></div>').join('');
        var pt = document.getElementById('evo-patches');
        pt.innerHTML = (d.patches||[]).length===0?'<p style="color:#4CAF50;">✅ 没有待处理的建议</p>':d.patches.map(p=>'<div class="patch-item"><div><strong>'+escapeHtml(p.type)+'</strong> · '+escapeHtml(p.target)+'</div><div style="font-size:13px;color:#aaa;margin-top:4px;">'+escapeHtml(p.description)+'</div></div>').join('');
    });
}

// ========== ★ 知识图谱（文本树状） ==========
var kgAllNodes = [], kgAllLinks = [];
var kgFilteredNodes = [];

function refreshKG() {
    loadKGData();
}

function loadKGData() {
    fetch('/data/knowledge-graph.json').then(r=>r.json()).then(d=>{
        kgAllNodes = d.nodes || [];
        kgAllLinks = d.links || [];
        document.getElementById('kg-total-nodes').textContent = kgAllNodes.length;
        document.getElementById('kg-total-links').textContent = kgAllLinks.length;
        document.getElementById('kg-update-time').textContent = new Date().toLocaleTimeString();
        filterKG(); // 首次加载不过滤
    }).catch(function(e){
        document.getElementById('kg-tree-container').innerHTML = '<span style="color:#ff4444;">加载失败，请确认框架正在运行</span>';
    });
}

function filterKG() {
    var query = document.getElementById('kg-search').value.trim().toLowerCase();
    if (!query) {
        kgFilteredNodes = kgAllNodes;
    } else {
        kgFilteredNodes = kgAllNodes.filter(function(n) {
            var label = (n.label || '').toLowerCase();
            var path = (n.path || '').toLowerCase();
            var level = (n.level || '').toLowerCase();
            return label.indexOf(query) >= 0 || path.indexOf(query) >= 0 || level.indexOf(query) >= 0;
        });
    }
    renderKGTree(kgFilteredNodes, kgAllLinks);
}

function renderKGTree(nodes, links) {
    var container = document.getElementById('kg-tree-container');
    if (!nodes.length) {
        container.innerHTML = '<span style="color:#666;">暂无匹配节点</span>';
        return;
    }

    // 按根路径分组
    var groups = {};
    for (var i = 0; i < nodes.length; i++) {
        var n = nodes[i];
        var path = n.path || '/';
        var root = path.split('/')[1] || '根';
        if (!groups[root]) groups[root] = [];
        groups[root].push(n);
    }

    var html = '';
    var rootNames = Object.keys(groups).sort();
    for (var r = 0; r < rootNames.length; r++) {
        var root = rootNames[r];
        var groupNodes = groups[root];
        html += '<div class="tree-group">';
        html += '<div class="tree-group-title" onclick="toggleGroup(this)">📁 /' + root + ' (' + groupNodes.length + '个节点)</div>';
        html += '<div class="tree-indent" style="display:block;">';
        // 按层级排序
        var levelOrder = {'L4':1, 'L3':2, 'L2':3, 'L1':4};
        groupNodes.sort(function(a,b){ return (levelOrder[a.level]||5) - (levelOrder[b.level]||5); });
        for (var j = 0; j < groupNodes.length; j++) {
            var node = groupNodes[j];
            var level = node.level || 'L1';
            var label = node.label || '?';
            var trust = node.trust || 0;
            var path = node.path || '/';
            var value = node.value || '';
            html += '<div class="tree-node">';
            html += '<span class="lvl lvl-' + level + '">' + level + '</span>';
            html += '<span class="path-tag">' + escapeHtml(path) + '</span>';
            html += '<span class="tree-label">' + escapeHtml(label) + '</span>';
            html += '<span class="tree-trust">信任:' + trust.toFixed(0) + '</span>';
            if (value) html += '<span class="tree-value">' + escapeHtml(value.substring(0, 60)) + '</span>';
            html += '</div>';
        }
        html += '</div></div>';
    }
    container.innerHTML = html;
}

function escapeHtml(str) {
    return (str||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function toggleGroup(el) {
    var indent = el.nextElementSibling;
    if (indent.style.display === 'none') {
        indent.style.display = 'block';
    } else {
        indent.style.display = 'none';
    }
}

// ========== 日志加载函数 ==========
function loadLog(url, containerId, renderFn) { fetch(url).then(r=>r.json()).then(d=>{ var el=document.getElementById(containerId); if(el) el.innerHTML=renderFn(d); }).catch(function(){}); }
function renderVision(logs) {
    if (!logs || !logs.length) return '暂无数据';
    var h='', recent=logs.slice(-15).reverse();
    for (var i=0; i<recent.length; i++) { var e=recent[i], t=new Date(e.timestamp*1000).toLocaleTimeString(); h+='<div style="font-size:0.8em;padding:2px 0;border-bottom:1px solid #151525;"><span style="color:#666;">'+t+'</span> <span style="color:'+(e.face_detected?'#00ff88':'#ff4444')+';">人脸='+e.face_detected+'</span><span style="color:#888;"> 评分='+e.score+'</span> '+e.state+'</div>'; }
    return h;
}
function renderEye(logs) { if(!logs||!logs.length)return '暂无数据'; var h='', recent=logs.slice(-10).reverse(); for(var i=0;i<recent.length;i++){var e=recent[i],t=new Date(e.timestamp*1000).toLocaleTimeString();h+='<div style="font-size:0.8em;padding:2px 0;"><span style="color:#666;">'+t+'</span> <span style="color:#00d4ff;">帧#'+e.frame_seq+'</span><span style="color:#888;"> '+e.shape+' '+e.dtype+'</span></div>';}return h;}
function renderEar(logs) { if(!logs||!logs.length)return '暂无数据'; var h='',recent=logs.slice(-15).reverse(); for(var i=0;i<recent.length;i++){var e=recent[i],t=new Date(e.timestamp*1000).toLocaleTimeString();var ac=e.active?'#00ff88':'#ffaa00',at=e.active?'有声音':'静默';h+='<div style="font-size:0.8em;padding:2px 0;border-bottom:1px solid #151525;"><span style="color:#666;">'+t+'</span> <span style="color:'+ac+';">'+at+'</span><span style="color:#888;"> RMS='+(e.rms||0).toFixed(5)+'</span></div>';}return h;}
function renderTrace(events) { if(!events||!events.length)return '暂无数据'; var h='',now=Date.now()/1000,recent=events.slice(-20).reverse();for(var i=0;i<recent.length;i++){var e=recent[i],ago=(now-e.timestamp).toFixed(1);if(e.type==='emit'){var m=e.matched_organs||[],ok=m.length>0;h+='<div style="font-size:0.75em;padding:2px 0;color:'+(ok?'#888':'#ffaa00')+';"><span style="color:#666;">'+ago+'s</span> '+(ok?'✅':'⚠️')+' '+e.organ+' → '+e.event_type+' ('+e.layer+')'+(ok?' → '+m.join(','):' (无接收)')+'</div>';}else{h+='<div style="font-size:0.75em;padding:2px 0;color:#888;"><span style="color:#666;">'+ago+'s</span> 📥 '+e.organ+' ← '+e.event_type+' from '+e.from+'</div>';}}return h;}
function renderMouth(logs) { if(!logs||!logs.length)return '暂无数据'; var h='',recent=logs.slice(-15).reverse();for(var i=0;i<recent.length;i++){var e=recent[i],t=new Date(e.timestamp*1000).toLocaleTimeString();var sc=e.status==='success'?'#00ff88':(e.status==='beep'?'#ffaa00':'#ff4444');h+='<div style="font-size:0.8em;padding:2px 0;border-bottom:1px solid #151525;"><span style="color:#666;">'+t+'</span> <span style="color:'+sc+';">'+(e.status==='success'?'✅':(e.status==='beep'?'🔔':'❌'))+'</span><span style="color:#888;"> '+(e.text||'')+'</span></div>';}return h;}
function fetchLegsLog() { loadLog('/data/stream/legs_learn_log.json', 'legsLog', function(logs) { if(!logs||!logs.length) return '暂无学习记录'; var h='',recent=logs.slice(-15).reverse(); for(var i=0;i<recent.length;i++){var e=recent[i],t=new Date(e.timestamp*1000).toLocaleTimeString();h+='<div style="font-size:0.8em;padding:2px 0;border-bottom:1px solid #151525;"><span style="color:#666;">'+t+'</span> <span style="color:'+(e.source==='网络抓取'?'#00ff88':'#ffaa00')+';">['+e.source+']</span> <span style="color:#00d4ff;">'+e.direction+'</span> <span style="color:#888;">'+e.content_preview+'</span></div>';} return h; }); }
function renderOrphans(orphans) { if(!orphans||!orphans.length) return '✅ 暂无异常'; var h='',now=Date.now()/1000; for(var i=orphans.length-1;i>=0;i--){var o=orphans[i],ago=(now-o.timestamp).toFixed(0);h+='<div style="background:#ff440010;border-left:3px solid #ff6644;padding:4px 8px;margin:3px 0;font-size:0.8em;"><span style="color:#666;">'+(ago<120?ago+'秒前':Math.floor(ago/60)+'分钟前')+'</span> ⚠️ '+o.organ+' → '+o.event_type+' ('+o.layer+') '+o.error+'</div>';} return h||'✅ 暂无异常'; }
function fetchEmotionLog() { loadLog('/data/stream/hormone_emotion_log.json', null, function(logs) { var el=document.getElementById('emotionCurrent'); if(!logs||!logs.length){ if(el)el.textContent='--'; return; } var latest=logs[logs.length-1]; var emoji={'喜悦':'😊','悲伤':'😢','愤怒':'😡','恐惧':'😨','惊讶':'😲','厌恶':'🤢','中性':'😐'}; if(el)el.textContent=(emoji[latest.emotion]||'')+' '+latest.emotion; el=document.getElementById('emotionIntensity'); if(el)el.textContent=latest.intensity.toFixed(2); }); }

// ========== 运行时指标（真实框架运行状态） ==========
function fetchRuntimeMetrics() {
    fetch('/runtime/metrics').then(function(r){ return r.json(); }).then(function(d) {
        var el;
        el = document.getElementById('rtPulseCount'); if (el) el.textContent = (d.pulse_count || 0) + ' 次';
        el = document.getElementById('rtPulseAvg'); if (el) el.textContent = (d.pulse_avg_ms || 0) + ' ms';
        el = document.getElementById('rtPulseMax'); if (el) el.textContent = (d.pulse_max_ms || 0) + ' ms';
        el = document.getElementById('rtLockWait'); if (el) el.textContent = (d.lock_wait_avg_ms || 0) + ' ms';
        el = document.getElementById('rtThreads'); if (el) el.textContent = (d.thread_count || 0);
        el = document.getElementById('rtQueueDepth'); if (el) el.textContent = (d.queue_max_depth || 0);
        el = document.getElementById('rtReentry'); if (el) el.textContent = (d.reentry_count || 0) + ' 次';
        el = document.getElementById('rtErrors'); if (el) el.textContent = (d.pulse_errors || 0) + ' 次';
        var snapshots = d.error_snapshots || [];
        var snapEl = document.getElementById('rtErrorSnapshots');
        if (snapEl) {
            if (snapshots.length === 0) {
                snapEl.innerHTML = '<span style="color:#666;">暂无异常</span>';
            } else {
                var h = '';
                for (var i=snapshots.length-1; i>=0; i--) {
                    var s = snapshots[i];
                    var t = new Date(s.timestamp*1000).toLocaleTimeString();
                    h += '<div style="font-size:0.75em;padding:2px 0;border-bottom:1px solid #151525;">' +
                         '<span style="color:#666;">'+t+'</span> ' +
                         '<span style="color:#ffaa00;">'+escapeHtml(s.pulse_type||'?')+'</span> ' +
                         '<span style="color:#ff4444;">'+escapeHtml((s.error||'').substring(0,80))+'</span></div>';
                }
                snapEl.innerHTML = h;
            }
        }
    }).catch(function(){});
}

// ========== 参数进化面板 ==========
function loadParamsData() {
    fetch('/params/data').then(r=>r.json()).then(d=>{
        var el;
        el = document.getElementById('paramTotal'); if (el) el.textContent = d.total_patches || 0;
        el = document.getElementById('paramApplied'); if (el) el.textContent = d.applied || 0;
        el = document.getElementById('paramRolledBack'); if (el) el.textContent = d.rolled_back || 0;
        el = document.getElementById('paramVerified'); if (el) el.textContent = d.effect_verified || 0;
        el = document.getElementById('paramPositive'); if (el) el.textContent = d.effect_positive || 0;
        el = document.getElementById('currentPreset'); if (el) el.textContent = d.current_preset || '默认';
        el = document.getElementById('presetDesc'); if (el) el.textContent = d.preset_description || '使用默认参数';
        el = document.getElementById('presetTime'); if (el) el.textContent = d.preset_time || '--';
        el = document.getElementById('runtimeParamsCount'); if (el) el.textContent = d.runtime_params_count || '--';
        el = document.getElementById('organsWithRefresh'); if (el) el.textContent = d.organs_with_refresh || '--';
        el = document.getElementById('configChanges'); if (el) el.textContent = d.config_changes || 0;
        el = document.getElementById('lastHotReload'); if (el) el.textContent = d.last_hot_reload || '--';

        // 预设方案下拉框
        var select = document.getElementById('presetSelect');
        if (select && d.presets) {
            select.innerHTML = '<option value="">选择预设方案...</option>';
            for (var i=0; i<d.presets.length; i++) {
                var p = d.presets[i];
                select.innerHTML += '<option value="'+p.key+'">'+p.name+' ('+Object.keys(p.params||{}).length+'参数)</option>';
            }
        }

        // 补丁列表
        var patchList = document.getElementById('paramPatchList');
        if (patchList) {
            if (!d.patches || d.patches.length === 0) {
                patchList.innerHTML = '<span style="color:#666;">暂无补丁记录</span>';
            } else {
                var html = '';
                for (var i=0; i<Math.min(d.patches.length, 20); i++) {
                    var p = d.patches[i];
                    var statusColor = p.status === 'applied' ? '#00ff88' : (p.status === 'rolled_back' ? '#ff4444' : '#ffaa00');
                    var statusLabel = p.status === 'applied' ? '已应用' : (p.status === 'rolled_back' ? '已回滚' : p.status);
                    var effectText = p.effect_verified ? (p.effect_score > 0 ? '✅正向' : '❌负向') : '⏳待验证';
                    html += '<div style="padding:6px 0;border-bottom:1px solid #151525;font-size:0.8em;">' +
                        '<span style="color:'+statusColor+';">['+statusLabel+']</span> ' +
                        '<span style="color:#00d4ff;">'+p.param+'</span>: ' +
                        '<span style="color:#888;">'+p.old_value+' → '+p.new_value+'</span> ' +
                        '<span style="color:#666;">'+effectText+'</span>' +
                        '</div>';
                }
                patchList.innerHTML = html;
            }
        }

        // 参数相关性
        var corrEl = document.getElementById('paramCorrelations');
        if (corrEl) {
            if (!d.correlations || d.correlations.length === 0) {
                corrEl.innerHTML = '<span style="color:#666;">数据不足，无法分析相关性</span>';
            } else {
                var html = '';
                for (var i=0; i<d.correlations.length; i++) {
                    var c = d.correlations[i];
                    var strengthColor = c.strength === '强' ? '#00ff88' : (c.strength === '中' ? '#ffaa00' : '#666');
                    html += '<div style="padding:4px 0;border-bottom:1px solid #151525;font-size:0.8em;">' +
                        '<span style="color:#00d4ff;">'+c.param1+'</span> ↔ ' +
                        '<span style="color:#00d4ff;">'+c.param2+'</span> ' +
                        '<span style="color:'+strengthColor+';">['+c.strength+']</span> ' +
                        '<span style="color:#888;">'+c.jaccard_similarity+'</span>' +
                        '</div>';
                }
                corrEl.innerHTML = html;
            }
        }

        // 变更历史
        var historyEl = document.getElementById('paramChangeHistory');
        if (historyEl) {
            if (!d.change_history || d.change_history.length === 0) {
                historyEl.innerHTML = '<span style="color:#666;">暂无变更记录</span>';
            } else {
                var html = '';
                for (var i=0; i<Math.min(d.change_history.length, 30); i++) {
                    var ch = d.change_history[i];
                    html += '<div style="padding:3px 0;border-bottom:1px solid #151525;font-size:0.75em;">' +
                        '<span style="color:#666;">'+ch.timestamp+'</span> ' +
                        '<span style="color:#00d4ff;">'+ch.param+'</span>: ' +
                        '<span style="color:#888;">'+ch.old_value+' → '+ch.new_value+'</span> ' +
                        '<span style="color:#555;">('+ch.source+')</span>' +
                        '</div>';
                }
                historyEl.innerHTML = html;
            }
        }
    }).catch(function(e){
        console.error('加载参数数据失败:', e);
    });
}

function applyPreset() {
    var select = document.getElementById('presetSelect');
    var presetKey = select.value;
    if (!presetKey) { alert('请先选择预设方案'); return; }
    if (!confirm('确定应用此预设方案吗？参数将在5秒内热加载生效。')) return;
    fetch('/params/apply_preset?key=' + encodeURIComponent(presetKey), {method: 'POST'})
        .then(r=>r.json()).then(d=>{
            alert(d.message || '应用完成');
            loadParamsData();
        }).catch(function(e){ alert('应用失败: ' + e); });
}

function refreshParams() {
    loadParamsData();
}

// ========== 定时器 ==========
fetchData(); setInterval(fetchData, 1000);
fetchRuntimeMetrics(); setInterval(fetchRuntimeMetrics, 3000);
setInterval(function(){ loadLog('/data/stream/vision_stream_log.json','visionLog',renderVision); }, 2000);
setInterval(function(){ loadLog('/data/stream/eye_stream_log.json','eyeLog',renderEye); }, 2000);
setInterval(function(){ loadLog('/data/stream/ear_stream_log.json','earLog',renderEar); }, 2000);
setInterval(function(){ loadLog('/data/monitor/pulse_trace_events.json','traceEvents',renderTrace); }, 1000);
setInterval(function(){ loadLog('/data/stream/mouth_tts_log.json','mouthLog',renderMouth); }, 2000);
setInterval(function(){ loadLog('/data/monitor/pulse_orphans.json','orphanEvents',renderOrphans); }, 2000);
fetchEmotionLog(); setInterval(fetchEmotionLog, 2000);
fetchLegsLog(); setInterval(fetchLegsLog, 2000);
loadLog('/data/stream/vision_stream_log.json','visionLog',renderVision);
loadLog('/data/stream/eye_stream_log.json','eyeLog',renderEye);
loadLog('/data/stream/ear_stream_log.json','earLog',renderEar);
loadLog('/data/monitor/pulse_trace_events.json','traceEvents',renderTrace);
loadLog('/data/stream/mouth_tts_log.json','mouthLog',renderMouth);
loadLog('/data/monitor/pulse_orphans.json','orphanEvents',renderOrphans);
</script>
</body>
</html>
"""

class HealthHandler(BaseHTTPRequestHandler):
    """极简HTTP请求处理器"""

    def log_message(self, format, *args):
        """静默日志，不输出到控制台"""

    def do_GET(self):
        try:
            # 日志类 JSON 端点 → 统一走 _serve_json_file（★FIX: 消除 8 个重复方法）
            _log_routes = {
                '/data/monitor/pulse_trace_events.json': ('monitor', 'pulse_trace_events.json'),
                '/data/monitor/pulse_orphans.json': ('monitor', 'pulse_orphans.json'),
                '/data/stream/vision_stream_log.json': ('stream', 'vision_stream_log.json'),
                '/data/stream/eye_stream_log.json': ('stream', 'eye_stream_log.json'),
                '/data/stream/ear_stream_log.json': ('stream', 'ear_stream_log.json'),
                '/data/stream/mouth_tts_log.json': ('stream', 'mouth_tts_log.json'),
                '/data/stream/legs_learn_log.json': ('stream', 'legs_learn_log.json'),
                '/data/stream/hormone_emotion_log.json': ('stream', 'hormone_emotion_log.json'),
            }
            if self.path == '/' or self.path == '/index.html':
                self._serve_html()
            elif self.path == '/data/monitor/health_snapshot.json':
                self._serve_snapshot()
            elif self.path in _log_routes:
                _sub, _file = _log_routes[self.path]
                self._serve_json_file(_sub, _file)
            elif self.path == '/llmdep/data':
                self._serve_llmdep()
            elif self.path == '/runtime/metrics':
                self._serve_runtime_metrics()
            elif self.path == '/runtime/metrics/history':
                self._serve_runtime_metrics_history()
            elif self.path == '/runtime/task_pipeline/history':
                self._serve_task_pipeline_history()
            # ===== 进化仪表盘 =====
            elif self.path == '/evolution':
                self._serve_evolution_page()
            elif self.path == '/evolution/data':
                self._serve_evolution_data()
            # ===== 知识图谱 =====
            elif self.path == '/knowledge-graph':
                self._serve_knowledge_graph_page()
            elif self.path == '/data/knowledge-graph.json':
                self._serve_knowledge_graph_data()
            elif self.path == '/params/data':
                self._serve_params_data()
            elif self.path == '/reports/alerts':
                self._serve_jsonl('reports', 'alerts.jsonl')
            elif self.path == '/reports/todo':
                self._serve_jsonl('reports', 'todo.jsonl')
            else:
                self.send_response(404)
                self.end_headers()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError) as e:
            silent_exc(e, where="functions.health_ui::do_GET L850")

    def _is_same_origin(self) -> bool:
        """同源校验：仅允许来自本面板的请求，防御 CSRF。"""
        from urllib.parse import urlparse
        _host = (self.headers.get('Host') or '').split(':')[0]
        _origin = self.headers.get('Origin', '')
        _referer = self.headers.get('Referer', '')
        if _origin:
            try:
                _op = urlparse(_origin)
                if _op.netloc and _op.netloc.split(':')[0] == _host and _host:
                    return True
            except Exception as e:
                silent_exc(e, where="functions.health_ui::_is_same_origin L864")
                return False
            return False
        if _referer:
            try:
                _rp = urlparse(_referer)
                if _rp.netloc and _rp.netloc.split(':')[0] == _host and _host:
                    return True
            except Exception as e:
                silent_exc(e, where="functions.health_ui::_is_same_origin L872")
                return False
        # 无 Origin/Referer 的同源简单请求（如同源 fetch、测试）放行
        return True

    def do_POST(self):
        try:
            if not self._is_same_origin():
                self.send_response(403)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": "CSRF: origin not allowed"}, ensure_ascii=False).encode('utf-8'))
                return
            if self.path.startswith('/params/apply_preset'):
                self._serve_apply_preset()
            else:
                self.send_response(404)
                self.end_headers()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError) as e:
            silent_exc(e, where="functions.health_ui::do_POST L890")

    def _serve_html(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(HEALTH_UI_HTML.encode('utf-8'))

    def _serve_snapshot(self):
        """提供最新的健康快照JSON数据"""
        snapshot_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data', 'monitor', 'health_snapshot.json'
        )
        try:
            if os.path.exists(snapshot_path):
                with open(snapshot_path, encoding='utf-8') as f:
                    data = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache')

                self.end_headers()
                self.wfile.write(data.encode('utf-8'))
            else:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write('{"status":"waiting","message":"等待首次快照..."}'.encode('utf-8'))  # noqa: UP012
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f'{{"error":"{e!s}"}}'.encode())

    def _serve_json_file(self, subdir: str, filename: str):
        """统一提供 data/{subdir}/{filename} JSON 文件（★FIX: 消除 8 个重复 serve 方法）"""
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _path = os.path.join(_root, 'data', subdir, filename)
        try:
            if os.path.exists(_path):
                with open(_path, encoding='utf-8') as f:
                    _data = f.read()
                _body = _data.encode('utf-8')
            else:
                _body = b'[]'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(_body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError) as e:
            silent_exc(e, where="functions.health_ui::_serve_json_file L945")
        except Exception:
            try:
                self.send_response(500)
                self.end_headers()
            except Exception as e:
                silent_exc(e, where="functions.health_ui::_serve_json_file L951")

    def _serve_jsonl(self, subdir: str, filename: str):
        """★157 T-报告契约-3：提供 data/{subdir}/{filename} JSONL 的只读端点。

        reports 消费者（nucleus/reporting/consumers.py）把 P0 告警 / 清洗建议待办 /
        自认知跟进待办写入 data/reports/alerts.jsonl、data/reports/todo.jsonl，
        但此前全仓无读取方（写入即二阶断点）。本端点把 JSONL 逐行解析为 JSON 数组
        返回，使行为建议可经健康面板只读查阅。文件缺失时返回 []。
        """
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _path = os.path.join(_root, 'data', subdir, filename)
        try:
            if os.path.exists(_path):
                _rows = []
                with open(_path, encoding='utf-8') as f:
                    for _line in f:
                        _line = _line.strip()
                        if not _line:
                            continue
                        try:
                            _rows.append(json.loads(_line))
                        except Exception as e:
                            silent_exc(e, where="functions.health_ui::_serve_jsonl Lparse")
                _body = json.dumps(_rows, ensure_ascii=False).encode('utf-8')
            else:
                _body = b'[]'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(_body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError) as e:
            silent_exc(e, where="functions.health_ui::_serve_jsonl Lconn")
        except Exception:
            try:
                self.send_response(500)
                self.end_headers()
            except Exception as e:
                silent_exc(e, where="functions.health_ui::_serve_jsonl L500")

    def _serve_runtime_metrics(self):
        """提供真实运行时指标（★FIX: 反映框架真实运行状态，替代硬编码假数据）"""
        try:
            from nucleus.runtime_metrics import get_runtime_metrics
            _rt = get_runtime_metrics().get_snapshot()
            # ★主线B(B1)：lock_wait_avg_ms 由 runtime_metrics 用滑动窗口计算，
            #   直接取快照值，避免此处用累计值（lock_wait_total_ms/lock_wait_count）导致「冻结」。
            _lock_wait_count = _rt.get('lock_wait_count', 0)
            _avg_lock = _rt.get('lock_wait_avg_ms', 0.0)
            _data = {
                "pulse_count": _rt.get("pulse_count", 0),
                "pulse_errors": _rt.get("pulse_errors", 0),
                "pulse_avg_ms": round(_rt.get("pulse_avg_ms", 0.0), 2),
                "pulse_max_ms": round(_rt.get("pulse_max_ms", 0.0), 2),
                "lock_wait_avg_ms": round(_avg_lock, 3),
                "lock_wait_count": _lock_wait_count,
                "queue_max_depth": _rt.get("queue_max_depth", 0),
                "thread_count": _rt.get("thread_count", 0),
                "reentry_count": _rt.get("reentry_count", 0),
                "error_snapshots": _rt.get("error_snapshots", [])[-10:],
            }
        except Exception:
            _data = {"pulse_count": 0, "pulse_errors": 0, "pulse_avg_ms": 0, "error_snapshots": []}
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(json.dumps(_data, ensure_ascii=False).encode('utf-8'))

    def _serve_llmdep(self):
        """★第十一批 任务1（B-7）：LLM 依赖度面板数据源。

        返回 nucleus.LLMDependencyMetrics 的指标快照（4 类计数器 + 依赖度 /
        自持力派生指标），供「LLM 依赖度」卡片展示。度量模块不可用时返回空
        对象，前端兜底显示 0，绝不影响面板其余部分。
        """
        try:
            from nucleus.LLMDependencyMetrics import get_dependency_snapshot
            _data = get_dependency_snapshot() or {}
        except Exception:
            _data = {}
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(json.dumps(_data, ensure_ascii=False).encode('utf-8'))

    def _serve_runtime_metrics_history(self):
        """★主线B(B4)：提供运行时指标的历史趋势（时间序列环形缓冲）。

        供健康面板展示脉冲耗时/错误数/锁等待/队列深度随时间的变化，
        让「当前快照」升级为「可看趋势」，支撑运维判断指标是恶化还是改善。
        """
        try:
            from nucleus.runtime_metrics import get_runtime_metrics
            _rt = get_runtime_metrics()
            # 默认返回最近 120 个采样点（约 10 分钟，按 5 秒采样间隔）
            _history = _rt.get_history(limit=120)
            _data = {
                "sample_interval": getattr(_rt, "_sample_interval", 5.0),
                "points": _history,
            }
        except Exception:
            _data = {"sample_interval": 5.0, "points": []}
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(json.dumps(_data, ensure_ascii=False).encode('utf-8'))

    def _serve_task_pipeline_history(self):
        """★B5【P2】：提供最近 N 个 TaskPipeline（元流程实体）的状态流转记录。

        返回内存环形缓冲中的任务流水线序列化记录。        当前 TaskPipeline 为实验性预埋接口，尚未接入
        真实任务流，返回空列表属正常——端点已就绪，待接入后自动有数据。
        """
        try:
            from nucleus.TaskPipeline import get_recent_pipelines
            _pipelines = get_recent_pipelines(limit=50)
            _data = {"count": len(_pipelines), "pipelines": _pipelines}
        except Exception:
            _data = {"count": 0, "pipelines": []}
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(json.dumps(_data, ensure_ascii=False).encode('utf-8'))

    def _compute_module_scores(self) -> list:
        """根据真实运行时指标与静态审查计算模块健康评分（★FIX: 替代硬编码 85/78/82/90）"""
        _modules = []

        # 代码健康：静态审查问题数
        _code_score = 85
        try:
            from nucleus.self_inspector import get_self_inspector
            _issues = get_self_inspector().detect_code_issues()
            _code_score = max(50, 95 - len(_issues))
        except Exception as e:
            silent_exc(e, where="functions.health_ui::_compute_module_scores L1049")
        _modules.append({"name": "代码健康", "score": _code_score})

        # 运行稳定性：脉冲错误数 + 重入次数
        _runtime_score = 90
        try:
            from nucleus.runtime_metrics import get_runtime_metrics
            _rt = get_runtime_metrics().get_snapshot()
            _errors = _rt.get("pulse_errors", 0)
            _reentry = _rt.get("reentry_count", 0)
            _runtime_score = max(40, 95 - _errors * 2 - _reentry * 3)
        except Exception as e:
            silent_exc(e, where="functions.health_ui::_compute_module_scores L1061")
        _modules.append({"name": "运行稳定性", "score": _runtime_score})

        # 资源管理：队列峰值深度
        _resource_score = 90
        try:
            from nucleus.runtime_metrics import get_runtime_metrics
            _rt = get_runtime_metrics().get_snapshot()
            _depth = _rt.get("queue_max_depth", 0)
            _resource_score = max(50, 95 - max(0, _depth - 200) // 20)
        except Exception as e:
            silent_exc(e, where="functions.health_ui::_compute_module_scores L1072")
        _modules.append({"name": "资源管理", "score": _resource_score})

        # 知识质量：知识节点总数
        _knowledge_score = 80
        try:
            if _node_pool:
                _total = len(_node_pool.get_all())
                _knowledge_score = 75 if _total > 0 else 50
        except Exception as e:
            silent_exc(e, where="functions.health_ui::_compute_module_scores L1082")
        _modules.append({"name": "知识质量", "score": _knowledge_score})

        return _modules

    def _serve_evolution_page(self):
        """提供进化仪表盘HTML页面"""
        html = r'''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>曈曈 · 进化仪表盘</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Microsoft YaHei', sans-serif; background: #0a0a0f; color: #e0e0e0; padding: 20px; }
.header { text-align: center; padding: 20px; }
.header h1 { font-size: 28px; color: #00d4ff; }
.header p { color: #666; margin-top: 5px; }
.dashboard { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; max-width: 1200px; margin: 0 auto; }
.card { background: #0d0d1a; border-radius: 12px; padding: 20px; border: 1px solid #1a1a2e; }
.card h2 { font-size: 18px; color: #00d4ff; margin-bottom: 15px; border-bottom: 1px solid #1a1a2e; padding-bottom: 10px; }
.score-circle { width: 120px; height: 120px; border-radius: 50%; border: 8px solid #2a2a3a; display: flex; align-items: center; justify-content: center; margin: 20px auto; font-size: 36px; font-weight: bold; }
.timeline { max-height: 400px; overflow-y: auto; }
.timeline-item { padding: 10px; border-left: 3px solid #00d4ff; margin: 10px 0; background: #14141f; border-radius: 0 8px 8px 0; }
.timeline-item .time { font-size: 12px; color: #555; }
.patch-item { padding: 12px; margin: 8px 0; background: #14141f; border-radius: 8px; border: 1px solid #2a2a3a; }
.module-bar { margin: 10px 0; }
.module-bar .label { display: flex; justify-content: space-between; margin-bottom: 4px; font-size: 13px; }
.module-bar .bar { height: 8px; border-radius: 4px; background: #2a2a3a; }
.module-bar .fill { height: 100%; border-radius: 4px; transition: width 0.5s; }
.refresh { text-align: center; margin-top: 20px; color: #555; font-size: 12px; }
</style>
</head>
<body>
<div class="header">
    <h1>🧬 曈曈 · 进化仪表盘</h1>
    <p>自我审视 · 策略推演 · 安全进化</p>
</div>
<div class="dashboard" id="dashboard">
    <div class="card">
        <h2>综合健康评分</h2>
        <div class="score-circle" id="score-circle">--</div>
        <div style="text-align:center;color:#888;" id="score-label">加载中...</div>
    </div>
    <div class="card">
        <h2>模块健康</h2>
        <div id="module-bars">加载中...</div>
    </div>
    <div class="card">
        <h2>进化时间线</h2>
        <div class="timeline" id="timeline">加载中...</div>
    </div>
    <div class="card">
        <h2>待处理建议</h2>
        <div id="patches">加载中...</div>
    </div>
</div>
<div class="refresh">每60秒自动刷新 · 最后更新：<span id="last-update">--</span></div>
<script>
async function loadData() {
    try {
        const resp = await fetch('/evolution/data');
        const data = await resp.json();
        renderScore(data.score);
        renderModules(data.modules);
        renderTimeline(data.timeline);
        renderPatches(data.patches);
        document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
    } catch(e) {
        console.error('加载进化数据失败:', e);
    }
}
function renderScore(score) {
    const circle = document.getElementById('score-circle');
    const label = document.getElementById('score-label');
    circle.textContent = score || '--';
    circle.style.borderColor = score >= 90 ? '#4CAF50' : score >= 75 ? '#8BC34A' : score >= 60 ? '#FF9800' : '#f44336';
    circle.style.color = score >= 90 ? '#4CAF50' : score >= 75 ? '#8BC34A' : score >= 60 ? '#FF9800' : '#f44336';
    label.textContent = score >= 90 ? '优秀' : score >= 75 ? '良好' : score >= 60 ? '一般' : '需要关注';
}
function renderModules(modules) {
    const container = document.getElementById('module-bars');
    if (!modules || modules.length === 0) { container.innerHTML = '<p style="color:#666;">暂无数据</p>'; return; }
    container.innerHTML = modules.map(m => 
        '<div class="module-bar"><div class="label"><span>'+m.name+'</span><span>'+m.score+'/100</span></div>' +
        '<div class="bar"><div class="fill" style="width:'+m.score+'%;background:'+(m.score>=80?'#4CAF50':m.score>=60?'#FF9800':'#f44336')+'"></div></div></div>'
    ).join('');
}
function renderTimeline(timeline) {
    const container = document.getElementById('timeline');
    if (!timeline || timeline.length === 0) { container.innerHTML = '<p style="color:#666;">暂无进化事件</p>'; return; }
    container.innerHTML = timeline.map(t => 
        '<div class="timeline-item"><div class="time">'+t.time+'</div><div class="content">'+t.event+'</div></div>'
    ).join('');
}
function renderPatches(patches) {
    const container = document.getElementById('patches');
    if (!patches || patches.length === 0) { container.innerHTML = '<p style="color:#4CAF50;">✅ 没有待处理的建议</p>'; return; }
    container.innerHTML = patches.map(p => 
        '<div class="patch-item"><div><strong>'+p.type+'</strong> · '+p.target+'</div>' +
        '<div style="font-size:13px;color:#aaa;margin-top:4px;">'+p.description+'</div></div>'
    ).join('');
}
loadData();
setInterval(loadData, 60000);
</script>
</body>
</html>
'''
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def _serve_evolution_data(self):
        """提供进化数据JSON API"""
        data = {
            "score": 85,
            "modules": [],
            "timeline": [],
            "patches": [],
        }
        
        try:
            from nucleus.InsightBoard import get_insight_board
            board = get_insight_board()
            
            # 获取最新深度审视评分
            reviews = board.query(insight_type="deep_self_review", limit=1)
            if reviews:
                import re
                content = reviews[0].get("content", "")
                score_match = re.search(r'评分[：:](\d+)', content)
                if score_match:
                    data["score"] = int(score_match.group(1))
            
            # 进化事件时间线
            all_events = board.query(limit=20)
            for evt in sorted(all_events, key=lambda e: e.get("posted_at", 0), reverse=True)[:10]:
                age = evt.get("age_seconds", 0)
                time_str = f"{age/3600:.1f}小时前" if age > 3600 else f"{max(1, int(age/60))}分钟前"
                data["timeline"].append({
                    "time": time_str,
                    "event": evt.get("content", "")[:120],
                })
            
            # 待处理补丁
            patches = board.query(insight_type="code_patch", limit=10)
            for p in patches[:5]:
                data["patches"].append({
                    "type": (p.get("keywords") or ["修复"])[0],
                    "target": p.get("related_dimension", "未知"),
                    "description": p.get("content", "")[:100],
                })

            # ★健康面板数据闭环：显式聚合健康/状态类洞察（此前仅写未读）
            # ★PHASE12-P1-4（2026-09-06）：补齐 5 类「写了没人读」的死洞察。
            #   这些类型都有实际写入方，且 InsightBoard 已为其定义 TTL，
            #   但此前不在此读取清单内 → 产出即沉没，健康面板永远看不到：
            #       eureka_moment          ← organs/brain/PulseInnerWorld.py:14175（顿悟）
            #       evolution_plan         ← organs/brain/PulseInnerWorld.py:13944（进化方案）
            #       code_health_improvement← main.py:2473（自进化健康改善）
            #       knowledge_association  ← organs/body/PulseLiver.py:1951（知识关联）
            #       temporal_self_insight  ← organs/identity/PulseSelfAwareness.py:1047（时间自我）
            #   补齐后，自进化链路的产出与顿悟/进化方案首次对健康面板可见，
            #   小林能直接看到「曈曈自己想了什么、改了什么」。
            _health_types = {
                "comprehensive_diagnosis": "综合诊断",
                "startup_health": "启动健康",
                "model_quality_feedback": "模型质量",
                "boundary_reinforcement": "边界加固",
                "knowledge_contradiction": "知识矛盾",
                "modification_suggestion": "修改建议",
                # ★P1-4 新增（原为死洞察）
                "eureka_moment": "顿悟时刻",
                "evolution_plan": "进化方案",
                "code_health_improvement": "健康改善",
                "knowledge_association": "知识关联",
                "temporal_self_insight": "时间自我",
            }
            for _ht, _label in _health_types.items():
                try:
                    _items = board.query(insight_type=_ht, limit=3)
                    for _it in _items[:2]:
                        data["timeline"].append({
                            "time": _label,
                            "event": _it.get("content", "")[:120],
                        })
                except Exception:
                    silent_exc(where="functions/health_ui.py:1277")
                    continue
            
            # ★FIX: 模块评分由真实运行时指标 + 静态审查推导（替代硬编码假数据）
            _modules = self._compute_module_scores()
            data["modules"] = _modules
            # 若未从深度审视报告提取到评分，则用模块均值作为综合分
            if data["score"] == 85:
                data["score"] = round(sum(m["score"] for m in _modules) / max(1, len(_modules)))
            
        except Exception:
            data["timeline"].append({
                "time": "现在",
                "event": "进化仪表盘已就绪，等待首次深度审视报告生成。"
            })
        
        result = json.dumps(data, ensure_ascii=False)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        
        self.end_headers()
        self.wfile.write(result.encode('utf-8'))

    def _serve_knowledge_graph_page(self):
        """提供知识图谱可视化页面（文本版）"""
        html = r'''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>曈曈 · 知识图谱</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Microsoft YaHei', sans-serif; background: #0a0a0f; color: #e0e0e0; padding: 20px; }
.header { margin-bottom: 20px; }
.header h1 { font-size: 22px; color: #00d4ff; }
.header p { color: #666; font-size: 13px; margin-top: 3px; }
.search-box { margin-bottom: 10px; display: flex; gap: 8px; }
.search-box input { flex: 1; background: #0d0d1a; border: 1px solid #1a1a2e; color: #e0e0e0; padding: 6px 10px; border-radius: 6px; font-family: inherit; }
.search-box button { background: #00d4ff; color: #0a0a0f; border: none; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-weight: bold; }
.search-box button:hover { background: #00b8e6; }
.tree-container { max-height: 80vh; overflow-y: auto; padding: 10px; background: #0d0d1a; border-radius: 8px; border: 1px solid #1a1a2e; font-family: 'Consolas', 'Microsoft YaHei', monospace; }
.tree-node { padding: 4px 0; border-bottom: 1px solid #151525; }
.lvl { display: inline-block; width: 28px; text-align: center; border-radius: 4px; font-size: 0.7em; font-weight: bold; margin-right: 8px; }
.lvl-L4 { background: #ff4477; color: #fff; }
.lvl-L3 { background: #ffaa00; color: #000; }
.lvl-L2 { background: #00d4ff; color: #000; }
.lvl-L1 { background: #555; color: #aaa; }
.path-tag { display: inline-block; background: #1a1a2e; color: #aaa; padding: 1px 6px; border-radius: 3px; font-size: 0.7em; margin-right: 4px; }
.tree-label { color: #e0e0e0; font-weight: bold; margin-left: 10px; }
.tree-trust { color: #ffaa00; font-size: 0.8em; margin-left: 8px; }
.tree-value { color: #888; font-size: 0.75em; margin-left: 8px; word-break: break-all; }
.tree-indent { margin-left: 20px; border-left: 1px solid #222; padding-left: 10px; }
.tree-group { margin-bottom: 15px; }
.tree-group-title { color: #00d4ff; font-size: 0.9em; font-weight: bold; padding: 5px 0; border-bottom: 1px solid #1a1a2e; margin-bottom: 5px; cursor: pointer; }
.tree-group-title:hover { color: #fff; }
.refresh { text-align: center; margin-top: 10px; color: #666; font-size: 12px; }
</style>
</head>
<body>
<div class="header">
    <h1>🧠 曈曈 · 知识图谱</h1>
    <p>可选中、可复制的文本树状结构</p>
</div>
<div class="search-box">
    <input type="text" id="kg-search" placeholder="搜索节点（关键词/路径/层级）..." oninput="filterKG()">
    <button onclick="refreshKG()">🔄 刷新</button>
</div>
<div style="margin-bottom:10px;font-size:0.75em;color:#666;">总节点：<span id="kg-total-nodes">0</span> · 连线：<span id="kg-total-links">0</span> · 最后更新：<span id="kg-update-time">--</span></div>
<div class="tree-container" id="kg-tree-container">加载中...</div>
<div class="refresh">每30秒自动刷新</div>
<script>
var kgAllNodes = [], kgAllLinks = [];
function refreshKG() { loadKGData(); }
function loadKGData() {
    fetch('/data/knowledge-graph.json').then(r=>r.json()).then(d=>{
        kgAllNodes = d.nodes || [];
        kgAllLinks = d.links || [];
        document.getElementById('kg-total-nodes').textContent = kgAllNodes.length;
        document.getElementById('kg-total-links').textContent = kgAllLinks.length;
        document.getElementById('kg-update-time').textContent = new Date().toLocaleTimeString();
        filterKG();
    });
}
function filterKG() {
    var query = document.getElementById('kg-search').value.trim().toLowerCase();
    var filtered = kgAllNodes;
    if (query) {
        filtered = kgAllNodes.filter(function(n) {
            var label = (n.label || '').toLowerCase();
            var path = (n.path || '').toLowerCase();
            var level = (n.level || '').toLowerCase();
            return label.indexOf(query) >= 0 || path.indexOf(query) >= 0 || level.indexOf(query) >= 0;
        });
    }
    renderKGTree(filtered);
}
function renderKGTree(nodes) {
    var container = document.getElementById('kg-tree-container');
    if (!nodes.length) { container.innerHTML = '<span style="color:#666;">暂无匹配节点</span>'; return; }
    var groups = {};
    for (var i=0; i<nodes.length; i++) {
        var n = nodes[i];
        var path = n.path || '/';
        var root = path.split('/')[1] || '根';
        if (!groups[root]) groups[root] = [];
        groups[root].push(n);
    }
    var html = '';
    var rootNames = Object.keys(groups).sort();
    var levelOrder = {'L4':1,'L3':2,'L2':3,'L1':4};
    for (var r=0; r<rootNames.length; r++) {
        var root = rootNames[r];
        var groupNodes = groups[root];
        groupNodes.sort(function(a,b){ return (levelOrder[a.level]||5) - (levelOrder[b.level]||5); });
        html += '<div class="tree-group">';
        html += '<div class="tree-group-title" onclick="toggleGroup(this)">📁 /' + escapeHtml(root) + ' (' + groupNodes.length + '个节点)</div>';
        html += '<div class="tree-indent" style="display:block;">';
        for (var j=0; j<groupNodes.length; j++) {
            var node = groupNodes[j];
            var level = node.level || 'L1';
            var label = node.label || '?';
            var trust = node.trust || 0;
            var path = node.path || '/';
            var value = node.value || '';
            html += '<div class="tree-node">';
            html += '<span class="lvl lvl-' + level + '">' + level + '</span>';
            html += '<span class="path-tag">' + escapeHtml(path) + '</span>';
            html += '<span class="tree-label">' + escapeHtml(label) + '</span>';
            html += '<span class="tree-trust">信任:' + trust.toFixed(0) + '</span>';
            if (value) html += '<span class="tree-value">' + escapeHtml(value.substring(0, 60)) + '</span>';
            html += '</div>';
        }
        html += '</div></div>';
    }
    container.innerHTML = html;
}
function escapeHtml(str) { return (str||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function toggleGroup(el) { var indent = el.nextElementSibling; indent.style.display = indent.style.display === 'none' ? 'block' : 'none'; }
loadKGData();
setInterval(loadKGData, 30000);
</script>
</body>
</html>
'''
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def _serve_knowledge_graph_data(self):
        """提供知识图谱数据JSON（添加节点value信息）"""
        data = {"nodes": [], "links": []}
        
        if not _node_pool:
            result = json.dumps(data, ensure_ascii=False)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            
            self.end_headers()
            self.wfile.write(result.encode('utf-8'))
            return
        
        try:
            all_nodes = _node_pool.get_all()
            # 构建节点数据（添加value字段）
            for node in all_nodes:
                level = node.evol_level if hasattr(node, 'evol_level') else 'L1'
                trust = getattr(node, 'trust_score', 50.0)
                value = str(node.value)[:80] if node.value else ""
                kws = node.keywords if hasattr(node, 'keywords') and node.keywords else []
                label = kws[0] if kws else value[:15]
                path = getattr(node, 'space_path', '/')
                
                data["nodes"].append({
                    "id": node.node_id,
                    "label": label,
                    "level": level,
                    "trust": round(trust, 1),
                    "path": path,
                    "value": value,
                })
            
            # 构建连线数据（基于关键词重叠）
            node_index = {n["id"]: i for i, n in enumerate(data["nodes"])}
            for i in range(len(all_nodes)):
                for j in range(i + 1, min(i + 30, len(all_nodes))):
                    node_a = all_nodes[i]
                    node_b = all_nodes[j]
                    kw_a = {k.lower() for k in (node_a.keywords or []) if isinstance(k, str) and len(k) >= 2}
                    kw_b = {k.lower() for k in (node_b.keywords or []) if isinstance(k, str) and len(k) >= 2}
                    if len(kw_a & kw_b) >= 2:
                        id_a = node_a.node_id
                        id_b = node_b.node_id
                        if id_a in node_index and id_b in node_index:
                            data["links"].append({
                                "source": node_index[id_a],
                                "target": node_index[id_b],
                            })
            
            if len(data["links"]) > 200:
                data["links"] = data["links"][:200]
            
        except Exception as e:
            data["error"] = str(e)[:80]
        
        result = json.dumps(data, ensure_ascii=False)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        
        self.end_headers()
        self.wfile.write(result.encode('utf-8'))

    def _serve_params_data(self):
        data = {
            "total_patches": 0, "applied": 0, "rolled_back": 0,
            "effect_verified": 0, "effect_positive": 0,
            "current_preset": "default", "preset_description": "default params",
            "preset_time": "--", "runtime_params_count": 0,
            "organs_with_refresh": 0, "config_changes": 0,
            "last_hot_reload": "--", "patches": [], "correlations": [],
            "change_history": [], "presets": [],
        }
        try:
            from nucleus.evolution.ParamAnalysisReport import (
                ParamAnalyzer,
                ParamPresets,
            )
            analyzer = ParamAnalyzer()
            stats = analyzer.get_statistics()
            data["total_patches"] = stats.get("total_patches", 0)
            data["applied"] = stats.get("applied", 0)
            data["rolled_back"] = stats.get("rolled_back", 0)
            data["effect_verified"] = stats.get("effect_verified", 0)
            data["effect_positive"] = stats.get("effect_positive", 0)
            patches = analyzer.load_patch_history()
            for p in patches[-20:][::-1]:
                data["patches"].append({
                    "param": p.get("param", "?"),
                    "old_value": p.get("old_value", "?"),
                    "new_value": p.get("new_value", "?"),
                    "status": p.get("status", "?"),
                    "effect_verified": p.get("effect_verified", False),
                    "effect_score": p.get("effect_score", 0),
                    "source": p.get("source", "?"),
                })
            corr = analyzer.analyze_correlations()
            data["correlations"] = corr.get("correlations", [])[:10]
            presets = ParamPresets()
            data["presets"] = presets.list_presets()
        except Exception as e:
            silent_exc(e, where="functions.health_ui::_serve_params_data L1530")
            data["param_error"] = str(e)[:80]
        try:
            import config
            data["runtime_params_count"] = len(getattr(config, 'RUNTIME_PARAMS', {}))
        except Exception as e:
            silent_exc(e, where="functions.health_ui::_serve_params_data L1535")
        try:
            changes = []
            log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'config_changes.log')
            if os.path.exists(log_path):
                with open(log_path, encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                changes.append(json.loads(line))
                            except Exception as e:
                                silent_exc(e, where="functions.health_ui::_serve_params_data L1547")
            data["change_history"] = changes[-30:][::-1]
            data["config_changes"] = len(changes)
        except Exception as e:
            silent_exc(e, where="functions.health_ui::_serve_params_data L1551")
        try:
            override_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'config_override.json')
            if os.path.exists(override_path):
                override = safe_read_json(override_path, default={})
                data["current_preset"] = override.get("_last_preset_applied", "custom")
                data["preset_time"] = override.get("_last_preset_time", "--")
        except Exception as e:
            silent_exc(e, where="functions.health_ui::_serve_params_data L1559")
        result = json.dumps(data, ensure_ascii=False)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(result.encode('utf-8'))

    def _serve_apply_preset(self):
        try:
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            preset_key = params.get('key', [''])[0]
            if not preset_key:
                result = {"success": False, "message": "missing key"}
            else:
                from nucleus.evolution.ParamAnalysisReport import ParamPresets
                presets = ParamPresets()
                result = presets.apply_preset(preset_key)
        except Exception as e:
            result = {"success": False, "message": str(e)[:100]}
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))


class HealthUIServer:
    """人体UI监控服务器"""

    def __init__(self, port: int = 5051):
        self.port = port
        self._server: HTTPServer | None = None 
        self._thread: threading.Thread | None = None
        self._running = False
        self._watchdog_running = False
        self._watchdog: threading.Thread | None = None
        self.node_pool = None

    def start(self):
        self._server = ThreadingHTTPServer(('127.0.0.1', self.port), HealthHandler)
        self._running = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        # ★主线第104批 T-104c（D169）：5051 停服感知看门狗
        self._watchdog_running = True
        self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog.start()
        print(f"[人体UI] 监控面板已启动 → http://localhost:{self.port}")

    def stop(self):
        self._running = False
        self._watchdog_running = False
        if self._server:
            self._server.shutdown()

    def set_node_pool(self, node_pool):
        self.node_pool = node_pool
        global _node_pool
        _node_pool = node_pool

    # ===== ★主线第104批 T-104c（D169）：5051 停服感知看门狗 =====
    def _watchdog_loop(self):
        """监控 serve 线程存活；意外死亡则记入 error_snapshots 并告警（治理 5051 零感知）。"""
        import logging as _logging
        import time as _time
        _lg = _logging.getLogger("pulse.framework")
        while self._watchdog_running and self._running:
            _time.sleep(10)
            if not self._running or not self._watchdog_running:
                break
            if self._thread is not None and not self._thread.is_alive():
                try:
                    from nucleus.runtime_metrics import get_runtime_metrics
                    get_runtime_metrics().record_error(
                        pulse_type="health_ui",
                        error=f"5051 监控面板服务线程意外退出（端口 {self.port}）",
                        traceback_text="",
                        lock_held=False,
                    )
                except Exception as _wde:
                    _logging.getLogger("pulse.silent_except").debug("5051 看门狗记录失败: %s", _wde)
                _lg.warning("[人体UI] 5051 监控面板服务线程已停止，故障面板/HTTP 不可访问（D169 治理：现已可感知）")
                break


# 自测
if __name__ == "__main__":
    print("=== 人体UI监控面板自测 ===")
    server = HealthUIServer(5051)
    server.start()
    print("请打开浏览器访问 http://localhost:5051")
    print("按 Ctrl+C 退出")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
        print("已退出")